"""Liste des ecrans selon la session.

Windows : ``screen_context`` (primaire d'abord, puis gauche). Inchange.
X11 : mss via ``screen_capture`` (ordre mss, pas encore le tri Windows).
Wayland : pas branche. Prévu : xdg-desktop-portal, pas un grab silencieux.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from plateforme.session import BackendManquant, nom

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@dataclass(frozen=True)
class Ecran:
    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool
    source: str


def _depuis(mon: object, source: str) -> Ecran:
    return Ecran(
        index=int(mon.index),  # type: ignore[attr-defined]
        left=int(mon.left),  # type: ignore[attr-defined]
        top=int(mon.top),  # type: ignore[attr-defined]
        width=int(mon.width),  # type: ignore[attr-defined]
        height=int(mon.height),  # type: ignore[attr-defined]
        is_primary=bool(getattr(mon, "is_primary", False)),
        source=source,
    )


def lister(env: Mapping[str, str] | None = None) -> list[Ecran]:
    kind = nom(env)
    if kind == "wayland":
        raise BackendManquant(
            "wayland",
            "liste des ecrans prevue via xdg-desktop-portal (ScreenCast). "
            "mss sur Wayland rend souvent une image noire. Non branche.",
        )
    if kind == "win32":
        from screen_context import list_monitors
        return [_depuis(m, "screen_context") for m in list_monitors()]
    if kind == "x11":
        from screen_capture import list_monitors
        return [_depuis(m, "mss") for m in list_monitors()]
    raise BackendManquant(kind, "session inconnue, aucun ecran liste")
