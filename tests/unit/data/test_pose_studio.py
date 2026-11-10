"""Unit tests for the Spatial 96-dim feature extractor, 4-second Spatial MLP trainer, and PoseSnapshotStudio."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from manux.data.pose_studio import (
    ACTION_IDS,
    PoseSnapshotStudio,
    STUDIO_ACTIONS,
    is_full_joints_tracked,
    normalize_system_action,
)
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

    from manux.data.pose_studio import pose_training_partition
    tx, ty, tw, validation = pose_training_partition(np.stack(xs), np.array(ys))
    model, metrics = train_spatial_mlp(tx, ty, tw, validation_data=validation, classes=STUDIO_CLASSES, epochs=50)
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
    assert len(STUDIO_ACTIONS) == 10
    assert len(ACTION_IDS) == 10

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


def test_is_full_joints_tracked():
    hand = _synthetic_hand()
    ok, reason = is_full_joints_tracked(hand)
    assert ok is True
    assert reason == "ok"

    # None or fewer than 21 landmarks
    assert is_full_joints_tracked(None)[0] is False
    assert is_full_joints_tracked(hand[:15])[0] is False

    # Out-of-bounds joint (e.g. index finger tip clipped at x = 1.05 or y = -0.02)
    clipped = list(hand)
    clipped[8] = (1.06, 0.28, -0.01)
    ok_c, reason_c = is_full_joints_tracked(clipped)
    assert ok_c is False
    assert "clipped_edge_8" in reason_c

    # Collapsed/tiny hand
    collapsed = [(0.5, 0.5, 0.0)] * 21
    ok_col, reason_col = is_full_joints_tracked(collapsed)
    assert ok_col is False
    assert reason_col == "collapsed_or_too_small"


def test_pose_studio_require_full_joints_filtering(tmp_path: Path):
    studio = make_pose_studio(tmp_path)
    studio.stream_throttle_ms = 0.0
    studio.set_ground_truth("thumb_middle")
    studio.set_require_full_joints(True)
    assert studio.require_full_joints is True

    hand = _synthetic_hand()
    clipped = list(hand)
    clipped[4] = (-0.05, 0.50, -0.04)  # thumb tip out of frame

    # 1. Single snapshot with clipped joint is rejected
    res = studio.capture_snapshot(landmarks=clipped)
    assert res["success"] is False
    assert "clipped_edge_4" in res["reason"]
    assert len(studio.samples) == 0

    # 2. Continuous stream ignores clipped frame
    studio.start_stream()
    res_st = studio.observe_frame(clipped, None, "none", now_ms=100.0)
    assert res_st is not None and res_st["success"] is False
    assert "partial_joints" in res_st["reason"]
    assert len(studio.samples) == 0

    # 3. Full valid hand is captured
    res_valid = studio.observe_frame(hand, None, "none", now_ms=160.0)
    assert res_valid is not None and res_valid["success"] is True
    assert len(studio.samples) == 1
    studio.stop_stream()


def test_pose_studio_multi_model_management(tmp_path: Path):
    studio = make_pose_studio(tmp_path)
    models = studio.list_models()
    assert len(models) >= 1
    assert studio.active_model_id == "spatial_pose_mlp.json"

    # 1. Create a new model and verify it immediately appears in list_models() before training
    created = studio.create_model("custom_fast_model")
    assert created["success"] is True
    assert created["id"] == "custom_fast_model.json"
    assert studio.active_model_id == "custom_fast_model.json"
    immediate_list = studio.list_models()
    slot_entry = next(m for m in immediate_list if m["id"] == "custom_fast_model.json")
    assert slot_entry["active"] is True
    assert slot_entry["exists"] is False

    # Collect 4 samples and train into custom_fast_model
    hand = _synthetic_hand(12)
    for i in range(4):
        studio.capture_snapshot(ground_truth="thumb_middle", landmarks=hand, now_ms=100.0 + i * 50)
    train_res = studio.train_now()
    assert train_res["success"] is True

    model_list = studio.list_models()
    active_entry = next(m for m in model_list if m["id"] == "custom_fast_model.json")
    assert active_entry["active"] is True
    assert active_entry["exists"] is True
    assert active_entry["total_samples"] == 4

    # 2. Create another model and switch between them — cumulative count must reset to 0 for the new model!
    studio.create_model("model_two")
    assert studio.active_model_id == "model_two.json"
    assert studio.summary()["savedTotal"] == 0
    assert studio.summary()["total"] == 0

    # Activate custom_fast_model again — its 4 saved samples are restored!
    assert studio.activate_model("custom_fast_model.json") is True
    assert studio.active_model_id == "custom_fast_model.json"
    assert studio.model_summary["loaded"] is True
    assert studio.summary()["savedTotal"] == 4
    assert studio.summary()["savedByAction"]["thumb_middle"] == 4

    # 3. Delete model
    assert studio.delete_model("model_two.json") is True
    assert not (tmp_path / "models" / "spatial_mlp" / "model_two.json").exists()




def test_eval_buffer_and_saved_eval_do_not_enter_pose_training(tmp_path):
    studio = make_pose_studio(tmp_path)
    studio.profile = "synthetic"
    hand = _synthetic_hand(16)
    studio.capture_snapshot(ground_truth="thumb_ring", landmarks=hand)
    train_id = studio.samples[0]["sampleId"]
    studio.save_dataset()
    studio.split = "eval"
    studio.capture_snapshot(ground_truth="thumb_middle", landmarks=hand)
    assert [s["sampleId"] for s in studio.load_all_samples()] == [train_id]
    studio.save_dataset()
    assert [s["sampleId"] for s in studio.load_all_samples()] == [train_id]


def test_same_millisecond_saves_preserve_training_and_eval_datasets(tmp_path, monkeypatch):
    monkeypatch.setattr("manux.data.pose_studio.time.time", lambda: 1234.567)
    studio = make_pose_studio(tmp_path)
    studio.profile = "synthetic"
    hand = _synthetic_hand(16)
    paths = []
    train_ids = []
    contents = []
    for split in ("train", "eval", "train"):
        studio.split = split
        studio.capture_snapshot(ground_truth="thumb_ring", landmarks=hand)
        if split == "train":
            train_ids.append(studio.samples[0]["sampleId"])
        paths.append(studio.save_dataset())
        contents.append(paths[-1].read_bytes())
    assert len(set(paths)) == 3
    assert all(path.read_bytes() == data for path, data in zip(paths, contents))
    assert len(list(studio.snapshots_dir.glob("*.json"))) == 3
    assert {s["sampleId"] for s in studio.load_all_samples()} == set(train_ids)


def test_failed_dataset_publication_preserves_saved_file_and_unsaved_buffer(tmp_path, monkeypatch):
    studio = make_pose_studio(tmp_path)
    hand = _synthetic_hand(16)
    studio.capture_snapshot(ground_truth="thumb_ring", landmarks=hand)
    original = studio.save_dataset()
    original_bytes = original.read_bytes()
    studio.capture_snapshot(ground_truth="thumb_middle", landmarks=hand)
    buffered = list(studio.samples)
    def fail_publication(*_):
        raise OSError("synthetic publication failure")
    import os
    publish = "rename" if os.name == "nt" else "link"
    monkeypatch.setattr(f"manux.data.pose_studio.os.{publish}", fail_publication)
    with pytest.raises(OSError, match="synthetic publication failure"):
        studio.save_dataset()
    assert studio.samples == buffered
    assert original.read_bytes() == original_bytes
    assert list(studio.snapshots_dir.iterdir()) == [original]


def test_colliding_dataset_name_never_replaces_existing_capture(tmp_path, monkeypatch):
    from uuid import UUID
    studio = make_pose_studio(tmp_path)
    monkeypatch.setattr("manux.data.pose_studio.time.time", lambda: 1234.567)
    hand = _synthetic_hand(16)
    studio.capture_snapshot(ground_truth="thumb_ring", landmarks=hand)
    original = studio.save_dataset()
    original_bytes = original.read_bytes()
    studio.capture_snapshot(ground_truth="thumb_middle", landmarks=hand)
    buffered = list(studio.samples)
    reused_id = UUID(hex=original.stem.rsplit("_", 1)[1])
    monkeypatch.setattr("manux.data.pose_studio.uuid4", lambda: reused_id)
    with pytest.raises(FileExistsError):
        studio.save_dataset()
    assert studio.samples == buffered
    assert original.read_bytes() == original_bytes
    assert list(studio.snapshots_dir.iterdir()) == [original]


def test_existing_dataset_stage_is_not_removed_when_exclusive_open_fails(tmp_path, monkeypatch):
    from uuid import UUID
    studio = make_pose_studio(tmp_path)
    studio.capture_snapshot(ground_truth="thumb_ring", landmarks=_synthetic_hand(16))
    buffered = list(studio.samples)
    monkeypatch.setattr("manux.data.pose_studio.time.time", lambda: 1234.567)
    monkeypatch.setattr("manux.data.pose_studio.uuid4", lambda: UUID(int=0))
    part = studio.snapshots_dir / ".manux_pose_snapshots_spatial_pose_mlp_train_1234567_00000000000000000000000000000000.json.part"
    part.write_bytes(b"synthetic-other-save-in-progress")
    with pytest.raises(FileExistsError):
        studio.save_dataset()
    assert studio.samples == buffered
    assert part.read_bytes() == b"synthetic-other-save-in-progress"
    assert list(studio.snapshots_dir.iterdir()) == [part]


def test_normalize_system_action_and_mlp_prediction_preserve_right_and_both_click(tmp_path: Path):
    class FakeSnap:
        def __init__(self, intents, results):
            self.intents = intents
            self.results = results
            self.events = ()

    # 1. Right drag routed to mouse.drag must still normalize to thumb_ring (not thumb_middle!)
    snap_rdrag = FakeSnap(intents=(("pinch.right_drag", "hold"),), results=(("mouse.drag", "hold", "ok"),))
    assert normalize_system_action(snap_rdrag, ("mouse.drag",)) == "thumb_ring"

    # 2. Both left and right click/drag intents normalize to thumb_middle_ring
    snap_both = FakeSnap(
        intents=(("pinch.thumb_middle", "tap"), ("pinch.thumb_ring", "tap")),
        results=(("mouse.left_click", "tap", "ok"), ("mouse.right_click", "tap", "ok")),
    )
    assert normalize_system_action(snap_both) == "thumb_middle_ring"

    # 3. Studio live_hybrid_action must preserve MLP's thumb_ring / thumb_middle_ring even if 2D rule says thumb_middle
    studio = make_pose_studio(tmp_path)
    studio.model_summary = {"loaded": True}
    studio._predict_fn = lambda lm, wd: {
        "action": "thumb_ring",
        "confidence": 0.94,
        "probabilities": {"thumb_ring": 0.94, "thumb_middle": 0.04, "none": 0.02},
    }
    studio.observe_frame(_synthetic_hand(16), None, "thumb_middle", now_ms=100.0)
    assert studio.live_ai_action == "thumb_ring"
    assert studio.live_hybrid_action == "thumb_ring"
    assert studio.effective_predicted_action() == "thumb_ring"

    studio._predict_fn = lambda lm, wd: {
        "action": "thumb_middle_ring",
        "confidence": 0.92,
        "probabilities": {"thumb_middle_ring": 0.92, "thumb_middle": 0.05, "none": 0.03},
    }
    studio.observe_frame(_synthetic_hand(16), None, "thumb_middle", now_ms=200.0)
    assert studio.live_ai_action == "thumb_middle_ring"
    assert studio.live_hybrid_action == "thumb_middle_ring"
    assert studio.effective_predicted_action() == "thumb_middle_ring"

