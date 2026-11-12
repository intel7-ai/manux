"""Synthetic desktop safety regressions; these never send real Windows input."""
from types import SimpleNamespace

import pytest

from manux.actions.bindings import Binding
from manux.actions.router import Router
from manux.app import schema
from manux.core.contracts import Activity, FrameContext, FrameInfo, HandObservation, Intent, OutputTarget, Stage
from manux.engine.commands import RequestMode, SetControl, SetStage
from manux.engine.pipeline import Engine, EngineConfig
from manux.features.spatial_pose import SpatialPoseEvidence, SpatialPoseGuard
from manux.output.windows import FakeBackend, WindowsOutputSink
from manux.platform.monitors import ScreenLayout
from manux.settings.schema import Schema
from manux.settings.store import Settings

DESKTOP = "pinch.thumb_tucked_open"


def observation(t, shift=(0., 0.), geometry=(1280, 720, True, 0), horizontal=True):
    width, height, mirrored, rotation = geometry
    frame = FrameInfo(round(max(0., t) * 1000), t, width, height, mirrored, rotation)
    points = [(.4 + shift[0], .5 + shift[1], 0.)] * 21
    points[9] = (.6 + shift[0], .5 + shift[1], 0.) if horizontal else (.4, .2, 0.)
    return HandObservation(frame, tuple(points), "Right", .99)


class Run:
    def __init__(self, settings=None):
        self.feature = SimpleNamespace(model=object(), evidence=None, step=self.no_evidence)
        self.guard = SpatialPoseGuard(self.feature, settings=settings)

    def no_evidence(self, hand, ctx):
        self.feature.evidence = None
        return ()

    def step(self, t, *, action="palm_side", confidence=.99, mode="pinch", active_mode=None,
             shift=(0., 0.), missing=False, stale=False, intents=(), **hand_options):
        hand = observation(t, shift, **hand_options)
        ctx = FrameContext(hand.frame, Stage.MAIN, OutputTarget.WINDOWS, mode, active_mode=active_mode)
        self.feature.evidence = SpatialPoseEvidence(action, confidence, {action: confidence},
                                                    t - 1 if stale else t)
        self.guard.observe(None if missing else hand, ctx)
        return self.guard.filter(intents, ctx)


def desktop(intents):
    return [i for i in intents if i.gesture == DESKTOP]


@pytest.mark.parametrize("fps,rule_intent", [(10, False), (30, False), (60, False), (30, True)])
def test_desktop_waits_for_elapsed_time_and_fires_once_per_pose(fps, rule_intent):
    run = Run()
    fired = []
    for n in range(4 * fps):
        t = n / fps
        incoming = (Intent(t, "pinch", DESKTOP, "tap"),) if rule_intent else ()
        fired.extend(i.t for i in desktop(run.step(t, intents=incoming)))
    assert len(fired) == 1
    assert .45 <= fired[0] <= .45 + 1 / fps


def test_variable_frame_intervals_use_elapsed_time():
    run = Run()
    times = [0., .02, .09, .19, .23, .35, .43, .49, .55]
    assert [t for t in times if desktop(run.step(t))] == [.49]


# Representative rates on each axis; vertical cases cover both aspect ratios.
@pytest.mark.parametrize("fps,axis,geometry", [
    (10, 0, (1280, 720, True, 0)), (60, 0, (1280, 720, True, 0)),
    (10, 1, (640, 480, True, 0)), (60, 1, (640, 480, True, 0)),
    (30, 1, (1280, 720, True, 0)),
])
@pytest.mark.parametrize("speed,expected", [(.7, 1), (.8, 0)])
def test_same_physical_speed_has_same_result_across_fps_and_aspect(fps, axis, geometry, speed, expected):
    run = Run()
    fired = []
    for n in range(fps + 1):
        t = n / fps
        # Equal image-width displacement on either pixel axis.
        shift = (speed * t, 0.) if axis == 0 else (0., speed * t * geometry[0] / geometry[1])
        fired.extend(desktop(run.step(t, shift=shift, geometry=geometry)))
    assert len(fired) == expected


@pytest.mark.parametrize("interruption", ["hand", "evidence", "vertical", "confidence", "speed", "motion"])
def test_interruption_requires_a_fresh_hold(interruption):
    run = Run()
    for n in range(5):
        assert not desktop(run.step(n * .1))
    options = {
        "hand": {"missing": True}, "evidence": {"stale": True},
        "vertical": {"horizontal": False}, "confidence": {"confidence": .7},
        "speed": {"shift": (.2, 0.)}, "motion": {"active_mode": "motion"},
    }[interruption]
    assert not desktop(run.step(.44, intents=(Intent(.44, "pinch", DESKTOP, "tap"),), **options))
    shift = (.2, 0.) if interruption == "speed" else (0., 0.)
    for t in [.5, .6, .7, .8, .9]:
        assert not desktop(run.step(t, shift=shift))
    assert desktop(run.step(1., shift=shift))


@pytest.mark.parametrize("geometry", [(640, 480, True, 0), (1280, 720, False, 0), (1280, 720, True, 90)])
def test_geometry_change_does_not_inherit_hold(geometry):
    run = Run()
    for t in [0., .1, .2, .3, .4]:
        assert not desktop(run.step(t))
    for t in [.5, .6, .7, .8, .9]:
        assert not desktop(run.step(t, geometry=geometry))
    assert desktop(run.step(1., geometry=geometry))


@pytest.mark.parametrize("new_time", [.2, .4, 1.])
def test_repeated_backward_or_gapped_timestamp_cannot_complete_hold(new_time):
    run = Run()
    for t in [0., .1, .2, .3, .4]:
        assert not desktop(run.step(t))
    assert not desktop(run.step(new_time))
    for delta in [.1, .2, .3, .4]:
        assert not desktop(run.step(new_time + delta))
    assert desktop(run.step(new_time + .6))


