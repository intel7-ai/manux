"""Drag speed stays independent of finger curl, including MLP-recovered drags."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from manux.actions.bindings import resolve_bindings
from manux.actions.router import Router
from manux.core.contracts import Intent
from manux.features.pinch import DEFAULT_BINDINGS, GESTURE_PHASES, PinchFeature
from manux.features.spatial_pose import SpatialPoseEvidence, SpatialPoseGuard
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.platform.monitors import ScreenLayout

import pinch_hands as H


@pytest.mark.parametrize("tip,pose,drag,button,fps", [
    (12, "thumb_middle", "pinch.drag", "left", 30),
    (16, "thumb_ring", "pinch.right_drag", "right", 60),
])
def test_mlp_only_contact_tracks_live_palm_through_missing_rules_and_stale_end(tip, pose, drag, button, fps):
    feature = PinchFeature()
    evidence = SimpleNamespace(model=object(), evidence=None)
    settings = {"pinch.cursor_anchor_ratio": .25, "pinch.palm_axes": [1., .2, 0., 1.]}
    guard = SpatialPoseGuard(evidence, settings=settings)
    router, sink = router_and_sink()
    clicks, points = [], []

    def step(n, prediction=pose, missing=False, stale_end=False):
        t = 1. + n / fps
        # Fold only the pinched finger: MLP sees a click, but the 2D entry rule rejects it.
        pts = H.open_hand(shift=(n * .003, n * .001))
        pts[tip] = (pts[tip][0], .67 + n * .001, 0.)
        pts[4] = (pts[tip][0] + .01, pts[tip][1], 0.)
        hand, ctx = (None if missing else H.obs(t, pts)), H.ctx(t)
        raw = feature.step(hand, ctx)
        assert not raw
        evidence.evidence = SpatialPoseEvidence(prediction, .99, {prediction: .99}, t)
        guard.observe(hand, ctx)
        if stale_end:
            raw = [Intent(t, "pinch", drag, "end", point=points[-1])]
        out = guard.filter(raw, ctx)
        for intent in out:
            if intent.phase == "tap":
                clicks.append(intent)
            command = router.route(intent).command
            assert command is not None and sink.apply(command).accepted
        return out

    try:
        step(0)
        step(1)
        assert len(clicks) == 1 and sink.backend.buttons_down == {button}
        for n in range(2, 12):
            out = step(n, stale_end=n == 7)
            assert len(out) == 1 and out[0].gesture == drag
            assert out[0].phase == ("begin" if n == 2 else "hold")
            assert (out[0].scale or 1.) == 1.
            # Same configured anchor and aspect-aware calibration as the rule cursor.
            x, y = .495 + n * .003, .75 + n * .001
            expected = (x + .6 * .2 * (y - .5) * (720 / 1280), y)
            assert out[0].point == pytest.approx(expected)
            points.append(out[0].point)
            assert sink.backend.buttons_down == {button}
        assert sink.backend.pos[0] > 500 and len(clicks) == 1
        before = sink.backend.pos
        step(12, missing=True)
        assert sink.backend.pos == before  # Never extrapolate a lost hand.
        out = step(13, prediction="none")
        assert [(i.gesture, i.phase) for i in out] == [(drag, "end")]
        assert not sink.backend.buttons_down
    finally:
        sink.close()


@pytest.mark.parametrize("settings", [
    {"pinch.long_press_drag": False},
    {"pinch.drag_on_move": False, "pinch.long_press_time": 1.},
])
def test_mlp_silent_rules_respect_drag_settings(settings):
    evidence = SimpleNamespace(model=object(), evidence=None)
    guard = SpatialPoseGuard(evidence, settings=settings)
    for n in range(12):
        t = 1. + n / 30
        evidence.evidence = SpatialPoseEvidence("thumb_middle", .99, {}, t)
        ctx = H.ctx(t)
        guard.observe(H.obs(t, H.middle_pinch()), ctx)
        assert not any(i.gesture == "pinch.drag" for i in guard.filter([], ctx))


def test_mlp_drag_recovery_does_not_reuse_observation_after_reset_or_mode_change():
    evidence = SimpleNamespace(model=object(), evidence=None)
    guard = SpatialPoseGuard(evidence)
    for n in range(2):
        t = 1. + n / 30
        ctx = H.ctx(t)
        evidence.evidence = SpatialPoseEvidence("thumb_middle", .99, {}, t)
        guard.observe(H.obs(t, H.middle_pinch()), ctx)
        guard.filter([], ctx)
    ctx = replace(H.ctx(1.1), active_mode="motion")
    evidence.evidence = SpatialPoseEvidence("thumb_middle", .99, {}, ctx.frame.t)
    guard.observe(H.obs(ctx.frame.t, H.middle_pinch()), ctx)
    assert not guard.filter([], ctx)
    guard.reset("mode_change")
    assert not guard.filter([], H.ctx(ctx.frame.t))


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
