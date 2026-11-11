"""The hand drawn at the cursor: palm centre at the cursor, fixed size, pinch lines, click point and label (no camera)."""
import math
import os
from types import SimpleNamespace

import pytest

from src.virtual_hand import ACROSS, PALM, SIZE_PX, ViewSmoother, click_offset, palm_centre, view
from tests.test_online_data import hand_world


def landmarks(scale=1.0, shift=0.0):
    w = hand_world()
    return [SimpleNamespace(x=.5 + shift + p[0] * scale, y=.5 + p[1] * scale, z=p[2]) for p in w]


def test_the_palm_centre_is_the_origin_and_the_size_is_fixed_whatever_the_distance():
    near, far = view(landmarks(1.0), 1280, 720), view(landmarks(.3, .2), 1280, 720)
    for v in (near, far):
        assert sum(v["pts"][2 * i] for i in PALM) == pytest.approx(0, abs=.5)
        assert sum(v["pts"][2 * i + 1] for i in PALM) == pytest.approx(0, abs=.5)
        p = v["pts"]
        size = max(math.dist(p[0:2], p[18:20]), math.dist(p[10:12], p[34:36]) / ACROSS)
        assert size == pytest.approx(SIZE_PX, abs=.5)
    assert near["pts"] == pytest.approx(far["pts"], abs=1.0)


def test_a_hand_turned_toward_the_camera_is_not_drawn_huge():
    """2026-11-10 report: turning the hand made the drawn hand huge and broken (the 2D palm length collapsed)."""
    flat = landmarks()
    turned = [SimpleNamespace(x=p.x, y=.5 + (p.y - .5) * .1, z=p.z) for p in flat]   # fingers toward the camera
    span = lambda v: max(v["pts"][0::2]) - min(v["pts"][0::2])
    assert span(view(turned, 1280, 720)) <= span(view(flat, 1280, 720)) * 1.5


def test_pinch_lines_and_the_click_name_come_along():
    v = view(landmarks(), 1280, 720, guides=[("middle", "action")], action="left_click")
    assert v["guides"] == [["middle", "action"]] and v["action"] == "좌클릭"
    pts = v["pts"]
    assert v["click"] == pytest.approx([(pts[8] + pts[24]) / 2, (pts[9] + pts[25]) / 2], abs=.1)   # thumb tip <-> middle tip
    assert view(landmarks(), 1280, 720)["click"] is None                                          # no pinch: no click point
    assert click_offset(pts, [("ring", "pinched")]) == pytest.approx([(pts[8] + pts[32]) / 2, (pts[9] + pts[33]) / 2], abs=.1)
    assert click_offset(pts, [("index", "pinched")]) is None                                      # scrolling is not a click
    assert view(landmarks(), 1280, 720, action="move_cursor")["action"] == ""
    assert view(landmarks()[:20], 1280, 720) is None
    flat = [SimpleNamespace(x=.5, y=.5, z=0)] * 21
    assert view(flat, 1280, 720) is None


def test_the_runtime_sends_the_hand_only_while_one_is_seen(tmp_path):
    import dataclasses
    from tests.test_qt_runtime import runtime
    rt = runtime(tmp_path)
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=False)
    rt._step()
    assert rt._snap.virtual_hand and len(rt._snap.virtual_hand["pts"]) == 42
    rt.tracker.present = False
    rt._step()
    import time
    from src.qt_runtime import VH_LOST_KEEP
    time.sleep(VH_LOST_KEEP + .05)       # kept faded a moment, then gone
    rt._step()
    assert rt._snap.virtual_hand is None


def test_the_window_follows_the_cursor_and_hides_without_a_hand():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication
    from src.qt_dashboard import QtDashboard
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    board = QtDashboard(dry_run=True)
    win = board.engine.rootObjects()[0].findChild(QObject, "virtualHandWindow")
    board.set_virtual_hand(view(landmarks(), 1280, 720))
    import time
    end = time.time() + .3                     # the window glides over 33 ms
    while time.time() < end:
        app.processEvents()
    hand = board.virtualHand
    assert win.property("visible") and win.property("x") == hand["x"] - 320 and win.property("y") == hand["y"] - 320
    board.setVirtualHand(False)
    app.processEvents()
    assert not win.property("visible")
    board.setVirtualHand(True)
    board.set_virtual_hand(None)
    app.processEvents()
    assert not win.property("visible") and board.virtualHand == {}


