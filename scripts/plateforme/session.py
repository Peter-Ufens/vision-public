"""Nomme la session graphique : win32, x11 ou wayland.

Lecture seule. Ne capture rien, ne deplace rien.
``VISION_SESSION`` force le nom (tests). Sans cela, Windows reste win32.
Sous Linux, Ubuntu recent est wayland par defaut (``XDG_SESSION_TYPE``).
"""
from __future__ import annotations

import os
import sys
from typing import Mapping

SESSIONS = ("win32", "x11", "wayland")


class BackendManquant(RuntimeError):
    def __init__(self, session: str, suite: str) -> None:
        self.session = session
        self.suite = suite
        super().__init__(f"{session} : {suite}")


def nom(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    force = (source.get("VISION_SESSION") or "").strip().lower()
    if force in SESSIONS:
        return force
    if env is None and sys.platform == "win32":
        return "win32"
    session = (source.get("XDG_SESSION_TYPE") or "").strip().lower()
    if session == "wayland" or (source.get("WAYLAND_DISPLAY") and session != "x11"):
        return "wayland"
    if session == "x11" or source.get("DISPLAY"):
        return "x11"
    if env is None and sys.platform.startswith("linux"):
        return "wayland"
    return "inconnu"
