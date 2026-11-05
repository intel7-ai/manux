"""The one capture-task catalog (rasp-jun's 18 + the tester collector's extras)."""
import pytest

from manux.actions.catalog import CATALOG
from manux.data import catalog
from manux.data.catalog import ALL_TASKS, BY_KEY, CAPTURE_ONLY, NONE, TASKS, GestureSpec, check_tasks, tasks_for
from manux.data.guided import DEFAULT_SPECS
from manux.features import motion, pinch, pointer

DECLARED = set(pinch.GESTURES) | set(pointer.GESTURES) | set(motion.GESTURES) | {motion.ENTER}

RASP_JUN = {  # key -> training label, from origin/codex/rasp-jun-training-center src/gesture_catalog.py
    "thumb_ring": "thumb_ring", "thumb_index": "thumb_index", "none": "none", "thumb_middle": "thumb_middle",
    "thumb_middle_drag": "thumb_middle", "thumb_index_middle": "thumb_index_middle", "thumb_pinky": "thumb_pinky",
    "palm_side": "palm_side", "v_sign": "v_sign", "rock_sign": "rock_sign", "thumbs_up": "thumbs_up",
    "swipe_left": "swipe_left", "swipe_right": "swipe_right", "swipe_tab_left": "swipe_tab_left",
    "swipe_tab_right": "swipe_tab_right", "swipe_down": "swipe_down", "open_move": "none", "fist_move": "none",
}


def test_rasp_juns_eighteen_keep_keys_order_and_labels():
    assert [(t.key, t.label) for t in TASKS] == list(RASP_JUN.items())
    assert len(TASKS) == 18 and set(catalog.DISPLAY_KEYS) == set(RASP_JUN)
    assert {t.category for t in TASKS} == set(catalog.CATEGORIES[1:])
    assert all(t.name and t.do for t in ALL_TASKS)


def test_gesture_ids_exist_in_v04_features_or_are_capture_only():
    for task in ALL_TASKS:
        for gid in (task.gesture, task.related):
            assert gid is None or gid in DECLARED, (task.key, gid)
    assert set(CAPTURE_ONLY) == {"v_sign", "rock_sign", "swipe_down"}
    assert BY_KEY["thumb_ring"].gesture == pinch.THUMB_RING
    assert BY_KEY["thumb_index"].gesture == pinch.SCROLL
    assert BY_KEY["swipe_left"].gesture == "motion.swipe_left"
    assert BY_KEY["swipe_tab_right"].gesture == "motion.swipe_right_tucked"
    assert BY_KEY["palm_side"].gesture == pinch.THUMB_TUCKED_OPEN
    assert BY_KEY["fist_move"].label == NONE and BY_KEY["fist_move"].related == pinch.MOVE_FIST


def test_no_action_names_in_the_catalog():
    """D-004: a task names a gesture, never what it does."""
    actions = set(CATALOG) | {name for spec in CATALOG.values() for name in spec.v03}
    for task in ALL_TASKS:
        assert not {task.gesture, task.related, task.key, task.label} & actions
        assert not hasattr(task, "action")


def test_each_mode_is_one_consistent_class_set():
    for mode in catalog.MODES:
        tasks = tasks_for(mode=mode)
        check_tasks(tasks)                                  # one class <-> one gesture id within a mode
        assert BY_KEY[NONE] in tasks and all(catalog.in_mode(t, mode) for t in tasks)
    assert {t.label for t in tasks_for(mode="pinch") if t.key in ("thumb_middle", "double_click",
                                                                     "thumb_middle_drag")} == {"thumb_middle"}
    assert catalog.class_gestures()["thumb_index_middle"] == pinch.DRAG


def test_tasks_for_focus_category_and_mode():
    assert tasks_for() == TASKS
    assert tasks_for("thumb_ring") == (BY_KEY["thumb_ring"],)
    assert tasks_for("unknown") == TASKS                  # rasp-jun's fallback
    assert [t.key for t in tasks_for(["swipe_left", "none", "swipe_left"])] == ["swipe_left", "none"]
    assert {t.key for t in tasks_for(category="방향 모션")} == {
        "swipe_left", "swipe_right", "swipe_tab_left", "swipe_tab_right", "swipe_down"}
    assert tasks_for(category="전체") == TASKS
    assert "trigger_drag" in {t.key for t in tasks_for(mode="trigger")}
    assert "thumb_ring" not in {t.key for t in tasks_for(mode="pointer")}
    for bad in (lambda: tasks_for(["nope"]), lambda: tasks_for(category="x"), lambda: tasks_for(mode="x")):
        with pytest.raises(ValueError):
            bad()
    assert catalog.is_free("open_move") and not catalog.is_free("thumb_ring") and not catalog.is_free("x")


def test_classes_and_pilot_specs():
    assert catalog.classes()[:3] == ["none", "thumb_ring", "thumb_index"]
    assert len(catalog.classes()) == len(set(RASP_JUN.values()))
    assert DEFAULT_SPECS == tuple(BY_KEY[k] for k in ("thumb_ring", "thumb_index", "none"))


@pytest.mark.parametrize("bad", [
    lambda: GestureSpec("open_move", "x", "x", target="none"),                    # none must be free
    lambda: GestureSpec("a", "x", "x", target="Bad"),
    lambda: GestureSpec("a", "x", "x", direction="up"),
    lambda: GestureSpec("a", "x", "x", related="move"),
    lambda: GestureSpec("a", "x", "x", modes=("mouse",)),
    lambda: GestureSpec("a", "x", "x", label_delay=-1),
    lambda: check_tasks([GestureSpec("a", "x", "x", gesture="p.a"), GestureSpec("b", "x", "x", target="a")]),
    lambda: check_tasks([GestureSpec("a", "x", "x", gesture="p.a"), GestureSpec("b", "x", "x", gesture="p.a")]),
])
def test_bad_tasks(bad):
    with pytest.raises(ValueError):
        bad()
