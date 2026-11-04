from src import gesture_guide
from src.gesture_engine import GestureResult
from src.qt_runtime import practice_output
from src.tutorial import ADVANCE_AFTER, MOVE_SECONDS, Tutorial


def _frames(t, action, seconds, fps=30, motion=False):
    hits = []
    for n in range(int(seconds * fps)):
        hits.append(t.observe(action, motion, n / fps + t._last_t_offset))
    t._last_t_offset += seconds
    return any(hits)


def make(steps=None):
    t = Tutorial(steps or gesture_guide.cards(False))
    t._last_t_offset = 0.0
    return t


def test_move_needs_a_moment_of_movement():
    t = make()
    assert t.current["key"] == "move"
    assert not _frames(t, "move_cursor", MOVE_SECONDS / 2)
    assert 0.3 < t.progress < 0.7
    assert _frames(t, "move_cursor", MOVE_SECONDS)
    assert t.done[0]


def test_one_shot_steps_count_the_start_of_the_action():
    t = make()
    t.next()                                    # click step
    assert t.current["key"] == "click"
    assert not t.observe("move_cursor", False, 0.0)
    assert not t.observe("right_click", False, 0.1)   # the wrong gesture does not count
    assert t.observe("left_click", False, 0.2)
    assert not t.tick(0.2 + ADVANCE_AFTER / 2)
    assert t.tick(0.2 + ADVANCE_AFTER)
    assert t.current["key"] == "right"


def test_motion_step_succeeds_when_motion_mode_turns_on():
    t = make()
    while t.current["key"] != "motion":
        t.next()
    assert not t.observe("none", False, 0.0)
    assert t.observe("none", True, 0.1)


def test_skip_back_and_finish():
    t = make()
    t.next()
    t.back()
    assert t.index == 0
    for _ in range(len(t.steps)):
        t.next()
    assert t.finished and t.current is None and t.progress == 1.0
    assert not t.observe("left_click", False, 1.0)


def test_practice_output_moves_but_never_presses():
    move = GestureResult(action="drag_hold", cursor_point=(.5, .5), is_moving=True)
    assert practice_output(move).action == "move_cursor"
    assert practice_output(GestureResult(action="left_click")).action == "none"
    assert practice_output(GestureResult(action="scroll_grab", cursor_point=(.5, .5))).action == "none"
