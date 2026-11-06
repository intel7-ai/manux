import copy
import gzip
import json

import numpy as np
import pytest

from src import recording_plan as plans
from src.capture_dataset import load_sessions
from src.guided_recording import GuidedSession, tasks_for
from src.hand_tracker import HandDetection
from src.recorder import SessionRecorder, read_session
from src.training_center import dataset_summary
from tests.test_gesture_mouse import make_real_hand


def plan(*rows):
    return {"schema": plans.SCHEMA, "conditions": list(rows)}


def hand():
    lm = make_real_hand(curled=False)
    return HandDetection(landmarks=lm, handedness="Left", score=.95,
                         world=[[p.x, p.y, p.z] for p in lm])


def write_session(folder, rows, *, complete=True):
    rec = SessionRecorder(folder, "guided", {"capture_plan": plans.preset()}, now=0.0)
    for i, label in enumerate(rows):
        rec.frame(i / 20, hand() if label.get("hand", True) else None, label=label)
    path = rec.close()
    return path


def labels(key, *, n=12, index=0, offset=0):
    return [{"target": "thumb_ring" if i < n - 2 else "none", "task": "thumb_ring",
             "phase": "do" if i < n - 2 else "rest", "rep": 1, "set": offset,
             "condition_index": index, "condition_key": key} for i in range(n)]


def test_presets_cover_diverse_factors_and_positions():
    diverse = plans.normalise(plans.preset("diverse"))
    assert len(diverse["conditions"]) == 9
    for field in plans.OPTIONS:
        assert len({r[field] for r in diverse["conditions"]}) >= 2
    assert len({r["position"] for r in plans.preset("positions")["conditions"]}) == 9


@pytest.mark.parametrize("key,value", [("reps", 0), ("reps", 2.5), ("hold", 0), ("prep", float("nan")),
                                      ("free_seconds", 901), ("orientation", "unknown"), ("name", "")])
def test_bad_settings_do_not_replace_saved_plan(tmp_path, key, value):
    path = tmp_path / "plans.json"
    plans.save_plan(plans.preset(), "thumb_ring", path)
    before = path.read_bytes()
    bad = plans.preset()
    bad["conditions"][0][key] = value
    with pytest.raises((ValueError, TypeError)):
        plans.save_plan(bad, "thumb_ring", path)
    assert path.read_bytes() == before


def test_each_gesture_keeps_its_own_plan_and_disabled_rows_are_skipped(tmp_path):
    path = tmp_path / "plans.json"
    a = plans.preset("diverse")
    a["conditions"][1]["enabled"] = False
    plans.save_plan(a, "thumb_ring", path)
    plans.save_plan(plans.preset(), "none", path)
    assert len(plans.load_plan("thumb_ring", path)["conditions"]) == 9
    assert len(plans.load_plan("none", path)["conditions"]) == 3
    session = GuidedSession(tasks=tasks_for("thumb_ring"), plan=a)
    assert len(session.sets) == 8
    assert plans.duration(plan(plans.condition(reps=2, prep=1, hold=2, rest=1)), "thumb_ring") == 8


def test_empty_selection_cannot_start():
    with pytest.raises(ValueError, match="하나 이상"):
        GuidedSession(plan=plan(plans.condition(enabled=False)))


def test_per_pose_timings_and_metadata_reach_the_session():
    rows = [plans.condition(name="가까이", reps=1, prep=.5, hold=.5, rest=.5),
            plans.condition(name="멀리", reps=2, prep=1, hold=2, rest=1, distance=plans.OPTIONS["distance"][2])]
    g = GuidedSession(tasks=tasks_for("thumb_ring"), plan=plan(*rows))
    g.advance(0)
    assert g.label(.75)["target"] == "thumb_ring"
    assert g.label(.75)["condition_key"] == plans.condition_key(rows[0])
    assert g.update(1.51) and g.view(1.51)["waiting"]
    g.advance(2)
    assert not g.update(4)
    assert g.cfg("rec_reps") == 2 and g.label(4)["condition"] == "멀리"
    assert "100cm" in g.view(4)["condition_detail"]
    assert g.update(10.01) and g.done


def test_missing_hand_and_manual_pause_freeze_time_and_exclude_labels():
    g = GuidedSession(tasks=tasks_for("thumb_ring"), plan=plan(plans.condition(reps=1)))
    g.advance(0)
    g.update(.5)
    progress = g.view(.5)["progress"]
    assert not g.update(20, available=False)
    assert g.label(20)["target"] == "idle"
    assert g.view(20)["progress"] == progress
    g.update(30, available=True)
    assert g.view(30)["progress"] == progress
    g.paused = True
    g.update(40)
    assert g.view(40)["paused"] and g.label(40)["phase"] == "paused"
    g.paused = False
    g.update(41)
    assert not g.done and g.view(41)["progress"] == progress


