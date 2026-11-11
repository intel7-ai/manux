# Run from the project folder: uv run --locked --extra qt --extra train python scripts/NAME.py [motion key]
"""Stress test: replay every recording's FREE-movement stretches (the hand moving about, no gesture asked for) through the real
motion-mode rules with motion mode on, and count how often the registered fist+down motion fires by itself."""
import collections
import glob
import gzip
import json
import sys

sys.path.insert(0, ".")
from src.gesture_engine import GestureEngine, Point3D
from src.motion_mode import MotionMode

KEY = sys.argv[1] if len(sys.argv) > 1 else "motion_f5"
cfg = GestureEngine().config
free_seconds = collections.Counter()
fires = collections.Counter()
other = collections.Counter()
for path in sorted(glob.glob("recordings/*.jsonl.gz")):
    frames = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") != "meta" and "t" in r:
                frames.append(r)
    if not any((f.get("label") or {}).get("phase") == "free" for f in frames):
        continue
    mm = MotionMode(cfg)
    mm.update(frames[0]["t"], None, request="on")
    prev_t = None
    for f in frames:
        t = float(f["t"])
        lab = f.get("label") or {}
        lm = f.get("lm")
        pts = [Point3D(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in lm] if lm else None
        ev = mm.update(t, pts)
        task = lab.get("task")
        if lab.get("phase") == "free":
            if prev_t is not None and 0 < t - prev_t < 0.5:
                free_seconds[task] += t - prev_t
            if ev is not None and ev.kind == "swipe":
                (fires if ev.action == KEY else other)[(task, ev.action)] += 1
        prev_t = t
print("free-movement stretches replayed (minutes):", {k: round(v / 60, 1) for k, v in free_seconds.items()})
total_min = sum(free_seconds.values()) / 60
n = sum(fires.values())
print(f"{KEY} fired by itself {n} times in {total_min:.1f} min of free movement = {n / max(total_min, 1e-9):.2f} per minute")
print("by free-movement kind:", {k[0]: v for k, v in fires.items()})
print("other motion commands fired in the same stretches:", {f"{k[0]}->{k[1]}": v for k, v in other.items()})