# ---------------------------------------------------------------- the box on the camera image = the whole monitor
from src.virtual_hand import DEFAULT_BOX, BoxMapper, holds_still, valid_box  # noqa: E402


def at(x, y):
    pts = [SimpleNamespace(x=.5, y=.5, z=0) for _ in range(21)]
    for i in PALM:
        pts[i] = SimpleNamespace(x=x, y=y, z=0)
    return pts


def test_the_drawn_joints_stop_shaking_but_follow_a_real_move():
    import random
    random.seed(1)
    smooth = ViewSmoother()
    base = view(landmarks(), 1280, 720)["pts"]
    raw_moves, drawn_moves, prev = [], [], None
    for i in range(60):
        noisy = [v + random.uniform(-3, 3) for v in base]
        out = smooth(noisy, i / 30)
        if prev is not None:
            raw_moves.append(abs(noisy[24] - base[24]))
            drawn_moves.append(abs(out[24] - prev[24]))
        prev = out
    assert sum(drawn_moves) < .3 * sum(raw_moves)             # jitter of a few px is mostly gone
    moved = [v + 60 for v in base]
    for i in range(60, 66):
        out = smooth(moved, i / 30)
    assert out[24] == pytest.approx(moved[24], abs=6)         # a real 60 px move arrives within 0.2 s


def test_the_box_corners_are_the_monitor_corners_and_outside_is_clamped():
    box = (.2, .1, .6, .5)
    for tip, want in (((.2, .1), (0, 0)), ((.6, .5), (1, 1)), ((.4, .3), (.5, .5)), ((.9, .0), (1, 0))):
        assert BoxMapper().target(at(*tip), box) == pytest.approx(want)       # the box edge is the monitor edge


def test_slow_moves_are_smoothed_and_fast_ones_followed():
    box = (0, 0, 1, 1)
    slow, fast = BoxMapper(), BoxMapper()
    slow.target(at(.5, .5), box); fast.target(at(.1, .5), box)
    slow_x = slow.target(at(.505, .5), box)[0]
    fast_x = fast.target(at(.6, .5), box)[0]
    assert .5 < slow_x < .503                  # a small move is damped (jitter)
    assert fast_x > .5                         # a big move is followed almost at once


def test_a_click_lands_where_the_thumb_was_just_before_the_fingers_touched():
    m = BoxMapper()
    box = (0, 0, 1, 1)
    t = 0.0
    for i in range(10):                        # resting at .3
        m.target(at(.3, .3), box, now=t); t += 1 / 30
    for i in range(6):                         # the pinch tilts the hand a little (tilt-sized: under AIM_BACK_MAX)
        m.target(at(.3 + .003 * i, .3), box, now=t); t += 1 / 30
    held = m.target(at(.318, .3), box, hold=True, now=t)
    assert held[0] < .305                      # back at the aim, not where the pinch pulled the thumb
    assert m.target(at(.9, .9), box, hold=True, now=t + .05) == held
    t += .05
    for i in range(6):                         # released: follows again
        t += 1 / 30
        last = m.target(at(.9, .9), box, now=t)
    assert last[0] > .5


def test_a_real_move_before_the_pinch_is_not_undone_and_a_found_hand_eases_in():
    """manux BoxMapper: the aim-back only goes back a tilt-sized way; after a gap the hand eases in over EASE s."""
    m = BoxMapper()
    box, t = (0, 0, 1, 1), 0.0
    for i in range(10):
        m.target(at(.3, .3), box, now=t); t += 1 / 30
    for i in range(6):                         # a real move of .05
        m.target(at(.3 + .01 * i, .3), box, now=t); t += 1 / 30
    before = m.pos
    assert m.target(at(.36, .3), box, hold=True, now=t) == before
    m.target(at(.36, .3), box, now=t + .05)
    t += .5
    first = m.target(at(.9, .3), box, now=t)                     # found again far away after a gap
    assert first[0] < .45                                        # no jump across the screen in one frame
    for i in range(12):                                          # 0.4 s of frames: the ease (0.2 s) is over
        t += 1 / 30
        last = m.target(at(.9, .3), box, now=t)
    assert last[0] > .85


