"""Drag speed stays independent of finger curl, including MLP-recovered drags."""
from types import SimpleNamespace

import pytest

from manux.actions.bindings import resolve_bindings
from manux.actions.router import Router
from manux.core.contracts import Intent
from manux.features.pinch import DEFAULT_BINDINGS, GESTURE_PHASES, PinchFeature
from manux.features.spatial_pose import SpatialPoseEvidence, SpatialPoseGuard
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.platform.monitors import ScreenLayout

import pinch_hands as H


def router_and_sink():
    bindings, problems = resolve_bindings(DEFAULT_BINDINGS, gesture_phases=GESTURE_PHASES)
    assert not problems
    sink = WindowsOutputSink(
        FakeBackend((500, 400)), ScreenLayout.single(1920, 1080),
        WindowsOutputConfig(smoothing=1., relative_gain_min=1.15, relative_gain_max=2.3),
        threaded=False,
    )
    return Router(bindings), sink


@pytest.mark.parametrize("button,shape,tip,pose,drag", [
    ("left", H.middle_pinch, 12, "thumb_middle", "pinch.drag"),
    ("right", H.ring_pinch, 16, "thumb_ring", "pinch.right_drag"),
    ("left", H.drag_pinch, 12, "thumb_middle", "pinch.drag"),
])
@pytest.mark.parametrize("use_mlp", [False, True])
@pytest.mark.parametrize("axis", [0, 1])
def test_curled_drag_keeps_normal_speed_and_fist_acceleration_returns_after_release(
    button, shape, tip, pose, drag, use_mlp, axis,
):
    feature = PinchFeature()
    evidence = SimpleNamespace(model=object(), evidence=None)
    guard = SpatialPoseGuard(evidence)
    router, sink = router_and_sink()
    frame, offset = 0, 0.

    def step(hand_shape, prediction):
        nonlocal frame
        t = 1. + frame / 30
        frame += 1
        shift = (offset, 0.) if axis == 0 else (0., offset)
        hand, context = H.obs(t, hand_shape(shift=shift)), H.ctx(t)
        intents = feature.step(hand, context)
        if use_mlp:
            evidence.evidence = SpatialPoseEvidence(prediction, .99, {prediction: .99}, t)
            guard.observe(hand, context)
            intents = guard.filter(intents, context)
        for intent in intents:
            command = router.route(intent).command
            assert command is not None and sink.apply(command).accepted
        return intents

    def curled_contact(shift):
        hand = H.fist(shift=shift)
        hand[4] = hand[tip]
        if shape is H.drag_pinch:
            hand[8] = hand[tip]
        return hand

    def move(hand_shape, prediction):
        nonlocal offset
        start = sink.backend.pos[axis]
        for _ in range(8):
            offset += .002
            intents = step(hand_shape, prediction)
            if prediction != "none":
                assert intents and all(i.gesture == drag and i.phase == "hold" for i in intents)
                assert sink.backend.buttons_down == {button}
        return sink.backend.pos[axis] - start

    try:
        for _ in range(30):
            step(shape, pose)
        normal_distance = move(shape, pose)
        assert normal_distance > 0
        for _ in range(12):
            intents = step(curled_contact, pose)
            assert intents and all(i.gesture == drag and i.phase == "hold" for i in intents)
            assert sink.backend.buttons_down == {button}
        assert move(curled_contact, pose) == pytest.approx(normal_distance, abs=2)

        for _ in range(20):
            step(H.open_hand, "none")
        assert not sink.backend.buttons_down
        for _ in range(12):
            step(H.fist, "none")
        assert move(H.fist, "none") == pytest.approx(2 * normal_distance, abs=3)
        assert not sink.backend.buttons_down
    finally:
        sink.close()


@pytest.mark.parametrize("pose,drag,shape,button", [
    ("thumb_middle", "pinch.drag", H.middle_pinch, "left"),
    ("thumb_ring", "pinch.right_drag", H.ring_pinch, "right"),
])
# Each movement source exercises rule recovery and MLP creation; both drag gains remain covered.
@pytest.mark.parametrize("move_gesture,move_scale,start,drag_scale", [
    ("pinch.move_fist", 2., "rule_drag", 1.25), ("pinch.move_fist", 2., "mlp_click", 1.),
    ("pinch.move", 1.5, "rule_drag", 1.), ("pinch.move", 1.5, "mlp_click", 1.25),
    ("pointer.move", 2., "rule_drag", 1.), ("pointer.move", 2., "mlp_click", 1.25),
])
def test_mlp_recovered_drag_uses_drag_speed_instead_of_movement_speed(
    pose, drag, shape, button, move_gesture, move_scale, start, drag_scale,
):
    evidence = SimpleNamespace(model=object(), evidence=None)
    guard = SpatialPoseGuard(evidence, settings={
        "pinch.enable_thumb_tucked_open": True,
        "pinch.long_press_drag_multiplier": drag_scale,
    })
    router, sink = router_and_sink()

    def step(n, prediction, intents):
        t = 1. + n / 30
        evidence.evidence = SpatialPoseEvidence(prediction, .99, {prediction: .99}, t)
        guard.observe(H.obs(t, shape()), H.ctx(t))
        out = guard.filter(intents, H.ctx(t))
        for intent in out:
            command = router.route(intent).command
            assert command is not None and sink.apply(command).accepted
            if command.action == "mouse.drag":
                assert command.params.get("gain", 1.) == drag_scale
        return out

    try:
        if start == "rule_drag":
            # None is the normal encoding of the default 1x drag speed.
            step(0, pose, [Intent(1., "pinch", drag, "begin", point=(.5, .5),
                                 scale=None if drag_scale == 1. else drag_scale)])
        else:
            step(0, pose, [])
            step(1, pose, [])  # MLP starts a click that the 2D rules missed.
        for n in range(2, 14):
            out = step(n, pose, [Intent(1. + n / 30, "pinch", move_gesture, "hold",
                                       point=(.5 + .002 * n, .5), scale=move_scale)])
            assert out and all(i.gesture == drag and i.phase in ("begin", "hold") for i in out)
            assert sink.backend.buttons_down == {button}
        assert sink.backend.pos[0] > 500
        # Releasing the pose must still end the drag and allow normal fist movement.
        out = step(14, "none", [Intent(1. + 14 / 30, "pinch", "pinch.move_fist", "hold",
                                      point=(.53, .5), scale=2.)])
        assert any(i.gesture == drag and i.phase == "end" for i in out)
        assert any(i.gesture == "pinch.move_fist" and i.scale == 2. for i in out)
        assert not sink.backend.buttons_down
    finally:
        sink.close()
