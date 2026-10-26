"""Overlay quadrillage visible (V1) · toggle + watchdog anti-blocage.

Usage :
  python scripts/grille.py on
  python scripts/grille.py off
  python scripts/grille.py bascule
  python scripts/grille.py test-exclusion
  python scripts/grille.py test-watchdog   # prouve que le watchdog tue un overlay bloque

Cause blocage 15/10 : overlay topmost plein ecran sans click-through
(WS_EX_TRANSPARENT perdu) → souris mangee ; Alt+Tab encore possible.
(Corrige depuis : click-through + watchdog ; details hors vitrine.)
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Le fichier d'etat est UNIQUE pour tout l'utilisateur. `VISION_GRILLE_STATE` le
# redirige : un test travaille alors sur SON fichier, sans jamais eteindre ni
# polluer l'overlay de l'utilisateur (15/10). Le drapeau et les journaux
# suivent le meme dossier.
STATE_PATH = Path(
    os.environ.get("VISION_GRILLE_STATE")
    or (Path(tempfile.gettempdir()) / "vision-public-grille-state.json")
)
ETAT_DIR = STATE_PATH.parent
FLAG_PATH = ETAT_DIR / "vision-public-grille-killed.flag"

import ctypes
from ctypes import wintypes

# Hors Windows, le module s'importe (les smokes le chargent) mais n'a pas de
# user32. L'overlay de ce fichier reste celui de Windows (pas d'overlay X11 ici).
try:
    user32 = ctypes.windll.user32
except AttributeError:
    user32 = None

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
LWA_ALPHA = 0x2
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GA_ROOT = 2

HEARTBEAT_MAX_AGE_S = 2.5
WATCHDOG_POLL_S = 0.4
STYLE_REFRESH_MS = 800
# Watchdog orphelin : sans fichier d'etat pendant ce delai, il s'arrete au
# lieu de boucler indefiniment en ecrivant son journal (audit V1).
SANS_ETAT_MAX_S = 5.0
# test-watchdog : l'overlay simule reste bloque AU PLUS ce delai, pour que
# seul le watchdog puisse le tuer avant. Avant, il se tuait lui-meme a 0,6 s
# et le test passait meme avec un watchdog casse (audit V1).
SIMULATION_MAX_MS = 5000
CLICKTHROUGH_MASK = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _write_state(data: dict) -> None:
    data = dict(data)
    data["ts"] = time.time()
    STATE_PATH.write_text(json.dumps(data), encoding="utf-8")


def _read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _clear_state() -> None:
    try:
        STATE_PATH.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        # os.kill(pid, 0) dit encore oui pour un zombie : le fils est mort,
        # le pere ne l'a pas encore ramasse. off() croirait alors qu'il vit.
        try:
            import psutil

            proc = psutil.Process(int(pid))
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:  # noqa: BLE001
            return False
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    ctypes.windll.kernel32.CloseHandle(h)
    return True


def _cree_a(pid: int) -> float | None:
    """Heure de creation d'un processus (psutil), None si illisible."""
    try:
        import psutil

        return float(psutil.Process(int(pid)).create_time())
    except Exception:  # noqa: BLE001
        return None


def _est_processus_grille(pid: int, cree_a: float | None = None) -> bool:
    """Ce numero designe-t-il VRAIMENT un overlay ou un watchdog de la grille ?

    POURQUOI (audit V1, mesure 15/10). `off()` tuait tout numero ecrit dans le
    fichier d'etat, avec `taskkill /T` (l'arbre entier). Demontre sur un
    processus jetable : tue sans broncher. Or ce fichier survit a un plantage
    ou a un redemarrage, et Windows REUTILISE les numeros de processus. Un
    `grille.py on` (qui commence par `off()`) pouvait donc fermer l'editeur,
    l'Explorateur ou n'importe quel programme, avec ses enfants.

    Deux preuves sont exigees : la ligne de commande (`grille.py` puis `_run`
    ou `_watchdog`) et, si elle est connue, l'heure de creation notee au
    lancement. Dans le doute, on NE tue PAS.
    """
    pid = int(pid)
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        import psutil

        p = psutil.Process(pid)
        args = p.cmdline()
        if cree_a is not None and abs(p.create_time() - float(cree_a)) > 1.0:
            return False
    except Exception:  # noqa: BLE001
        return False
    lance_grille = any(str(a).replace("\\", "/").endswith("grille.py") for a in args)
    role = any(a in ("_run", "_watchdog") for a in args)
    return lance_grille and role


