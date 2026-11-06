"""Synthetic `hand-mouse-recording/1` files for the pose tools (no real person's data, D-006).

Each class is a bent-finger pose of `perception/pose_hands.make_hand` with a little noise, so the 33
`pose-angles-v1` columns of the classes are clearly apart and a small MLP separates them.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perception"))
import pose_hands as H   # noqa: E402

POSES: Dict[str, dict] = {
    "none": {},
    "thumb_ring": {"ring": 0.9, "thumb": 0.5},
    "thumb_index": {"index": 0.9, "thumb": 0.5},
    "thumb_middle": {"middle": 0.9, "thumb": 0.5},
}
GESTURE_OF = {"thumb_ring": "pinch.thumb_ring", "thumb_index": "pinch.scroll", "thumb_middle": "pinch.thumb_middle"}
FPS = 30.0


class Run:
    """`frames` consecutive frames of one pose. `gap` seconds of silence come first (a new segment)."""

    def __init__(self, label: str, frames: int = 30, *, gap: float = 0.0, gesture: Optional[str] = ...,
                 world: bool = True, condition: Optional[str] = None, target: Optional[str] = ...):
        self.label, self.frames, self.gap, self.world, self.condition = label, frames, gap, world, condition
        self.gesture = GESTURE_OF.get(label) if gesture is ... else gesture
        self.target = label if target is ... else target


def write_recording(path: Path, runs: Sequence[Run], *, person: str = "tester01", split: str = "train",
                    complete: bool = True, seed: int = 0, noise: float = 0.0012) -> Path:
    rng = np.random.default_rng(seed)
    lines: List[dict] = [{"type": "meta", "schema": "hand-mouse-recording/1", "mode": "free",
                          "created": "2026-11-06T00:00:00Z", "pc": "pc-test", "pc_named": False, "person": person,
                          "split": split, "camera": {"source": "0", "mode": [1280, 720, 30], "mirror": True, "note": ""}}]
    t = 0.0
    n = 0
    for run in runs:
        t += run.gap
        base = H.make_hand(POSES.get(run.label, {}))
        for _ in range(run.frames):
            label = {"phase": "do"}
            if run.target is not None:
                label["target"] = run.target
            if run.gesture:
                label["gesture"] = run.gesture
            if run.condition:
                label["condition_key"] = run.condition
            frame = {"type": "frame", "t": round(t, 4), "lm": [[0.5, 0.5, 0.0]] * 21, "hand": ["Right", 0.99],
                     "label": label}
            if run.world:
                frame["world"] = (np.asarray(base) + rng.normal(0.0, noise, size=(21, 3))).round(6).tolist()
            lines.append(frame)
            t += 1.0 / FPS
            n += 1
    if complete:
        lines.append({"type": "end", "frames": n, "counts": {}, "marks": 0})
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def standard_runs(per_class: int = 8, frames: int = 30, classes: Sequence[str] = ("none", "thumb_ring", "thumb_index")
                  ) -> List[Run]:
    """`per_class` separate segments of every class, each after a gap (so they are independent segments)."""
    runs: List[Run] = []
    for k in range(per_class):
        for name in classes:
            runs.append(Run(name, frames, gap=1.0))
    return runs
