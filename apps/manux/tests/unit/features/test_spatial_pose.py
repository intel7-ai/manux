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
