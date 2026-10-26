"""Contexte ecran : moniteurs, fenetre active, identite applicative, events.

Repond a la question de l'auteur : "quel ecran, quelle fenetre, quelle
application, quel contenu". Deux couches empilees, utilisables separement.

Couche R-S1a, Win32 pur, sans pixel et sans modele :
  - moniteurs physiques (EnumDisplayMonitors, sans cv2 : voir list_monitors)
  - fenetre au premier plan : titre, pid, nom et chemin de l'executable, bbox
  - mapping fenetre -> moniteur par INTERSECTION D'AIRES
    (DISPLAY2 est a y=182 sur ce poste : un mapping arithmetique serait faux)
  - DPI per-monitor v2 pose au chargement du module
  - bbox visible via DWM (GetWindowRect inclut les bordures invisibles)
  -> `get_screen_context()`

Couche R-S1b, fusion avec UIA (`screen_uia`, importe paresseusement) :
  - identite a DEUX niveaux : processus (Win32) ET document (URL via UIA).
    un site d'emploi, chat-ia.example et Outlook web sont le meme `msedge.exe` :
    le niveau processus seul ne peut pas les distinguer.
  - categories d'applications (context/screen-app-categories.json)
  - localisation d'un controle nomme, avec ABSTENTION si absent
  - niveau de confiance, et `unknown` distinct de "rien a lire"
  - emission d'un event `screen.context` v1 sur changement de contexte
  -> `get_screen_snapshot()`

Modele produit : OUTIL A LA DEMANDE (decision I7 du 31/08). Un appel,
un dict. Aucune boucle, aucun thread, aucun daemon.

Hors perimetre, ne pas ajouter ici sans GO :
  - OCR / VLM / golden set / harnais de rejeu          -> R-S2
  - suivi du curseur                                   -> R-S4
  - toute actuation souris ou clavier                  -> jamais dans ce depot (ADR-0007)

Usage :
    from screen_context import get_screen_context, get_screen_snapshot
    ctx  = get_screen_context()                       # Win32 seul, ~0,6 ms
    snap = get_screen_snapshot(target="Postuler")     # + UIA

Local only. Aucun reseau. Seule l'emission d'event ecrit sur disque
(data/events/, gitignore).
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

try:
    import win32gui
    import win32process
except ImportError:  # Linux : pas de pywin32
    # Sous Windows, pywin32 manquant doit casser net (audit 23/10) : sinon
    # list_windows rend [] en silence, et « 0 fenetre » se lit comme un fait.
    if sys.platform == "win32":
        raise
    win32gui = None  # type: ignore[assignment]
    win32process = None  # type: ignore[assignment]

__all__ = [
    "CONFIDENCE_CONF",
    "Monitor",
    "WindowInfo",
    "classify_app",
    "context_signature",
    "dpi_awareness",
    "get_foreground_window",
    "get_screen_context",
    "get_screen_snapshot",
    "get_window_info",
    "list_monitors",
    "list_windows",
    "monitor_for_rect",
]

# Taille minimale pour qu'une fenetre compte comme "reelle" (px)
MIN_WINDOW_SIDE = 200

_DWMWA_CLOAKED = 14
_DWMWA_EXTENDED_FRAME_BOUNDS = 9

_GWL_EXSTYLE = -20
# Une fenetre marquee TOOLWINDOW ou NOACTIVATE n'est pas une fenetre
# applicative ordinaire. Observe sur ce poste : ce seul critere ecarte
# exactement les trois intrus (overlay Wispr Flow, overlay NVIDIA, et le
# Program Manager du bureau) sans liste noire d'executables a maintenir.
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


# --------------------------------------------------------------------------- #
# DPI
# --------------------------------------------------------------------------- #

_DPI_LABELS = {0: "unaware", 1: "system", 2: "per-monitor"}


def _set_dpi_awareness() -> str:
    """PER_MONITOR_AWARE_V2, pose une seule fois au chargement.

    Aujourd'hui les deux ecrans de l'utilisateur sont a 96 dpi : sans ce reglage tout
    fonctionne quand meme. Le jour ou un ecran passe a 125 % ou 150 % (4K
    prevue au plan), les bbox Win32 se decalent SILENCIEUSEMENT. On paie ici
    une ligne pour ne pas payer une chasse au bug plus tard.
    """
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor-v1"
    except Exception:  # noqa: BLE001
        pass
    return dpi_awareness()


def dpi_awareness() -> str:
    """Etat DPI reellement constate du process (pas ce qu'on a demande)."""
    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
        return _DPI_LABELS.get(awareness.value, f"inconnu({awareness.value})")
    except Exception:  # noqa: BLE001
        return "indisponible"


DPI_MODE = _set_dpi_awareness()


# --------------------------------------------------------------------------- #
# Moniteurs (Win32 natif)
# --------------------------------------------------------------------------- #

_MONITORINFOF_PRIMARY = 1


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


if sys.platform == "win32":
    _MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )
