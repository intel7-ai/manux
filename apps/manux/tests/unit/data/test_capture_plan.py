"""Capture plans (`manux-capture-plan/1`) and guided sessions over them.

Ported from rasp-jun `origin/codex/rasp-jun-training-center` `tests/test_recording_plan.py`; the Qt
editor and runtime tests stay with the UI port. Synthetic data only (D-006)."""
import json
from pathlib import Path

import pytest

from manux.data import capture_plan as plans
from manux.data.catalog import tasks_for
from manux.data.guided import GuidedSession


def plan(*rows):
    return {"schema": plans.SCHEMA, "conditions": list(rows)}


def test_presets_cover_diverse_factors_and_positions():
    diverse = plans.normalise(plans.preset("diverse"))
    assert len(diverse["conditions"]) == 9
    for field in plans.OPTIONS:
        assert len({r[field] for r in diverse["conditions"]}) >= 2
    assert len({r["position"] for r in plans.preset("positions")["conditions"]}) == 9
    assert [r["position"] for r in plans.preset("basic")["conditions"]] == ["중앙", "왼쪽", "오른쪽"]
    with pytest.raises(ValueError, match="basic"):
        plans.preset("nope")


def test_condition_keys_match_rasp_jun():
    """Same hash as rasp-jun's recording_plan.condition_key, so v0.3 and v0.4 recordings share keys."""
    keys = [plans.condition_key(r) for r in plans.preset("diverse")["conditions"][:3]]
    assert keys == ["pose-481ac8122c20d57b", "pose-92e7af1903dfa03b", "pose-997c280c1cf7279d"]


@pytest.mark.parametrize("key,value", [("reps", 0), ("reps", 2.5), ("hold", 0), ("prep", float("nan")),
                                       ("free_seconds", 901), ("orientation", "unknown"), ("name", ""),
                                       ("rest", "slow"), ("reps", True)])