def _kill_pid(pid: int, cree_a: float | None = None) -> bool:
    """Tue un PID de la grille ; True si une tentative a ete faite.

    Refuse tout processus qui n'est pas prouve comme etant de la grille
    (voir `_est_processus_grille`), et le dit.
    """
    pid = int(pid)
    if not _pid_alive(pid):
        return False
    if not _est_processus_grille(pid, cree_a):
        print(f"  PID {pid} ignore : pas un processus de la grille "
              "(numero reutilise ou etat perime)")
        return False
    # 1) signal Python (TerminateProcess sous Windows)
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.2)
    if not _pid_alive(pid):
        return True
    # 2) taskkill, SANS /T : l'overlay et le watchdog n'ont pas d'enfants, et
    # tuer un arbre entier sur un numero douteux est exactement le danger.
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        # Linux : pas de taskkill. Le signal fort finit ce que SIGTERM a commence.
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.25)
    if not _pid_alive(pid):
        return True
    # 3) Win32 brut
    if sys.platform != "win32":
        return not _pid_alive(pid)
    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
    if h:
        ctypes.windll.kernel32.TerminateProcess(h, 1)
        ctypes.windll.kernel32.WaitForSingleObject(h, 2000)
        ctypes.windll.kernel32.CloseHandle(h)
        time.sleep(0.1)
    return True


def _pid_dead(pid: int, wait_s: float = 2.0) -> bool:
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def _hwnd_has_clickthrough(hwnd: int) -> bool:
    if not hwnd:
        return False
    if not user32.IsWindow(wintypes.HWND(hwnd)):
        return False
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    return (ex & WS_EX_TRANSPARENT) == WS_EX_TRANSPARENT


if user32 is not None:
    user32.GetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(wintypes.DWORD),
    ]


def _couches_posees(hwnd: int) -> bool:
    """La fenetre a-t-elle deja des attributs de transparence (ceux de Tk) ?"""
    key, alpha, flags = wintypes.DWORD(), ctypes.c_ubyte(), wintypes.DWORD()
    return bool(
        user32.GetLayeredWindowAttributes(
            wintypes.HWND(hwnd), ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)
        )
    )


def _apply_clickthrough(hwnd: int) -> None:
    """Pose les styles click-through SANS toucher a la transparence de Tk.

    MESURE (audit V1, 15/10, sur le bureau de l'utilisateur) : l'ancienne version
    appelait `SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)`, ce qui
    REMPLACE la couleur transparente posee par Tk (`-transparentcolor`).
    Zone de test 160x160 : 0 % de pixels noirs avant l'appel, 97,5 % apres.
    L'overlay devenait un ecran noir opaque avec la grille par-dessus. Le
    correctif de 18:03, qui rejoue cette fonction toutes les 0,8 s, l'aurait
    applique en permanence sur les deux ecrans.

    On ne pose un alpha que si la fenetre n'a encore AUCUN attribut (une
    fenetre layered sans attributs ne s'affiche pas du tout), jamais
    par-dessus ceux de Tk. `SWP_NOACTIVATE` : la fenetre ne doit jamais
    prendre le focus (non reproduit, mais rien ne justifie de le permettre).
    """
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | CLICKTHROUGH_MASK)
    if not _couches_posees(hwnd):
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


def _resolve_hwnd(win) -> int:
    return int(user32.GetAncestor(int(win.winfo_id()), GA_ROOT) or win.winfo_id())


