"""User-picked skeleton points as the cursor reference (GestureEngine.palm_anchor_point)."""
import time

import pytest

from src.gesture_engine import (
    GestureEngine, Point3D, describe_anchor_joints, normalize_anchor_joints,
)
from src.pointer_engine import PointerEngine, PointerSettings
from tests.test_gesture_mouse import make_real_hand


def distinct_hand():
    """21 joints at different places, so every choice gives a different point."""
    return [Point3D(0.1 + 0.03 * i, 0.9 - 0.02 * i, 0.0) for i in range(21)]


def test_normalize_accepts_lists_and_strings_and_drops_bad_values():
    assert normalize_anchor_joints([9, 0, 9]) == (0, 9)
    assert normalize_anchor_joints("5, 9;13,x,99,-1") == (5, 9, 13)
    assert normalize_anchor_joints("") == ()
    assert normalize_anchor_joints(None) == ()
    assert normalize_anchor_joints(3) == ()


def test_default_is_the_old_wrist_to_middle_base_point():
    eng = GestureEngine()
    hand = distinct_hand()
    ratio = eng.config["cursor_anchor_ratio"]
    expected = (hand[0].x + (hand[9].x - hand[0].x) * ratio, hand[0].y + (hand[9].y - hand[0].y) * ratio)
    assert eng.palm_anchor_point(hand) == pytest.approx(expected)


def test_picked_joints_give_their_centre():
    eng = GestureEngine()
    hand = distinct_hand()
    eng.cursor_anchor_override = (8,)
    assert eng.palm_anchor_point(hand) == pytest.approx((hand[8].x, hand[8].y))
    eng.cursor_anchor_override = (5, 9, 13, 17)
    xs = [hand[j].x for j in (5, 9, 13, 17)]
    ys = [hand[j].y for j in (5, 9, 13, 17)]
    assert eng.palm_anchor_point(hand) == pytest.approx((sum(xs) / 4, sum(ys) / 4))


def test_config_points_are_used_and_the_user_pick_wins():
    eng = GestureEngine()
    hand = distinct_hand()
    eng.config["cursor_anchor_points"] = [0]
    assert eng.palm_anchor_point(hand) == pytest.approx((hand[0].x, hand[0].y))
    eng.cursor_anchor_override = (12,)
    assert eng.palm_anchor_point(hand) == pytest.approx((hand[12].x, hand[12].y))


def test_user_pick_survives_a_config_reload():
    eng = GestureEngine()
    eng.cursor_anchor_override = (5,)
    eng.load_config(eng.config_path)
    assert eng.anchor_joints() == (5,)


def test_index_pointer_custom_anchor_follows_the_picked_point():
    eng = PointerEngine(GestureEngine())
    eng.gestures.cursor_anchor_override = (0,)
    hand = make_real_hand(curled=False)
    f = eng.step(1.0, hand, 9 / 16, PointerSettings(anchor="palm", click_style="off"), active=True)
    assert f.preview_point == pytest.approx((hand[0].x, hand[0].y))


def test_names_for_the_ui():
    assert describe_anchor_joints(()) == "손바닥 중심 (기본)"
    assert describe_anchor_joints((8,)) == "검지 끝"
    assert describe_anchor_joints((5, 9, 13, 17)) == "손가락 뿌리 4곳"
    assert "가운데" in describe_anchor_joints((4, 8))


def test_runtime_applies_the_ui_choice_to_both_engines(tmp_path):
    from src.qt_runtime import UiState
    from tests.test_qt_runtime import runtime
    rt = runtime(tmp_path)
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off", anchor_joints=(5, 9, 13, 17))
    rt._step()
    time.sleep(0.01)
    assert rt.engine.cursor_anchor_override == (5, 9, 13, 17)
    assert rt.pointer.gestures.cursor_anchor_override == (5, 9, 13, 17)
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off")
    rt._step()
    assert rt.engine.cursor_anchor_override == () and rt.pointer.gestures.cursor_anchor_override == ()


class _Prefs(dict):
    def setValue(self, k, v):
        self[k] = v


class _Signal:
    def __init__(self):
        self.count = 0

    def emit(self):
        self.count += 1


class _Dash:
    """Just what QtDashboard.setAnchorJoints touches."""
    def __init__(self, index_pointer):
        self.use_index_pointer = index_pointer
        self.pointer_anchor = "index"
        self.anchor_joints = ()
        self.preferences = _Prefs()
        self.stateChanged = _Signal()
        self.paused = 0

    def _pause_for_change(self):
        self.paused += 1


def test_dashboard_saves_the_pick_and_switches_index_mode_to_custom():
    pytest.importorskip("PySide6")
    from src.qt_dashboard import QtDashboard
    d = _Dash(index_pointer=True)
    QtDashboard.setAnchorJoints(d, [17, 5, 9, 13])
    assert d.anchor_joints == (5, 9, 13, 17)
    assert d.preferences["cursorAnchorJoints"] == "5,9,13,17"
    assert d.pointer_anchor == "palm" and d.preferences["pointerAnchor"] == "palm"
    assert d.paused == 1
    QtDashboard.setAnchorJoints(d, [5, 9, 13, 17])        # same pick again: nothing to do
    assert d.paused == 1
    QtDashboard.setAnchorJoints(d, [0, 9])                # wrist + middle base = the default
    assert d.anchor_joints == () and d.preferences["cursorAnchorJoints"] == ""


def test_dashboard_palm_mode_keeps_the_index_anchor_setting():
    pytest.importorskip("PySide6")
    from src.qt_dashboard import QtDashboard
    d = _Dash(index_pointer=False)
    QtDashboard.setAnchorJoints(d, [0])
    assert d.anchor_joints == (0,) and d.pointer_anchor == "index"
