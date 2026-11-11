# Run from the project folder: uv run --locked --extra qt --extra train python scripts/NAME.py [motion key]
"""Replay the guided motion_f5 recordings through the REAL motion-mode rules (src/motion_mode.py) and count, per repetition,
whether the registered custom motion (fist + down, config/gesture_bindings.json) fires during the `do` phase, and whether it fires
when it should not (while the hand returns up in `rest`, or in `prep`)."""
import collections
import glob
import gzip
import json
import sys

import numpy as np

sys.path.insert(0, ".")
from src.gesture_engine import GestureEngine, Point3D
from src.motion_mode import MotionMode, custom_posture_ok, hand_features

KEY = sys.argv[1] if len(sys.argv) > 1 else "motion_f5"
cfg = GestureEngine().config
GRACE = 0.4      # an event up to this long after the do phase still belongs to that repetition (the stroke is judged at its end)

files = []
for path in sorted(glob.glob("recordings/*.jsonl.gz")):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        head = [json.loads(next(fh)) for _ in range(1)]
    summary = path + ".summary.json"
    try:
        s = json.load(open(summary, encoding="utf-8"))
    except OSError:
        continue
    if KEY in s.get("reps", {}):
        files.append(path)
print("recordings with", KEY, ":", [f[-40:] for f in files])

rows = []      # (pose, rep, fired_in_do, fired_after_grace, posture_ok_share, best_dy, do_seconds)
false_events = collections.Counter()
other_events = collections.Counter()
for path in files:
    frames = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("type") != "meta" and "t" in r:
                frames.append(r)
    mm = MotionMode(cfg)
    mm.update(frames[0]["t"], None, request="on")           # motion mode is on, as the user had it
    events = []
    per_rep = collections.defaultdict(lambda: {"t0": None, "t1": None, "posture": [], "palm": []})
    for f in frames:
        t = float(f["t"])
        lab = f.get("label") or {}
        lm = f.get("lm")
        pts = [Point3D(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in lm] if lm else None
        ev = mm.update(t, pts)
        if ev is not None and ev.kind == "swipe":
            events.append((t, ev.action, lab.get("phase"), lab.get("rep"), lab.get("condition_key")))
        if lab.get("task") == KEY and lab.get("phase") == "do":
            key = (lab.get("condition_key"), lab.get("set"), lab.get("rep"))
            e = per_rep[key]
            e["t0"] = t if e["t0"] is None else e["t0"]
            e["t1"] = t
            if pts:
                feats = hand_features(pts)
                if feats is not None:
                    e["posture"].append(custom_posture_ok("fist", feats))
                    e["palm"].append((t, feats["palm"][0], feats["palm"][1]))
    for key, e in per_rep.items():
        t0, t1 = e["t0"], e["t1"]
        in_do = [x for x in events if t0 <= x[0] <= t1 and x[1] == KEY]
        late = [x for x in events if t1 < x[0] <= t1 + GRACE and x[1] == KEY]
        palm = e["palm"]
        dy = (max(p[2] for p in palm) - min(p[2] for p in palm)) if palm else 0.0
        rows.append((str(key[0])[-6:], key[2], bool(in_do), bool(in_do or late),
                     float(np.mean(e["posture"])) if e["posture"] else 0.0, dy, t1 - t0))
    do_windows = [(e["t0"], e["t1"]) for e in per_rep.values()]
    for t, action, phase, rep, pose in events:
        inside = any(a <= t <= b + GRACE for a, b in do_windows)
        if action == KEY and not inside:
            false_events[phase] += 1
        elif action != KEY:
            other_events[action] += 1

print(f"\nrepetitions: {len(rows)}")
for pose in sorted({r[0] for r in rows}):
    sel = [r for r in rows if r[0] == pose]
    print(f"  pose ..{pose}: {sum(r[2] for r in sel)}/{len(sel)} fired inside do, {sum(r[3] for r in sel)}/{len(sel)} counting {GRACE}s after; "
          f"fist posture on {np.mean([r[4] for r in sel]):.0%} of do frames; palm moved {np.median([r[5] for r in sel]):.2f} (median)")
tot = len(rows)
print(f"TOTAL: fired in do {sum(r[2] for r in rows)}/{tot} ({sum(r[2] for r in rows)/tot:.0%}); with grace {sum(r[3] for r in rows)}/{tot}")
print("fired when it should not (outside any do window, by phase):", dict(false_events))
print("other motion commands fired during these recordings:", dict(other_events))
print("misses: pose, rep, fist-posture share, palm vertical travel, do seconds")
for r in rows:
    if not r[3]:
        print("  ", r[0], r[1], f"{r[4]:.0%}", f"{r[5]:.2f}", f"{r[6]:.1f}")