else:
    _MONITORENUMPROC = None


@dataclass(frozen=True)
class Monitor:
    """Moniteur physique, vu par Win32.

    Indexation 1-based, dans l'ordre de EnumDisplayMonitors, pour rester
    aligne avec screen_capture.list_monitors (mss). Le smoke verifie que les
    deux sources concordent : deux enumerations qui divergeraient en silence
    seraient pire qu'une seule.
    """

    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool
    name: str
    work_area: tuple[int, int, int, int]

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.left + self.width, self.top + self.height)

    @property
    def label(self) -> str:
        prim = " primaire" if self.is_primary else ""
        return f"ecran {self.index}{prim}  {self.width}x{self.height}"


def list_monitors() -> list[Monitor]:
    """Moniteurs physiques.

    Windows : EnumDisplayMonitors (corps inchange).
    Linux : mss, puis le meme tri (primaire d'abord, puis gauche).
    Pas d'import cv2 : la liste vit dans ``liste_ecrans_mss``.
    """
    if sys.platform != "win32":
        from liste_ecrans_mss import lister

        return [
            Monitor(
                index=e.index,
                left=e.left,
                top=e.top,
                width=e.width,
                height=e.height,
                is_primary=e.is_primary,
                name=e.name,
                work_area=(e.left, e.top, e.left + e.width, e.top + e.height),
            )
            for e in lister()
        ]
    return _list_monitors_win32()


def _list_monitors_win32() -> list[Monitor]:
    """Moniteurs physiques via EnumDisplayMonitors.

    Volontairement en Win32 pur : screen_capture tire cv2, mesure a 159 ms
    d'import sur ce poste, dont ce module n'a aucun besoin. La vision ecran
    etant un OUTIL A LA DEMANDE (decision I7 du 31/08), chaque appel
    paie le demarrage a froid. Gain mesure : ~218 ms -> ~60 ms.
    """
    out: list[Monitor] = []

    def _cb(hmon: int, _hdc: int, _rect: Any, _lparam: int) -> int:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return 1
        m, w = info.rcMonitor, info.rcWork
        out.append(
            Monitor(
                index=len(out) + 1,
                left=int(m.left),
                top=int(m.top),
                width=int(m.right - m.left),
                height=int(m.bottom - m.top),
                is_primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                name=str(info.szDevice),
                work_area=(int(w.left), int(w.top), int(w.right), int(w.bottom)),
            )
        )
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    # mss place le moniteur primaire en tete ; on aligne pour que les index
    # restent comparables entre les deux sources.
    out.sort(key=lambda mo: (not mo.is_primary, mo.left, mo.top))
    return [
        Monitor(
            index=i + 1,
            left=mo.left,
            top=mo.top,
            width=mo.width,
            height=mo.height,
            is_primary=mo.is_primary,
            name=mo.name,
            work_area=mo.work_area,
        )
        for i, mo in enumerate(out)
    ]