def off() -> int:
    st = _read_state()
    killed = 0
    # Chaque numero une seule fois, avec son heure de creation si on la
    # connait : c'est elle qui demasque un numero reutilise.
    cibles: dict[int, float | None] = {}
    for key, cle_cree in (("watchdog_pid", "watchdog_cree"), ("overlay_pid", "overlay_cree")):
        if st.get(key):
            cibles[int(st[key])] = st.get(cle_cree)
    for pid in st.get("pids", []):
        cibles.setdefault(int(pid), None)
    for pid, cree in cibles.items():
        if _kill_pid(pid, cree):
            killed += 1
    # Pas de filet « tuer tout grille.py » : ca suicidait le process `off`
    # (sa propre CommandLine matche). Les PID d'etat + /T suffisent.
    _clear_state()
    print(f"grille OFF (kills={killed})")
    return 0


def _set_exclude_from_capture(hwnd: int, enable: bool) -> bool:
    try:
        return bool(
            user32.SetWindowDisplayAffinity(
                wintypes.HWND(hwnd),
                WDA_EXCLUDEFROMCAPTURE if enable else WDA_NONE,
            )
        )
    except Exception:  # noqa: BLE001
        return False


def empreinte_ecrans(moniteurs: list) -> tuple:
    """Empreinte de la geometrie des ecrans (piege debranchement, 18/10).

    Idee : retour audit V1.2 § 11 · code : revue 18/10 · sortie en fonction de
    module (testable sans fenetre) : audit 18/10.

    Garde index, position, taille et ecran principal. Ignore volontairement :
      - le nom Windows (`\\\\.\\DISPLAYn`), qui s'est deja inverse sans que la
        geometrie bouge (sept. -> oct. 2026) ;
      - la zone de travail : la barre des taches qui bouge ne coupe pas la grille.
    """
    return tuple(
        (int(m.index), int(m.left), int(m.top), int(m.width), int(m.height), bool(m.is_primary))
        for m in moniteurs
    )


