import math

from manux.perception.palm_axes import PalmAxesCalibration, apply_axes, axes_from_directions, principal_direction


def _strokes(direction_deg, t0, seconds, centre=(0.5, 0.5), amp=0.08, aspect=1.0, fps=30):
    """Back-and-forth palm positions along a direction (0 = straight down in the image, + = toward +x)."""
    a = math.radians(direction_deg)
    ux, uy = math.sin(a), math.cos(a)
    out = []
    for k in range(int(seconds * fps)):
        s = amp * math.sin(2 * math.pi * 0.8 * k / fps)
        out.append((t0 + k / fps, (centre[0] + ux * s, centre[1] + uy * s / aspect)))
    return out


def _run(cal, frames, aspect=1.0):
    for t, p in frames:
        if cal.update(t, p, aspect):
            return True
    return False


def test_principal_direction_of_a_tilted_line():
    pts = [(0.5 + 0.1 * i * math.sin(math.radians(30)), 0.5 + 0.1 * i * math.cos(math.radians(30))) for i in range(-5, 6)]
    ux, uy, major, minor = principal_direction(pts)
    assert abs(abs(ux) - math.sin(math.radians(30))) < 1e-6 and minor < 1e-9


def test_tilted_up_down_stroke_becomes_screen_vertical():
    """The elbow case: 'up and down' is 30 degrees off in the image, 'left and right' is level."""
    axes = axes_from_directions((math.sin(math.radians(30)), math.cos(math.radians(30))), (1.0, 0.0))
    step = (0.01 * math.sin(math.radians(30)), 0.01 * math.cos(math.radians(30)))
    sx, sy = apply_axes(*step, axes)
    assert abs(sx) < 1e-9 and abs(sy - 0.01) < 1e-9                       # straight down, same length
    sx, sy = apply_axes(0.01, 0.0, axes)
    assert abs(sx - 0.01) < 1e-9 and abs(sy) < 1e-9                        # left/right unchanged


def test_wizard_measures_up_down_only_and_keeps_left_right():
    cal = PalmAxesCalibration(start=0.0)
    assert _run(cal, _strokes(25, 0.0, 5.6), aspect=1.0)
    assert cal.axes is not None, cal.error
    a = math.radians(25)
    sx, sy = apply_axes(math.sin(a), math.cos(a), cal.axes)
    assert abs(sx) < 0.05 and sy > 0.95                                     # the tilted up/down stroke -> vertical
    sx, sy = apply_axes(1.0, 0.0, cal.axes)
    assert abs(sx - 1.0) < 1e-9 and abs(sy) < 1e-9                          # left/right untouched


def test_wizard_works_in_a_16_9_frame():
    aspect = 9 / 16
    cal = PalmAxesCalibration(start=0.0)
    frames = _strokes(20, 0.0, 5.6, aspect=aspect)
    assert _run(cal, frames, aspect=aspect) and cal.axes is not None, cal.error


def test_wizard_refuses_tiny_or_round_movement_and_too_similar_directions():
    cal = PalmAxesCalibration(start=0.0)
    assert _run(cal, _strokes(0, 0.0, 5.6, amp=0.01)) and cal.axes is None and "작아요" in cal.error
    cal = PalmAxesCalibration(start=0.0)
    circle = [(k / 30, (0.5 + 0.08 * math.cos(k / 5), 0.5 + 0.08 * math.sin(k / 5))) for k in range(170)]
    assert _run(cal, circle) and cal.axes is None and "한 방향" in cal.error
    cal = PalmAxesCalibration(start=0.0)
    assert _run(cal, _strokes(70, 0.0, 5.6)) and cal.axes is None and "옆으로" in cal.error