# --------------------------------------------------------------------------- #
# Geometrie
# --------------------------------------------------------------------------- #

def _rect_area(rect: tuple[int, int, int, int]) -> int:
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _overlap_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    ox = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    oy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ox * oy


def monitor_for_rect(
    rect: tuple[int, int, int, int],
    monitors: list[Monitor] | None = None,
) -> tuple[int | None, int]:
    """Moniteur qui contient le plus grand morceau de rect.

    Retourne (index moniteur 1-based mss, aire de recouvrement en px2).

    Pourquoi une intersection et pas `left // largeur_ecran` : sur ce poste
    DISPLAY2 est a (2560, 182). Les ecrans ne sont alignes ni en x ni en y, et
    une fenetre peut chevaucher les deux. L'arithmetique donnerait une reponse
    plausible et fausse, donc invisible au debug.
    """
    mons = monitors if monitors is not None else list_monitors()
    best_idx: int | None = None
    best_area = 0
    for mon in mons:
        area = _overlap_area(rect, mon.bbox)
        if area > best_area:
            best_idx, best_area = mon.index, area
    return best_idx, best_area


# --------------------------------------------------------------------------- #
# Fenetres
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe_name: str
    exe_path: str
    rect: tuple[int, int, int, int]  # GetWindowRect (inclut bordures invisibles)
    rect_visible: tuple[int, int, int, int]  # DWM, bords reellement peints
    monitor_index: int | None
    monitor_overlap_px: int
    is_foreground: bool
    is_cloaked: bool
    class_name: str = ""
    z_order: int = -1  # 0 = au-dessus ; -1 = non renseigne (hors enumeration)
    is_overlay: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return (self.rect[2] - self.rect[0], self.rect[3] - self.rect[1])

    @property
    def label(self) -> str:
        ecran = f"ecran {self.monitor_index}" if self.monitor_index else "hors ecran"
        w, h = self.size
        return f"{self.exe_name} {w}x{h} {ecran}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["size"] = list(self.size)
        return d


def _dwm_int(hwnd: int, attr: int) -> int | None:
    val = ctypes.c_int(0)
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attr), ctypes.byref(val), ctypes.sizeof(val)
        )
    except Exception:  # noqa: BLE001
        return None
    return val.value if hr == 0 else None


def _dwm_frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Bords reellement peints.

    GetWindowRect inclut la bordure de redimensionnement invisible du DWM
    (typiquement 7 px de chaque cote). Pour du "au pixel pres", c'est ce
    rectangle-ci qui correspond a ce que l'utilisateur voit.
    """
    rect = wintypes.RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(_DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
    except Exception:  # noqa: BLE001
        return None
    if hr != 0:
        return None
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def _class_name(hwnd: int) -> str:
    try:
        return str(win32gui.GetClassName(hwnd) or "")
    except Exception:  # noqa: BLE001
        return ""


def _process_of(pid: int) -> tuple[str, str]:
    """(nom, chemin complet). Le chemin peut etre vide si acces refuse."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
    except Exception:  # noqa: BLE001
        return ("?", "")
    try:
        return (name, proc.exe())
    except Exception:  # noqa: BLE001
        # Processus eleve ou protege : le nom suffit pour identifier l'app
        return (name, "")


def _is_overlay(hwnd: int) -> bool:
    """Fenetre de decor plutot que fenetre applicative.

    TOOLWINDOW ou NOACTIVATE : elle ne peut pas recevoir le focus, ou elle
    est exclue de la barre des taches. Dans les deux cas ce n'est pas ce que
    l'utilisateur regarde.
    """
    try:
        ex = win32gui.GetWindowLong(hwnd, _GWL_EXSTYLE)
    except Exception:  # noqa: BLE001
        return False
    return bool(ex & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE))


