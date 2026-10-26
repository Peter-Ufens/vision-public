"""Barre des taches et horloge systeme (R-S2c).

L'utilisateur veut savoir ce qu'il y a dans sa barre des taches, et l'heure. Deux
constats de mesure ont oriente ce module :

  1. Windows expose ses boutons d'application avec une CLASSE dediee
     (`Taskbar.TaskListButtonAutomationPeer`), distincte des boutons systeme
     (`ToggleButton` : Demarrer, Recherche, zone de notification). Le tri se
     fait donc sur un critere de la plateforme, pas sur une liste de noms.
  2. Le widget meteo et l'horloge sont exposes en `Text` lisible par UIA. Il
     n'y a AUCUNE raison de les lire par OCR : c'est plus lent et moins sur.

L'heure elle-meme vient de l'horloge Python, qui est exacte par construction.
Le texte du widget est rendu a cote, a titre indicatif.

Deux barres coexistent sur un poste multi-ecran : `Shell_TrayWnd` sur l'ecran
principal et `Shell_SecondaryTrayWnd` sur les autres. Les deux sont rendues,
chacune rattachee a son ecran.

VIE PRIVEE : les noms d'applications epinglees et le texte du widget (ville,
meteo) sont des donnees personnelles. Ne pas les commiter.

Local only. Aucun reseau.
"""

from __future__ import annotations

import sys
import time
from typing import Any

try:
    import win32gui
except ImportError:  # Linux : pas de pywin32
    if sys.platform == "win32":  # Windows sans pywin32 : casser net, pas « 0 barre »
        raise
    win32gui = None  # type: ignore[assignment]

__all__ = ["get_system_info", "get_taskbars"]

# Classes des fenetres de barre des taches, ecran principal puis secondaires.
_CLASSES_BARRE = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")

# Fragment de classe qui identifie un bouton d'APPLICATION, par opposition
# aux boutons systeme. Verifie sur Windows 11 : les applications portent
# `Taskbar.TaskListButtonAutomationPeer`, les boutons systeme `ToggleButton`.
_CLASSE_APP = "tasklistbutton"


def _fenetres_barres() -> list[tuple[str, int]]:
    if win32gui is None:
        return []
    out: list[tuple[str, int]] = []
    for cls in _CLASSES_BARRE:
        hwnd = win32gui.FindWindow(cls, None)
        if hwnd:
            out.append((cls, hwnd))
        # Les barres secondaires peuvent etre multiples : on enumere aussi
        # toutes les fenetres portant cette classe.
        def _cb(h: int, _: Any, _cls: str = cls) -> None:
            try:
                if win32gui.GetClassName(h) == _cls and h not in [x[1] for x in out]:
                    out.append((_cls, h))
            except Exception:  # noqa: BLE001
                pass

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:  # noqa: BLE001
            pass
    return out


def get_taskbars(monitors: Any = None) -> dict[str, Any]:
    """Barres des taches visibles, leurs applications et leur zone systeme.

    Ne leve jamais. `available` faux avec une raison si UIA est hors service.
    """
    res: dict[str, Any] = {"available": False, "reason": "", "bars": [], "source": "uia"}
    try:
        import screen_uia as su
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"screen_uia indisponible : {exc}"
        return res
    if not su.is_available():
        res["reason"] = su.unavailable_reason() or "UIA indisponible"
        return res

    try:
        from screen_context import list_monitors, monitor_for_rect

        mons = monitors if monitors is not None else list_monitors()
    except Exception:  # noqa: BLE001
        mons, monitor_for_rect = None, None  # type: ignore[assignment]

    barres = _fenetres_barres()
    if not barres:
        res["reason"] = "aucune fenetre de barre des taches trouvee"
        return res
    res["available"] = True

    for cls, hwnd in barres:
        try:
            rect = list(win32gui.GetWindowRect(hwnd))
        except Exception:  # noqa: BLE001
            continue
        # Root frais : un element UIA en cache devient invalide des que
        # l'arbre est reconstruit.
        root = su.element_from_hwnd(hwnd)
        if root is None:
            res["bars"].append(
                {"class": cls, "bbox": rect, "available": False,
                 "reason": "ElementFromHandle a echoue"}
            )
            continue

        boutons, err_b = su.collect(root, "Button", limite=120)
        textes, err_t = su.collect(root, "Text", limite=60)

        apps = [
            {"name": b["name"], "bbox": b["bbox"], "source": "uia"}
            for b in boutons
            if not b["offscreen"] and _CLASSE_APP in (b["class"] or "").lower()
        ]
        apps.sort(key=lambda a: a["bbox"][0])

        systeme = [
            {"name": b["name"], "bbox": b["bbox"]}
            for b in boutons
            if not b["offscreen"] and _CLASSE_APP not in (b["class"] or "").lower()
        ]
        systeme.sort(key=lambda a: a["bbox"][0])

        # Zone de notification : les textes les plus a droite (horloge, meteo).
        tray = [
            {"text": t["name"], "bbox": t["bbox"]}
            for t in textes
            if not t["offscreen"] and t["name"].strip()
        ]
        tray.sort(key=lambda t: (t["bbox"][0], t["bbox"][1]))

        idx = None
        if mons is not None and monitor_for_rect is not None:
            try:
                idx, _ = monitor_for_rect(tuple(rect), mons)
            except Exception:  # noqa: BLE001
                idx = None

        res["bars"].append(
            {
                "class": cls,
                "bbox": rect,
                "monitor_index": idx,
                "available": True,
                "apps": apps,
                "apps_count": len(apps),
                "system_buttons_count": len(systeme),
                "tray_text": tray,
                "errors": err_b + err_t,
            }
        )
    return res


def get_system_info() -> dict[str, Any]:
    """Heure et date locales.

    Source : horloge Python, exacte par construction. On ne lit PAS l'heure
    par OCR sur la barre des taches : ce serait plus lent, faillible, et sans
    aucun gain. Le texte du widget est rendu par `get_taskbars`, a titre
    indicatif et sans etre confondu avec cette valeur.
    """
    maintenant = time.localtime()
    return {
        "time": time.strftime("%H:%M:%S", maintenant),
        "date": time.strftime("%Y-%m-%d", maintenant),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", maintenant),
        "weekday": time.strftime("%A", maintenant),
        "tz": time.strftime("%Z", maintenant),
        "epoch_ms": int(time.time() * 1000),
        "source": "horloge systeme",
    }
