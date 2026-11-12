"""Synthetic hand shapes through MLP guard, bindings and a fake Windows cursor."""
from types import SimpleNamespace

import pytest

from manux.actions.bindings import resolve_bindings
from manux.actions.router import Router
from manux.features.pinch import DEFAULT_BINDINGS, GESTURE_PHASES, PinchFeature
from manux.features.spatial_pose import SpatialPoseEvidence, SpatialPoseGuard
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.platform.monitors import ScreenLayout

import pinch_hands as H


@pytest.mark.parametrize("fps", [15, 30, 60])
def test_moving_open_fist_open_transition_has_no_missing_cursor_frames(fps):
    feature = PinchFeature()
    bindings, problems = resolve_bindings(DEFAULT_BINDINGS, gesture_phases=GESTURE_PHASES)
    assert not problems
    router = Router(bindings)
    sink = WindowsOutputSink(FakeBackend((500, 400)), ScreenLayout.single(1920, 1080),
        WindowsOutputConfig(smoothing=1., relative_gain_min=1.15, relative_gain_max=2.3), threaded=False)
    shapes = ([H.open_hand()] * 10
              + [H._lerp(H.open_hand(), H.fist(), n / 20) for n in range(1, 21)]
              + [H.fist()] * 10
              + [H._lerp(H.fist(), H.open_hand(), n / 20) for n in range(1, 21)]
              + [H.open_hand()] * 10)
    try:
        for n, pts in enumerate(shapes):
            t = 1. + n / fps
            intents = feature.step(H.obs(t, H._shift(pts, (n * .003, 0.))), H.ctx(t))
            assert len(intents) == 1 and intents[0].gesture in ("pinch.move", "pinch.move_fist"), n
            before = sink.backend.pos[0]
            command = router.route(intents[0]).command
            assert command is not None and sink.apply(command).accepted
            if n:
                assert sink.backend.pos[0] > before, n
        assert sink.backend.clicks() == [] and not sink.backend.buttons_down
    finally:
        sink.close()


@pytest.mark.parametrize("axis", (0, 1))
def test_open_fist_open_speed_reaches_output_without_clicking(axis):
    feature = PinchFeature()
    evidence = SimpleNamespace(evidence=None, model=object())
    guard = SpatialPoseGuard(evidence)
    bindings, problems = resolve_bindings(DEFAULT_BINDINGS, gesture_phases=GESTURE_PHASES)
    assert not problems
    router = Router(bindings)
    sink = WindowsOutputSink(FakeBackend((500, 400)), ScreenLayout.single(1920, 1080),
        WindowsOutputConfig(smoothing=1., relative_gain_min=1.15, relative_gain_max=2.3), threaded=False)
    frame, offset = 0, 0.

    def step(shape):
        nonlocal frame
        t = 1. + frame / 30
        frame += 1
        shift = (offset, 0.) if axis == 0 else (0., offset)
        hand, context = H.obs(t, shape(shift=shift)), H.ctx(t)
        evidence.evidence = SpatialPoseEvidence("none", .99, {"none": .99}, t)
        intents = feature.step(hand, context)
        guard.observe(hand, context)
        for intent in guard.filter(intents, context):
            command = router.route(intent).command
            assert command is not None and command.action == "mouse.move"
            assert sink.apply(command).accepted

    distances = []
    try:
        for shape in (H.open_hand, H.fist, H.open_hand):
            for _ in range(12):
                step(shape)
            start = sink.backend.pos[axis]
            for _ in range(8):
                offset += .002
                step(shape)
            distances.append(sink.backend.pos[axis] - start)
        assert distances[0] > 0
        assert distances[1] == pytest.approx(2 * distances[0], abs=3)
        assert distances[2] == pytest.approx(distances[0], abs=2)
        assert sink.backend.clicks() == [] and not sink.backend.buttons_down
    finally:
        sink.close()