def get_window_info(
    hwnd: int,
    monitors: list[Monitor] | None = None,
    foreground_hwnd: int | None = None,
    z_order: int = -1,
) -> WindowInfo | None:
    """Contexte complet d'une fenetre. Retourne None si le handle est invalide."""
    if win32gui is None or win32process is None:
        return None
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    try:
        rect = tuple(win32gui.GetWindowRect(hwnd))  # type: ignore[assignment]
        title = win32gui.GetWindowText(hwnd) or ""
    except Exception:  # noqa: BLE001
        return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:  # noqa: BLE001
        pid = -1

    exe_name, exe_path = _process_of(pid) if pid > 0 else ("?", "")
    visible = _dwm_frame_bounds(hwnd) or rect  # type: ignore[arg-type]
    idx, overlap = monitor_for_rect(visible, monitors)
    fg = foreground_hwnd if foreground_hwnd is not None else win32gui.GetForegroundWindow()

    return WindowInfo(
        hwnd=int(hwnd),
        title=title,
        pid=int(pid),
        exe_name=exe_name,
        exe_path=exe_path,
        rect=rect,  # type: ignore[arg-type]
        rect_visible=visible,
        monitor_index=idx,
        monitor_overlap_px=overlap,
        is_foreground=(hwnd == fg),
        is_cloaked=bool(_dwm_int(hwnd, _DWMWA_CLOAKED)),
        class_name=_class_name(hwnd),
        z_order=z_order,
        is_overlay=_is_overlay(hwnd),
    )


def get_foreground_window(monitors: list[Monitor] | None = None) -> WindowInfo | None:
    """Fenetre au premier plan, ou None si le bureau a le focus."""
    if win32gui is None:
        return None
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    return get_window_info(hwnd, monitors, foreground_hwnd=hwnd)


def list_windows(
    min_side: int = MIN_WINDOW_SIDE,
    include_cloaked: bool = False,
    monitors: list[Monitor] | None = None,
) -> list[WindowInfo]:
    """Fenetres top-level visibles et reelles, triees premier plan d'abord.

    Filtre les fenetres "fantomes" (cloaked) que Windows garde pour les apps
    UWP suspendues : elles ont un titre et un rect, mais rien n'est peint.
    """
    if win32gui is None:
        return []
    mons = monitors if monitors is not None else list_monitors()
    fg = win32gui.GetForegroundWindow()
    out: list[WindowInfo] = []
    seen: set[int] = set()
    rank = {"z": 0}

    def _cb(hwnd: int, _: Any) -> None:
        if hwnd in seen or not win32gui.IsWindowVisible(hwnd):
            return
        seen.add(hwnd)
        if not win32gui.GetWindowText(hwnd):
            return
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] < min_side or rect[3] - rect[1] < min_side:
            return
        # EnumWindows parcourt les fenetres en ordre Z, la plus haute d'abord.
        # On capte ce rang ici : le tri de sortie le detruirait.
        info = get_window_info(hwnd, mons, foreground_hwnd=fg, z_order=rank["z"])
        rank["z"] += 1
        if info is None:
            return
        if info.is_cloaked and not include_cloaked:
            return
        out.append(info)

    win32gui.EnumWindows(_cb, None)
    out.sort(key=lambda w: (not w.is_foreground, -_rect_area(w.rect_visible)))
    return out


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #

