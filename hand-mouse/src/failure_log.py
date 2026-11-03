"""
Failure recording: keep the last few seconds of tracking data, save them when it goes wrong.

Proposed 2026-11-04: instead of guessing why tracking drops (blur, darkness, frame edge,
hand too far), save what the tracker saw around each bad moment. Only numbers are kept:
21 joint positions per frame, confidence, cursor, speed, brightness, crop. No images.

Saved automatically when the hand vanishes while moving or in the middle of the frame
(not when it leaves through an edge on purpose), and manually with F / the dashboard
button for anything else that felt wrong (a misfired click...). Files go to
logs/failures/ (git-ignored) as JSON, oldest deleted beyond `max_files`.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

EDGE_MARGIN = .06  # a hand whose box ends this close to a frame edge may simply have left


def hand_box(landmarks: Sequence) -> Tuple[float, float, float, float]:
    xs = [p.x for p in landmarks]
    ys = [p.y for p in landmarks]
    return min(xs), min(ys), max(xs), max(ys)


def left_through_edge(landmarks: Sequence, margin: float = EDGE_MARGIN) -> bool:
    x0, y0, x1, y1 = hand_box(landmarks)
    return x0 <= margin or y0 <= margin or x1 >= 1 - margin or y1 >= 1 - margin


class FailureRecorder:
    def __init__(self, directory: Path, before: float = 2.0, after: float = 1.0,
                 cooldown: float = 3.0, max_files: int = 200) -> None:
        self.directory = Path(directory)
        self.before = float(before)
        self.after = float(after)
        self.cooldown = float(cooldown)
        self.max_files = int(max_files)
        self.samples: Deque[Dict[str, Any]] = deque()
        self.pending: Optional[Dict[str, Any]] = None  # trigger waiting for its "after" frames
        self.last_trigger = -1e9
        self.saved: List[Path] = []
        self.counts: Dict[str, int] = {}
        self.last_error = ""
        # automatic detection state
        self._had_hand = False
        self._last_landmarks = None
        self._tracked_since: Optional[float] = None

    # ------------------------------------------------------------------ per frame
    def record(self, t: float, *, landmarks: Optional[Sequence], score: float = 0.0,
               moving: bool = False, **extra: Any) -> Optional[str]:
        """Add one frame. Returns the kind of an automatic trigger it caused, if any."""
        sample = {"t": round(t, 4), "hand": landmarks is not None, "moving": bool(moving)}
        if landmarks is not None:
            sample["lm"] = [[round(p.x, 4), round(p.y, 4), round(p.z, 4)] for p in landmarks]
            sample["score"] = round(float(score), 3)
        sample.update({k: v for k, v in extra.items() if v is not None})
        self.samples.append(sample)
        # Keep `before` seconds; while a save is pending, keep everything from its window start
        keep_from = t - self.before
        if self.pending is not None:
            keep_from = min(keep_from, self.pending["t"] - self.before)
        while self.samples and self.samples[0]["t"] < keep_from:
            self.samples.popleft()

        kind = None
        if landmarks is not None:
            if not self._had_hand:
                self._tracked_since = t
            self._last_landmarks = landmarks
        elif self._had_hand and self._last_landmarks is not None:
            tracked_for = t - (self._tracked_since or t)
            if moving:
                kind = "lost_while_moving"
            elif tracked_for >= 1.0 and not left_through_edge(self._last_landmarks):
                kind = "lost_mid_frame"
            if kind is not None and not self.trigger(t, kind):
                kind = None
        self._had_hand = landmarks is not None
        return kind

    def trigger(self, t: float, kind: str, note: str = "") -> bool:
        """Save the window around `t` once `after` seconds have been recorded."""
        if self.pending is not None or t - self.last_trigger < self.cooldown:
            return False
        self.last_trigger = t
        self.pending = {"t": t, "kind": kind, "note": note}
        return True

    def poll(self, t: float, meta: Dict[str, Any]) -> Optional[Path]:
        """Write a pending trigger when its window is complete. Returns the file, if written."""
        p = self.pending
        if p is None or t < p["t"] + self.after:
            return None
        self.pending = None
        window = [s for s in self.samples if p["t"] - self.before <= s["t"] <= p["t"] + self.after]
        payload = {
            "kind": p["kind"],
            "note": p["note"],
            "trigger_t": p["t"],
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "meta": meta,
            "frames": [{**s, "t": round(s["t"] - p["t"], 4)} for s in window],  # t relative to trigger
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{p['kind']}.json"
            n = 1
            while path.exists():
                n += 1
                path = path.with_name(f"{time.strftime('%Y%m%d-%H%M%S')}-{p['kind']}-{n}.json")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self.last_error = str(exc)
            return None
        self.saved.append(path)
        self.counts[p["kind"]] = self.counts.get(p["kind"], 0) + 1
        self._prune()
        return path

    def _prune(self) -> None:
        try:
            files = sorted(self.directory.glob("*.json"))
        except OSError:
            return
        for old in files[:max(0, len(files) - self.max_files)]:
            try:
                old.unlink()
            except OSError:
                pass

    def summary_text(self) -> str:
        if not self.counts:
            return "아직 저장된 기록이 없습니다."
        names = {"lost_while_moving": "이동 중 놓침", "lost_mid_frame": "화면 안에서 놓침", "manual": "직접 표시"}
        return " · ".join(f"{names.get(k, k)} {v}건" for k, v in self.counts.items())