def test_only_a_click_finger_touching_or_a_fist_holds_the_point():
    assert holds_still([("middle", "pinched")], "move_cursor") and holds_still([("ring", "action")], "right_click")
    assert not holds_still([], "move_cursor", "fist")                      # a fist grabs: it must move
    assert not holds_still([("middle", "near")], "move_cursor")             # only close: keep following
    assert not holds_still([("index", "pinched")], "scroll_grab")
    assert not holds_still([("middle", "pinched")], "drag_hold", "fist")


def test_boxes_are_checked():
    assert valid_box(DEFAULT_BOX) and valid_box((0, 0, 1, 1))
    for bad in ((.5, .5, .55, .9), (.6, .1, .2, .5), (-.1, 0, .5, .5), (0, 0, 1.2, 1), None, "x"):
        assert not valid_box(bad)


def test_box_mode_puts_the_cursor_at_the_palm_and_only_while_running(tmp_path):
    import dataclasses
    from tests.test_qt_runtime import runtime
    rt = runtime(tmp_path)
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=False, virtual_hand=True, virtual_box=(0.0, 0.0, 1.0, 1.0))
    cx, cy = palm_centre(rt.tracker.hand)
    m = rt.mouse.absolute_monitor
    start = tuple(rt.mouse.last_screen_pos)
    rt.paused = True
    rt._step()
    assert tuple(rt.mouse.last_screen_pos) == start                     # paused: the cursor stays
    assert rt._snap.virtual_hand["screen"] == [round(m.left + cx * (m.width - 1)), round(m.top + cy * (m.height - 1))]
    rt.paused = False
    rt._step()
    assert list(rt.mouse.last_screen_pos) == rt._snap.virtual_hand["screen"]


def test_the_drawn_hand_goes_where_the_box_says_and_a_drawn_box_is_kept(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication
    from src.qt_dashboard import QtDashboard
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    board = QtDashboard(dry_run=True)
    ratio = QGuiApplication.primaryScreen().devicePixelRatio()
    board.set_virtual_hand({**view(landmarks(), 1280, 720), "screen": [400, 300]})
    assert board.virtualHand["x"] == pytest.approx(400 / ratio) and board.virtualHand["y"] == pytest.approx(300 / ratio)
    assert board.setVirtualBox(.7, .8, .1, .2) and board.virtualBox == [.1, .2, .7, .8]       # either drag direction
    assert board.ui_state().virtual_box == (.1, .2, .7, .8)
    assert not board.setVirtualBox(.1, .1, .12, .9) and board.virtualBox == [.1, .2, .7, .8]   # too thin: kept the old one
    board.setVirtualBox(*DEFAULT_BOX)


def box_runtime(tmp_path):
    import dataclasses
    from tests.test_qt_runtime import runtime
    rt = runtime(tmp_path)
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=False, virtual_hand=True, virtual_box=(0.0, 0.0, 1.0, 1.0))
    rt.paused = False
    return rt


def forcing(rt, **fields):
    process = rt.engine.process

    def forced(lm, *a, **k):
        r = process(lm, *a, **k)
        if lm is not None:
            for name, value in fields.items():
                setattr(r, name, value)
        return r
    rt.engine.process = forced
    return process


def test_index_and_middle_pinches_are_off_in_the_virtual_hand(tmp_path):
    from src.virtual_hand import virtual_guides
    for action in ("left_click", "right_click", "scroll_grab", "drag_hold"):
        rt = box_runtime(tmp_path)
        forcing(rt, action=action)
        rt._step(); rt._step()
        assert not {"left_click", "right_click", "mouseDown"} & set(rt.mouse.event_log)
        assert rt.mouse._scroll_prev is None
    assert virtual_guides([("index", "pinched"), ("middle", "action"), ("ring", "near"), ("pinky", "pinched")]) ==         []


