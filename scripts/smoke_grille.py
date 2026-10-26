"""Smoke de l'overlay de grille : surete d'abord (audit V1, 15/10).

Usage :
  python scripts/smoke_grille.py

Sortie : exit 0 si tous les controles passent, 1 sinon. Environ 15 s.

CE QUE CE SMOKE NE FAIT JAMAIS

  - il n'allume pas l'overlay plein ecran (`grille.py on`) : les fenetres
    Tk de ce processus apparaissent sur le bureau de l'utilisateur (mesure 15/10),
    et un test ne doit pas lui masquer l'ecran ni bloquer sa souris ;
  - il ne touche pas au fichier d'etat de l'utilisateur : tout passe par
    `VISION_GRILLE_STATE`, dans un dossier temporaire jetable ;
  - ses seules fenetres sont minuscules et placees HORS ECRAN.

CE QU'IL SURVEILLE

  1. `off()` ne tue QUE des processus de la grille : un processus sans
     rapport, ou un numero reutilise (bonne ligne de commande, mauvaise
     heure de creation), survit. Avant : tue avec tout son arbre.
  2. `_apply_clickthrough` garde la couleur transparente de Tk. Avant :
     effacee, overlay noir opaque (97,5 % de pixels noirs mesures).
  3. Le watchdog tue un overlay bloque, en ecrivant son drapeau AVANT.
  4. Un watchdog sans etat s'arrete au lieu de boucler pour toujours.
  5. Le test 1.0 ne rend plus 0 sur un verdict NON-DIT.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOSSIER = Path(tempfile.mkdtemp(prefix="smoke-grille-"))
os.environ["VISION_GRILLE_STATE"] = str(DOSSIER / "etat.json")
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import grille as g  # noqa: E402  (apres la redirection de l'etat)

CHECKS: list[tuple[str, bool]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def jetable(*argv: str) -> subprocess.Popen:
    """Un processus qui dort, avec la ligne de commande qu'on veut."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", *argv],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def ecrire_etat(data: dict) -> None:
    g.STATE_PATH.write_text(json.dumps(data), encoding="utf-8")


