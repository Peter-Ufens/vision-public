"""Events vision : journal JSONL v1 + PUB ZeroMQ (pont Avatar).

Local only : data/events/ (gitignored).
Contrat : events_v1.py + schema evenements v1 (Avatar, ADR-0005).

Retention : plafond lignes + age max.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from events_v1 import (
    BODY_POSE,
    DEFAULT_ZMQ_ADDR,
    FACE_DETECTED,
    FACE_LOST,
    GESTURE_RECOGNIZED,
    PERSON_IDENTIFIED,
    SCREEN_CONTEXT,
    SOURCE_SCREEN,
    SOURCE_WEBCAM,
    bbox_norm_from_xywh,
    event_to_line,
    make_event,
    now_ns,
)

ROOT = Path(__file__).resolve().parents[1]
EVENTS_DIR = ROOT / "data" / "events"
EVENTS_FILE = EVENTS_DIR / "vision_events.jsonl"

MAX_EVENT_LINES = 2000
MAX_EVENT_AGE_S = 7 * 24 * 3600

# Cooldowns réflexe (alignés TTL Avatar ~300-500 ms)
COOLDOWN_FACE_S = 0.22
COOLDOWN_GESTURE_S = 1.0
COOLDOWN_BODY_S = 1.0
COOLDOWN_DEFAULT_S = 0.5


def prune_events_file(
    path: Path,
    max_lines: int = MAX_EVENT_LINES,
    max_age_s: float = MAX_EVENT_AGE_S,
    now: float | None = None,
) -> int:
    """Garde les N dernières lignes v1 récentes (fallback V0 via ts epoch)."""
    if not path.exists():
        return 0
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    if not raw:
        return 0
    tnow = time.time() if now is None else now
    tnow_ns = now_ns()
    kept: list[str] = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # v1 : âge via t_emit_ns (perf_counter) peu comparable à wall clock
        # → rétention surtout par nombre de lignes ; âge wall si champ ts V0
        if "ts" in obj and "t_emit_ns" not in obj:
            try:
                if tnow - float(obj["ts"]) > max_age_s:
                    continue
            except (TypeError, ValueError):
                continue
        elif "t_wall_s" in obj:
            try:
                if tnow - float(obj["t_wall_s"]) > max_age_s:
                    continue
            except (TypeError, ValueError):
                continue
        elif "t_emit_ns" in obj:
            # perf_counter_ns : pas d'âge wall fiable → garder, trim par max_lines
            pass
        kept.append(line)
    before = len(raw)
    if len(kept) > max_lines:
        kept = kept[-max_lines:]
    removed = before - len(kept)
    if removed <= 0:
        return 0
    try:
        path.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")
    except OSError as exc:
        print(f"events prune KO : {exc}")
        return 0
    return removed


class EventBus:
    """Émet events v1 : JSONL local + optionnel ZeroMQ PUB."""

    def __init__(
        self,
        path: Path | None = None,
        cooldown_s: float = COOLDOWN_DEFAULT_S,
        cooldown_face_s: float = COOLDOWN_FACE_S,
        enabled: bool = True,
        max_lines: int = MAX_EVENT_LINES,
        max_age_s: float = MAX_EVENT_AGE_S,
        zmq_enabled: bool = True,
        zmq_addr: str = DEFAULT_ZMQ_ADDR,
        source: str = SOURCE_WEBCAM,
    ) -> None:
        self.path = path or EVENTS_FILE
        self.cooldown_s = cooldown_s
        self.cooldown_face_s = cooldown_face_s
        self.enabled = enabled
        self.max_lines = max_lines
        self.max_age_s = max_age_s
        self.source = source
        self._last: dict[str, float] = {}
        self.last_event: dict[str, Any] | None = None
        self._writes_since_prune = 0
        self._active_track_ids: set[int] = set()
        self._zmq_sock = None
        self._zmq_ctx = None
        self.zmq_addr = zmq_addr
        self.zmq_ok = False
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            prune_events_file(self.path, self.max_lines, self.max_age_s)
            if zmq_enabled:
                self._init_zmq()

    def _init_zmq(self) -> None:
        try:
            import zmq

            self._zmq_ctx = zmq.Context.instance()
            sock = self._zmq_ctx.socket(zmq.PUB)
            sock.bind(self.zmq_addr)
            self._zmq_sock = sock
            self.zmq_ok = True
            # Slow joiner : SUB Avatar a besoin d’un instant
            time.sleep(0.15)
            print(f"Events ZMQ PUB : {self.zmq_addr}")
        except Exception as exc:  # noqa: BLE001
            print(f"Events ZMQ KO ({exc}) → JSONL seul")
            self._zmq_sock = None
            self.zmq_ok = False

    def close(self) -> None:
        if self._zmq_sock is not None:
            try:
                self._zmq_sock.close(linger=0)
            except Exception:  # noqa: BLE001
                pass
            self._zmq_sock = None

    def _cooldown_ok(self, key: str, cooldown_s: float | None = None) -> bool:
        cd = self.cooldown_s if cooldown_s is None else cooldown_s
        now = time.time()
        prev = self._last.get(key, 0.0)
        if now - prev < cd:
            return False
        self._last[key] = now
        return True

    def emit_v1(
        self,
        type_: str,
        payload: dict[str, Any],
        *,
        conf: float,
        t_capture_ns: int,
        ttl_ms: int = 300,
        key: str | None = None,
        cooldown_s: float | None = None,
        source: str | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        sig = key or f"{type_}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
        cd = cooldown_s
        if cd is None:
            if type_ in (FACE_DETECTED, FACE_LOST, PERSON_IDENTIFIED):
                cd = self.cooldown_face_s
            elif type_ == GESTURE_RECOGNIZED:
                cd = COOLDOWN_GESTURE_S
            elif type_ == BODY_POSE:
                cd = COOLDOWN_BODY_S
            else:
                cd = self.cooldown_s
        if not self._cooldown_ok(sig, cd):
            return False
        evt = make_event(
            type_=type_,
            source=source or self.source,
            conf=conf,
            payload=payload,
            t_capture_ns=t_capture_ns,
            ttl_ms=ttl_ms,
        )
        self.last_event = evt
        line_bus = event_to_line(evt)
        line_file = event_to_line({**evt, "t_wall_s": time.time()})
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line_file + "\n")
        except OSError as exc:
            print(f"events write KO : {exc}")
            return False
        if self._zmq_sock is not None:
            try:
                self._zmq_sock.send_string(line_bus)
            except Exception as exc:  # noqa: BLE001
                print(f"events zmq send KO : {exc}")
        self._writes_since_prune += 1
        if self._writes_since_prune >= 50:
            self._writes_since_prune = 0
            prune_events_file(self.path, self.max_lines, self.max_age_s)
        return True

    # --- Helpers mapping webcam ---

    def emit_faces_frame(
        self,
        *,
        t_capture_ns: int,
        frame_w: int,
        frame_h: int,
        faces: list[dict[str, Any]],
    ) -> None:
        """
        faces items: {
          track_id, x,y,bw,bh, det_score,
          identity_label, identity_score, matched: bool,
          emotion: str|None
        }
        """
        current_ids: set[int] = set()
        if faces:
            for f in faces:
                tid = int(f.get("track_id", 0))
                current_ids.add(tid)
                bbox, center, area = bbox_norm_from_xywh(
                    int(f["x"]), int(f["y"]), int(f["bw"]), int(f["bh"]), frame_w, frame_h
                )
                identity = {
                    "label": f.get("identity_label") or "inconnu",
                    "score": float(f.get("identity_score") or 0.0),
                }
                payload: dict[str, Any] = {
                    "track_id": tid,
                    "bbox_norm": bbox,
                    "center_norm": center,
                    "area_norm": area,
                    "identity": identity,
                }
                emo = f.get("emotion")
                if emo and emo not in ("-", "neutre"):
                    payload["emotion"] = emo
                conf = float(f.get("det_score") or 0.5)
                if f.get("matched"):
                    self.emit_v1(
                        PERSON_IDENTIFIED,
                        payload,
                        conf=max(conf, float(identity["score"])),
                        t_capture_ns=t_capture_ns,
                        ttl_ms=500,
                        key=f"person:{tid}",
                    )
                else:
                    self.emit_v1(
                        FACE_DETECTED,
                        payload,
                        conf=conf,
                        t_capture_ns=t_capture_ns,
                        ttl_ms=400,
                        key=f"face:{tid}",
                    )

        for lost_tid in self._active_track_ids - current_ids:
            self.emit_v1(
                FACE_LOST,
                {"track_id": int(lost_tid)},
                conf=1.0,
                t_capture_ns=t_capture_ns,
                ttl_ms=200,
                key=f"face_lost:{lost_tid}",
            )
        self._active_track_ids = current_ids

    def emit_gesture(
        self,
        *,
        t_capture_ns: int,
        label: str,
        side: str,
        score: float,
    ) -> None:
        self.emit_v1(
            GESTURE_RECOGNIZED,
            {"label": label, "side": side, "score": score},
            conf=max(0.0, min(1.0, score)),
            t_capture_ns=t_capture_ns,
            ttl_ms=400,
            key=f"gesture:{label}:{side}",
        )

    def emit_bodies(
        self,
        *,
        t_capture_ns: int,
        count: int,
        max_poses: int,
        tags: list[str] | None = None,
    ) -> None:
        self.emit_v1(
            BODY_POSE,
            {"count": count, "max": max_poses, "tags": tags or []},
            conf=1.0 if count > 0 else 0.0,
            t_capture_ns=t_capture_ns,
            ttl_ms=400,
            key=f"body:{count}",
        )

    # --- Helper ecran (R-S1b) ---

    def emit_screen_context(
        self,
        payload: dict[str, Any],
        *,
        conf: float,
        t_capture_ns: int,
        key: str | None = None,
        ttl_ms: int = 5000,
    ) -> bool:
        """Emet un `screen.context` v1 (source `vision.screen`).

        Contrat : events v1 / ADR-0005. Aucun second schema.
        Payload documente dans context/screen-context-schema-v1.md.

        `ttl_ms` volontairement long (5 s) par rapport aux events webcam
        (300 a 500 ms) : un contexte ecran reste vrai tant que l'utilisateur ne
        change ni de fenetre ni de page, alors qu'un visage detecte se
        perime en quelques centaines de millisecondes.

        `cooldown_s=0.0` : la deduplication se fait en amont, sur le
        changement de contexte (screen_context.emit_screen_context). Une
        temporisation en plus ici avalerait un changement rapide de fenetre,
        qui est justement ce que le consommateur veut voir.

        `source` est force a `vision.screen` meme si ce bus a ete cree pour
        la webcam : le type d'event et sa source doivent rester coherents.
        """
        return self.emit_v1(
            SCREEN_CONTEXT,
            payload,
            conf=conf,
            t_capture_ns=t_capture_ns,
            ttl_ms=ttl_ms,
            key=key or "screen.context",
            cooldown_s=0.0,
            source=SOURCE_SCREEN,
        )

    def hud_line(self) -> str:
        if self.last_event is None:
            return "evt:-"
        # Affiche type court (pas d'âge wall clock fiable avec perf_counter)
        typ = str(self.last_event.get("type", "?"))
        short = typ.split(".")[-1] if typ else "?"
        zmq = "z" if self.zmq_ok else "-"
        return f"evt:{short}/{zmq}"
