"""Capture d'un rectangle deja affiche. Aucune fenetre, aucune souris.

Windows et X11 : ``screen_capture.grab_region`` (mss, repli ImageGrab).
Wayland : refus. La suite prevue est xdg-desktop-portal + PipeWire,
avec un accord de l'utilisateur (pas un grab silencieux).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

from plateforme.session import BackendManquant, nom

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def capturer(left: int, top: int, width: int, height: int, env: Mapping[str, str] | None = None):
    kind = nom(env)
    if kind == "wayland":
        raise BackendManquant(
            "wayland",
            "capture prevue via xdg-desktop-portal ScreenCast et PipeWire. "
            "Non branche. mss n'est pas un backend Wayland fiable.",
        )
    if kind not in {"win32", "x11"}:
        raise BackendManquant(kind, "session inconnue, capture refusee")
    from screen_capture import grab_region
    return grab_region(int(left), int(top), int(width), int(height))