def test_a_held_fist_browses_right_down_and_left_up(tmp_path):
    import time
    rt = box_runtime(tmp_path)
    rt._step()
    forcing(rt, active_gesture="fist")
    sent = []
    rt.mouse._send_scroll = lambda v, h: sent.append(v)
    rt._step()
    still = tuple(rt.mouse.last_screen_pos)
    for _ in range(8):
        rt.tracker.dx += .03
        time.sleep(1 / 60)
        rt._step()
    assert not rt.mouse.is_dragging and rt._snap.virtual_hand["action"] == "스크롤"
    assert tuple(rt.mouse.last_screen_pos) == still                              # the cursor stays over the page
    assert sum(sent) < 0                                                          # fist right -> wheel down (browse down)
    down = sum(sent)
    for _ in range(8):                                                            # still holding, back left
        rt.tracker.dx -= .03
        time.sleep(1 / 60)
        rt._step()
    assert sum(sent) > down                                                       # ... browses back up (no one-way lock)


def test_one_fist_scrolls_one_way_only_when_the_lock_is_on(monkeypatch):
    from src.virtual_hand import Ratchet
    monkeypatch.setattr(Ratchet, "ONE_WAY", True)
    r, t, out = Ratchet(), 0.0, []
    path = [.5 + .01 * i for i in range(10)] + [.6 - .01 * i for i in range(10)] + [.5 + .01 * i for i in range(10)]
    for y in path:
        for _ in range(3):                    # a few frames per step so the filter settles
            out.append(r.step(y, t)); t += 1 / 30
    assert all(b >= a - 1e-9 for a, b in zip(out, out[1:]))                     # never goes back
    assert out[-1] - out[0] > .12                                                # both strokes down count
    still = Ratchet()
    vals = [still.step(.5 + (.004 if i % 2 else -.004), i / 30) for i in range(60)]
    assert max(vals) - min(vals) < .002                                           # tremor does not creep the page


def test_a_grip_survives_a_short_loss():
    from src.virtual_hand import Grip
    g = Grip()
    assert g.step("fist", 0.0) == "scroll" and g.started
    assert g.step("fist", .03) == "scroll" and not g.started
    assert g.step(None, .06) == "scroll"
    assert g.step(None, .3) == "scroll"
    assert g.step(None, .5) is None


def test_the_thumb_index_click_comes_from_the_hand_shape_alone():
    from src.virtual_hand import ShapePinch
    sp = ShapePinch()
    open_hand = landmarks()
    closed = list(open_hand)
    closed[4] = SimpleNamespace(x=closed[8].x, y=closed[8].y, z=closed[8].z)       # thumb tip on the index tip
    assert sp.step(open_hand, 1280, 720) is None and sp.ratio > ShapePinch.OPEN
    assert sp.step(closed, 1280, 720) is None                                    # one frame is not enough
    assert sp.step(closed, 1280, 720) == "down" and sp.closed
    assert sp.step(closed, 1280, 720) is None
    assert sp.step(open_hand, 1280, 720) == "up" and not sp.closed
    fist = list(closed)
    fist[8] = SimpleNamespace(x=fist[0].x + (fist[9].x - fist[0].x) * .5, y=fist[0].y + (fist[9].y - fist[0].y) * .5, z=0)
    fist[4] = fist[8]
    for _ in range(3):
        assert sp.step(fist, 1280, 720) is None                                  # index curled in: not a pinch


def pinch_hand(rt):
    from src.virtual_hand import TIPS
    hand = rt.tracker.hand
    opened = list(hand)
    tip = hand[TIPS["index"]]
    hand[4] = type(tip)(tip.x, tip.y, tip.z)
    hand[12] = type(tip)(hand[9].x, hand[9].y, hand[9].z)     # middle folded away from the thumb (only the index pinches)
    return opened


def test_a_short_thumb_index_pinch_clicks_between_the_fingers(tmp_path):
    rt = box_runtime(tmp_path)
    rt._step()
    opened = pinch_hand(rt)
    rt._step(); rt._step()
    assert "left_click" not in rt.mouse.event_log                               # closed: wait, it may become a drag
    rt.tracker.hand[:] = opened
    rt._step()
    assert rt.mouse.event_log.count("left_click") == 1 and "mouseDown" not in rt.mouse.event_log
    # the cursor is the clicking point (no jump when the fingers meet); the drawn hand's anchor sits on it
    assert list(rt.mouse.last_screen_pos) == rt._snap.virtual_hand["screen"]
    assert rt._snap.virtual_hand["click"] == rt._snap.virtual_hand["anchor"]