def get_screen_context(with_windows: bool = True) -> dict[str, Any]:
    """Instantane du contexte ecran (R-S1a).

    Outil a la demande (decision I7, 31/08) : pas de boucle, pas de
    daemon, pas d'emission d'event. On appelle, on recoit un dict, c'est tout.

    La forme du dict n'est PAS encore le schema `screen.context` v1 : celui-ci
    sera fige en R-S1b/R-S5 avec confidence, unknown et horloge murale.
    """
    t0 = time.perf_counter()
    monitors = list_monitors()
    fg = get_foreground_window(monitors)
    windows = list_windows(monitors=monitors) if with_windows else []

    return {
        "phase": "R-S1a",
        "t_wall_ms": int(time.time() * 1000),  # horloge murale : tracabilite des preuves
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        "dpi_mode": DPI_MODE,
        "dpi_awareness": dpi_awareness(),
        "monitors": [
            {
                "index": m.index,
                "left": m.left,
                "top": m.top,
                "width": m.width,
                "height": m.height,
                "is_primary": m.is_primary,
                "name": m.name,
                "work_area": list(m.work_area),
            }
            for m in monitors
        ],
        "foreground": fg.to_dict() if fg else None,
        "windows": [w.to_dict() for w in windows],
    }


# --------------------------------------------------------------------------- #
# Categories d'applications (R-S1b, S1b.3)
# --------------------------------------------------------------------------- #

_CATEGORIES_PATH = Path(__file__).resolve().parents[1] / "context" / "screen-app-categories.json"

# Repli minimal si le fichier manque ou est illisible : le module doit rester
# utilisable, quitte a etre moins precis. Une coquille JSON ne doit pas casser
# la vision ecran.
_FALLBACK_APPS: dict[str, dict[str, Any]] = {
    "msedge.exe": {"category": "browser", "label": "Microsoft Edge", "document": "url"},
    "chrome.exe": {"category": "browser", "label": "Google Chrome", "document": "url"},
    "brave.exe": {"category": "browser", "label": "Brave", "document": "url"},
}
_FALLBACK_DEFAULT: dict[str, Any] = {"category": "unknown", "label": None, "document": "title"}

_categories_cache: dict[str, Any] = {"loaded": False, "apps": {}, "fallback": _FALLBACK_DEFAULT}


def _load_categories() -> tuple[dict[str, Any], dict[str, Any]]:
    if _categories_cache["loaded"]:
        return _categories_cache["apps"], _categories_cache["fallback"]
    _categories_cache["loaded"] = True
    apps: dict[str, Any] = dict(_FALLBACK_APPS)
    default = dict(_FALLBACK_DEFAULT)
    try:
        import json

        data = json.loads(_CATEGORIES_PATH.read_text(encoding="utf-8"))
        apps = {str(k).lower(): v for k, v in (data.get("apps") or {}).items()}
        default = data.get("fallback") or default
    except Exception as exc:  # noqa: BLE001
        print(f"screen_context : categories illisibles ({exc}) -> table de repli")
    _categories_cache["apps"] = apps
    _categories_cache["fallback"] = default
    return apps, default


def classify_app(exe_name: str) -> dict[str, Any]:
    """Categorie d'une application, par NOM D'EXECUTABLE.

    Jamais par titre de fenetre : un titre change avec la langue, la version
    et le contenu affiche. Le nom d'executable est stable.

    Le champ `document` porte la distinction qui compte pour l'abstention :
      "url"   -> il y a une adresse a lire (navigateur)
      "title" -> seul le titre renseigne
      None    -> l'application n'a pas de document. Ce n'est pas une
                 incertitude, c'est une certitude qu'il n'y a rien a lire.
    """
    apps, default = _load_categories()
    entry = apps.get((exe_name or "").lower())
    if not entry:
        out = dict(default)
        out["known"] = False
        out.setdefault("document", "title")
        return out
    out = dict(entry)
    out["known"] = True
    out.setdefault("document", "title")
    return out


# --------------------------------------------------------------------------- #
# Confiance
# --------------------------------------------------------------------------- #

# Correspondance libelle -> `conf` numerique de l'enveloppe events v1 (0..1).
CONFIDENCE_CONF: dict[str, float] = {
    "high": 0.95,
    "medium": 0.80,
    "low": 0.45,
    "unknown": 0.0,
}


