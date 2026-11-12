"""Confirmed contact follows the hand without a separate long-press / travel threshold."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from manux.actions.bindings import resolve_bindings
from manux.actions.router import Router
from manux.features.pinch import DEFAULT_BINDINGS, GESTURE_PHASES, PinchFeature
from manux.features.spatial_pose import SpatialPoseEvidence, SpatialPoseGuard
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.platform.monitors import ScreenLayout

import pinch_hands as H


@pytest.mark.parametrize("shape,pose,drag,button", [
    (H.middle_pinch, "thumb_middle", "pinch.drag", "left"),
    (H.ring_pinch, "thumb_ring", "pinch.right_drag", "right"),
])
# Still, tiny and ordinary movement each run with and without MLP across 30/60fps.
@pytest.mark.parametrize("fps,shift_per_frame,use_mlp", [
    (30, 0., False), (60, 0., True),
    (30, .00005, True), (60, .00005, False),
    (30, .003, False), (60, .003, True),
])
def test_confirmed_pinch_drags_on_next_frame_even_with_saved_one_second_wait(
    shape, pose, drag, button, fps, shift_per_frame, use_mlp,
):
    settings = {"pinch.long_press_time": 1., "pinch.long_press_drag_multiplier": 1.}
    feature = PinchFeature()
    evidence = SimpleNamespace(model=object(), evidence=None)
    guard = SpatialPoseGuard(evidence, settings=settings)
    bindings, problems = resolve_bindings(DEFAULT_BINDINGS, gesture_phases=GESTURE_PHASES)
    assert not problems
    router = Router(bindings)
    sink = WindowsOutputSink(FakeBackend((500, 400)), ScreenLayout.single(1920, 1080),
                            WindowsOutputConfig(smoothing=1., relative_gain_min=1., relative_gain_max=2.),
                            threaded=False)
    clicks, drags = [], []

    def step(n, hand_shape, prediction):
        t = 1. + n / fps
        hand = H.obs(t, hand_shape(shift=(shift_per_frame * n, 0.)))
        ctx = H.ctx(t, settings=settings)
        intents = feature.step(hand, ctx)
        if use_mlp:
            ctx = replace(ctx, others={"pinch": feature.activity()})
            evidence.evidence = SpatialPoseEvidence(prediction, .99, {prediction: .99}, t)
            guard.observe(hand, ctx)
            intents = guard.filter(intents, ctx)
        for intent in intents:
            if intent.phase == "tap":
                clicks.append(intent)
            if intent.gesture == drag and intent.phase != "end":
                drags.append(intent)
            command = router.route(intent).command
            assert command is not None and sink.apply(command).accepted
        return intents

    try:
        for n in range(12):
            step(n, shape, pose)
        assert len(clicks) == 1
        assert drags and drags[0].t == pytest.approx(clicks[0].t + 1 / fps)
        assert all((i.scale or 1.) == 1. for i in drags)
        assert sink.backend.buttons_down == {button}
        if shift_per_frame:
            assert sink.backend.pos[0] > 500
        else:
            assert sink.backend.pos == (500, 400)
        for n in range(12, 32):
            step(n, H.open_hand, "none")
        assert not sink.backend.buttons_down
        assert [e for e in sink.backend.events if e[0] == "button"] == [
            ("button", button, True), ("button", button, False),
        ]
    finally:
        sink.close()


@pytest.mark.parametrize("shape", [H.middle_pinch, H.ring_pinch])
def test_brief_unconfirmed_touch_does_not_click_or_drag(shape):
    feature = PinchFeature()
    intents = []
    for n, hand in enumerate([shape(), H.open_hand(), H.open_hand(), H.open_hand()]):
        t = 1 + n / 30
        intents.extend(feature.step(H.obs(t, hand), H.ctx(t)))
    assert not [i for i in intents if i.phase in ("tap", "begin")]


@pytest.mark.parametrize("shape", [H.middle_pinch, H.ring_pinch])
def test_drag_switch_and_explicit_legacy_long_press_mode_are_preserved(shape):
    for settings in ({"long_press_drag": False}, {"drag_on_move": False, "long_press_time": 1.}):
        feature = PinchFeature()
        intents = []
        for n in range(15):
            t = 1 + n / 30
            intents.extend(feature.step(H.obs(t, shape()), H.ctx(t, settings=settings)))
        assert len([i for i in intents if i.phase == "tap"]) == 1
        assert not [i for i in intents if i.phase == "begin"]