def test_a_held_thumb_index_pinch_drags_and_opening_drops(tmp_path):
    import time
    from src.virtual_hand import PINCH_HOLD
    rt = box_runtime(tmp_path)
    rt._step()
    opened = pinch_hand(rt)
    rt._step(); rt._step()
    time.sleep(PINCH_HOLD + .05)
    rt._step()
    assert rt.mouse.is_dragging and "left_click" not in rt.mouse.event_log
    before = tuple(rt.mouse.last_screen_pos)
    for _ in range(4):
        rt.tracker.dx += .03
        time.sleep(1 / 60)
        rt._step()
    assert rt.mouse.is_dragging and rt.mouse.last_screen_pos[0] > before[0]     # the held thing follows the hand
    rt.tracker.hand[:] = opened
    rt._step()
    assert not rt.mouse.is_dragging and "mouseUp" in rt.mouse.event_log and "left_click" not in rt.mouse.event_log


def test_a_briefly_lost_hand_stays_faded_where_it_was(tmp_path):
    import time
    from src.qt_runtime import VH_LOST_KEEP
    rt = box_runtime(tmp_path)
    rt._step()
    seen = rt._snap.virtual_hand
    rt.tracker.present = False
    rt._step()
    kept = rt._snap.virtual_hand
    assert kept is not None and kept["lost"] and kept["screen"] == seen["screen"]
    time.sleep(VH_LOST_KEEP + .05)
    rt._step()
    assert rt._snap.virtual_hand is None


def test_a_dragged_thing_is_dropped_between_thumb_and_index(tmp_path):
    import time
    from src.virtual_hand import PINCH_HOLD
    rt = box_runtime(tmp_path)
    rt._step()
    opened = pinch_hand(rt)
    rt._step(); rt._step()
    time.sleep(PINCH_HOLD + .05)
    rt._step()
    ups = []
    up = rt.mouse._mouse_up
    rt.mouse._mouse_up = lambda: (ups.append(tuple(rt.mouse.last_screen_pos)), up())
    rt.tracker.hand[:] = opened
    rt._step()
    hand = rt._snap.virtual_hand
    assert ups and ups[0] == tuple(hand["screen"])          # dropped at the cursor, where the drawn anchor sits
    assert hand["click"] == hand["anchor"]


def test_two_quick_taps_click_twice_on_the_same_pixel(tmp_path):
    clicks = []
    rt = box_runtime(tmp_path)
    rt._step()
    original = rt.mouse.apply

    def apply(result, *a, **k):
        if result.action == "left_click":
            clicks.append(tuple(rt.mouse.last_screen_pos))
        return original(result, *a, **k)
    rt.mouse.apply = apply
    opened = pinch_hand(rt)
    closed = list(rt.tracker.hand)
    rt._step(); rt._step()
    rt.tracker.hand[:] = opened
    rt._step()                                       # first click, on opening
    rt.tracker.dx += .004                            # the slow-following hand drifts a little between the taps
    rt.tracker.hand[:] = closed
    rt._step(); rt._step()                           # second click, at once when the fingers close again
    assert len(clicks) == 2 and clicks[0] == clicks[1]
    rt.tracker.hand[:] = opened
    rt._step()
    assert len(clicks) == 2                          # opening after the second click is not a third


def test_in_the_task_switcher_the_box_moves_the_cursor_and_the_hand_rises(tmp_path):
    rt = box_runtime(tmp_path)
    rt._step()
    import time
    rt.mouse.open_switcher(time.perf_counter())     # (0.0 would time out at once)
    rt.tracker.dx += .05
    rt._step(); rt._step()
    hand = rt._snap.virtual_hand
    assert hand["switcher"] and list(rt.mouse.last_screen_pos) == hand["screen"]


def test_thumb_index_and_middle_together_right_click_and_no_left_click(tmp_path):
    import time
    from src.virtual_hand import TIPS
    rt = box_runtime(tmp_path)
    rt._step()
    hand = rt.tracker.hand
    opened = list(hand)
    tip = hand[TIPS["index"]]
    hand[4] = type(tip)(tip.x, tip.y, tip.z)
    hand[12] = type(tip)(tip.x + .004, tip.y, tip.z)       # middle tip next to the index tip: all three together
    rt._step(); rt._step()
    assert "right_click" in rt.mouse.event_log
    assert list(rt.mouse.last_screen_pos) == rt._snap.virtual_hand["screen"]    # at the cursor (no jump)
    time.sleep(.5)
    rt._step()
    assert not rt.mouse.is_dragging                        # held: still not a drag
    rt.tracker.hand[:] = opened
    for _ in range(3):
        rt._step()
    assert "left_click" not in rt.mouse.event_log and rt.mouse.event_log.count("right_click") == 1


