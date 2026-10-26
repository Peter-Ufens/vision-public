"""Events v1 : contrat unique Vision ↔ Avatar.

Canon evenements v1 (schema Avatar, ADR-0005).
Ne pas inventer un 2e schema.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_ZMQ_ADDR = "tcp://127.0.0.1:5556"

# Catalogue type (aligné Avatar EventType)
FACE_DETECTED = "face.detected"
FACE_LOST = "face.lost"
PERSON_IDENTIFIED = "person.identified"
GESTURE_RECOGNIZED = "gesture.recognized"
BODY_POSE = "body.pose"
MOTION_BURST = "motion.burst"
SCREEN_CONTEXT = "screen.context"

SOURCE_WEBCAM = "vision.webcam"
SOURCE_SCREEN = "vision.screen"


def now_ns() -> int:
    return time.perf_counter_ns()


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def make_event(
    *,
    type_: str,
    source: str,
    conf: float,
    payload: dict[str, Any],
    t_capture_ns: int,
    ttl_ms: int = 300,
    t_emit_ns: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Construit un objet event v1 (dict JSON-serializable)."""
    conf_c = max(0.0, min(1.0, float(conf)))
    return {
        "v": SCHEMA_VERSION,
        "id": event_id or new_event_id(),
        "t_capture_ns": int(t_capture_ns),
        "t_emit_ns": int(t_emit_ns if t_emit_ns is not None else now_ns()),
        "source": source,
        "type": type_,
        "conf": conf_c,
        "ttl_ms": int(ttl_ms),
        "payload": payload,
    }


def event_to_line(evt: dict[str, Any]) -> str:
    return json.dumps(evt, ensure_ascii=False)


def bbox_norm_from_xywh(
    x: int, y: int, bw: int, bh: int, frame_w: int, frame_h: int
) -> tuple[list[float], list[float], float]:
    """Retourne (bbox_norm [x,y,w,h], center_norm, area_norm)."""
    fw = max(1, frame_w)
    fh = max(1, frame_h)
    nx = max(0.0, min(1.0, x / fw))
    ny = max(0.0, min(1.0, y / fh))
    nw = max(0.0, min(1.0, bw / fw))
    nh = max(0.0, min(1.0, bh / fh))
    cx = max(0.0, min(1.0, (x + bw * 0.5) / fw))
    cy = max(0.0, min(1.0, (y + bh * 0.5) / fh))
    area = max(0.0, min(1.0, nw * nh))
    return [nx, ny, nw, nh], [cx, cy], area
