"""Reading training recordings and replaying them as HandObservations (docs/contracts.md section 9).

Reads the `hand-mouse-recording/1` format written by Dan's recorder (hand-mouse-gesture-overhaul
`src/recorder.py`): gzip or plain JSON Lines with a `meta` first line, `frame` / `event` lines,
and an `end` last line. Only finished files (with an `end` line, not `.part`) count as complete.

Replay turns each frame line into the same `(FrameInfo, HandObservation | None)` the live tracker
produces, so engine code can be compared on recorded input without a camera.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from manux.core.contracts import FrameInfo, HandObservation

SCHEMAS = ("hand-mouse-recording/1",)
DEFAULT_WIDTH = 1280   # used when a recording does not say its camera size


class RecordingError(ValueError):
    pass


@dataclass
class Recording:
    path: Path
    meta: Dict[str, Any]
    frames: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    end: Optional[Dict[str, Any]] = None
    bad_lines: int = 0

    @property
    def complete(self) -> bool:
        return self.end is not None and not self.path.name.endswith(".part")

    @property
    def mirrored(self) -> bool:
        return bool((self.meta.get("camera") or {}).get("mirror", True))

    def frame_size(self) -> Tuple[int, int, bool]:
        """(width, height, known). Unknown size: DEFAULT_WIDTH and the recorded frame_aspect."""
        mode = (self.meta.get("camera") or {}).get("mode")
        if isinstance(mode, (list, tuple)) and len(mode) >= 2 and int(mode[0]) > 0 and int(mode[1]) > 0:
            return int(mode[0]), int(mode[1]), True
        aspect = float(self.meta.get("frame_aspect") or 0.5625)
        return DEFAULT_WIDTH, max(1, round(DEFAULT_WIDTH * aspect)), False


def _open(path: Path):
    with open(path, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rt", encoding="utf-8") if gz else open(path, "r", encoding="utf-8")


def read(path: Path) -> Recording:
    """Read a whole recording. Raises RecordingError for a wrong or missing meta line."""
    path = Path(path)
    rec: Optional[Recording] = None
    with _open(path) as f:
        for n, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                if rec is None:
                    raise RecordingError(f"{path.name}: first line is not JSON")
                rec.bad_lines += 1    # a half-written last line, typically
                continue
            kind = obj.get("type") if isinstance(obj, dict) else None
            if rec is None:
                if kind != "meta":
                    raise RecordingError(f"{path.name}: first line must be meta, got {kind!r}")
                if obj.get("schema") not in SCHEMAS:
                    raise RecordingError(f"{path.name}: unknown schema {obj.get('schema')!r}")
                rec = Recording(path=path, meta=obj)
            elif kind == "frame":
                rec.frames.append(obj)
            elif kind == "event":
                rec.events.append(obj)
            elif kind == "end":
                rec.end = obj
            else:
                rec.bad_lines += 1
    if rec is None:
        raise RecordingError(f"{path.name}: empty file")
    return rec


@dataclass(frozen=True)
class ReplayFrame:
    frame: FrameInfo
    hand: Optional[HandObservation]
    record: Dict[str, Any]          # the raw frame line (label, rule, cursor ...)


def replay(rec: Recording) -> Iterator[ReplayFrame]:
    """Each frame line as the tracker would have produced it. A malformed hand becomes None."""
    width, height, _ = rec.frame_size()
    mirrored = rec.mirrored
    for seq, obj in enumerate(rec.frames):
        frame = FrameInfo(seq=seq, t=float(obj.get("t", 0.0)), width=width, height=height, mirrored=mirrored)
        yield ReplayFrame(frame, _hand(obj, frame, mirrored), obj)


def _hand(obj: Dict[str, Any], frame: FrameInfo, mirrored: bool) -> Optional[HandObservation]:
    lm, info = obj.get("lm"), obj.get("hand")
    if not lm or not info:
        return None
    label, score = (info + [1.0])[:2] if isinstance(info, list) else (None, None)
    if label not in ("Left", "Right"):
        return None
    side = {"Left": "Right", "Right": "Left"}[label] if mirrored else label   # same rule as tracking.selection
    try:
        return HandObservation(frame=frame, landmarks=tuple(tuple(p) for p in lm), handedness=side,
                               score=float(score), world=tuple(tuple(p) for p in obj["world"]) if obj.get("world") else None)
    except (ValueError, TypeError):
        return None


def summary(rec: Recording) -> Dict[str, Any]:
    """Counts a person can check quickly (no frame data, no names)."""
    frames = rec.frames
    with_hand = sum(1 for f in frames if f.get("lm"))
    replayed = sum(1 for r in replay(rec) if r.hand is not None)
    labels = Counter(str((f.get("label") or {}).get("target", "none")) for f in frames if f.get("lm"))
    duration = float(frames[-1].get("t", 0.0)) - float(frames[0].get("t", 0.0)) if len(frames) > 1 else 0.0
    width, height, known = rec.frame_size()
    return {
        "file": rec.path.name, "schema": rec.meta.get("schema"), "mode": rec.meta.get("mode"),
        "complete": rec.complete, "frames": len(frames), "with_hand": with_hand,
        "replayed_hands": replayed, "invalid_hands": with_hand - replayed,
        "labels": dict(sorted(labels.items())), "marks": sum(1 for e in rec.events if e.get("kind") == "mark"),
        "duration_s": round(duration, 2), "fps": round((len(frames) - 1) / duration, 1) if duration > 0 else None,
        "frame_size": [width, height], "frame_size_known": known, "mirrored": rec.mirrored,
        "world_joints": sum(1 for f in frames if f.get("world")), "bad_lines": rec.bad_lines,
    }
