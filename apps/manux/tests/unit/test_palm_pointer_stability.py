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


# palm_precision=1: these check the filter and angle mapping on their own (precision has its own tests)
CALIBRATED = dict(palm_left=.35, palm_right=.65, palm_up=115., palm_down=65., smoothing=.3, palm_precision=1.)


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


def test_profiles_saved_before_precision_get_default_and_lose_the_neutral_zone():
    values = dict(palm_left=.3, palm_right=.7, palm_up=110., palm_down=70., palm_x_gain=1.,
                  palm_y_gain=1., smoothing=.3)
    profiles = read_profiles(write_profiles({"b" * 64: values}))
    assert profiles["b" * 64]["palm_precision"] == .7
    assert "palm_hold" not in read_profiles(write_profiles({"c" * 64: {**values, "palm_hold": .02}}))["c" * 64]


PRECISE = {**CALIBRATED, "palm_precision": .4}


def test_slow_drift_moves_the_cursor_less():
    def wobble(precision):
        mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**{**PRECISE, "palm_precision": precision}))
        run(mapper, [.5] * 30, [90.] * 30)
        # slow hand drift of about +-0.6 deg / +-0.4% frame width at 0.7 Hz (holding still on a menu row)
        n = 60
        xs = [.5 + .004 * math.sin(i / 30 * 2 * math.pi * .7) for i in range(n)]
        ps = [90 + .6 * math.cos(i / 30 * 2 * math.pi * .7) for i in range(n)]
        out = run(mapper, xs, ps)
        return max(abs(a - b) for p in out for q in out for a, b in zip(p, q))
    assert wobble(.4) <= .6 * wobble(1.)


def test_fast_sweep_still_reaches_the_edge_and_resyncs():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**PRECISE))
    run(mapper, [.5] * 30, [90.] * 30)
    xs = [.5 + .15 * min(1., i / 9) for i in range(40)]      # 0.3 s sweep to the right edge, then hold
    out = run(mapper, xs, [90.] * 40)
    assert out[-1][0] >= 1919 - 10


def test_fist_gain_scales_moves_and_keeps_the_cursor_there():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**PRECISE))
    for i in range(30):
        mapper.step_palm(.5, 90., 10 + i / 30, gain=.35, fist=True)
    start = mapper.pos
    for i in range(60):                                      # hand moves right by 100 mapped px, then rests
        x = .5 + 100 / 1919 * .3 * min(1., i / 15)
        mapper.step_palm(x, 90., 11 + i / 30, gain=.35, fist=True)
    moved = mapper.pos[0] - start[0]
    assert 25 <= moved <= 45, moved                           # ~0.35 x 100 px, and no re-sync towards 100


def test_cursor_does_not_slide_on_after_the_hand_stops():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**{**PRECISE, "smoothing": .42}))
    run(mapper, [.5] * 30, [90.] * 30)
    dx = 120 / 1919 * .3
    xs = [.5 + dx * (lambda f: f * f * (3 - 2 * f))(min(1., i / 9)) for i in range(70)]   # 0.3 s move, then hold
    out = [p[0] for p in run(mapper, xs, [90.] * 70)]
    assert out[-1] - out[9] <= 10, out                     # was ~40 px of creep with the slow filter + rest re-sync


def test_gain_rises_smoothly_with_speed():
    def moved(duration):
        mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**PRECISE))
        run(mapper, [.5] * 30, [90.] * 30)
        start = mapper.pos[0]
        n = max(1, round(duration * 30))
        dx = 120 / 1919 * .3                                   # the same 120 px (mapped) move at different speeds
        xs = [.5 + dx * min(1., i / n) for i in range(n + 30)]
        return run(mapper, xs, [90.] * (n + 30))[-1][0] - start
    dists = [moved(d) for d in (1.2, .6, .4, .25, .15)]     # 100 -> 800 px/s
    assert all(b >= a - 1 for a, b in zip(dists, dists[1:])), dists      # faster never travels less
    assert all(b <= a * 1.4 for a, b in zip(dists, dists[1:])), dists    # and no cliff between speeds
    assert dists[1] >= 60, dists                                         # 200 px/s is not damped to 0.4x anymore

