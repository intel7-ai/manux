"""Replay actual action decisions, separate from pose-classification accuracy.

Recorded instructions are proxy labels, not proof of user intent. Reports never
turn a model on automatically, and explicitly identify missing quick-click data.
"""
from collections import defaultdict
from types import SimpleNamespace

import numpy as np

from src.gesture_catalog import TASKS, BY_KEY
from src.gesture_gate import GestureGate
from src.capture_dataset import frame_durations


def replay_interactions(session, conds, probabilities, threshold=.7, k=3):
    classes = session["classes"]
    info = {"classes": classes, "window": session["x"].shape[1], "threshold": threshold, "frames_in_a_row": k,
            "pipeline_version": "continuous-v2"}
    gate = GestureGate(None, info, session["meta"].get("config", session["meta"].get("rules", {})))
    frames = session["frames"]
    times = np.asarray([f["t"] for f in frames])
    durations = frame_durations(frames)
    baseline, candidate = [], []
    reps = defaultdict(list)
    rested = set()
    tasks = [t for t in TASKS if not t.free and t.label in classes]
    previous_rule, previous_out = None, None
    for i, f in enumerate(frames):
        lab, rule = f.get("label") or {}, f.get("rule") or {}
        if lab.get("phase") == "rest":
            rested.add((lab.get("task"), int(session["cond"][i]), lab.get("set"), lab.get("rep")))
        gate.observe(times[i], probabilities[i], session["features"][i])
        action = rule.get("action")
        result = SimpleNamespace(action=action, active_gesture=rule.get("gesture", "NONE"), is_moving=action == "move_cursor")
        output = gate.filter(times[i], result).action
        raw_targets, out_targets = set(), set()
        for task in tasks:
            if task.action:
                alias_ok = task.key != "thumb_index_middle" or rule.get("gesture") == "thumb_index_middle_pinch"
                alias_ok &= task.key != "thumb_middle_drag" or rule.get("gesture") != "thumb_index_middle_pinch"
                if alias_ok and action == task.action and (action not in ("scroll_grab", "drag_hold") or previous_rule != action):
                    raw_targets.add(task.key)
                if alias_ok and output == task.action and (output not in ("scroll_grab", "drag_hold") or previous_out != output):
                    out_targets.add(task.key)
            elif task.key in rule.get("targets", []):
                raw_targets.add(task.key)
                if gate.allows(task.key, times[i]):
                    out_targets.add(task.key)
        baseline.append(raw_targets)
        candidate.append(out_targets)
        previous_rule, previous_out = action, output
        if session["cond"][i] in conds and lab.get("phase") == "do" and lab.get("task") in BY_KEY and session["features"][i, -1]:
            reps[(lab["task"], int(session["cond"][i]), lab.get("set"), lab.get("rep"))].append(i)
    report = {}
    for task in tasks:
        row = {"reps": 0, "rules_success": 0, "tcn_success": 0, "lost_reps": 0,
               "quick_reps": 0, "quick_success": 0, "rules_false": 0, "tcn_false": 0,
               "negative_seconds": 0., "latencies_ms": [], "added_reps": 0}
        for key, indices in reps.items():
            if key[0] != task.key or key not in rested:
                continue
            start, end = indices[0], indices[-1]
            # A short reaction allowance, not the entire 1.5-second rest period.
            eligible = range(start, int(np.searchsorted(times, times[end] + .2, side="right")))
            old = [i for i in eligible if task.key in baseline[i]]
            new = [i for i in eligible if task.key in candidate[i]]
            row["reps"] += 1
            row["rules_success"] += bool(old)
            row["tcn_success"] += bool(new)
            row["lost_reps"] += bool(old) and not bool(new)
            row["added_reps"] += bool(new) and not bool(old)
            if new:
                row["latencies_ms"].append(round((times[new[0]] - times[start]) * 1000))
            # A truncated long hold is not evidence of a deliberately short tap.
            requested = frames[start]["label"].get("requested_hold")
            if requested is not None and float(requested) <= .7 and times[end] - times[start] + durations[end] <= .75:
                row["quick_reps"] += 1
                row["quick_success"] += bool(new)
        for i, f in enumerate(frames):
            lab = f.get("label") or {}
            # Other labelled gestures and explicit free movement are negatives;
            # rest onset is ambiguous while the user releases, so exclude it.
            negative = (lab.get("phase") == "free" or
                        (lab.get("phase") == "do" and lab.get("target") in classes
                         and lab.get("target") not in (task.label, "transition")) or
                        (lab.get("phase") == "do" and lab.get("target") in BY_KEY
                         and BY_KEY[lab["target"]].label != task.label))
            if negative and session["cond"][i] in conds and session["features"][i, -1]:
                row["negative_seconds"] += float(durations[i])
                row["rules_false"] += task.key in baseline[i]
                row["tcn_false"] += task.key in candidate[i]
        report[task.key] = row
    return report


def summarise_interactions(reports, classes):
    result = {}
    for label in classes:
        if label == "none":
            continue
        # Click and long-hold share a classifier, but click validation must never
        # be credited with the much easier long-hold repetition counts.
        entries = [r.get(label, {}) for r in reports]
        summed = {field: sum(r.get(field, 0) for r in entries) for field in (
            "reps", "rules_success", "tcn_success", "lost_reps", "added_reps", "quick_reps", "quick_success",
            "rules_false", "tcn_false", "negative_seconds")}
        latencies = [n for r in entries for n in r.get("latencies_ms", [])]
        summed["p95_latency_ms"] = round(float(np.percentile(latencies, 95))) if latencies else None
        enough = summed["reps"] >= 30 and summed["negative_seconds"] >= 60
        if label in ("thumb_middle", "thumb_ring"):
            enough &= summed["quick_reps"] >= 20
        summed["evidence_sufficient"] = bool(enough)
        summed["non_regression"] = bool(enough and summed["lost_reps"] == 0 and summed["tcn_false"] <= summed["rules_false"])
        if not summed["reps"]:
            text = "실제 동작 전달 평가 없음 · 별도 자세 녹화가 필요합니다."
        else:
            text = (f"녹화 동작 전달: 규칙 {summed['rules_success']}/{summed['reps']} → TCN {summed['tcn_success']}/{summed['reps']}"
                    f" · 놓친 동작 {summed['lost_reps']} · 비동작 구간 오작동 {summed['rules_false']}→{summed['tcn_false']}")
            if label in ("thumb_middle", "thumb_ring"):
                text += f" · 짧은 클릭 {summed['quick_success']}/{summed['quick_reps']}"
            if summed["p95_latency_ms"] is not None:
                text += f" · 지시 후 반응 P95 {summed['p95_latency_ms']}ms"
            if not enough:
                text += " · 검증 녹화 부족: 시험 모드 권장"
        summed["text"] = text
        result[label] = summed
    return result
