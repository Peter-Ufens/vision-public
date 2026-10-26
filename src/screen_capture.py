"""Capture multi-moniteurs : mss (Windows).

monitors[0] = bureau virtuel (tous écrans).
monitors[1..] = moniteurs physiques.

Robustesse jeux :
  BitBlt peut échouer au passage plein écran / exclusive GPU.
  → retry + recréation MSS + fallback Pillow ImageGrab.
  → jamais de crash : frame placeholder si tout échoue.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

_sct: Any = None
_last_err_ts = 0.0
_ERR_LOG_COOLDOWN = 2.0


@dataclass(frozen=True)
class MonitorInfo:
    index: int  # 1-based physique (mss index)
    left: int
    top: int
    width: int
    height: int
    is_primary: bool
    name: str

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """left, top, right, bottom (coords virtuelles)."""
        return (self.left, self.top, self.left + self.width, self.top + self.height)

    @property
    def label(self) -> str:
        prim = " primaire" if self.is_primary else ""
        return f"ecran {self.index}{prim}  {self.width}x{self.height}"


def _log_err(msg: str) -> None:
    global _last_err_ts
    now = time.perf_counter()
    if now - _last_err_ts >= _ERR_LOG_COOLDOWN:
        print(msg)
        _last_err_ts = now


def _get_sct() -> Any:
    global _sct
    if sys.platform != "win32":
        # Linux : mss 10.2 (XCB) peut refuser le visuel. Repli xlib
        # dans liste_ecrans_mss, sans cv2 de plus (deja charge ici).
        from liste_ecrans_mss import sct as sct_linux

        _sct = sct_linux()
        return _sct
    import mss

    if _sct is None:
        _sct = mss.MSS()
    return _sct


def reset_capture() -> None:
    """Ferme le handle GDI (utile après plein écran jeu)."""
    global _sct
    if sys.platform != "win32":
        from liste_ecrans_mss import reset as reset_linux

        reset_linux()
        _sct = None
        return
    if _sct is not None:
        try:
            _sct.close()
        except Exception:  # noqa: BLE001
            pass
        _sct = None


def _list_monitors_linux() -> list[MonitorInfo]:
    from liste_ecrans_mss import lister
    from liste_ecrans_mss import reset as reset_linux

    try:
        brut = lister()
    except Exception as exc:  # noqa: BLE001
        _log_err(f"list_monitors mss KO ({exc}) -> reset")
        reset_linux()
        global _sct
        _sct = None
        try:
            brut = lister()
        except Exception as exc2:  # noqa: BLE001
            _log_err(f"list_monitors impossible : {exc2}")
            return []
    return [
        MonitorInfo(
            index=e.index,
            left=e.left,
            top=e.top,
            width=e.width,
            height=e.height,
            is_primary=e.is_primary,
            name=e.name,
        )
        for e in brut
    ]


def list_monitors() -> list[MonitorInfo]:
    if sys.platform != "win32":
        return _list_monitors_linux()
    out: list[MonitorInfo] = []
    try:
        sct = _get_sct()
        monitors = list(sct.monitors)
    except Exception as exc:  # noqa: BLE001
        _log_err(f"list_monitors mss KO ({exc}) -> reset")
        reset_capture()
        try:
            sct = _get_sct()
            monitors = list(sct.monitors)
        except Exception as exc2:  # noqa: BLE001
            _log_err(f"list_monitors impossible : {exc2}")
            return out

    for i, mon in enumerate(monitors):
        if i == 0:
            continue
        out.append(
            MonitorInfo(
                index=i,
                left=int(mon["left"]),
                top=int(mon["top"]),
                width=int(mon["width"]),
                height=int(mon["height"]),
                is_primary=bool(mon.get("is_primary", False)),
                name=str(mon.get("name") or f"Monitor-{i}"),
            )
        )
    return out


def _placeholder(width: int, height: int, text: str) -> np.ndarray:
    w = max(64, width)
    h = max(64, height)
    # Miniature raisonnable si énorme
    if w > 960:
        scale = 960 / w
        w, h = 960, max(64, int(h * scale))
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = (40, 40, 50)
    cv2.putText(
        frame,
        text[:48],
        (16, min(h - 16, 40)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (180, 180, 220),
        1,
        cv2.LINE_AA,
    )
    return frame


def _grab_mss(region: dict[str, int]) -> np.ndarray | None:
    try:
        from mss.exception import ScreenShotError
    except ImportError:
        ScreenShotError = Exception  # type: ignore[misc, assignment]

    for attempt in range(2):
        try:
            sct = _get_sct()
            shot = sct.grab(region)
            bgra = np.array(shot, dtype=np.uint8)
            return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        except ScreenShotError as exc:
            _log_err(f"mss BitBlt KO (tentative {attempt + 1}) : {exc}")
            reset_capture()
            time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            _log_err(f"mss grab KO : {exc}")
            reset_capture()
            time.sleep(0.05)
    return None


def _grab_pil(left: int, top: int, width: int, height: int) -> np.ndarray | None:
    try:
        from PIL import ImageGrab

        bbox = (left, top, left + width, top + height)
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
        rgb = np.array(img)
        if rgb.ndim != 3:
            return None
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as exc:  # noqa: BLE001
        _log_err(f"PIL ImageGrab KO : {exc}")
        return None


def grab_region(left: int, top: int, width: int, height: int) -> np.ndarray:
    """Capture BGR d'une zone. Ne lève pas : placeholder si échec."""
    width = max(1, int(width))
    height = max(1, int(height))
    region = {"left": int(left), "top": int(top), "width": width, "height": height}

    frame = _grab_mss(region)
    if frame is not None:
        return frame

    frame = _grab_pil(left, top, width, height)
    if frame is not None:
        return frame

    return _placeholder(width, height, "capture KO (jeu / plein ecran?)")