def _run_overlay(
    exclude_capture: bool = True,
    *,
    tint: bool = False,
    simulate_block: bool = False,
) -> None:
    """Boucle Tk + heartbeat. simulate_block : retire click-through apres 1.5s (test)."""
    import tkinter as tk

    from screen_context import list_monitors
    from screen_grid import grid_for_monitor, label_of

    mons = list_monitors()
    if not mons:
        print("aucun moniteur", file=sys.stderr)
        sys.exit(1)

    geo0 = empreinte_ecrans(mons)

    root = tk.Tk()
    root.withdraw()
    windows: list[tk.Toplevel] = []
    hwnds: list[int] = []
    blocked = {"done": False}
    t0 = time.time()
    log_path = ETAT_DIR / "vision-public-grille-overlay.log"

    def _log(msg: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f} {msg}\n")
        except Exception:  # noqa: BLE001
            pass

    def paint_grid(canvas: tk.Canvas, mon, spec) -> None:
        canvas.delete("all")
        for c in range(spec.cols + 1):
            x = int(round(c * spec.cell_w))
            if c == spec.cols:
                x = mon.width - 1
            canvas.create_line(x, 0, x, mon.height, fill="#00FF88", width=2)
        for r in range(spec.rows + 1):
            y = int(round(r * spec.cell_h))
            if r == spec.rows:
                y = mon.height - 1
            canvas.create_line(0, y, mon.width, y, fill="#00FF88", width=2)
        for row in range(spec.rows):
            for col in range(spec.cols):
                cx = int((col + 0.5) * spec.cell_w)
                cy = int((row + 0.5) * spec.cell_h)
                canvas.create_text(
                    cx,
                    cy,
                    text=label_of(spec.screen_index, col, row),
                    fill="#00FF88",
                    font=("Consolas", 11, "bold"),
                )

    for mon in mons:
        spec = grid_for_monitor(mon)
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.geometry(f"{mon.width}x{mon.height}+{mon.left}+{mon.top}")
        win.attributes("-topmost", True)
        if tint:
            win.configure(bg="#00FF00")
            win.attributes("-alpha", 0.45)
            canvas = tk.Canvas(
                win, width=mon.width, height=mon.height, bg="#00FF00", highlightthickness=0, bd=0
            )
            canvas.pack(fill="both", expand=True)
        else:
            try:
                win.attributes("-transparentcolor", "black")
            except Exception:  # noqa: BLE001
                win.attributes("-alpha", 0.35)
            win.configure(bg="black")
            canvas = tk.Canvas(
                win, width=mon.width, height=mon.height, bg="black", highlightthickness=0, bd=0
            )
            canvas.pack(fill="both", expand=True)
            paint_grid(canvas, mon, spec)
        win.update_idletasks()
        hwnd = _resolve_hwnd(win)
        _apply_clickthrough(hwnd)
        if exclude_capture:
            ok = _set_exclude_from_capture(hwnd, True)
            print(
                f"  ecran {mon.index} {mon.width}x{mon.height} "
                f"grille {spec.cols}x{spec.rows} exclude={'OK' if ok else 'KO'}"
            )
        else:
            print(f"  ecran {mon.index} grille {spec.cols}x{spec.rows} (no exclude)")
        windows.append(win)
        hwnds.append(hwnd)

    def _cleanup(*_a):
        for w in windows:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_cleanup)
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))
    except Exception:  # noqa: BLE001
        pass

    def tick() -> None:
        # heartbeat + re-pose click-through (sauf simulation de blocage)
        st = _read_state()
        st["overlay_pid"] = os.getpid()
        st["hwnds"] = list(hwnds)
        st["heartbeat"] = time.time()
        st["role"] = "overlay"
        st["started"] = st.get("started") or t0
        # Sortie cooperative : plus de state ou watchdog mort → on se ferme
        wd = int(st.get("watchdog_pid") or 0)
        if not STATE_PATH.exists():
            _log("state gone → exit")
            _cleanup()
            sys.exit(0)
        if wd and not _pid_alive(wd) and not simulate_block:
            _log("watchdog dead → exit")
            _cleanup()
            sys.exit(0)
        # Piège écrans : si un moniteur part / change, les labels `2:…` mentent.
        # On coupe proprement (pas de reconstruction à chaud) → `grille.py on` à relancer.
        try:
            geo_now = empreinte_ecrans(list_monitors())
        except Exception as exc:  # noqa: BLE001
            _log(f"list_monitors fail → exit ({exc})")
            print(f"grille OFF : lecture ecrans impossible ({exc})", file=sys.stderr)
            _cleanup()
            sys.exit(0)
        if geo_now != geo0:
            _log(f"geometry changed {geo0} → {geo_now} → exit")
            print(
                "grille OFF : geometrie ecrans changee "
                "(debranchement / branchement). Relancer : python scripts/grille.py on",
                file=sys.stderr,
            )
            _cleanup()
            sys.exit(0)
        if simulate_block and not blocked["done"] and (time.time() - t0) > 1.5:
            for hwnd in hwnds:
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ex &= ~WS_EX_TRANSPARENT
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
                _log(f"simulate_block hwnd={hwnd} ex={ex}")
            blocked["done"] = True
            st["simulate_block"] = True
            _write_state(st)
            print("SIMULATE_BLOCK: WS_EX_TRANSPARENT retire (test watchdog)")
            _log(f"SIMULATE_BLOCK : bloque au plus {SIMULATION_MAX_MS} ms, le watchdog doit tuer avant")
            # Filet de securite seulement : si le watchdog est casse, on ne
            # laisse pas la souris de l'utilisateur bloquee plus de SIMULATION_MAX_MS.
            # Avant (600 ms), l'overlay se tuait toujours avant que le
            # watchdog ait pu agir, et le test ne prouvait rien (audit V1).
            def _self_die():
                _log("simulate_block : auto-sortie de securite (le watchdog n'a PAS agi)")
                _cleanup()
                sys.exit(0)

            root.after(SIMULATION_MAX_MS, _self_die)
            return
        # Repose des styles + auto-sortie si le click-through a disparu
        for hwnd in hwnds:
            if user32.IsWindow(wintypes.HWND(hwnd)):
                _apply_clickthrough(hwnd)
        for hwnd in hwnds:
            if user32.IsWindow(wintypes.HWND(hwnd)) and not _hwnd_has_clickthrough(hwnd):
                _log(f"self-exit clickthrough_lost hwnd={hwnd}")
                _write_state(st)
                _cleanup()
                sys.exit(0)
        _write_state(st)
        try:
            root.after(STYLE_REFRESH_MS, tick)
        except Exception:  # noqa: BLE001
            pass

    st0 = _read_state()
    st0["overlay_pid"] = os.getpid()
    st0["hwnds"] = hwnds
    st0["heartbeat"] = time.time()
    st0["started"] = time.time()
    st0["role"] = "overlay"
    _write_state(st0)

    print(f"grille ON pid={os.getpid()}  (off / watchdog)")
    root.after(200, tick)
    try:
        root.mainloop()
    finally:
        _cleanup()