def test_condition_identity_does_not_depend_on_order_title_or_reps():
    row = plans.condition()
    same = {**row, "name": "다른 이름", "reps": 50, "hold": 4}
    assert plans.condition_key(row) == plans.condition_key(same)
    assert plans.condition_key(row) != plans.condition_key({**row, "position": "왼쪽"})


def test_condition_groups_and_windows_do_not_mix_between_recordings_or_poses(tmp_path):
    a, b = plans.condition_key(plans.condition()), plans.condition_key(plans.condition(position="왼쪽"))
    p1 = write_session(tmp_path / "a", labels(a) + labels(b, offset=1))
    p2 = write_session(tmp_path / "b", labels(b, index=0) + labels(a, index=1, offset=1))
    sessions = load_sessions([p1, p2])
    s, t = sessions
    assert s["cond"][0] == t["cond"][12] and s["cond"][12] == t["cond"][0]
    assert s["cond"][0] != s["cond"][12]
    assert np.count_nonzero(s["x"][12, :-1]) == 0
    assert np.count_nonzero(s["features"][12, 63:126]) == 0


def test_pauses_and_missing_3d_reset_temporal_context(tmp_path):
    rows = labels("pose-a") + [{"target": "idle", "phase": "paused", "condition_key": "pose-a"}] + labels("pose-a")
    rows[14]["hand"] = False
    s = load_sessions([write_session(tmp_path, rows)])[0]
    assert s["y"][12] == s["y"][14] == -1
    assert not s["x"][13, :-1].any() and not s["x"][15, :-1].any()


def test_coverage_uses_actual_time_and_requires_a_complete_repetition(tmp_path):
    key = plans.condition_key(plans.condition())
    rows = labels(key) + [{"target": "none", "task": "none", "phase": "free", "condition_key": key} for _ in range(21)]
    write_session(tmp_path, rows)
    summary = dataset_summary(tmp_path)
    assert summary["coverage"]["thumb_ring"][key]["reps"] == 1
    assert summary["coverage"]["none"][key]["seconds"] == pytest.approx(1.0)
    incomplete = write_session(tmp_path / "short", labels(key, n=3)[:1])
    assert dataset_summary(incomplete.parent)["coverage"]["thumb_ring"] == {}


def test_same_second_recordings_do_not_overwrite_each_other(tmp_path):
    a = write_session(tmp_path, labels("pose-a"))
    before = a.read_bytes()
    b = write_session(tmp_path, labels("pose-b"))
    assert a != b and a.read_bytes() == before


def test_legacy_recordings_still_load(tmp_path):
    rows = [{k: v for k, v in r.items() if k != "condition_key"} for r in labels("unused")]
    s = load_sessions([write_session(tmp_path, rows)])[0]
    assert s["condition_groups"] == ["legacy-0"]
    assert (s["y"] >= 0).all()


def test_editor_cancel_save_and_start_are_separate(tmp_path):
    pytest.importorskip("PySide6")
    from src.recording_plan_editor import RecordingPlanEditor
    editor = RecordingPlanEditor(tmp_path / "plans.json")
    editor.open("thumb_index")
    editor.setField("name", "취소할 이름")
    editor.open("thumb_index")
    assert editor.selected["name"] != "취소할 이름"
    editor.setField("reps", 20)
    assert editor.save()
    editor.open("thumb_index")
    assert editor.selected["reps"] == 20
    started = []
    editor.startRequested.connect(lambda key, p: started.append((key, p)))
    assert not started and editor.start()
    editor.setField("reps", 30)
    assert started[0][0] == "thumb_index" and started[0][1]["conditions"][0]["reps"] == 20


def test_runtime_persists_plan_and_ignores_enable_during_guided_capture(tmp_path):
    from src.qt_runtime import UiState
    from tests.test_qt_runtime import runtime, steps
    rt = runtime(tmp_path)
    rt.rec_dir = tmp_path / "recordings"
    capture = plan(plans.condition(name="먼 곳", distance=plans.OPTIONS["distance"][2]))
    ui = dict(stage="main", control_hand="Right", use_index_pointer=False, rec_focus="thumb_ring", rec_plan=capture)
    rt.ui = UiState(**ui, rec_mode="guided")
    steps(rt, 1)
    assert rt._snap.rec["waiting"] and "100cm" in rt._snap.rec["condition_detail"]
    rt.post("key", ord(" "))
    rt.ui = UiState(**ui, rec_mode="guided", rec_advance=1)
    steps(rt, 1)
    assert rt.paused and rt._guided.hold_reason  # fake tracker has no 3D world landmarks
    rt.ui = UiState(**ui, rec_mode="off")
    steps(rt, 1)
    (file,) = rt.rec_dir.glob("*.jsonl.gz")
    meta, frames, _ = read_session(file)
    assert meta["capture_plan"]["conditions"][0]["name"] == "먼 곳"
    assert all(f["label"]["target"] == "idle" for f in frames)
