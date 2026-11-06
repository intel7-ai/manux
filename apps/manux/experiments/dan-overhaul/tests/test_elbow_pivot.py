import math
import random

from src.elbow_pivot import ElbowPivot, fit_circle, polar_step

ELBOW = (0.65, 1.05)        # square frame units, below the picture (hidden by the chair)
FOREARM = 0.45


def hand_at(phi_deg, r=FOREARM, elbow=ELBOW, aspect=1.0):
    """Wrist position (normalised image x, y) with the forearm turned phi degrees from straight up."""
    a = math.radians(phi_deg)
    x, y = elbow[0] + r * math.sin(a), elbow[1] - r * math.cos(a)
    return x, y / aspect


def sweep(pivot, t, aspect=1.0, lo=-35.0, hi=0.0, cycles=2, fps=30, noise=0.0, rng=None, elbow=ELBOW):
    """Left / right sweeps of the forearm about the elbow."""
    frames = int(cycles * 1.2 * fps)
    for k in range(frames):
        phi = lo + (hi - lo) * 0.5 * (1 - math.cos(2 * math.pi * k / (1.2 * fps)))
        x, y = hand_at(phi, elbow=elbow, aspect=aspect)
        if noise:
            x, y = x + rng.gauss(0, noise), y + rng.gauss(0, noise)
        pivot.observe(t + k / fps, (x, y), aspect)
    return t + frames / fps


def test_fit_circle_recovers_centre_and_radius():
    pts = [(1.0 + 2.0 * math.cos(a), -0.5 + 2.0 * math.sin(a)) for a in (0.1, 0.4, 0.7, 1.0, 1.3)]
    cx, cy, r, rms = fit_circle(pts)
    assert abs(cx - 1.0) < 1e-6 and abs(cy + 0.5) < 1e-6 and abs(r - 2.0) < 1e-6 and rms < 1e-9


def test_polar_step_splits_arc_and_bend():
    above = (ELBOW[0], ELBOW[1] - FOREARM)
    arc, away = polar_step(ELBOW, above, (above[0] + 0.01, above[1]))
    assert arc > 0.0099 and abs(away) < 1e-3                     # swing right above the elbow: arc +
    arc, away = polar_step(ELBOW, above, (above[0], above[1] - 0.01))
    assert abs(arc) < 1e-9 and abs(away - 0.01) < 1e-9          # straight away from the elbow


def test_left_right_sweeps_find_the_hidden_elbow():
    pivot = ElbowPivot()
    sweep(pivot, 0.0)
    assert pivot.source == "arcs"
    assert math.hypot(pivot.elbow[0] - ELBOW[0], pivot.elbow[1] - ELBOW[1]) < 0.01
    assert abs(pivot.radius - FOREARM) < 0.01


def test_found_with_landmark_noise_and_a_16_9_frame():
    rng = random.Random(3)
    aspect = 9 / 16
    pivot = ElbowPivot()
    sweep(pivot, 0.0, aspect=aspect, cycles=4, noise=0.002, rng=rng)
    assert pivot.elbow is not None
    assert math.hypot(pivot.elbow[0] - ELBOW[0], pivot.elbow[1] - ELBOW[1]) < 0.05


def test_bending_the_forearm_is_straight_up_down_and_swinging_is_left_right():
    """The diagonal problem: bending the forearm moves the hand along the line to the elbow (tilted in the image).
    Converted, it is purely vertical; a swing about the elbow is purely horizontal, even along the curve."""
    pivot = ElbowPivot()
    sweep(pivot, 0.0)
    aspect = 1.0
    # bend: the hand at phi = -30 deg moves 0.05 toward the elbow (tilted 30 deg in the image)
    a = hand_at(-30)
    b = hand_at(-30, r=FOREARM - 0.05)
    dx, dy = pivot.convert(a, b, aspect)
    assert abs(dx) < 0.003 and abs(dy - 0.05) < 0.003          # straight down on the screen
    # swing along the arc from -30 to -10 deg: a curve in the image, a straight line on the screen
    total_x = total_y = 0.0
    for k in range(20):
        dx, dy = pivot.convert(hand_at(-30 + k), hand_at(-29 + k), aspect)
        total_x += dx
        total_y += dy
    assert total_x > 0.15 and abs(total_y) < 0.005


def test_straight_line_movement_or_a_pivot_above_the_hand_is_not_an_elbow():
    pivot = ElbowPivot()
    for k in range(80):                                        # level straight strokes: no curve, no centre
        x = 0.4 + 0.2 * (0.5 - 0.5 * math.cos(2 * math.pi * k / 36))
        pivot.observe(k / 30, (x, 0.5), 1.0)
    assert pivot.elbow is None
    pivot = ElbowPivot()
    for k in range(80):                                        # an arc bending the other way: centre above
        a = math.radians(-20 + 40 * (0.5 - 0.5 * math.cos(2 * math.pi * k / 36)))
        pivot.observe(k / 30, (0.5 + 0.4 * math.sin(a), 0.1 + 0.4 * math.cos(a)), 1.0)
    assert pivot.elbow is None


def test_up_down_steps_do_not_disturb_the_fit_and_a_prior_is_replaced_by_the_arcs():
    pivot = ElbowPivot()
    pivot.set_prior((0.3, 0.9), aspect=1.0)
    assert pivot.source == "prior" and pivot.convert(hand_at(-10), hand_at(-5), 1.0) is not None
    t = 0.0
    for k in range(30):                                        # bending up and down only: radial steps are ignored
        r = FOREARM - 0.04 * (0.5 - 0.5 * math.cos(2 * math.pi * k / 15))
        pivot.observe(t + k / 30, hand_at(-20, r=r), 1.0)
    assert pivot.source == "prior"
    sweep(pivot, 2.0)
    assert pivot.source == "arcs"
    assert math.hypot(pivot.elbow[0] - ELBOW[0], pivot.elbow[1] - ELBOW[1]) < 0.01


def test_the_estimate_follows_a_moved_elbow():
    pivot = ElbowPivot()
    t = sweep(pivot, 0.0)
    moved = (0.45, 1.0)
    sweep(pivot, t + 7.0, cycles=4, elbow=moved)               # the window (6 s) has dropped the old sweeps
    assert math.hypot(pivot.elbow[0] - moved[0], pivot.elbow[1] - moved[1]) < 0.02


def test_an_elbow_beside_the_hand_never_swaps_left_right_and_up_down():
    """Pose elbow to the side of the hand (forearm lying sideways in the image): a left/right step stays left/right
    and an up/down step stays up/down (the old polar mapping swapped them)."""
    from src.elbow_pivot import ElbowPivot
    ep = ElbowPivot()
    ep.set_pose((0.2, 0.5), aspect=1.0, now=0.0)          # elbow far left of the hand at (0.6, 0.5)
    sx, sy = ep.convert((0.6, 0.5), (0.61, 0.5), 1.0)
    assert sx > 0.009 and abs(sy) < 1e-6
    sx, sy = ep.convert((0.6, 0.5), (0.6, 0.51), 1.0)
    assert sy > 0.009 and abs(sx) < 1e-3