def test_cursor_holds_while_click_fingers_touch():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**CALIBRATED))
    t = 10.
    for _ in range(30):
        t += 1 / 30
        mapper.step_palm(.5, 90., t, contact=2.)
    before = mapper.pos
    for _ in range(6):                                        # fingers touch: the pitch jumps 6 deg
        t += 1 / 30
        assert mapper.step_palm(.5, 84., t, contact=.7) == before
    for _ in range(5):                                        # parted, pitch back, still settling (< 0.2 s)
        t += 1 / 30
        assert mapper.step_palm(.5, 90., t, contact=2.) == before
    for _ in range(30):
        t += 1 / 30
        mapper.step_palm(.5, 90., t, contact=2.)
    assert math.dist(mapper.pos, before) <= 2                 # no jump afterwards either
    for _ in range(30):
        t += 1 / 30
        mapper.step_palm(.6, 90., t, contact=2.)
    assert mapper.pos[0] > before[0] + 100                    # and it follows the hand again


def test_cursor_also_holds_while_click_fingers_close_fast():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**CALIBRATED))
    t = 10.
    for _ in range(30):
        t += 1 / 30
        mapper.step_palm(.5, 90., t, contact=3.4)
    before = mapper.pos
    # recorded click: contact 3.4 -> 2.76 -> 1.62 -> 0.94 while the hand tilts up by 5 deg, before touching
    for contact, pitch in ((2.76, 92.6), (1.62, 95.), (.94, 93.)):
        t += 1 / 30
        assert mapper.step_palm(.5, pitch, t, contact=contact) == before


def test_long_contact_is_a_drag_and_moves_without_the_tilt_jump():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**PRECISE))   # default precision: the drag continues relatively
    t = 10.
    for _ in range(30):
        t += 1 / 30
        mapper.step_palm(.5, 90., t, contact=3.)
    start = mapper.pos
    xs = []
    for i in range(45):                                       # hold thumb+middle 1.5 s (tilted 4 deg, ~90 px here) and drag right
        t += 1 / 30
        x = .5 + .1 * max(0., (i - 12) / 30)                  # starts moving after 0.4 s
        xs.append(mapper.step_palm(x, 86., t, contact=.7))
    assert xs[8] == start                                     # first 0.3 s: held like a click
    assert xs[-1][0] > start[0] + 60                          # then the drag follows the hand
    assert abs(xs[-1][1] - start[1]) <= 3                     # and the 4 deg pinch tilt never became a jump
    for _ in range(15):                                       # release: pitch tilts back
        t += 1 / 30
        end = mapper.step_palm(.6, 90., t, contact=3.)
    assert abs(end[1] - start[1]) <= 3                        # no jump at the drop either


def test_cursor_stays_put_after_a_long_drag_ends():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**PRECISE))
    t = 10.
    for _ in range(30):
        t += 1 / 30
        mapper.step_palm(.5, 90., t, contact=3.)
    for i in range(90):                                       # 3 s drag to the right: the precision gap grows large
        t += 1 / 30
        mapper.step_palm(.5 + .12 * min(1., i / 80), 86., t, contact=.7)
    for _ in range(10):                                       # release and settle
        t += 1 / 30
        mapper.step_palm(.62, 90., t, contact=3.)
    dropped = mapper.pos
    for _ in range(60):                                       # hand rests for 2 s
        t += 1 / 30
        mapper.step_palm(.62, 90., t, contact=3.)
    assert math.dist(mapper.pos, dropped) <= 3, (dropped, mapper.pos)   # was pulled back by the gap cap


def test_slow_drag_across_the_screen_keeps_up_with_the_hand():
    mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**PRECISE))
    t = 10.
    for _ in range(30):
        t += 1 / 30
        mapper.step_palm(.38, 90., t, contact=3.)
    start = mapper.pos[0]
    for i in range(120):                                      # hold thumb+middle, drag slowly (~550 px/s) to the right
        t += 1 / 30
        mapper.step_palm(.38 + .25 * min(1., i / 100), 86., t, contact=.7)
    moved = mapper.pos[0] - start
    # hand covered ~1600 px of mapping; it trails by the gap cap (130 px) and the 0.3 s click hold at the start.
    # Without re-sync during drags (previous version) it reached ~650.
    assert moved >= 1250, moved


def test_half_curled_hand_gain_is_not_fist_precision():
    def end(gain, fist):
        mapper = CursorMapper(ScreenLayout.single(1920, 1080), WindowsOutputConfig(**PRECISE))
        t = 10.
        for i in range(60):                                   # quick 0.3 s move right, then hold
            t += 1 / 30
            mapper.step_palm(.5 + .03 * min(1., i / 9), 90., t, gain=gain, fist=fist)   # ~190 px mapped
        return mapper.pos[0]
    assert end(.8, False) == end(1., False)                   # open-hand curl gain does not change the palm cursor
    assert end(.35, True) - 960 <= .55 * (end(1., False) - 960)   # the fist does (~0.35x of the hand move)