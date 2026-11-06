"""Unit tests for the Spatial 96-dim feature extractor, 4-second Spatial MLP trainer, and PoseSnapshotStudio."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from manux.data.pose_studio import ACTION_IDS, PoseSnapshotStudio, STUDIO_ACTIONS, normalize_system_action
from manux.desktop import make_pose_studio
from manux.ml.spatial_mlp import STUDIO_CLASSES, SpatialMlpModel, train_spatial_mlp
from manux.perception.spatial_features import SPATIAL_FEATURES, extract_spatial_features


def _synthetic_hand(pinch_target: int | None = None, spread_y: float = 0.0) -> list[tuple[float, float, float]]:
    """Generate a realistic 21-joint synthetic hand with optional thumb pinch to a target fingertip."""
    pts = [
        (0.50, 0.80, 0.00),  # 0: WRIST
        (0.42, 0.74, -0.02), (0.36, 0.66, -0.03), (0.32, 0.58, -0.04), (0.28, 0.50, -0.04),  # 1..4: THUMB
        (0.44, 0.56, -0.01), (0.43, 0.44, -0.01), (0.43, 0.36, -0.01), (0.43, 0.28 + spread_y, -0.01),  # 5..8: INDEX
        (0.50, 0.54, 0.00), (0.50, 0.42, 0.00), (0.50, 0.33, 0.00), (0.50, 0.25 - spread_y, 0.00),  # 9..12: MIDDLE
        (0.56, 0.56, 0.01), (0.57, 0.45, 0.01), (0.57, 0.37, 0.01), (0.57, 0.30, 0.01),  # 13..16: RING
        (0.62, 0.60, 0.02), (0.64, 0.50, 0.02), (0.65, 0.43, 0.02), (0.66, 0.36, 0.02),  # 17..20: PINKY
    ]
    if pinch_target is not None:
        tx, ty, tz = pts[pinch_target]
        pts[4] = (tx + 0.004, ty + 0.004, tz)
    return pts


def test_extract_spatial_features_dimensions_and_invariance():
    hand = _synthetic_hand()
    feats = extract_spatial_features(hand)
    assert feats is not None
    assert feats.shape == (SPATIAL_FEATURES,)
    assert feats.dtype == np.float32
    assert np.isfinite(feats).all()

    # Translation invariance: shifting all points by (+0.25, -0.15, +0.05) produces identical features
    shifted = [(x + 0.25, y - 0.15, z + 0.05) for x, y, z in hand]
    feats_shifted = extract_spatial_features(shifted)
    assert feats_shifted is not None
    assert np.allclose(feats, feats_shifted, atol=1e-5)

    # Degenerate or invalid inputs return None
    assert extract_spatial_features(None) is None
    assert extract_spatial_features([(0.5, 0.5, 0.0)] * 21) is None


def test_spatial_mlp_trains_and_separates_pinch_classes(tmp_path: Path):
    poses = {
        "none": _synthetic_hand(None),
        "thumb_index": _synthetic_hand(8),
        "thumb_middle": _synthetic_hand(12),
        "thumb_ring": _synthetic_hand(16),
        "thumb_pinky": _synthetic_hand(20),
    }
    xs, ys = [], []
    rng = np.random.default_rng(123)
    for cls_name, base_pts in poses.items():
        c_idx = STUDIO_CLASSES.index(cls_name)
        for _ in range(12):
            noisy = [(x + rng.normal(0, 0.002), y + rng.normal(0, 0.002), z) for x, y, z in base_pts]
            f = extract_spatial_features(noisy)
            assert f is not None
            xs.append(f)
            ys.append(c_idx)

    model, metrics = train_spatial_mlp(np.stack(xs), np.array(ys), classes=STUDIO_CLASSES, epochs=50)
    assert metrics.train_accuracy >= 95.0
    assert metrics.test_accuracy >= 90.0
    assert metrics.elapsed_ms < 5000.0

    saved_path = model.save(tmp_path / "spatial_mlp.json")
    loaded = SpatialMlpModel.load(saved_path)
    for cls_name, base_pts in poses.items():
        f = extract_spatial_features(base_pts)
        assert f is not None
        pred = loaded.predict(f)
        assert pred["action"] == cls_name
        assert pred["confidence"] > 0.6


def test_pose_snapshot_studio_stream_discrepancy_and_training(tmp_path: Path):
    assert len(STUDIO_ACTIONS) == 9
    assert len(ACTION_IDS) == 9

    studio = make_pose_studio(tmp_path)
    studio.stream_throttle_ms = 0.0  # disable throttle for fast unit test
    studio.set_ground_truth("thumb_ring")
    studio.set_discrepancy_only(True)
    studio.start_stream()

    hand_ring = _synthetic_hand(16)
    hand_middle = _synthetic_hand(12)

    # 1. When system_action matches ground_truth ("thumb_ring") and discrepancy_only is True -> skipped!
    res_skip = studio.observe_frame(hand_ring, None, "thumb_ring", now_ms=100.0)
    assert res_skip is not None and not res_skip["success"]
    assert res_skip["reason"] == "match_skipped_discrepancy_only"
    assert len(studio.samples) == 0

    # 2. When system_action ("none") disagrees with ground_truth ("thumb_ring") -> captured automatically!
    for i in range(6):
        res_cap = studio.observe_frame(hand_ring, None, "none", now_ms=200.0 + i * 60.0)
        assert res_cap is not None and res_cap["success"]

    # 3. Also capture 6 samples for "thumb_middle" in normal stream mode
    studio.set_discrepancy_only(False)
    studio.set_ground_truth("thumb_middle")
    for i in range(6):
        res_cap = studio.observe_frame(hand_middle, None, "thumb_middle", now_ms=600.0 + i * 60.0)
        assert res_cap is not None and res_cap["success"]

    assert len(studio.samples) == 12
    assert studio.undo_last() is True
    assert len(studio.samples) == 11

    # 4. Instant 4-second training saves JSON dataset + trains Spatial MLP + hot-loads live AI prediction
    train_res = studio.train_now()
    assert train_res["success"] is True
    assert studio.model_summary["loaded"] is True
    assert train_res["total_samples"] == 11

    # Live frame observation now populates live_ai_action from the newly trained Spatial MLP!
    studio.stop_stream()
    studio.observe_frame(hand_ring, None, "none", now_ms=2000.0)
    assert studio.live_ai_action == "thumb_ring"
    assert studio.live_ai_confidence > 0.5
    assert normalize_system_action(None, ("mouse.right_click",)) == "thumb_ring"
