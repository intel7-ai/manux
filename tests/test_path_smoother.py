import math
import random

from src.path_smoother import PathSmoother


def _run(points, fps=30):
    ps = PathSmoother()
    out = [(0.0, 0.0)]
    for k in range(1, len(points)):
        dx, dy = points[k][0] - points[k - 1][0], points[k][1] - points[k - 1][1]
        sx, sy = ps.step(k / fps, dx, dy)
        out.append((out[-1][0] + sx, out[-1][1] + sy))
    return out


def _zigzag(path):
    """Mean change of direction between successive steps (radians)."""
    turns = []
    for p, q, s in zip(path, path[1:], path[2:]):
        if math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-4 and math.hypot(s[0] - q[0], s[1] - q[1]) > 1e-4:
            a1, a2 = math.atan2(q[1] - p[1], q[0] - p[0]), math.atan2(s[1] - q[1], s[0] - q[0])
            turns.append(abs(math.atan2(math.sin(a2 - a1), math.cos(a2 - a1))))
    return sum(turns) / len(turns)


def test_a_wobbly_straight_stroke_comes_out_straight():
    random.seed(3)
    a = math.radians(-35)
    pts = [(0.01 * k * math.cos(a) + random.gauss(0, 0.004), 0.01 * k * math.sin(a) + random.gauss(0, 0.004)) for k in range(40)]
    out = _run(pts)
    assert _zigzag(pts) > math.radians(30)                    # the hand zigzagged
    assert _zigzag(out) < 0.4 * _zigzag(pts)                  # the cursor much less
    length = math.hypot(out[-1][0] - out[0][0], out[-1][1] - out[0][1])
    assert length > 0.3                                       # and still went most of the way (0.39)


def test_a_curve_stays_a_smooth_curve():
    random.seed(4)
    r = 0.3
    pts = [(r * math.sin(k * 0.03) + random.gauss(0, 0.003), r - r * math.cos(k * 0.03) + random.gauss(0, 0.003)) for k in range(50)]
    out = _run(pts)
    assert _zigzag(out) < 0.5 * _zigzag(pts)                  # no zigzag
    assert out[-1][1] > 0.05                                  # it did bend (the arc goes down 0.06)


def test_turning_back_follows_at_once_and_a_still_hand_is_left_alone():
    pts = [(0.01 * k, 0.0) for k in range(20)] + [(0.19 - 0.01 * k, 0.0) for k in range(1, 20)]
    out = _run(pts)
    assert out[-1][0] < out[19][0] - 0.12                     # came back
    ps = PathSmoother()
    assert ps.step(0.0, 0.001, -0.0005) == (0.001, -0.0005)   # tiny aiming steps pass through
