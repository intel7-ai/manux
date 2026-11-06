from types import SimpleNamespace

import numpy as np
import pytest

from src.gesture_catalog import TASKS, BY_KEY, CLASSES, NAMES
from src.guided_recording import GuidedSession, tasks_for
from src.gesture_gate import GestureGate
from src.capture_dataset import load_sessions
from src import recording_plan as plans, training_center as tc
from src.recorder import SessionRecorder
from tests.test_recording_plan import hand, plan


def record_tasks(folder, keys, condition="pose-a"):
    rec = SessionRecorder(folder, "guided", {}, now=0)
    now = 0.
    for s, key in enumerate(keys):
        task = BY_KEY[key]
        for i in range(30):
            now += .05
            h = hand()
            if task.direction:
                sign = -1 if task.direction == "left" else 1
                for p in h.landmarks:
                    if task.direction == "down":
                        p.y += i * .02
                    else:
                        p.x += sign * i * .02
            phase = "free" if task.free else "do" if i < 20 else "rest"
            rec.frame(now, h, label={"target": task.label if phase != "rest" else "none", "task": key,
                                    "phase": phase, "set": s, "rep": 1,
                                    "condition_index": 0, "condition_key": condition},
                      rule={"action": task.action, "evaluated": True, "targets": [key] if phase == "do" else []})
    return rec.close()


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.key)
def test_each_task_has_focus_timing_and_a_valid_model_label(tmp_path, task):
    p = plan(plans.condition(reps=1, prep=.5, hold=2, rest=.5, free_seconds=10))
    plans.save_plan(p, task.key, tmp_path / "plans.json")
    g = GuidedSession(tasks=tasks_for(task.key), plan=plans.load_plan(task.key, tmp_path / "plans.json"))
    g.advance(0)
    assert g.label(1)["task"] == task.key
    assert g.label(1)["target"] == task.label and task.label in CLASSES
    assert task.do in g.view(1)["prompt"]
    assert plans.duration(p, task.key) == (10 if task.free else 3)


def test_every_task_reaches_coverage_and_export_without_becoming_idle(tmp_path, monkeypatch):
    file = record_tasks(tmp_path, list(BY_KEY))
    session = load_sessions([file])[0]
    assert {CLASSES[int(y)] for y in session["y"] if y >= 0} == set(CLASSES)
    summary = tc.dataset_summary(tmp_path)
    for task in TASKS:
        row = summary["per_gesture"][task.key]
        assert row["minutes"] > 0 if task.free else row["reps"] == 1
        assert summary["coverage"][task.key]
    monkeypatch.setattr(tc, "REC_DIR", tmp_path)
    tc._export_dataset(tmp_path / "dataset.npz", {"classes": CLASSES[:3]})
    with np.load(tmp_path / "dataset.npz") as exported:
        assert exported["classes"].tolist() == CLASSES
        assert exported["model_classes"].tolist() == CLASSES[:3]
        assert set(exported["task"]) == set(BY_KEY)
        middle = exported["task"] == "thumb_middle"
        assert "thumb_middle" in set(exported["classes"][exported["y"][middle]])


def test_old_model_shows_new_tasks_untrained_and_includes_left_click_first():
    summary = {"per_gesture": {}, "newest": 0}
    report = {"classes": CLASSES[:3], "per_gesture": {}, "mtime": 1}
    rows = tc.gesture_rows(summary, report)
    assert len(rows) == len(TASKS) == 18 and rows[0]["key"] == "thumb_middle"
    assert "학습 전" in rows[0]["model_text"] and rows[0]["rate"] is None


@pytest.mark.parametrize("action,target,gesture", [
    ("left_click", "thumb_middle", "thumb_middle_pinch"),
    ("task_switcher", "thumb_pinky", "thumb_pinky_pinch"),
    ("show_desktop", "palm_side", "thumb_tucked_open"),
    ("drag_hold", "thumb_middle", "thumb_middle_pinch"),
    ("drag_hold", "thumb_index_middle", "thumb_index_middle_pinch"),
])
def test_new_actions_need_confirmation_only_when_model_learned_them(action, target, gesture):
    info = {"classes": CLASSES, "window": 15, "frames_in_a_row": 1}
    g = GestureGate(lambda x: np.eye(len(CLASSES))[0], info)
    g.step(0, None, .5)
    result = lambda: SimpleNamespace(action=action, active_gesture=gesture, is_moving=True)
    assert g.filter(0, result()).action == (action if action == "left_click" else "none")
    features = np.zeros(133); features[-1] = 1
    g.observe(.1, np.eye(len(CLASSES))[CLASSES.index(target)], features)
    assert g.filter(.1, result()).action == action
    legacy = GestureGate(lambda x: np.array([1, 0, 0]), {**info, "classes": CLASSES[:3]})
    assert legacy.filter(0, result()).action == action
    assert legacy.allows("v_sign", 0) and not g.allows("v_sign", 0)


def test_multiple_classes_evaluate_per_task_and_do_not_count_missing_rule_metrics(tmp_path):
    from scripts.train_tcn import evaluate, per_gesture
    file = record_tasks(tmp_path, ["thumb_middle", "thumb_pinky", "thumb_middle_drag", "none"])
    session = load_sessions([file])[0]
    prob = np.zeros((len(session["y"]), len(CLASSES)))
    for i, y in enumerate(session["y"]):
        if y >= 0:
            prob[i, y] = 1
    tcn = evaluate(session, [0], prob)
    assert tcn["reps"] == {"thumb_middle": (1, 1), "thumb_pinky": (1, 1), "thumb_middle_drag": (1, 1)}
    assert sum(tcn["misfires"].values()) == 0
    rules = evaluate(session, [0])
    assert rules["reps"] == tcn["reps"]
    scores = per_gesture([{"tcn": tcn, "rules": rules, "free_minutes": 1}], {}, {}, by_task=True)
    assert scores["thumb_middle_drag"]["rate"] == 1 and scores["thumb_pinky"]["rate"] == 1


def test_stationary_frames_are_not_labelled_as_a_swipe(tmp_path):
    rec = SessionRecorder(tmp_path, "guided", {}, now=0)
    for i in range(20):
        rec.frame(i / 30, hand(), label={"task": "swipe_left", "target": "swipe_left", "phase": "do", "condition_index": 0})
    session = load_sessions([rec.close()])[0]
    assert (session["y"] == -1).all()
