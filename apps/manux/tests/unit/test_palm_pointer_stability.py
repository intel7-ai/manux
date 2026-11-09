"""Palm cursor: pitch must not fold at upright, calibration must accept an upright palm,
and the calibrated cursor must hold still without lagging into an overshoot."""
from __future__ import annotations

import math
import random

from manux.data.pointer_setup import PHASES, PointerSetup, read_profiles, recommend, write_profiles
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.cursor import CursorMapper
from manux.perception.palm_pitch import palm_pitch
from manux.platform.monitors import ScreenLayout


def world_for(angle_deg, sideways=0.):
    """Wrist→knuckles at `angle_deg` in the camera's vertical plane (0 = at the camera, 90 = up)."""
    a = math.radians(angle_deg)
    knuckle = (sideways, -.08 * math.sin(a), -.08 * math.cos(a))
    return ((0., 0., 0.),) + (knuckle,) * 20


def test_pitch_keeps_increasing_past_upright():
    angles = [palm_pitch(world_for(a)) for a in range(60, 131, 5)]
    assert all(b > a for a, b in zip(angles, angles[1:]))
    assert round(angles[0]) == 60 and round(angles[-1]) == 130


def test_pitch_is_unknown_when_fingers_point_sideways():
    sideways = ((0., 0., 0.),) + ((.08, -.005, -.005),) * 20
    assert palm_pitch(sideways) is None
    assert palm_pitch(world_for(90, sideways=.03)) is not None


def test_upright_palm_tilted_both_ways_calibrates():
    # Arm rested, palm toward the camera with fingers up: neutral ≈ 90°, tilts on both sides of upright.
    samples = {"rest": [(.5, 92.)] * 20, "up": [(.5, 118.)] * 20, "down": [(.5, 70.)] * 20,
               "horizontal": [(.35 + .3 * i / 39, 92.) for i in range(40)]}
    values = recommend(samples)
    assert values["palm_up"] == 118. and values["palm_down"] == 70.
    stored = write_profiles({"a" * 64: values})
    assert read_profiles(stored) == {"a" * 64: values}


def test_v2_profiles_are_not_reused():
    values = dict(palm_left=.3, palm_right=.7, palm_up=20., palm_down=-15., palm_x_gain=1.,
                  palm_y_gain=1., smoothing=.3)
    assert read_profiles('{"version": 2, "profiles": {"%s": %s}}' % ("a" * 64, str(values).replace("'", '"'))) == {}


def test_setup_flow_completes_with_upright_palm():
    monitor = ScreenLayout.single(1920, 1080).primary
    layout = ScreenLayout([monitor])
    setup = PointerSetup(monitor, 1280, lambda v: CursorMapper(layout, WindowsOutputConfig(**v)))
    rng = random.Random(1)
    t = 0.
    for phase, pitch in zip(PHASES, (90., 90., 115., 68.)):
        t += 1 / 30
        setup.step(t, (.5, pitch))
        setup.capture()
        for i in range(200):
            t += 1 / 30
            x = .5 + .15 * math.sin(i / 8) if phase == "horizontal" else .5
            setup.step(t, (x, pitch + rng.uniform(-1.5, 1.5)))
            if setup.phase != phase:
                break
    assert setup.phase == "aim", setup.note


CALIBRATED = dict(palm_left=.35, palm_right=.65, palm_up=115., palm_down=65., smoothing=.3)


def run(mapper, xs, pitches, fps=30):
    out = []
    for x, p in zip(xs, pitches):
        mapper.clock = getattr(mapper, "clock", 10.) + 1 / fps
        out.append(mapper.step_palm(x, p, mapper.clock))
    return out


def test_resting_hand_jitter_is_suppressed():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**CALIBRATED))
    rng = random.Random(2)
    n = 90
    xs = [.5 + rng.gauss(0, .002) for _ in range(n)]
    ps = [90 + rng.gauss(0, 1.) for _ in range(n)]
    out = run(mapper, xs, ps)[30:]
    # Raw noise is ~13 px (x) and ~22 px (y) standard deviation on this mapping.
    sx = max(p[0] for p in out) - min(p[0] for p in out)
    sy = max(p[1] for p in out) - min(p[1] for p in out)
    assert sx < 25 and sy < 40, (sx, sy)


def test_fast_move_settles_without_overshoot_or_reversal():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**CALIBRATED))
    run(mapper, [.5] * 30, [90.] * 30)
    # Quick 0.25 s tilt from neutral to fully down, then hold.
    ps = [90 - 25 * min(1., i / 7.5) for i in range(45)]
    ys = [p[1] for p in run(mapper, [.5] * 45, ps)]
    final = 1079
    assert all(b >= a for a, b in zip(ys, ys[1:])), ys      # never moves back up
    assert max(ys) <= final
    assert ys[15] >= final - 40, ys                         # within ~0.25 s of the hand stopping


def test_tilting_past_upright_moves_cursor_one_way():
    config = WindowsOutputConfig(**{**CALIBRATED, "palm_up": 120., "palm_down": 60.})
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), config)
    angles = [60 + 60 * i / 59 for i in range(60)]
    worlds = [palm_pitch(world_for(a)) for a in angles]
    run(mapper, [.5] * 30, [worlds[0]] * 30)
    ys = [p[1] for p in run(mapper, [.5] * 60, worlds)]
    assert all(b <= a for a, b in zip(ys, ys[1:])), ys


def test_resting_hand_parks_cursor_completely():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**CALIBRATED, palm_hold=.035))
    rng = random.Random(3)  # 1° rms rest noise: calibration recommends ~.035 for it
    out = run(mapper, [.5 + rng.gauss(0, .002) for _ in range(120)], [90 + rng.gauss(0, 1.) for _ in range(120)])
    assert mapper.parked
    assert len(set(out[45:])) == 1, sorted(set(out[45:]))


def test_leaving_neutral_zone_moves_without_jump():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**CALIBRATED, palm_hold=.03))
    run(mapper, [.5] * 30, [90.] * 30)
    assert mapper.parked
    # Slow steady tilt down: once it leaves the zone the cursor follows smoothly and ends on the hand.
    ps = [90 - 10 * i / 44 for i in range(45)] + [80.] * 30
    ys = [p[1] for p in run(mapper, [.5] * 75, ps)]
    steps = [b - a for a, b in zip(ys, ys[1:])]
    assert min(steps) >= 0 and max(steps) <= 15, steps
    assert ys[-1] == round(540 + 10 / 50 * 1079) or mapper.parked


def test_zero_neutral_zone_never_parks():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**CALIBRATED, palm_hold=0.))
    run(mapper, [.5] * 60, [90.] * 60)
    assert not mapper.parked


def test_profiles_saved_before_neutral_zone_get_default():
    values = dict(palm_left=.3, palm_right=.7, palm_up=110., palm_down=70., palm_x_gain=1.,
                  palm_y_gain=1., smoothing=.3)
    profiles = read_profiles(write_profiles({"b" * 64: values}))
    assert profiles["b" * 64]["palm_hold"] == .015
