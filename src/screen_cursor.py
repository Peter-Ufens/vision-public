"""Position du curseur (R-S4a) : ou pointe l'utilisateur, pendant une session.

L'auteur a tranche I7 : la vision ecran est un OUTIL A LA DEMANDE. Ce module ne
fait donc AUCUN suivi en arriere-plan. Il n'y a ni boucle, ni thread, ni
daemon : la position est lue a l'instant de l'appel, comme le reste du
snapshot. Entre deux appels, rien ne tourne.

Ce que ce module apporte, et pourquoi :

  - `x`, `y` en coordonnees ecran absolues, et le moniteur qui les porte.
    Le mapping se fait par appartenance a un rectangle, pas par arithmetique :
    DISPLAY2 est a (2560, 182) sur ce poste, les ecrans ne sont alignes ni en
    x ni en y, et il existe une zone du bureau virtuel qui n'appartient a
    aucun ecran physique. Un curseur qui s'y trouverait rend `monitor_index`
    a None plutot qu'un index plausible et faux.

  - `visible`. Windows garde une position de curseur meme quand le pointeur
    est CACHE : lecteur video en plein ecran, jeu, saisie tactile. La
    position est alors perimee et ne dit rien de l'attention. Un consommateur
    qui l'ignorerait conclurait que l'utilisateur regarde un coin d'ecran qu'il ne
    regarde pas. C'est la meme discipline d'abstention que partout ailleurs
    dans ce projet : la donnee est rendue, avec de quoi savoir si elle vaut
    quelque chose.

  - la fenetre sous le pointeur, ce qui repond a "il pointe sur quoi" et pas
    seulement "il pointe ou".

Mesures sur ce poste : `GetCursorPos` 0,0007 ms, `GetCursorInfo` 0,0008 ms,
`WindowFromPoint` + `GetAncestor` 0,0027 ms, `list_monitors()` 0,0088 ms.
L'ensemble reste tres en dessous du budget de 5 ms.

INTERDIT ET NON IMPLEMENTE : ce module LIT la position. Il ne la deplace pas,
ne clique pas, n'envoie aucune entree. Aucun `SetCursorPos`, aucun
`mouse_event`, aucun `SendInput`. L'actuation n'est pas dans ce
depot (ADR-0007).

Local only. Aucun reseau, aucune ecriture disque.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

__all__ = [
    "cursor_region_bbox",
    "get_cursor",
    "monitor_for_point",
    "window_at_point",
]

# GetCursorInfo : le pointeur est affiche.
_CURSOR_SHOWING = 0x00000001
# Windows 8+ : masque parce que l'utilisateur est passe au tactile.
_CURSOR_SUPPRESSED = 0x00000002

# GetAncestor : remonter a la fenetre de plus haut niveau.
_GA_ROOT = 2

# Taille maximale acceptee pour la region sous le curseur. Au-dela, ce n'est
# plus "autour du curseur", c'est une capture d'ecran deguisee.
MAX_REGION_PX = 1024


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", _POINT),
    ]


# WindowFromPoint recoit un POINT PAR VALEUR. Sans argtypes declares, le
# marshalling d'une structure par valeur en x64 n'est pas garanti.
# Hors Windows, _user32 reste None : get_cursor repond available=False.
_user32 = None
if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _user32.WindowFromPoint.argtypes = [_POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    _user32.GetAncestor.restype = wintypes.HWND


# --------------------------------------------------------------------------- #
# Geometrie (pure, testable sans ecran)
# --------------------------------------------------------------------------- #

def monitor_for_point(x: int, y: int, monitors: Any = None) -> int | None:
    """Index 1-based du moniteur qui contient ce point, ou None.

    Appartenance a un rectangle, bornes hautes exclues : un point a
    `left + width` est sur l'ecran suivant, pas sur celui-ci.

    None est une reponse valide : sur ce poste, la zone `x > 2560` et
    `y < 182` appartient au bureau virtuel mais a aucun ecran physique.
    """
    if monitors is None:
        from screen_context import list_monitors

        monitors = list_monitors()
    for mon in monitors:
        if mon.left <= x < mon.left + mon.width and mon.top <= y < mon.top + mon.height:
            return mon.index
    return None


def cursor_region_bbox(
    x: int,
    y: int,
    size: int,
    bornes: tuple[int, int, int, int],
) -> tuple[tuple[int, int, int, int] | None, bool]:
    """Carre de `size` px centre sur (x, y), decoupe aux `bornes`.

    Retourne (bbox, decoupe). bbox est None si l'intersection est vide.

    Choix : on DECOUPE, on ne deplace pas. Faire glisser le carre pour lui
    garder sa taille pres d'un bord eloignerait son centre du curseur, donc
    de ce que l'utilisateur regarde. Mieux vaut une region plus petite mais
    reellement centree sur le pointeur, et le signaler.
    """
    if size <= 0:
        return (None, False)
    size = min(int(size), MAX_REGION_PX)
    demi = size // 2
    brut = (x - demi, y - demi, x - demi + size, y - demi + size)

    bx0, by0, bx1, by1 = bornes
    x0 = max(brut[0], bx0)
    y0 = max(brut[1], by0)
    x1 = min(brut[2], bx1)
    y1 = min(brut[3], by1)
    if x1 <= x0 or y1 <= y0:
        return (None, True)
    return ((x0, y0, x1, y1), (x0, y0, x1, y1) != brut)


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #

def window_at_point(x: int, y: int) -> int | None:
    """Handle de la fenetre de plus haut niveau sous ce point, ou None.

    Lecture seule : `WindowFromPoint` interroge le gestionnaire de fenetres,
    il ne deplace ni ne clique rien.

    Sert a repondre "un clic ici atteindrait-il cette fenetre ?" (R-S8, U7).
    On interroge le gestionnaire plutot que de comparer des rectangles et des
    ordres Z, parce qu'il tient compte de ce que l'arithmetique ignore :
    plusieurs fenetres qui recouvrent une cible A DEUX, les regions non
    rectangulaires, les coins arrondis, les fenetres en couche. Mesure
    pendant R-S8 : une fenetre couverte conjointement par deux autres passait
    pour visible au test rectangle par rectangle, et pour couverte ici -- ce
    qui est la bonne reponse.

    Cout mesure : 0,007 a 0,016 ms par point.
    """
    try:
        hwnd = _user32.WindowFromPoint(_POINT(int(x), int(y)))
        if not hwnd:
            return None
        return int(_user32.GetAncestor(hwnd, _GA_ROOT) or hwnd)
    except Exception:  # noqa: BLE001
        return None


def _fenetre_sous(x: int, y: int) -> dict[str, Any] | None:
    """Fenetre de plus haut niveau sous le point. None si indisponible."""
    try:
        hwnd = _user32.WindowFromPoint(_POINT(x, y))
        if not hwnd:
            return None
        racine = _user32.GetAncestor(hwnd, _GA_ROOT) or hwnd
    except Exception:  # noqa: BLE001
        return None

    out: dict[str, Any] = {"hwnd": int(racine)}
    try:
        import win32gui
        import win32process

        out["class_name"] = str(win32gui.GetClassName(racine) or "")
        _, pid = win32process.GetWindowThreadProcessId(racine)
        out["pid"] = int(pid)
        try:
            import psutil

            out["exe_name"] = psutil.Process(pid).name()
        except Exception:  # noqa: BLE001
            out["exe_name"] = "?"
    except Exception:  # noqa: BLE001
        pass
    return out


def get_cursor(
    monitors: Any = None,
    *,
    region_size: int = 0,
    with_window: bool = True,
) -> dict[str, Any]:
    """Position du curseur a l'instant de l'appel. Ne leve jamais.

    `region_size` > 0 calcule le bbox d'un carre centre sur le pointeur,
    decoupe aux bornes de son ecran. La CAPTURE de cette region n'est pas
    faite ici : ce module reste sans dependance image, pour ne pas alourdir
    le demarrage a froid d'un outil appele a la demande.
    """
    res: dict[str, Any] = {
        "available": False,
        "reason": "",
        "source": "win32",
        "x": None,
        "y": None,
        "visible": None,
        "monitor_index": None,
        "window": None,
        "region": None,
    }

    if _user32 is None:
        res["reason"] = "lecture Win32 seulement (GetCursorPos). Sous X11, la main lit le pointeur."
        return res

    pos = _POINT()
    try:
        ok = _user32.GetCursorPos(ctypes.byref(pos))
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"{type(exc).__name__}: {exc}"
        return res
    if not ok:
        # Arrive sur le bureau securise : invite UAC, ecran de verrouillage.
        res["reason"] = "GetCursorPos refuse (bureau securise ou session verrouillee)"
        return res

    res["available"] = True
    res["x"] = int(pos.x)
    res["y"] = int(pos.y)

    info = _CURSORINFO()
    info.cbSize = ctypes.sizeof(_CURSORINFO)
    try:
        if _user32.GetCursorInfo(ctypes.byref(info)):
            res["visible"] = bool(info.flags & _CURSOR_SHOWING)
            if info.flags & _CURSOR_SUPPRESSED:
                res["hidden_reason"] = "pointeur supprime (saisie tactile)"
            elif not res["visible"]:
                res["hidden_reason"] = (
                    "pointeur masque : lecteur plein ecran, jeu, ou inactivite. "
                    "La position reste celle du dernier deplacement et ne dit "
                    "rien de l'attention."
                )
    except Exception:  # noqa: BLE001
        res["visible"] = None

    if monitors is None:
        from screen_context import list_monitors

        monitors = list_monitors()
    idx = monitor_for_point(res["x"], res["y"], monitors)
    res["monitor_index"] = idx
    if idx is None:
        res["reason"] = (
            "curseur hors de tout ecran physique (zone du bureau virtuel "
            "non couverte par un moniteur)"
        )

    if with_window:
        res["window"] = _fenetre_sous(res["x"], res["y"])

    if region_size and idx is not None:
        mon = next((m for m in monitors if m.index == idx), None)
        if mon is not None:
            bbox, decoupe = cursor_region_bbox(res["x"], res["y"], region_size, mon.bbox)
            if bbox is not None:
                res["region"] = {
                    "bbox": list(bbox),
                    "size": int(min(region_size, MAX_REGION_PX)),
                    "width": bbox[2] - bbox[0],
                    "height": bbox[3] - bbox[1],
                    "clamped": decoupe,
                    "monitor_index": idx,
                    "source": "autour du curseur",
                }
    elif region_size:
        res["region"] = None
    return res