def _confidence(
    app: dict[str, Any],
    document: dict[str, Any] | None,
    uia_available: bool,
    target_searched: bool,
    target_found: bool,
) -> tuple[str, bool]:
    """(libelle, unknown).

    `unknown` vrai veut dire : il y avait quelque chose a savoir et nous ne
    l'avons pas su. Un terminal sans document ne met PAS `unknown` : il n'y
    avait rien a lire, et c'est une reponse complete.

    Un consommateur qui agit (un agent qui clique) doit pouvoir distinguer les deux.
    Confondre "rien vu" et "pas pu regarder" est le mode d'echec qui fait
    cliquer au mauvais endroit.
    """
    if not uia_available:
        return ("unknown", True)

    expected = app.get("document")
    if expected == "url":
        if document is None:
            # On attendait une adresse et on ne l'a pas eue : c'est un trou,
            # pas une reponse.
            return ("unknown", True)
        level = "high" if document.get("via") == "omnibox-class" else "medium"
    elif expected == "title":
        level = "low"
    else:
        level = "medium"  # aucun document attendu : reponse complete

    if target_searched and target_found:
        level = "high"  # deux sources concordantes
    return (level, False)


# --------------------------------------------------------------------------- #
# Snapshot fusionne Win32 + UIA (R-S1b, S1b.2 / S1b.7)
# --------------------------------------------------------------------------- #

def context_signature(snapshot: dict[str, Any]) -> str:
    """Signature de contexte, pour n'emettre un event que sur changement.

    Volontairement HORS signature : le bbox de la cible. Sinon un simple
    defilement de page reemettrait en boucle. La PRESENCE de la cible en fait
    partie (un bouton qui apparait est un changement, pas un detail).

    A rediscuter en R-S5 si un consommateur a besoin du suivi de position.
    """
    proc = snapshot.get("process") or {}
    doc = snapshot.get("document") or {}
    win = snapshot.get("window") or {}
    return "|".join(
        str(x)
        for x in (
            proc.get("exe_name"),
            doc.get("host"),
            doc.get("path"),
            snapshot.get("monitor_index"),
            win.get("hwnd"),
            snapshot.get("target_requested"),
            bool(snapshot.get("target")),
        )
    )


