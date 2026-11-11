"""The per-distance pinch report counts what the engine and the TCN gate did in each repetition."""
import gzip
import json

from src.gesture_catalog import BY_KEY
from src.pinch_report import analyze, format_report, has_engine_reading, newest_recording_with_engine_reading, read_recording

ACTION = BY_KEY["thumb_index"].action


def hand(scale):
    """21 points: wrist at (0.5, 0.8), the four knuckles `scale` away, so hand size is about `scale` frame widths."""
    pts = [[0.5, 0.8, 0.0]] * 21
    pts = [list(p) for p in pts]
    for k in (5, 9, 13, 17):
        pts[k] = [0.5, 0.8 - scale / (720 / 1280), 0.0]         # a distance of `scale` frame widths, seen along the height
    return pts


def frame(t, cond, rep, scale, engine, gated):
    return {"t": t, "lm": hand(scale),
            "label": {"task": "thumb_index", "phase": "do", "condition_key": cond, "set": 0 if cond == "near" else 1, "rep": rep},
            "rule": {"engine_action": engine, "gated": gated}}


def test_counts_engine_and_gate_per_repetition_and_orders_near_to_far():
    frames = []
    for rep in (1, 2):                                          # near: the engine fires both times, the TCN passes both
        frames += [frame(rep * 10.0, "near", rep, 0.08, "none", "none"), frame(rep * 10.0 + 0.2, "near", rep, 0.08, ACTION, ACTION)]
    frames += [frame(30.0, "far", 1, 0.03, "none", "none"), frame(30.3, "far", 1, 0.03, ACTION, "none")]    # the TCN refuses
    frames += [frame(40.0, "far", 2, 0.03, "none", "none"), frame(40.4, "far", 2, 0.03, "left_click", "none")]  # not read; wrong action
    meta = {"capture_plan": {"conditions": []}}
    rows = analyze(meta, frames, "thumb_index")
    assert [r["condition"] for r in rows] == ["near", "far"]    # larger hand first
    near, far = rows
    assert (near["reps"], near["engine_fired"], near["gate_passed"]) == (2, 2, 2)
    assert (far["reps"], far["engine_fired"], far["gate_passed"]) == (2, 1, 0)
    assert far["other_actions"] == {"left_click": 1}
    assert abs(near["size"] - 0.08) < 0.01 and abs(near["first_fire_median"] - 0.2) < 1e-6
    text = format_report(rows, "thumb_index")
    assert "near" in text and "1/2" in text


def test_a_recording_without_the_live_engine_reading_is_not_used(tmp_path):
    old = [{"t": 0.0, "label": {"task": "thumb_index", "phase": "do"}, "rule": {"action": ACTION}}]
    assert not has_engine_reading(old, "thumb_index")
    meta = {"type": "meta"}
    path = tmp_path / "20261109-000000-x-guided.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for rec in [meta] + old:
            fh.write(json.dumps(rec) + "\n")
    assert read_recording(path)[1] == old
    assert newest_recording_with_engine_reading(tmp_path, "thumb_index") is None
    new = tmp_path / "20261109-010000-y-guided.jsonl.gz"
    with gzip.open(new, "wt", encoding="utf-8") as fh:
        for rec in [meta, frame(0.0, "near", 1, 0.08, ACTION, ACTION)]:
            fh.write(json.dumps(rec) + "\n")
    assert newest_recording_with_engine_reading(tmp_path, "thumb_index") == new