def test_index_first_then_middle_is_a_right_click_not_a_left_click(tmp_path):
    from src.virtual_hand import TIPS
    rt = box_runtime(tmp_path)
    rt._step()
    opened = pinch_hand(rt)                                # thumb + index closed, middle folded away
    rt._step(); rt._step()
    tip = rt.tracker.hand[TIPS["index"]]
    rt.tracker.hand[12] = type(tip)(tip.x + .004, tip.y, tip.z)
    rt._step(); rt._step()
    rt.tracker.hand[:] = opened
    for _ in range(3):
        rt._step()
    assert rt.mouse.event_log.count("right_click") == 1 and "left_click" not in rt.mouse.event_log

def test_a_thumb_index_tap_in_the_task_switcher_clicks_the_thumbnail(tmp_path):
    import time
    rt = box_runtime(tmp_path)
    rt._step()
    rt.mouse.open_switcher(time.perf_counter())
    forcing(rt, active_gesture="thumb_index_pinch")      # what the rule engine calls that shape: it must not eat the click
    opened = pinch_hand(rt)
    rt._step(); rt._step()
    rt.tracker.hand[:] = opened
    rt._step()
    time.sleep(.3)
    rt._step()
    assert "switcher_click" in rt.mouse.event_log and not rt.mouse.switcher_open


def switch_shape(scale=1.0):
    """Thumb folded onto the index knuckle, four straight fingers pointing up (image y grows downward)."""
    pts = [SimpleNamespace(x=.5, y=.7, z=0) for _ in range(21)]
    for k, (mcp, tip, x) in enumerate(((5, 8, .46), (9, 12, .49), (13, 16, .52), (17, 20, .55))):
        pts[mcp] = SimpleNamespace(x=x, y=.5, z=0)
        pts[mcp + 1] = SimpleNamespace(x=x, y=.42, z=0)
        pts[mcp + 2] = SimpleNamespace(x=x, y=.36, z=0)
        pts[tip] = SimpleNamespace(x=x, y=.3, z=0)
    pts[4] = SimpleNamespace(x=.47, y=.52, z=0)
    return pts


def test_thumb_folded_four_fingers_up_held_opens_the_task_switcher_once():
    from src.virtual_hand import ShapeSwitch
    sw = ShapeSwitch()
    hand = switch_shape()
    assert sw.shape(hand, 1280, 720)
    assert not sw.step(hand, 1280, 720, 0.0) and not sw.step(hand, 1280, 720, .3)
    assert sw.step(hand, 1280, 720, .55)                                  # held still for HOLD
    assert not sw.step(hand, 1280, 720, 1.5)                              # once per hold
    thumb_out = list(hand)
    thumb_out[4] = SimpleNamespace(x=.35, y=.5, z=0)
    assert not sw.shape(thumb_out, 1280, 720)                             # the ordinary open hand (thumb out) does not
    sideways = [SimpleNamespace(x=.5 + (p.y - .7) * -1, y=.7 + (p.x - .5), z=0) for p in hand]
    assert not sw.shape(sideways, 1280, 720)                              # pointing sideways does not
    assert not sw.step(thumb_out, 1280, 720, 2.0)
    for t in (2.1, 2.4, 2.7):
        fired = sw.step(hand, 1280, 720, t)
    assert fired                                                          # re-armed after the shape went away


def test_in_box_mode_the_shape_opens_the_switcher_and_pinky_pinch_does_nothing(tmp_path):
    import time
    rt = box_runtime(tmp_path)
    forcing(rt, action="task_switcher")
    rt._step(); rt._step()
    assert not rt.mouse.switcher_open                                     # the rule engine's thumb + pinky is off
    rt = box_runtime(tmp_path)
    rt.tracker.hand[:] = switch_shape()
    for _ in range(4):
        rt._step()
        time.sleep(.2)
    assert rt.mouse.switcher_open
