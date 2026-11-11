"""Phase 5: the rule engine's recorded readings through three gates: none (rules only), the existing checker (models/), and the learned model
(data/online/versions/production.pt with the OnlineGate). Only recordings with live engine readings (`rule.engine_action`, 2026-11-09 on).
Per recording: whether the base model trained on it (a fair test needs it not to). Results: logs/online_gate_replay.json.

프로젝트 폴더에서:  .venv\\Scripts\\python.exe scripts/online_gate_replay.py
"""
import collections
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)

import numpy as np  # noqa: E402

import src.gesture_gate as GG  # noqa: E402
from src.gesture_catalog import BY_KEY  # noqa: E402
from src.online.safety import OnlineGate  # noqa: E402
from src.online.samples import DEFAULT_ROOT  # noqa: E402
from src.pinch_report import read_recording  # noqa: E402

ACTIONS = {"scroll_grab", "left_click", "right_click", "task_switcher", "show_desktop", "drag_hold"}
CFG = json.loads((ROOT / "config" / "gestures.json").read_text(encoding="utf-8"))


def to_hand(f):
    if not f.get("lm") or not f.get("world"):
        return None
    return NS(landmarks=[NS(x=p[0], y=p[1], z=p[2]) for p in f["lm"]], world=f["world"], score=(f.get("hand") or [None, 1.0])[1])


def run(frames, gate, aspect):
    out = []
    for f in frames:
        r = f.get("rule") or {}
        res = NS(action=r.get("engine_action") or "none", active_gesture=r.get("engine_gesture") or "", is_moving=False)
        if gate is not None:
            gate.step(float(f["t"]), to_hand(f), aspect)
            res = gate.filter(float(f["t"]), res)
        out.append(res.action)
    return out


def score(frames, acts):
    t = np.array([float(f["t"]) for f in frames])
    labs = [f.get("label") or {} for f in frames]
    reps = collections.defaultdict(list)
    for i, lab in enumerate(labs):
        task = BY_KEY.get(lab.get("task"))
        if task and not task.free and lab.get("phase") == "do":
            reps[(lab.get("task"), lab.get("set"), lab.get("rep"))].append(i)
    hit = sum(1 for (task, _, _), idx in reps.items() if any(acts[i] == (BY_KEY[task].action or task) for i in idx))
    do_t = np.array([t[i] for idx in reps.values() for i in idx]) if reps else np.array([])
    quiet = {i for i in range(len(frames)) if labs[i].get("phase") in ("prep", "rest", "free") and (not len(do_t) or np.min(np.abs(do_t - t[i])) > .7)}
    minutes = sum(t[i + 1] - t[i] for i in quiet if i + 1 < len(t) and 0 < t[i + 1] - t[i] < .5) / 60
    false, prev = 0, None
    for i, a in enumerate(acts):
        if a in ACTIONS and a != prev and i in quiet:
            false += 1
        prev = a
    return {"hit": hit, "reps": len(reps), "false": false, "minutes": round(minutes, 3)}


def main() -> int:
    production = DEFAULT_ROOT / "versions" / "production.pt"
    if not production.is_file():
        print("no production model yet: run the app's 학습하기 once (it starts from the base model)")
        return 1
    validation = set(json.loads((DEFAULT_ROOT / "encoder.json").read_text(encoding="utf-8")).get("validation_sessions") or [])
    predict, info = GG.load_predictor(GG.DEFAULT_MODEL)
    results = {}
    for path in sorted((ROOT / "recordings").glob("*.jsonl.gz")):
        meta, frames = read_recording(path)
        frames = [f for f in frames if "t" in f]
        if not any("engine_action" in (f.get("rule") or {}) for f in frames):
            continue
        aspect = float(meta.get("frame_aspect") or .5625)
        session = path.name.split(".")[0]
        row = {"focus": meta.get("focus"), "held_out": session in validation, "pc": meta.get("pc")}
        row["rules"] = score(frames, run(frames, None, aspect))
        row["existing"] = score(frames, run(frames, GG.GestureGate(predict, dict(info), dict(CFG)), aspect))
        row["learned"] = score(frames, run(frames, OnlineGate(DEFAULT_ROOT, dict(CFG)), aspect))
        results[path.name] = row
        print(path.name[:30], row["focus"], "held-out" if row["held_out"] else "trained",
              {k: (row[k]["hit"], row[k]["reps"], row[k]["false"]) for k in ("rules", "existing", "learned")}, flush=True)
    (ROOT / "logs" / "online_gate_replay.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    for group in (True, False):
        rows = [r for r in results.values() if r["held_out"] == group]
        if not rows:
            continue
        line = []
        for k in ("rules", "existing", "learned"):
            hit = sum(r[k]["hit"] for r in rows); reps = sum(r[k]["reps"] for r in rows)
            false = sum(r[k]["false"] for r in rows); minutes = sum(r[k]["minutes"] for r in rows)
            line.append(f"{k} {hit}/{reps} false {false} ({false / max(minutes, 1e-9):.1f}/min)")
        print("held-out" if group else "trained on", len(rows), "recordings:", " | ".join(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