def get_screen_snapshot(
    *,
    target: str | None = None,
    with_uia: bool = True,
    hwnd: int | None = None,
    emit_event: bool = False,
    bus: Any = None,
) -> dict[str, Any]:
    """Contexte ecran complet : processus (Win32) + document (UIA).

    OUTIL A LA DEMANDE (decision I7 du 31/08) : un appel, un dict.
    Aucune boucle, aucun thread, aucun daemon.

    `target` : nom d'un controle a localiser (ex "Postuler"). Absent de la
    page -> `target` a None et `target_found` faux. On n'invente jamais un
    bbox : c'est la regle qui rend le resultat utilisable par un agent qui
    clique.

    `emit_event` : emet un `screen.context` v1 SEULEMENT si le contexte a
    change depuis le dernier appel de ce process.
    """
    t_capture_ns = time.perf_counter_ns()
    t0 = time.perf_counter()
    monitors = list_monitors()
    win = get_window_info(hwnd, monitors) if hwnd else get_foreground_window(monitors)

    snap: dict[str, Any] = {
        "v": 1,
        "phase": "R-S1b",
        "t_wall_ms": int(time.time() * 1000),
        "t_capture_ns": t_capture_ns,
        "dpi_mode": DPI_MODE,
        "monitor_index": win.monitor_index if win else None,
        "process": None,
        "document": None,
        "window": None,
        "target_requested": target,
        "target_searched": False,
        "target_found": False,
        "target": None,
        "confidence": "unknown",
        "unknown": True,
        "conf": 0.0,
        "uia": {"available": False, "reason": "non sollicite"},
        "elapsed_ms": 0.0,
    }

    if win is None:
        snap["uia"]["reason"] = "aucune fenetre au premier plan"
        snap["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return snap

    app = classify_app(win.exe_name)
    snap["process"] = {
        "exe_name": win.exe_name,
        "exe_path": win.exe_path,
        "pid": win.pid,
        "category": app.get("category"),
        "label": app.get("label"),
        "known": app.get("known"),
        "document_source": app.get("document"),
    }
    snap["window"] = {
        "hwnd": win.hwnd,
        "title": win.title,
        "rect": list(win.rect),
        "rect_visible": list(win.rect_visible),
        "is_foreground": win.is_foreground,
    }

    uia_available = False
    if with_uia:
        try:
            import screen_uia

            probe = screen_uia.probe(
                win.hwnd,
                target=target,
                window_rect=win.rect_visible,
                monitors=monitors,
                expect_browser=(app.get("document") == "url"),
            )
            uia_available = bool(probe.get("available"))
            snap["uia"] = {
                "available": uia_available,
                "reason": probe.get("reason", ""),
                "documents_count": probe.get("documents_count", 0),
                "timings_ms": probe.get("timings_ms", {}),
            }
            snap["document"] = probe.get("document")
            snap["target"] = probe.get("target")
            snap["target_searched"] = target is not None
            snap["target_found"] = bool(probe.get("target"))
        except Exception as exc:  # noqa: BLE001
            snap["uia"] = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    level, unknown = _confidence(
        app, snap["document"], uia_available, snap["target_searched"], snap["target_found"]
    )
    snap["confidence"] = level
    snap["unknown"] = unknown
    snap["conf"] = CONFIDENCE_CONF.get(level, 0.0)
    snap["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)

    if emit_event:
        snap["event_emitted"] = emit_screen_context(snap, bus=bus)
    return snap


# --------------------------------------------------------------------------- #
# Emission screen.context (R-S1b, S1b.5)
# --------------------------------------------------------------------------- #

_emit_cache: dict[str, Any] = {"signature": None, "bus": None}


def _default_bus() -> Any:
    """EventBus partage du process, cree au premier besoin.

    Un bus par appel paierait la temporisation ZMQ de 150 ms a chaque fois,
    ce qui contredirait le modele a la demande.
    """
    if _emit_cache["bus"] is None:
        from events_v1 import SOURCE_SCREEN
        from vision_events import EventBus

        _emit_cache["bus"] = EventBus(source=SOURCE_SCREEN)
    return _emit_cache["bus"]


def event_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Payload `screen.context` v1. Schema : context/screen-context-schema-v1.md."""
    return {
        "t_wall_ms": snapshot.get("t_wall_ms"),
        "phase": snapshot.get("phase"),
        "monitor_index": snapshot.get("monitor_index"),
        "process": snapshot.get("process"),
        "document": snapshot.get("document"),
        "window": snapshot.get("window"),
        "target_requested": snapshot.get("target_requested"),
        "target": snapshot.get("target"),
        "confidence": snapshot.get("confidence"),
        "unknown": snapshot.get("unknown"),
    }


def emit_screen_context(
    snapshot: dict[str, Any],
    bus: Any = None,
    *,
    force: bool = False,
) -> bool:
    """Emet un `screen.context` v1 si le contexte a change. Retourne l'emission.

    Contrat : events v1 (ADR-0005), source `vision.screen`, JSONL local plus
    PUB ZMQ. Pas de second schema invente.
    """
    signature = context_signature(snapshot)
    if not force and signature == _emit_cache["signature"]:
        return False
    try:
        target_bus = bus if bus is not None else _default_bus()
        sent = target_bus.emit_screen_context(
            event_payload(snapshot),
            conf=float(snapshot.get("conf", 0.0)),
            t_capture_ns=int(snapshot.get("t_capture_ns", time.perf_counter_ns())),
            key=signature,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"screen.context emission KO : {exc}")
        return False
    if sent:
        _emit_cache["signature"] = signature
    return bool(sent)


def reset_emit_cache() -> None:
    """Oublie le dernier contexte emis (tests, changement de session)."""
    _emit_cache["signature"] = None