def _run_watchdog() -> int:
    """Surveille heartbeat + WS_EX_TRANSPARENT ; kill si bloque."""
    log_path = ETAT_DIR / "vision-public-grille-watchdog.log"
    try:
        log_path.write_text("", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    def _wlog(msg: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f} {msg}\n")
        except Exception:  # noqa: BLE001
            pass

    _wlog(f"watchdog start pid={os.getpid()}")
    print(f"watchdog ON pid={os.getpid()}")
    sans_etat_depuis: float | None = None
    while True:
        time.sleep(WATCHDOG_POLL_S)
        st = _read_state()
        if not st:
            # Avant : `continue` sans fin, donc un watchdog orphelin pouvait
            # tourner pour toujours en ecrivant ~2,5 lignes de journal par
            # seconde (audit V1). Plus d'etat = plus rien a surveiller.
            sans_etat_depuis = sans_etat_depuis or time.time()
            if time.time() - sans_etat_depuis > SANS_ETAT_MAX_S:
                _wlog(f"plus d'etat depuis {SANS_ETAT_MAX_S:g} s → exit")
                return 0
            _wlog("no state")
            continue
        sans_etat_depuis = None
        # si un autre off a clear : state sans overlay_pid et sans nous
        overlay_pid = int(st.get("overlay_pid") or 0)
        wd_pid = int(st.get("watchdog_pid") or 0)
        if wd_pid and wd_pid != os.getpid():
            _wlog("autre watchdog → exit")
            return 0

        if overlay_pid and not _pid_alive(overlay_pid):
            _wlog("overlay mort → exit")
            _clear_state()
            return 0

        hb = float(st.get("heartbeat") or 0)
        age = time.time() - hb if hb else 999
        reason = None
        hwnds = st.get("hwnds") or []
        if age > HEARTBEAT_MAX_AGE_S:
            reason = f"heartbeat_stale age={age:.1f}s"
        elif hwnds:
            for hwnd in hwnds:
                ok = _hwnd_has_clickthrough(int(hwnd))
                _wlog(f"check hwnd={hwnd} clickthrough={ok}")
                if not ok:
                    reason = f"clickthrough_lost hwnd={hwnd}"
                    break
        else:
            _wlog(f"heartbeat ok age={age:.2f} hwnds=0")

        if reason:
            _wlog(f"KILL {reason} overlay={overlay_pid}")
            print(f"watchdog KILL overlay: {reason}")
            # Marqueur ecrit AVANT de tuer : c'est lui qui prouve, dans
            # test-watchdog, que la mort vient du watchdog et pas d'une
            # auto-sortie de l'overlay.
            try:
                FLAG_PATH.write_text(reason, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            dead = True
            if overlay_pid:
                _kill_pid(overlay_pid, st.get("overlay_cree"))
                dead = _pid_dead(overlay_pid, wait_s=3.0)
                _wlog(f"overlay dead={dead}")
            _clear_state()
            return 0 if dead else 1



def _spawn(args: list[str]) -> subprocess.Popen:
    # CREATE_NO_WINDOW (pas DETACHED) : tuable par taskkill / os.kill.
    # DETACHED_PROCESS laissait des overlays zombies (blocage 15/10).
    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
    return subprocess.Popen(
        args,
        cwd=str(ROOT),
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )


def on(
    *,
    exclude_capture: bool = True,
    detach: bool = True,
    tint: bool = False,
    simulate_block: bool = False,
    with_watchdog: bool = True,
) -> int:
    off()  # propre : pas d'orphelins
    time.sleep(0.15)

    if not detach:
        _run_overlay(
            exclude_capture=exclude_capture, tint=tint, simulate_block=simulate_block
        )
        return 0

    args = [sys.executable, str(Path(__file__).resolve()), "_run"]
    if not exclude_capture:
        args.append("--no-exclude")
    if tint:
        args.append("--tint")
    if simulate_block:
        args.append("--simulate-block")

    proc = _spawn(args)
    st = {
        "overlay_pid": proc.pid,
        # Heure de creation : la seule preuve qu'un numero designe encore NOTRE
        # overlay, et pas un programme qui a herite du numero apres un
        # plantage ou un redemarrage (voir `_est_processus_grille`).
        "overlay_cree": _cree_a(proc.pid),
        "pids": [proc.pid],
        "heartbeat": time.time(),
        "started": time.time(),
        "hwnds": [],
    }
    _write_state(st)
    time.sleep(0.5)

    wd_pid = None
    if with_watchdog:
        wd = _spawn([sys.executable, str(Path(__file__).resolve()), "_watchdog"])
        wd_pid = wd.pid
        st = _read_state()
        st["watchdog_pid"] = wd_pid
        st["watchdog_cree"] = _cree_a(wd_pid)
        st["pids"] = [proc.pid, wd_pid]
        _write_state(st)

    if _pid_alive(proc.pid):
        print(f"grille ON overlay={proc.pid} watchdog={wd_pid}")
        return 0
    print("echec demarrage overlay", file=sys.stderr)
    off()
    return 1


def bascule() -> int:
    st = _read_state()
    oid = int(st.get("overlay_pid") or 0)
    # Vivant ne suffit pas : un numero reutilise est vivant aussi.
    if oid and _est_processus_grille(oid, st.get("overlay_cree")):
        return off()
    return on()


def test_exclusion() -> int:
    import numpy as np
    import tkinter as tk

    from screen_capture import grab_region
    from screen_context import list_monitors

    mons = list_monitors()
    mon = mons[0]
    w = h = 240
    left = mon.left + 40
    top = mon.top + 40

    print("Test 1.0 exclusion capture overlay (sonde fixe)")
    print(f"  sonde ecran {mon.index} ({left},{top}) {w}x{h}")

    off()
    time.sleep(0.2)

    probe = tk.Tk()
    probe.overrideredirect(True)
    probe.geometry(f"{w}x{h}+{left}+{top}")
    probe.attributes("-topmost", True)
    probe.configure(bg="#808080")
    tk.Canvas(probe, width=w, height=h, bg="#808080", highlightthickness=0).pack()
    probe.update()
    time.sleep(0.3)

    def max_diff(a, b) -> float:
        if a is None or b is None or a.shape != b.shape:
            return float("inf")
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).max())

    base = grab_region(left, top, w, h)
    rc = on(exclude_capture=True, detach=True, with_watchdog=True)
    if rc != 0:
        probe.destroy()
        return 1
    time.sleep(0.9)
    with_ex = grab_region(left, top, w, h)

    off()
    time.sleep(0.3)
    probe.attributes("-topmost", True)
    probe.update()
    time.sleep(0.2)
    on(exclude_capture=False, detach=True, tint=True, with_watchdog=True)
    time.sleep(0.9)
    without_ex = grab_region(left, top, w, h)
    off()
    try:
        probe.destroy()
    except Exception:  # noqa: BLE001
        pass

    d_ex = max_diff(base, with_ex)
    d_no = max_diff(base, without_ex)
    print(f"  ecart max AVEC exclude   : {d_ex}")
    print(f"  ecart max SANS exclude   : {d_no}")
    code, texte = verdict_exclusion(d_ex, d_no)
    print(f"VERDICT: {texte}")
    return code


