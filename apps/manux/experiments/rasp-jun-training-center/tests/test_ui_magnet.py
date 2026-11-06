"""Tests for the UI target slowdown (aim assist)."""

import time

from src.gesture_engine import GestureResult
from src.mouse_controller import MouseController
from src.ui_magnet import UITargetMagnet, rect_distance, slowdown_factor

BUTTON = (100, 100, 120, 120)  # a 20x20 close button


def test_rect_distance():
    assert rect_distance(BUTTON, 110, 110) == 0
    assert rect_distance(BUTTON, 90, 110) == 10
    assert abs(rect_distance(BUTTON, 90, 90) - 14.142) < 0.01


def test_factor_ramps_from_one_to_min():
    f = lambda x, y: slowdown_factor([BUTTON], x, y, radius=28, min_factor=0.4)
    assert f(110, 110) == 0.4                    # on the button
    assert f(300, 300) == 1.0                    # far away
    assert f(72, 110) == 1.0                     # exactly at the radius
    assert f(90, 110) < f(80, 110) < f(75, 110)  # closer = slower
    assert slowdown_factor([], 5, 5, 28, 0.4) == 1.0
    assert slowdown_factor([BUTTON], 110, 110, 0, 0.4) == 1.0  # radius 0 disables it


def test_magnet_thread_caches_rects_from_probe():
    cfg = {"ui_magnet_enabled": True, "ui_magnet_radius": 28.0, "ui_magnet_min_factor": 0.4}
    probe = lambda x, y: BUTTON if (95 <= x <= 125 and 95 <= y <= 125) else None
    m = UITargetMagnet(lambda: cfg, probe_factory=lambda size: probe)
    m.available = True
    m.factor(110, 110)            # marks the magnet as "in use"
    m.start()
    deadline = time.time() + 3
    while time.time() < deadline and m.factor(110, 110) >= 0.99:
        time.sleep(0.02)
    assert m.factor(110, 110) <= 0.41
    assert m.factor(400, 400) == 1.0
    cfg["ui_magnet_enabled"] = False
    assert m.factor(110, 110) == 1.0
    m.shutdown()


def test_disabled_or_unavailable_never_slows():
    m = UITargetMagnet(lambda: {"ui_magnet_enabled": True})
    m.available = False
    assert m.factor(1, 1) == 1.0


def _move_result():
    return GestureResult(action="move_cursor", active_gesture="open", is_moving=True,
                         pinch_distance=1.0, distances={}, dpi_multiplier=1.0)


def test_mouse_applies_slowdown_only_to_fine_moves():
    mouse = MouseController(dry_run=True)
    seen = []
    mouse.slowdown_provider = lambda x, y: (seen.append((x, y)), 0.5)[1]
    mouse.apply(_move_result(), {"ui_magnet_enabled": True})
    assert mouse.dpi_multiplier == 0.5 and seen

    fist = _move_result()
    fist.dpi_multiplier = 20.0
    mouse.apply(fist, {"ui_magnet_enabled": True})
    assert mouse.dpi_multiplier == 20.0          # fast travel is not slowed

    mouse.apply(_move_result(), {"ui_magnet_enabled": False})
    assert mouse.dpi_multiplier == 1.0           # switched off in settings
    mouse.close()