def main() -> int:  # noqa: C901
    print("=" * 72)
    print("Smoke overlay de grille : surete (audit V1)")
    print("=" * 72)

    print()
    print("Isolement")
    check("l'etat de ce smoke est prive, pas celui de l'utilisateur",
          g.STATE_PATH.parent == DOSSIER, str(g.STATE_PATH))
    check("le drapeau du watchdog suit le meme dossier", g.FLAG_PATH.parent == DOSSIER)

    # --- 1. off() ne tue que la grille ------------------------------------ #
    print()
    print("off() ne tue que des processus de la grille")

    victime = jetable()
    ecrire_etat({"overlay_pid": victime.pid, "pids": [victime.pid]})
    g.off()
    time.sleep(0.3)
    check("un processus sans rapport, inscrit dans l'etat, SURVIT",
          victime.poll() is None, "avant ce correctif : tue, avec son arbre")
    victime.kill()

    faux = jetable("grille.py", "_run")
    ecrire_etat({"overlay_pid": faux.pid, "overlay_cree": g._cree_a(faux.pid) + 50.0,
                 "pids": [faux.pid]})
    g.off()
    time.sleep(0.3)
    check("bonne ligne de commande mais mauvaise heure de creation : SURVIT",
          faux.poll() is None, "c'est le cas d'un numero reutilise")

    ecrire_etat({"overlay_pid": faux.pid, "overlay_cree": g._cree_a(faux.pid),
                 "pids": [faux.pid]})
    g.off()
    time.sleep(0.5)
    check("un vrai processus de grille (ligne + heure) est bien arrete",
          faux.poll() is not None, "temoin : sans lui, refuser tout passerait pour sur")
    if faux.poll() is None:
        faux.kill()

    check("off() efface l'etat", not g.STATE_PATH.exists())

    if g.user32 is None:
        check("click-through Win32 joue", False, "pas de user32 : la suite est l'overlay Windows")
        ko = [label for label, ok in CHECKS if not ok]
        print(f"{len(CHECKS) - len(ko)}/{len(CHECKS)}")
        print("partie Win32 non jouee. Ce n'est pas un succes de l'overlay.")
        return 1

    # --- 2. la transparence de Tk survit ----------------------------------- #
    print()
    print("La couleur transparente de Tk survit au click-through")
    import tkinter as tk

    racine = tk.Tk()
    racine.withdraw()
    win = tk.Toplevel(racine)
    win.overrideredirect(True)
    win.geometry("8x8+-20000+-20000")  # hors ecran : invisible pour l'utilisateur
    win.attributes("-transparentcolor", "black")
    win.configure(bg="black")
    win.update_idletasks()
    win.update()
    hwnd = g._resolve_hwnd(win)
    import ctypes
    from ctypes import wintypes

    def colorkey() -> bool:
        k, a, f = wintypes.DWORD(), ctypes.c_ubyte(), wintypes.DWORD()
        g.user32.GetLayeredWindowAttributes(wintypes.HWND(hwnd), ctypes.byref(k),
                                            ctypes.byref(a), ctypes.byref(f))
        return bool(f.value & 1)

    check("temoin : Tk a bien pose la couleur transparente", colorkey())
    for _ in range(3):  # comme la boucle de 0,8 s
        g._apply_clickthrough(hwnd)
        win.update()
    check("apres 3 poses de click-through, la couleur transparente est GARDEE",
          colorkey(), "avant : effacee, overlay noir opaque")
    check("le click-through est bien pose", g._hwnd_has_clickthrough(hwnd))

    # --- 3. le watchdog tue un overlay bloque ------------------------------ #
    print()
    print("Le watchdog arrete un overlay bloque, et le dit")

    # La fenetre hors ecran SANS click-through joue l'overlay bloque.
    bloquee = tk.Toplevel(racine)
    bloquee.overrideredirect(True)
    bloquee.geometry("8x8+-20000+-20000")
    bloquee.update()
    h_bloquee = g._resolve_hwnd(bloquee)
    faux_overlay = jetable("grille.py", "_run")
    ecrire_etat({"overlay_pid": faux_overlay.pid,
                 "overlay_cree": g._cree_a(faux_overlay.pid),
                 "hwnds": [h_bloquee], "heartbeat": time.time()})
    g.FLAG_PATH.unlink(missing_ok=True)
    chien = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "grille.py"), "_watchdog"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < 6.0 and faux_overlay.poll() is None:
        racine.update()
        time.sleep(0.1)
    check("l'overlay bloque est arrete par le watchdog", faux_overlay.poll() is not None,
          f"en {time.time() - t0:.1f} s")
    check("le drapeau du watchdog est ecrit", g.FLAG_PATH.exists(),
          g.FLAG_PATH.read_text(encoding="utf-8") if g.FLAG_PATH.exists() else "absent")
    for p in (faux_overlay, chien):
        if p.poll() is None:
            p.kill()
    bloquee.destroy()
    win.destroy()
    racine.destroy()

    # --- 4. watchdog orphelin ---------------------------------------------- #
    print()
    print("Un watchdog sans etat s'arrete")
    g.STATE_PATH.unlink(missing_ok=True)
    orphelin = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "grille.py"), "_watchdog"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        orphelin.wait(timeout=g.SANS_ETAT_MAX_S + 4.0)
        fini = True
    except subprocess.TimeoutExpired:
        orphelin.kill()
        fini = False
    check(f"il s'arrete seul en moins de {g.SANS_ETAT_MAX_S + 4:g} s", fini,
          "avant : boucle sans fin")

    # --- 5. verdicts du test 1.0 ------------------------------------------- #
    print()
    print("Le test 1.0 dit vrai")
    check("0 et 58 (mesure audit) donnent OK, code 0", g.verdict_exclusion(0, 58)[0] == 0)
    check("0 et 0 (rien vu du tout) donnent NON-DIT, code 2",
          g.verdict_exclusion(0, 0)[0] == 2, "avant : code 0, un faux OK")
    check("40 et 58 donnent KO, code 1", g.verdict_exclusion(40, 58)[0] == 1)

    # --- 5 bis. piege debranchement (18/10) --------------------------- #
    # Sans fenetre : on teste la fonction d'empreinte que la boucle de
    # l'overlay compare a chaque tour. Avant (12:38) : fonction interne,
    # aucun test ne la jouait (retour audit V1.3 § 4).
    print()
    print("L'overlay voit un changement d'ecrans")
    from types import SimpleNamespace as _M

    def _ecr(i, l, t, w, h, p, nom):
        return _M(index=i, left=l, top=t, width=w, height=h, is_primary=p, name=nom)

    deux = [_ecr(1, 0, 0, 2560, 1440, True, r"\\.\DISPLAY2"), _ecr(2, 2560, 182, 1920, 1080, False, r"\\.\DISPLAY1")]
    noms_inverses = [_ecr(1, 0, 0, 2560, 1440, True, r"\\.\DISPLAY1"), _ecr(2, 2560, 182, 1920, 1080, False, r"\\.\DISPLAY2")]
    sans_2 = deux[:1]
    sans_1 = [_ecr(1, 0, 0, 1920, 1080, True, r"\\.\DISPLAY1")]
    e = g.empreinte_ecrans
    check("meme geometrie : empreinte egale (pas de coupure pour rien)", e(deux) == e(list(deux)))
    check("noms Windows inverses, geometrie identique : PAS de coupure", e(deux) == e(noms_inverses),
          "les noms DISPLAYn se sont deja inverses seuls")
    check("ecran 2 debranche : empreinte differente (coupure)", e(deux) != e(sans_2))
    check("ecran 1 debranche, l'autre devient principal : coupure", e(deux) != e(sans_1))
    check("la boucle de l'overlay compare bien cette empreinte",
          "empreinte_ecrans(list_monitors())" in (ROOT / "scripts" / "grille.py").read_text(encoding="utf-8"))

    # --- 6. perimetre ------------------------------------------------------- #
    print()
    print("Perimetre")
    source = (ROOT / "scripts" / "grille.py").read_text(encoding="utf-8")
    arbre = ast.parse(source)
    noms = {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
    check("aucune actuation (souris, clavier) dans l'overlay",
          not (noms & {"SetCursorPos", "mouse_event", "keybd_event", "SendInput"}))
    chaines = {n.value for n in ast.walk(arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    check("taskkill n'est plus appele avec /T (arbre entier)", "/T" not in chaines)

    print()
    print("=" * 72)
    ko = [label for label, ok in CHECKS if not ok]
    print(f"{len(CHECKS) - len(ko)}/{len(CHECKS)} controles OK")
    if ko:
        print("SMOKE KO")
        for label in ko:
            print(f"  - {label}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