def grab_monitor(mon: MonitorInfo) -> np.ndarray:
    """Capture BGR d'un moniteur physique. Ne lève pas."""
    return grab_region(mon.left, mon.top, mon.width, mon.height)


def scale_to_width(frame: np.ndarray, max_w: int) -> tuple[np.ndarray, float]:
    h, w = frame.shape[:2]
    if w <= max_w:
        return frame, 1.0
    scale = max_w / w
    view = cv2.resize(frame, (int(w * scale), int(h * scale)))
    return view, scale


def mosaic_layout(
    thumbs: list[np.ndarray],
    labels: list[str],
    gap: int = 12,
    pad: int = 16,
    header: int = 40,
) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Compose une rangée de miniatures. Retourne canvas + hitboxes (x0,y0,x1,y1) par thumb."""
    if not thumbs:
        blank = np.zeros((200, 400, 3), dtype=np.uint8)
        return blank, []

    th = max(t.shape[0] for t in thumbs)
    tw_sum = sum(t.shape[1] for t in thumbs)
    width = pad * 2 + tw_sum + gap * (len(thumbs) - 1)
    height = pad * 2 + header + th + 28
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:] = (28, 28, 32)

    cv2.putText(
        canvas,
        "vision-public ecrans  |  clic/1-9=focus  v=mode  q=quit",
        (pad, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (230, 230, 230),
        1,
        cv2.LINE_AA,
    )

    hitboxes: list[tuple[int, int, int, int]] = []
    x = pad
    y = pad + header
    for thumb, label in zip(thumbs, labels):
        h, w = thumb.shape[:2]
        y_off = y + (th - h) // 2
        canvas[y_off : y_off + h, x : x + w] = thumb
        x0, y0, x1, y1 = x, y_off, x + w, y_off + h
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (200, 200, 200), 1)
        cv2.putText(
            canvas,
            label,
            (x0, y1 + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        hitboxes.append((x0, y0, x1, y1))
        x += w + gap

    return canvas, hitboxes