def verdict_exclusion(d_ex: float, d_no: float) -> tuple[int, str]:
    """Verdict du test 1.0, en code de sortie. Fonction pure, testee a part.

    `NON-DIT` renvoie 2, pas 0 (audit V1). Ce cas veut dire « l'overlay
    n'apparait pas dans la capture, mais il n'apparaissait pas non plus SANS
    exclusion » : le controle positif a echoue, donc le test n'a rien prouve.
    Rendre 0 la faisait passer pour un OK aupres de quiconque regarde
    seulement le code de sortie.
    """
    if d_ex <= 8 and d_no >= 30:
        return 0, "OK  overlay invisible a BitBlt, visible sans exclude"
    if d_ex <= 8:
        return 2, "NON-DIT  exclude OK mais controle positif faible : le test n'a rien prouve"
    return 1, "KO  exclude echec"


def test_watchdog() -> int:
    """Prouve : overlay qui perd click-through → watchdog kill < ~5s."""
    print("Test watchdog (simulate-block)")
    print(f"  ATTENTION : la souris est bloquee au plus {SIMULATION_MAX_MS / 1000:g} s "
          "(le clavier reste libre)")
    flag = FLAG_PATH
    try:
        flag.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    off()
    rc = on(simulate_block=True, with_watchdog=True, exclude_capture=True)
    if rc != 0:
        print("KO demarrage")
        return 1
    st = _read_state()
    oid = int(st.get("overlay_pid") or 0)
    print(f"  overlay={oid} watchdog={st.get('watchdog_pid')}")
    t0 = time.time()
    deadline = t0 + 10.0
    mort = False
    while time.time() < deadline:
        if oid and not _pid_alive(oid):
            mort = True
            break
        time.sleep(0.1)
    duree = time.time() - t0
    par_le_watchdog = flag.exists()
    alive = bool(oid and _pid_alive(oid))
    off()
    # La mort seule ne prouve rien : l'overlay simule a aussi une auto-sortie
    # de securite. Seul le drapeau, ecrit par le watchdog AVANT de tuer, dit
    # QUI l'a arrete (audit V1).
    if mort and par_le_watchdog:
        print(f"VERDICT: OK  le watchdog a tue l'overlay bloque en {duree:.1f} s")
        return 0
    if mort:
        print("VERDICT: KO  l'overlay s'est arrete par son auto-sortie de securite : "
              "le watchdog n'a PAS agi")
        return 1
    print(f"VERDICT: KO  overlay encore vivant={alive} drapeau={par_le_watchdog}")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description="Overlay quadrillage vision-public V1 + watchdog")
    p.add_argument(
        "cmd",
        choices=["on", "off", "bascule", "test-exclusion", "test-watchdog", "_run", "_watchdog"],
    )
    p.add_argument("--no-exclude", action="store_true")
    p.add_argument("--tint", action="store_true")
    p.add_argument("--simulate-block", action="store_true")
    p.add_argument("--no-watchdog", action="store_true")
    args = p.parse_args()

    if args.cmd == "off":
        return off()
    if args.cmd == "bascule":
        return bascule()
    if args.cmd == "test-exclusion":
        return test_exclusion()
    if args.cmd == "test-watchdog":
        return test_watchdog()
    if args.cmd == "_watchdog":
        return _run_watchdog()
    if args.cmd == "_run":
        _run_overlay(
            exclude_capture=not args.no_exclude,
            tint=args.tint,
            simulate_block=args.simulate_block,
        )
        return 0
    return on(
        exclude_capture=not args.no_exclude,
        tint=args.tint,
        simulate_block=args.simulate_block,
        with_watchdog=not args.no_watchdog,
    )


if __name__ == "__main__":
    raise SystemExit(main())