def test_bad_settings_do_not_replace_saved_plan(tmp_path, key, value):
    path = tmp_path / "plans.json"
    plans.save_plan(plans.preset(), "thumb_ring", path)
    before = path.read_bytes()
    bad = plans.preset()
    bad["conditions"][0][key] = value
    with pytest.raises(ValueError):
        plans.save_plan(bad, "thumb_ring", path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_korean_messages():
    with pytest.raises(ValueError, match="형식"):
        plans.normalise({"schema": "other"})
    with pytest.raises(ValueError, match="1~30"):
        plans.normalise(plan())
    bad = plans.preset()
    bad["conditions"][0]["hold"] = 11
    with pytest.raises(ValueError, match="동작 시간: 0.5~10"):
        plans.normalise(bad)


def test_each_gesture_keeps_its_own_plan_and_disabled_rows_are_skipped(tmp_path):
    path = plans.plans_file(tmp_path)
    a = plans.preset("diverse")
    a["conditions"][1]["enabled"] = False
    plans.save_plan(a, "thumb_ring", path)
    plans.save_plan(plans.preset(), "none", path)
    assert len(plans.load_plan("thumb_ring", path)["conditions"]) == 9
    assert len(plans.load_plan("none", path)["conditions"]) == 3
    assert plans.load_plan("v_sign", path) == plans.preset()          # nothing saved -> basic
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == plans.SCHEMA
    session = GuidedSession(tasks_for("thumb_ring"), plan=a)
    assert len(session.sets) == 8
    assert plans.duration(plan(plans.condition(reps=2, prep=1, hold=2, rest=1)), "thumb_ring") == 8
    assert plans.duration(plan(plans.condition(free_seconds=10)), "open_move") == 10
    # min_hold: a drag's "do" lasts at least 2.6 s even with a 2 s plan
    assert plans.duration(plan(plans.condition(reps=1, prep=1, hold=2, rest=1)), "thumb_middle_drag") == 4.6


def test_plans_are_never_saved_inside_the_repository():
    repo = Path(__file__).resolve().parents[5]
    with pytest.raises(ValueError, match="저장소 밖"):
        plans.save_plan(plans.preset(), "", repo / "apps" / "manux" / "capture-plans.json")
    assert not (repo / "apps" / "manux" / "capture-plans.json").exists()


def test_plan_from_presets_saved_and_files(tmp_path):
    assert plans.plan_from("positions") == plans.normalise(plans.preset("positions"))
    with pytest.raises(ValueError):
        plans.plan_from("saved")
    saved = plans.preset("diverse")
    plans.save_plan(saved, "swipe_left", plans.plans_file(tmp_path))
    assert len(plans.plan_from("saved", "swipe_left", tmp_path)["conditions"]) == 9
    assert len(plans.plan_from("saved", "", tmp_path)["conditions"]) == 3
    one = tmp_path / "one.json"
    one.write_text(json.dumps(plans.preset("positions")), encoding="utf-8")
    assert len(plans.plan_from(str(one))["conditions"]) == 9
    doc = plans.plans_file(tmp_path)
    assert len(plans.plan_from(str(doc), "swipe_left")["conditions"]) == 9
    with pytest.raises(ValueError, match="'all'"):
        plans.plan_from(str(doc), "")
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        plans.plan_from(str(tmp_path / "missing.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="읽을 수 없습니다"):
        plans.plan_from(str(broken))


def test_empty_selection_cannot_start():
    with pytest.raises(ValueError, match="하나 이상"):
        GuidedSession(plan=plan(plans.condition(enabled=False)))


def test_per_pose_timings_and_metadata_reach_the_session():
    rows = [plans.condition(name="가까이", reps=1, prep=.5, hold=.5, rest=.5),
            plans.condition(name="멀리", reps=2, prep=1, hold=2, rest=1, distance=plans.OPTIONS["distance"][2])]
    g = GuidedSession(tasks_for("thumb_ring"), plan=plan(*rows))
    g.advance(0)
    assert g.label(.75)["target"] == "thumb_ring" and g.label(.75)["gesture"] == "pinch.thumb_ring"
    assert g.label(.75)["condition_key"] == plans.condition_key(rows[0])
    assert g.update(1.51) and g.view(1.51)["waiting"]
    g.advance(2)
    assert not g.update(4)
    assert g.reps == 2 and g.label(4)["condition"] == "멀리" and g.label(4)["condition_index"] == 1
    assert "100cm" in g.view(4)["condition_detail"]
    assert g.update(10.01) and g.done
    meta = g.describe()
    assert meta["capture_plan"]["conditions"][1]["name"] == "멀리"
    assert meta["condition_keys"] == [plans.condition_key(r) for r in rows]


def test_missing_hand_and_manual_pause_freeze_time_and_exclude_labels():
    g = GuidedSession(tasks_for("thumb_ring"), plan=plan(plans.condition(reps=1)))
    g.advance(0)
    g.update(.5)
    progress = g.view(.5)["progress"]
    assert not g.update(20, available=False)
    assert g.label(20)["target"] == "idle" and g.label(20)["phase"] == "paused"
    assert g.view(20)["progress"] == progress and "3D" in g.view(20)["prompt"]
    g.update(30, available=True)
    assert g.view(30)["progress"] == progress
    g.paused = True
    g.update(40)
    assert g.view(40)["paused"] and g.label(40)["phase"] == "paused"
    g.paused = False
    g.update(41)
    assert not g.done and g.view(41)["progress"] == progress


def test_shared_classes_settle_and_label_delay():
    rows = plan(plans.condition(reps=1, prep=1, hold=2, rest=2))
    g = GuidedSession(tasks_for(["double_click", "trigger_right"]), plan=rows, settle=0.35)
    g.advance(0)
    lab = g.label(1.5)
    assert (lab["task"], lab["target"], lab["gesture"]) == ("double_click", "thumb_middle", "pinch.thumb_middle")
    assert g.label(3.2)["target"] == "transition" and g.label(3.5)["target"] == "none"
    g.update(5.0)
    g.advance(10)
    assert g.label(11.3)["target"] == "transition" and "gesture" not in g.label(11.3)   # trigger_right waits 0.55 s
    assert g.label(11.6)["target"] == "trigger_right"


def test_condition_identity_does_not_depend_on_order_title_or_reps():
    row = plans.condition()
    same = {**row, "name": "다른 이름", "reps": 50, "hold": 4, "enabled": False}
    assert plans.condition_key(row) == plans.condition_key(same)
    assert plans.condition_key(row) != plans.condition_key({**row, "position": "왼쪽"})
    assert plans.condition_key(row) != plans.condition_key({**row, "notes": "창가"})


def test_label_condition_key_for_old_recordings():
    assert plans.label_condition_key({"condition_key": "pose-a", "condition_index": 1}) == "pose-a"
    assert plans.label_condition_key({"condition_index": 2}) == "legacy-2"
    assert plans.label_condition_key({"target": "idle"}) == "unassigned"


def test_sessions_without_a_plan_are_unchanged():
    g = GuidedSession(tasks_for("thumb_ring"), conditions=["평소"], config={"reps": 1})
    g.advance(0)
    assert "condition_key" not in g.label(1.5) and "capture_plan" not in g.describe()
