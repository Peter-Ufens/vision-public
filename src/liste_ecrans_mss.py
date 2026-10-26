"""Liste mss sans cv2.

`screen_context` ne doit pas importer cv2 (demarrage a froid).
Sous Linux, mss 10.2 ouvre par defaut un backend XCB qui refuse certains
visuels X11 (« drawable's visual not found »). Le backend xlib capture.
Windows ne passe pas ici : `screen_context.list_monitors` reste Win32.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Any

_sct: Any = None


@dataclass(frozen=True)
class EcranMss:
    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool
    name: str


def ouvrir_mss() -> Any:
    import mss
    from mss.exception import ScreenShotError

    try:
        return mss.MSS()
    except ScreenShotError:
        if not sys.platform.startswith("linux"):
            raise
        return mss.MSS(backend="xlib")


def sct() -> Any:
    global _sct
    if _sct is None:
        _sct = ouvrir_mss()
    return _sct


def reset() -> None:
    global _sct
    if _sct is not None:
        try:
            _sct.close()
        except Exception:  # noqa: BLE001
            pass
        _sct = None


def _origine_primaire_xrandr() -> tuple[int, int] | None:
    """Coin haut-gauche du moniteur marque `+` par xrandr, ou None."""
    try:
        proc = subprocess.run(
            ["xrandr", "--listmonitors"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[1].startswith("+"):
            continue
        bouts = parts[2].split("+")
        if len(bouts) != 3:
            continue
        try:
            return int(bouts[1]), int(bouts[2])
        except ValueError:
            continue
    return None


def _trier_comme_windows(
    out: list[EcranMss],
    origine: tuple[int, int] | None = None,
    *,
    interroger: bool = True,
) -> list[EcranMss]:
    """Primaire d'abord, puis gauche, puis haut. Meme cle que Win32.

    mss sur X11 suit l'ordre des CRTC, et ne remplit pas `is_primary`.
    Sans ce tri, l'index 1 n'est pas le primaire des qu'il n'est pas a gauche.
    Si xrandr ne dit pas qui est primaire, l'ordre mss est garde.
    ``interroger=False`` sert au smoke : on lui donne le coin du primaire.
    """
    if len(out) == 1:
        m = out[0]
        return [EcranMss(1, m.left, m.top, m.width, m.height, True, m.name)]
    if interroger:
        origine = _origine_primaire_xrandr()

    def primaire(m: EcranMss) -> bool:
        if m.is_primary:
            return True
        return origine is not None and (m.left, m.top) == origine

    if not any(primaire(m) for m in out):
        return out
    ranked = sorted(out, key=lambda m: (not primaire(m), m.left, m.top))
    return [
        EcranMss(i, m.left, m.top, m.width, m.height, primaire(m), m.name)
        for i, m in enumerate(ranked, start=1)
    ]


def lister() -> list[EcranMss]:
    """Moniteurs physiques mss. Index 1 = le premier apres le tri Linux."""
    try:
        monitors = list(sct().monitors)
    except Exception:
        reset()
        monitors = list(sct().monitors)
    out: list[EcranMss] = []
    for i, mon in enumerate(monitors):
        if i == 0:
            continue
        out.append(
            EcranMss(
                index=i,
                left=int(mon["left"]),
                top=int(mon["top"]),
                width=int(mon["width"]),
                height=int(mon["height"]),
                is_primary=bool(mon.get("is_primary", False)),
                name=str(mon.get("name") or f"Monitor-{i}"),
            )
        )
    if sys.platform == "win32":
        return out
    return _trier_comme_windows(out)
