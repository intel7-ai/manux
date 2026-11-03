"""Multi-monitor bounds: the cursor must reach every monitor, never a dead area."""
import pytest

from src.gesture_engine import GestureResult
from src.mouse_controller import MouseController
from src.screens import Monitor, ScreenLayout

PRIMARY = Monitor(0, 0, 1920, 1080, primary=True)
LEFT = Monitor(-1280, 0, 0, 1024)        # smaller, left of the primary: negative x
ABOVE = Monitor(0, -1440, 2560, 0)       # bigger, above the primary: negative y


def layout():
    return ScreenLayout([LEFT, PRIMARY, ABOVE])


def test_layout_orders_primary_first_and_finds_monitors():
    screens = layout()
    assert screens.primary == PRIMARY
    assert screens.monitor_at(-10, 500) == LEFT
    assert screens.monitor_at(100, -10) == ABOVE
    assert screens.monitor_at(-10, 1050) is None  # dead area under the shorter left monitor


@pytest.mark.parametrize(("point", "expected"), [
    ((-640, 500), (-640, 500)),        # inside the left monitor: unchanged
    ((-10, 1060), (0, 1060)),          # dead area -> nearest edge of the primary
    ((-5000, 500), (-1280, 500)),      # far left -> left monitor's edge
    ((5000, 5000), (1919, 1079)),      # far bottom-right -> primary's corner
    ((3000, -100), (2559, -100)),      # right of the upper monitor
])
def test_clamp_keeps_points_on_the_nearest_monitor(point, expected):
    assert layout().clamp(*point) == pytest.approx(expected)


def test_single_layout_matches_the_old_primary_only_bounds():
    one = ScreenLayout.single(1920, 1080)
    assert one.clamp(-50, 2000) == (0, 1079)
    assert len(one) == 1 and one.next_after(10, 10) == one.primary


def test_next_monitor_cycles_through_all_monitors():
    screens = layout()
    seen = [screens.primary]
    for _ in range(3):
        seen.append(screens.next_after(*seen[-1].center))
    assert seen[0] == seen[3] and set(seen[:3]) == {PRIMARY, LEFT, ABOVE}


REL_CFG = {"movement_mode": "relative", "smoothing": 1.0,
           "relative_gain_min": 1.0, "relative_gain_max": 1.0}


def _move(mouse, points, t0=0.0):
    pos = None
    for i, x in enumerate(points):
        pos = mouse.apply(GestureResult(action="move_cursor", is_moving=True, cursor_point=(x, .5)),
                          REL_CFG, now=t0 + i * .05)
    return pos


def test_relative_motion_crosses_onto_the_left_monitor():
    mouse = MouseController(dry_run=True, screens=layout())
    try:
        mouse.warp_to(100, 500)
        x, y = _move(mouse, [.5, .45, .40, .35, .30])
        assert x < 0 and LEFT.contains(x, y)  # the old 0..w clamp stopped at x = 0
    finally:
        mouse.close()


def test_relative_speed_uses_the_primary_width_not_the_whole_desktop():
    one = MouseController(dry_run=True, screen_size=(1920, 1080))
    many = MouseController(dry_run=True, screens=layout())
    try:
        for m in (one, many):
            m.warp_to(960, 540)
        assert _move(one, [.5, .52]) == _move(many, [.5, .52])
    finally:
        one.close()
        many.close()


def test_absolute_mode_maps_onto_one_monitor_and_n_moves_it():
    mouse = MouseController(dry_run=True, screens=layout())
    cfg = {"movement_mode": "absolute", "smoothing": 1.0, "active_margin": 0.0}
    at = lambda x, y, t: mouse.apply(
        GestureResult(action="move_cursor", is_moving=True, cursor_point=(x, y)), cfg, now=t)
    try:
        assert PRIMARY.contains(*at(.5, .5, 0.0))
        assert at(1.0, 1.0, .05) == (1919, 1079)       # camera corner = primary's corner
        target = mouse.cycle_monitor()
        assert target != PRIMARY and mouse.last_screen_pos == target.center
        mouse.release_all()                              # pause/resume keeps the chosen monitor
        assert target.contains(*at(.5, .5, .10))
        assert target.contains(*at(0.0, 0.0, .15))
        assert mouse.event_log.count("next_monitor") == 1
    finally:
        mouse.close()
