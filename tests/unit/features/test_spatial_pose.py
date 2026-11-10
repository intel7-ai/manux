"""Unit tests for the SpatialPoseFeature and SpatialPoseGuard (engine integration)."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import pytest

from manux.core.contracts import FrameContext, FrameInfo, HandObservation, Intent
from manux.data.pose_studio import ACTION_IDS
from manux.features.spatial_pose import CLASS_TO_GESTURE, GUARD_ID, SpatialPoseFeature, SpatialPoseGuard
from manux.ml.spatial_mlp import SpatialMlpModel, train_spatial_mlp
from manux.perception.spatial_features import SPATIAL_FEATURE_VERSION, extract_spatial_features


def _dummy_hand(pinch_target: int | None = None) -> list[tuple[float, float, float]]:
    pts = [
        (0.50, 0.80, 0.00),  # 0: WRIST
        (0.42, 0.74, -0.02), (0.36, 0.66, -0.03), (0.32, 0.58, -0.04), (0.28, 0.50, -0.04),  # 1..4: THUMB
        (0.44, 0.56, -0.01), (0.43, 0.44, -0.01), (0.43, 0.36, -0.01), (0.43, 0.28, -0.01),  # 5..8: INDEX
        (0.50, 0.54, 0.00), (0.50, 0.42, 0.00), (0.50, 0.33, 0.00), (0.50, 0.25, 0.00),  # 9..12: MIDDLE
        (0.56, 0.56, 0.01), (0.57, 0.45, 0.01), (0.57, 0.37, 0.01), (0.57, 0.30, 0.01),  # 13..16: RING
        (0.62, 0.60, 0.02), (0.64, 0.50, 0.02), (0.65, 0.43, 0.02), (0.66, 0.36, 0.02),  # 17..20: PINKY
    ]
    if pinch_target is not None:
        tx, ty, tz = pts[pinch_target]
        pts[4] = (tx + 0.004, ty + 0.004, tz)
    return pts


@pytest.fixture
def trained_mlp() -> SpatialMlpModel:
    poses = {
        "none": _dummy_hand(None),
        "thumb_middle": _dummy_hand(12),
        "thumb_ring": _dummy_hand(16),
        "thumb_index": _dummy_hand(8),
    }
    xs, ys, ws = [], [], []
    for name, pts in poses.items():
        c_idx = ACTION_IDS.index(name)
        for _ in range(16):
            f = extract_spatial_features(pts)
            assert f is not None
            xs.append(f)
            ys.append(c_idx)
            ws.append(1.0)
    model, _ = train_spatial_mlp(
        np.stack(xs), np.array(ys), np.array(ws), classes=ACTION_IDS, feature_version=SPATIAL_FEATURE_VERSION, epochs=50
    )
    return model


def test_spatial_pose_guard_vetoes_false_positive_clicks_during_rest(trained_mlp: SpatialMlpModel):
    feat = SpatialPoseFeature(trained_mlp, mode="assist")
    guard = SpatialPoseGuard(feat, tau_veto=0.75, k_frames=2)

    frame1 = FrameInfo(1, 100.0, 640, 480, False)
    frame2 = FrameInfo(2, 100.05, 640, 480, False)
    hand_idle = HandObservation(
        frame=frame1,
        landmarks=tuple(_dummy_hand(None)),
        handedness="Right",
        score=0.99,
    )

    ctx1 = FrameContext(frame1, "default", "none", "pointer")
    ctx2 = FrameContext(frame2, "default", "none", "pointer")

    # Frame 1: hand is idle ('none')
    guard.observe(hand_idle, ctx1)
    # 1 frame is not enough for k_frames=2
    intents1 = [Intent(100.0, "heuristic", "pinch.thumb_ring", "begin")]
    filtered1 = guard.filter(intents1, ctx1)
    assert len(filtered1) == 1

    # Frame 2: hand is still idle ('none') -> k_frames reached!
    guard.observe(hand_idle, ctx2)
    intents2 = [Intent(100.05, "heuristic", "pinch.thumb_ring", "begin")]
    filtered2 = guard.filter(intents2, ctx2)
    # The false right-click is successfully VETOED!
    assert len(filtered2) == 0
    assert guard.vetoed_count == 1
    events = guard.drain_events()
    assert any("spatial_guard:vetoed:pinch.thumb_ring:begin" in ev for ev in events)


def test_spatial_pose_feature_hot_reload():
    feat = SpatialPoseFeature(None)
    assert feat.model is None

    frame = FrameInfo(1, 100.0, 640, 480, False)
    hand = HandObservation(frame=frame, landmarks=tuple(_dummy_hand(12)), handedness="Right", score=0.9)
    ctx = FrameContext(frame, "default", "none", "pointer")

    # Without model, evidence is None
    feat.step(hand, ctx)
    assert feat.evidence is None

    # Hot reload model dynamically
    poses = {"none": _dummy_hand(None), "thumb_middle": _dummy_hand(12)}
    xs = [extract_spatial_features(p) for p in (poses["none"], poses["thumb_middle"])]
    model, _ = train_spatial_mlp(
        np.stack(xs), np.array([0, 1]), np.array([1.0, 1.0]), classes=ACTION_IDS, feature_version=SPATIAL_FEATURE_VERSION, epochs=10
    )
    feat.set_model(model)
    assert feat.model is not None

    feat.step(hand, ctx)
    assert feat.evidence is not None
    assert feat.evidence.action in ("thumb_middle", "none")


def test_rock_class_is_a_gesture_and_close_runs_once_via_binding():
    from manux.actions.catalog import CATALOG
    from manux.features.spatial_pose import DEFAULT_BINDINGS
    assert CLASS_TO_GESTURE["rock_sign"] == "spatial_pose.rock_sign"
    assert not set(CLASS_TO_GESTURE.values()) & set(CATALOG)
    assert DEFAULT_BINDINGS["spatial_pose.rock_sign"] == "window.close"
    model = SpatialMlpModel(np.zeros((4,96)), np.zeros(4), np.zeros((2,4)), np.array([-10.,10.]),
                            classes=("none","rock_sign"))
    feature = SpatialPoseFeature(model, mode="direct")
    frame = FrameInfo(1, 1., 640, 480, False)
    hand = HandObservation(frame, tuple(_dummy_hand()), "Right", .99)
    ctx = FrameContext(frame, "default", "none", "pointer")
    first = feature.step(hand, ctx)
    assert [(i.gesture,i.phase) for i in first] == [("spatial_pose.rock_sign","tap")]
    assert feature.step(hand, ctx) == ()  # holding a pose must not close more windows


def test_direct_mlp_v_is_evidence_only_so_it_cannot_bypass_control_hold():
    model = SpatialMlpModel(np.zeros((4,96)), np.zeros(4), np.zeros((2,4)), np.array([-10.,10.]),
                            classes=("none","v_sign"))
    feature = SpatialPoseFeature(model, mode="direct")
    frame = FrameInfo(1, 1., 640, 480, False)
    hand = HandObservation(frame, tuple(_dummy_hand()), "Right", .99)
    ctx = FrameContext(frame, "default", "none", "pointer")
    assert feature.step(hand, ctx) == ()
    assert feature.evidence.action == "v_sign"
    assert CLASS_TO_GESTURE["v_sign"] == "control.v_sign"


def test_guard_boost_clicks_once_for_a_confident_pose_the_rules_missed(trained_mlp: SpatialMlpModel):
    feat = SpatialPoseFeature(trained_mlp, mode="assist")
    guard = SpatialPoseGuard(feat, tau_boost=0.6, k_boost=3)
    taps = []
    for n in range(8):
        frame = FrameInfo(n, 200.0 + n * 0.03, 640, 480, False)
        hand = HandObservation(frame=frame, landmarks=tuple(_dummy_hand(12)), handedness="Right", score=0.9)
        ctx = FrameContext(frame, "default", "none", "pinch")
        guard.observe(hand, ctx)
        taps += [i for i in guard.filter([], ctx) if i.phase == "tap"]
    assert [i.gesture for i in taps] == ["pinch.thumb_middle"]
    assert guard.boosted_count == 1


def test_guard_boost_does_not_double_a_click_the_rules_already_fired(trained_mlp: SpatialMlpModel):
    feat = SpatialPoseFeature(trained_mlp, mode="assist")
    guard = SpatialPoseGuard(feat, tau_boost=0.6, k_boost=3)
    taps = []
    for n in range(8):
        frame = FrameInfo(n, 300.0 + n * 0.03, 640, 480, False)
        hand = HandObservation(frame=frame, landmarks=tuple(_dummy_hand(12)), handedness="Right", score=0.9)
        ctx = FrameContext(frame, "default", "none", "pinch")
        guard.observe(hand, ctx)
        rule = [Intent(frame.t, "pinch", "pinch.thumb_middle", "tap")] if n == 1 else []
        taps += [i for i in guard.filter(rule, ctx) if i.phase == "tap"]
    assert len(taps) == 1 and guard.boosted_count == 0


def test_guard_sustains_drag_when_heuristic_prematurely_ends_if_mlp_confirms_middle_pinch(
    trained_mlp: SpatialMlpModel,
):
    feat = SpatialPoseFeature(trained_mlp, mode="assist")
    guard = SpatialPoseGuard(feat, tau_boost=0.99)  # disable click boost

    frame_drag = FrameInfo(1, 400.0, 640, 480, False)
    hand_drag = HandObservation(frame=frame_drag, landmarks=tuple(_dummy_hand(12)), handedness="Right", score=0.95)
    ctx_drag = FrameContext(frame_drag, "default", "none", "pinch")

    # Step 1: Drag begins normally
    guard.observe(hand_drag, ctx_drag)
    out1 = guard.filter([Intent(400.0, "pinch", "pinch.drag", "begin", point=(0.5, 0.5))], ctx_drag)
    assert len(out1) == 1 and out1[0].gesture == "pinch.drag" and out1[0].phase == "begin"
    assert guard._dragging is True

    # Step 2: Heuristic prematurely emits "end" (e.g. 1-frame distance noise),
    # but MLP evidence still recognizes "thumb_middle"
    frame_noise = FrameInfo(2, 400.03, 640, 480, False)
    ctx_noise = FrameContext(frame_noise, "default", "none", "pinch")
    guard.observe(hand_drag, ctx_noise)
    out2 = guard.filter([Intent(400.03, "pinch", "pinch.drag", "end", point=(0.52, 0.51))], ctx_noise)
    assert len(out2) == 1
    assert out2[0].gesture == "pinch.drag" and out2[0].phase == "hold"
    assert guard._dragging is True
    assert "spatial_guard:drag_sustained_on_end" in guard.drain_events()

    # Step 3: Next frame, heuristic drops back to "pinch.move" hold,
    # but MLP is still holding thumb_middle -> converted to drag hold!
    frame_move = FrameInfo(3, 400.06, 640, 480, False)
    ctx_move = FrameContext(frame_move, "default", "none", "pinch")
    guard.observe(hand_drag, ctx_move)
    out3 = guard.filter([Intent(400.06, "pinch", "pinch.move", "hold", point=(0.54, 0.52))], ctx_move)
    assert len(out3) == 1
    assert out3[0].gesture == "pinch.drag" and out3[0].phase == "hold"
    assert guard._dragging is True
    assert "spatial_guard:move_converted_to_drag" in guard.drain_events()

    # Step 4: User opens hand (none)
    frame_open = FrameInfo(4, 400.09, 640, 480, False)
    hand_open = HandObservation(frame=frame_open, landmarks=tuple(_dummy_hand(None)), handedness="Right", score=0.95)
    ctx_open = FrameContext(frame_open, "default", "none", "pinch")
    guard.observe(hand_open, ctx_open)
    out4 = guard.filter([Intent(400.09, "pinch", "pinch.move", "hold", point=(0.55, 0.53))], ctx_open)
    # Drag end must be emitted to release the mouse!
    gestures_phases = [(i.gesture, i.phase) for i in out4]
    assert ("pinch.drag", "end") in gestures_phases
    assert guard._dragging is False


def test_guard_assists_and_sustains_scroll_when_mlp_confirms_thumb_index(
    trained_mlp: SpatialMlpModel,
):
    feat = SpatialPoseFeature(trained_mlp, mode="assist")
    guard = SpatialPoseGuard(feat, tau_boost=0.99)

    hand_index = HandObservation(
        frame=FrameInfo(1, 500.0, 640, 480, False),
        landmarks=tuple(_dummy_hand(8)),
        handedness="Right",
        score=0.95,
    )
    ctx_index = FrameContext(hand_index.frame, "default", "none", "pinch")

    # Frame 1: MLP sees thumb_index, run = 1 (candidate)
    guard.observe(hand_index, ctx_index)
    out1 = guard.filter([Intent(500.0, "pinch", "pinch.move", "hold", point=(0.5, 0.5))], ctx_index)
    assert guard._scrolling is False

    # Frame 2: MLP sees thumb_index again (run = 2) -> converts move into scroll begin!
    hand_index2 = HandObservation(
        frame=FrameInfo(2, 500.03, 640, 480, False),
        landmarks=tuple(_dummy_hand(8)),
        handedness="Right",
        score=0.95,
    )
    ctx_index2 = FrameContext(hand_index2.frame, "default", "none", "pinch")
    guard.observe(hand_index2, ctx_index2)
    out2 = guard.filter([Intent(500.03, "pinch", "pinch.move", "hold", point=(0.5, 0.52))], ctx_index2)
    assert len(out2) == 1 and out2[0].gesture == "pinch.scroll" and out2[0].phase == "begin"
    assert guard._scrolling is True
    assert "spatial_guard:move_converted_to_scroll_begin" in guard.drain_events()

    # Frame 3: Move converted to scroll hold
    hand_index3 = HandObservation(
        frame=FrameInfo(3, 500.06, 640, 480, False),
        landmarks=tuple(_dummy_hand(8)),
        handedness="Right",
        score=0.95,
    )
    ctx_index3 = FrameContext(hand_index3.frame, "default", "none", "pinch")
    guard.observe(hand_index3, ctx_index3)
    out3 = guard.filter([Intent(500.06, "pinch", "pinch.move", "hold", point=(0.5, 0.54))], ctx_index3)
    assert len(out3) == 1 and out3[0].gesture == "pinch.scroll" and out3[0].phase == "hold"

    # Frame 4: User opens hand (none) -> scroll cleanly ends
    hand_open = HandObservation(
        frame=FrameInfo(4, 500.09, 640, 480, False),
        landmarks=tuple(_dummy_hand(None)),
        handedness="Right",
        score=0.95,
    )
    ctx_open = FrameContext(hand_open.frame, "default", "none", "pinch")
    guard.observe(hand_open, ctx_open)
    out4 = guard.filter([Intent(500.09, "pinch", "pinch.move", "hold", point=(0.5, 0.55))], ctx_open)
    gestures_phases = [(i.gesture, i.phase) for i in out4]
    assert ("pinch.scroll", "end") in gestures_phases
    assert guard._scrolling is False

