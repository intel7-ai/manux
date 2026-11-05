"""TCN dataset reader: condition groups from capture plans, legacy recordings, swipe direction filter.

Synthetic hands only (D-006). torch is not needed here; training itself is checked by hand with the
`train` extra (see the PR)."""
import sys
from pathlib import Path

import pytest

from manux.core.contracts import FrameInfo, HandObservation
from manux.data import capture_plan as plans
from manux.data.catalog import tasks_for
from manux.data.guided import GuidedSession
from manux.data.recorder import SessionRecorder

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from tcn_data import AXES, class_mapping, class_names, groups, load, samples  # noqa: E402

FPS = 20.0


def hand(t, shape=0.0, dx=0.0, dy=0.0):
    frame = FrameInfo(0, t, 640, 480, True)
    lm = tuple((0.3 + i * 0.01 + dx, 0.5 + (i % 5) * 0.01 + dy, 0.0) for i in range(21))
    world = tuple((i * 0.001, (i % 4) * 0.001 + shape * (i % 3) * 0.002, (i % 2) * 0.001) for i in range(21))
    return HandObservation(frame, lm, "Right", 0.95, world)


def record(folder, tasks, plan=None, *, person="tester01", conditions=("평소",), still=False):
    """Run a whole guided session at 20 fps (pressing start at once) and save it."""
    session = GuidedSession(tasks, conditions=conditions, plan=plan,
                            config={"reps": 2, "prep": 0.5, "do": 1.0, "rest": 0.5, "free_seconds": 2.0})
    rec = SessionRecorder(folder, "guided", {"person": person, "guided": session.describe()}, now=0.0)
    t, x = 0.0, 0.0
    while not session.done:
        session.advance(t)
        session.update(t)
        label = session.label(t)
        task = session.current[1] if session.current else tasks[0]
        moving = label.get("phase") == "do" and task.direction and not still
        x += {"left": -0.02, "right": 0.02}.get(task.direction, 0.0) if moving else 0.0
        rec.frame(t, hand(t, shape=tasks.index(task), dx=x), label=label)
        t = round(t + 1 / FPS, 4)
    return rec.close()


def test_plan_conditions_become_held_out_groups(tmp_path):
    plan = plans.preset("basic")
    tasks = tasks_for(["thumb_ring", "thumb_index", "none"])
    a = record(tmp_path / "a", tasks, plan, person="tester01")
    b = record(tmp_path / "b", tasks, plan, person="tester02")
    segments = load([a, b])
    by_condition = groups(segments, "condition")
    assert sorted(by_condition) == sorted(plans.condition_key(r) for r in plan["conditions"])
    assert len(groups(segments, "person")) == 2 and len(groups(segments, "posture")) == 3
    for key, rows in by_condition.items():                    # a fold never mixes physical setups
        assert all({(f.get("label") or {}).get("condition_key") for f in s.frames} == {key} for s in rows)
    classes = class_names(segments)
    assert classes == ["none", "thumb_index", "thumb_ring"]
    assert class_mapping(segments, classes) == {"thumb_ring": "pinch.thumb_ring", "thumb_index": "pinch.scroll"}
    held = by_condition[plans.condition_key(plan["conditions"][0])]
    rest = [s for s in segments if s not in held]
    x_held, _ = samples(held, classes, 15)
    x_rest, _ = samples(rest, classes, 15)
    assert len(x_held) and len(x_rest)


def test_same_physical_condition_in_two_plans_is_one_group(tmp_path):
    left = plans.condition(name="왼쪽", position="왼쪽")
    first = {"schema": plans.SCHEMA, "conditions": [plans.condition(name="중앙"), left]}
    second = {"schema": plans.SCHEMA, "conditions": [{**left, "name": "다른 이름", "reps": 3}]}   # index 0 here
    tasks = tasks_for(["thumb_ring", "none"])
    segments = load([record(tmp_path / "a", tasks, first), record(tmp_path / "b", tasks, second)])
    by_condition = groups(segments, "condition")
    assert len(by_condition) == 2
    assert {s.posture for s in by_condition[plans.condition_key(left)]} == {"0", "1"}   # index differs, key not


def test_legacy_recordings_still_load_with_index_groups(tmp_path):
    tasks = tasks_for(["thumb_ring", "none"])
    path = record(tmp_path, tasks, conditions=("평소", "기대기"))
    segments = load([path])
    assert sorted(groups(segments, "condition")) == ["legacy-0", "legacy-1"]
    assert sorted(groups(segments, "posture")) == ["0", "1"]
    assert class_names(segments) == ["none", "thumb_ring"]


def test_swipe_frames_that_did_not_move_the_asked_way_are_not_training_targets(tmp_path):
    tasks = tasks_for(["swipe_left", "swipe_right", "none"])
    moved = load([record(tmp_path / "moved", tasks)])
    still = load([record(tmp_path / "still", tasks, still=True)])
    for segments, expect in ((moved, True), (still, False)):
        for name in ("swipe_left", "swipe_right"):
            rows = [(s, i) for s in segments for i, f in enumerate(s.frames)
                    if (f.get("label") or {}).get("phase") == "do" and s.labels()[i] == name]
            kept = [s.targets()[i] == name for s, i in rows]
            assert rows and (sum(kept) >= len(rows) - 2 if expect else not any(kept)), (name, expect)
    assert any(t == "none" for s in still for t in s.targets())   # other classes are untouched


def test_axes():
    assert AXES == ("person", "posture", "condition")


@pytest.mark.parametrize("gesture,action,hits", [
    ("thumb_ring_pinch", "right_click", 1),
    (None, "right_click", 1),
    ("pinch.thumb_ring", "none", 1),
    ("pinch.scroll", "right_click", 0),  # v0.4 gesture ID is authoritative
])
def test_rule_baseline_counts_v03_recordings(gesture, action, hits):
    """v0.3 recordings keep a v0.3 gesture name in rule.gesture ("thumb_ring_pinch"); the rule baseline must
    fall back to the recorded action instead of scoring 0 (found comparing TCN architectures, 2026-11-05)."""
    import numpy as np
    from tcn_data import Segment, evaluate
    frames, labels = [], ["none"] * 10 + ["thumb_ring"] * 10 + ["none"] * 10
    for i, target in enumerate(labels):
        rule = {"action": action, "gesture": gesture} if i == 12 else {"action": "none", "gesture": "NONE"}
        frames.append({"t": i / 30, "label": {"target": target}, "rule": rule})
    seg = Segment("p", "0", frames, np.zeros((len(frames), 133), dtype=np.float32))
    classes = ["none", "thumb_ring"]
    never = lambda w: np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(w), 1))
    result = evaluate([seg], classes, {"thumb_ring": "pinch.thumb_ring"}, never, 15, 0.7, 3)
    assert result["per_gesture"]["thumb_ring"] == {"attempts": 1, "tcn_hits": 0, "rule_hits": hits}
