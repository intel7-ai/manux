"""Cursor mapping and monitors. Ported from v0.3 tests/test_screens.py and the relative/absolute cases of
tests/test_gesture_mouse.py."""
from __future__ import annotations

import pytest

from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.cursor import CursorMapper
from manux.platform.monitors import Monitor, ScreenLayout

PRIMARY = Monitor(0, 0, 1920, 1080, primary=True)
LEFT = Monitor(-1280, 0, 0, 1024)        # smaller, left of the primary: negative x
ABOVE = Monitor(0, -1440, 2560, 0)       # bigger, above the primary: negative y
FRAME = 1 / 30


def layout():
    return ScreenLayout([LEFT, PRIMARY, ABOVE])


REL = WindowsOutputConfig(movement_mode="relative", smoothing=1.0, relative_gain_min=1.0, relative_gain_max=1.0)


def test_layout_orders_primary_first_and_finds_monitors():
    screens = layout()
    assert screens.primary == PRIMARY
    assert screens.monitor_at(-10, 500) == LEFT
    assert screens.monitor_at(100, -10) == ABOVE
    assert screens.monitor_at(-10, 1050) is None  # dead area under the shorter left monitor


@pytest.mark.parametrize(("point", "expected"), [
    ((-640, 500), (-640, 500)),
    ((-10, 1060), (0, 1060)),
    ((-5000, 500), (-1280, 500)),
    ((5000, 5000), (1919, 1079)),
    ((3000, -100), (2559, -100)),
])
def test_clamp_keeps_points_on_the_nearest_monitor(point, expected):
    assert layout().clamp(*point) == pytest.approx(expected)


def test_single_layout_and_next_monitor_cycle():
    one = ScreenLayout.single(1920, 1080)
    assert one.clamp(-50, 2000) == (0, 1079)
    assert len(one) == 1 and one.next_after(10, 10) == one.primary
    screens = layout()
    seen = [screens.primary]
    for _ in range(3):
        seen.append(screens.next_after(*seen[-1].center))
    assert seen[0] == seen[3] and set(seen[:3]) == {PRIMARY, LEFT, ABOVE}


def test_detect_falls_back_to_one_monitor_when_none_are_found(monkeypatch):
    import manux.platform.monitors as mon
    monkeypatch.setattr(mon, "windows_monitors", lambda: [])
    assert ScreenLayout.detect((1280, 720)).primary == Monitor(0, 0, 1280, 720, primary=True)
    assert ScreenLayout([]).primary.width == 1920  # empty input never leaves zero monitors


def _move(mapper, xs, t0=0.0, y=0.5):
    pos = None
    for i, x in enumerate(xs):
        pos = mapper.step((x, y), t0 + i * .05)
    return pos


def test_first_point_anchors_without_moving():
    m = CursorMapper(ScreenLayout.single(1920, 1080), REL, start=(100, 200))
    assert m.step((0.9, 0.9), 0.0) == (100, 200)


def test_relative_motion_crosses_onto_the_left_monitor():
    m = CursorMapper(layout(), REL, start=(100, 500))
    x, y = _move(m, [.5, .45, .40, .35, .30])
    assert x < 0 and LEFT.contains(x, y)


def test_relative_speed_uses_the_primary_width_not_the_whole_desktop():
    one = CursorMapper(ScreenLayout.single(1920, 1080), REL, start=(960, 540))
    many = CursorMapper(layout(), REL, start=(960, 540))
    assert _move(one, [.5, .52]) == _move(many, [.5, .52])


def _rel_move(speed, frames=10):
    cfg = WindowsOutputConfig(movement_mode="relative", smoothing=1.0, relative_gain_min=0.6,
                              relative_gain_max=2.0, relative_accel_speed_low=0.1, relative_accel_speed_high=0.8)
    m = CursorMapper(ScreenLayout.single(1920, 1080), cfg)
    t, x = 0.0, 0.5
    start = m.step((x, 0.5), t)
    for _ in range(frames):
        t += FRAME
        x += speed * FRAME
        m.step((x, 0.5), t)
    return m.pos[0] - start[0], x - 0.5


def test_relative_fine_control_has_pointer_acceleration():
    slow_px, slow_hand = _rel_move(0.05)
    fast_px, fast_hand = _rel_move(1.5, frames=4)
    assert slow_px / (slow_hand * 1920) == pytest.approx(0.6, abs=0.05)
    assert fast_px / (fast_hand * 1920) == pytest.approx(2.0, abs=0.1)


def test_gain_multiplies_relative_movement():
    a = CursorMapper(ScreenLayout.single(1920, 1080), REL, start=(500, 500))
    b = CursorMapper(ScreenLayout.single(1920, 1080), REL, start=(500, 500))
    for i, x in enumerate([.5, .51, .52]):
        a.step((x, .5), i * FRAME)
        b.step((x, .5), i * FRAME, gain=3.0)
    assert b.pos[0] - 500 == pytest.approx(3 * (a.pos[0] - 500), abs=2)


def test_pause_restarts_from_the_current_cursor_without_a_jump():
    m = CursorMapper(ScreenLayout.single(1920, 1080), REL, start=(500, 500))
    _move(m, [.5, .55])
    before = m.pos
    m.pause()
    assert m.step((.9, .9), 1.0) == before      # far away hand point: no jump, re-anchored
    assert m.step((.91, .9), 1.05)[0] > before[0]


ABS = WindowsOutputConfig(movement_mode="absolute", smoothing=1.0, active_margin=0.0)


def test_absolute_maps_onto_one_monitor_and_next_monitor_moves_it():
    m = CursorMapper(layout(), ABS)
    assert PRIMARY.contains(*m.step((.5, .5), 0.0))
    assert m.step((1.0, 1.0), .05) == (1919, 1079)       # camera corner = primary's corner
    target = m.next_monitor()
    assert target != PRIMARY and m.pos == target.center
    m.pause()                                            # pause keeps the chosen monitor
    assert target.contains(*m.step((.5, .5), .10))
    assert target.contains(*m.step((0.0, 0.0), .15))
    m.reset()                                            # release_all re-picks the monitor under the cursor
    assert m.absolute_monitor == layout().nearest(*m.pos)


def test_absolute_active_margin_maps_the_box_to_the_full_screen():
    m = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(movement_mode="absolute",
                                                                         active_margin=0.2))
    assert m.map_absolute(0.2, 0.2) == (0.0, 0.0)
    assert m.map_absolute(0.8, 0.8) == pytest.approx((1919.0, 1079.0))
    assert m.map_absolute(0.1, 0.5, clamp=False)[0] < 0


def test_absolute_gain_moves_faster_without_a_jump():
    m = CursorMapper(ScreenLayout.single(1920, 1080), ABS)
    p0 = m.step((.5, .5), 0.0)
    p1 = m.step((.5, .5), .05, gain=3.0)                 # gain switches on: no jump
    assert p1 == p0
    p2 = m.step((.52, .5), .10, gain=3.0)
    assert p2[0] - p1[0] == pytest.approx(3 * 0.02 * 1919, abs=2)


def test_absolute_warp_with_rebase_keeps_the_cursor_put():
    m = CursorMapper(ScreenLayout.single(1920, 1080), ABS)
    m.step((.5, .5), 0.0)
    m.warp(100, 100, rebase=True)
    assert m.step((.5, .5), .05) == (100, 100)
    assert m.step((.6, .5), .10)[0] == pytest.approx(100 + 0.1 * 1919, abs=1)