def test_fired_pose_requires_confident_release_even_after_noise_or_motion():
    run = Run()
    fired = []
    for n in range(45):
        t = n / 10
        options = {}
        if n == 10:
            options = {"action": "none", "confidence": .4}
        elif n == 15:
            options = {"missing": True}
        elif n == 20:
            options = {"shift": (.2, 0.)}
        elif n == 25:
            options = {"active_mode": "motion"}
        fired.extend(desktop(run.step(t, **options)))
    assert len(fired) == 1
    run.step(4.5, action="none")
    assert sum(len(desktop(run.step(4.6 + n / 10))) for n in range(7)) == 1


def test_released_pose_still_respects_existing_two_second_cooldown():
    run = Run()
    fired = []
    for n in range(32):
        t = n / 10
        fired.extend(i.t for i in desktop(run.step(t, action="none" if n == 6 else "palm_side")))
    assert fired == [pytest.approx(.5), pytest.approx(2.5)]


@pytest.mark.parametrize("gesture,phase", [
    ("pinch.drag", "begin"), ("pinch.right_drag", "begin"), ("pinch.scroll", "begin"),
    ("pointer.drag", "hold"), ("pinch.thumb_middle", "tap"), ("pinch.thumb_ring", "tap"),
])
def test_input_start_on_threshold_frame_cannot_be_replaced_by_desktop(gesture, phase):
    run = Run()
    for t in [0., .1, .2, .3, .4]:
        assert not desktop(run.step(t))
    incoming = Intent(.5, gesture.split(".")[0], gesture, phase, point=(.5, .5))
    assert not desktop(run.step(.5, intents=(incoming,)))


def test_desktop_does_not_discard_input_release_events():
    run = Run()
    for t in [0., .1, .2, .3, .4]:
        run.step(t)
    end = Intent(.5, "pointer", "pointer.drag", "end")
    out = run.step(.5, intents=(end,))
    assert out[0] == end and desktop(out)


@pytest.mark.parametrize("settings", [None, {}, {"pinch.enable_thumb_tucked_open": None},
                                     Settings(Schema()), Settings(schema())])
def test_default_setting_lookup_supports_actual_settings_and_partial_mappings(settings):
    run = Run(settings)
    assert sum(len(desktop(run.step(n / 10))) for n in range(7)) == 1
    assert run.guard._configured_drag_scale() == 1.


@pytest.mark.parametrize("settings", [{"pinch.enable_thumb_tucked_open": False},
    Settings(schema(), {"pinch.enable_thumb_tucked_open": False})])
def test_explicit_false_blocks_mlp_and_rule_desktop(settings):
    run = Run(settings)
    for n in range(30):
        t = n / 10
        assert not desktop(run.step(t, intents=(Intent(t, "pinch", DESKTOP, "tap"),)))


def test_reset_clears_desktop_hold_latch_and_fast_boost_state():
    run = Run()
    for n in range(7):
        run.step(n / 10)
    # A confident click also sets the fast-boost flag before reset.
    run.step(.7, action="thumb_middle")
    assert run.guard._boost_fast
    # The guard uses the same reset body for all reasons; engine tests cover dispatch.
    run.guard.reset("settings")
    assert not run.guard._boost_fast
    for n in range(5):
        assert not desktop(run.step(.8 + n / 10))
    assert desktop(run.step(1.3))


class MotionRequest:
    id = "motion"
    modes = frozenset({"pinch", "pointer", "motion"})
    gestures = ()

    def __init__(self):
        self.enter = False

    def step(self, hand, ctx):
        if self.enter:
            self.enter = False
            return (Intent(ctx.frame.t, self.id, "motion.enter", "tap"),)
        return ()

    def activity(self):
        return Activity()

    def reset(self, reason):
        pass


@pytest.mark.parametrize("base", ["pinch", "pointer"])
@pytest.mark.parametrize("via", ["ui", "intent"])
def test_real_engine_passes_motion_state_and_blocks_desktop_until_fresh_base_hold(base, via):
    run = Run(Settings(schema()))
    motion = MotionRequest()
    backend = FakeBackend()
    sink = WindowsOutputSink(backend, ScreenLayout.single(1920, 1080), threaded=False)
    engine = Engine([motion], [run.guard], Router({DESKTOP: Binding("window.show_desktop")}),
                    {target: sink for target in OutputTarget}, lambda _: {}, EngineConfig(base_mode=base))
    engine.commands.put(SetStage(Stage.MAIN))
    engine.commands.put(SetControl(True))
    contexts = []
    original_observe = run.guard.observe

    def observe(hand, ctx):
        contexts.append((ctx.mode, ctx.active_mode))
        original_observe(hand, ctx)

    run.guard.observe = observe

    def step(t):
        hand = observation(t)
        run.feature.evidence = SpatialPoseEvidence("palm_side", .99, {"palm_side": .99}, t)
        result = engine.step(hand.frame, hand)
        assert not result.errors
        return result

    try:
        for n in range(5):
            assert not step(n / 10).results
        if via == "ui":
            engine.commands.put(RequestMode("motion"))
        else:
            motion.enter = True
        for n in range(5, 25):
            result = step(n / 10)
            assert result.mode == "motion" and not result.results
            assert contexts[-1] == (base, "motion")
        engine.commands.put(RequestMode(None))
        for n in range(25, 30):
            assert not step(n / 10).results
            assert contexts[-1] == (base, base)
        assert ("window.show_desktop", "tap", "ok") in step(3.).results
        assert not backend.buttons_down and not backend.keys_down
    finally:
        engine.shutdown()
