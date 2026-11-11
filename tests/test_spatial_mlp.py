from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from src.gesture_gate import GestureGate
from src.spatial_features import SPATIAL_FEATURES, SPATIAL_FEATURE_VERSION, extract_spatial_features, session_spatial_features
from src.spatial_mlp import SpatialMlpModel, blend_tcn_and_spatial_probs, train_spatial_mlp


def _synthetic_hand(pinch_target: int | None = None):
    """Build 21 anatomically plausible 3D landmarks (`[x, y, z]`) with optional thumb pinch."""
    pts = np.zeros((21, 3), dtype=np.float64)
    # Wrist at (0.5, 0.8, 0.0), Middle MCP at (0.5, 0.55, 0.0)
    pts[0] = [0.50, 0.80, 0.00]
    base_x = {1: 0.38, 5: 0.43, 9: 0.50, 13: 0.56, 17: 0.62}
    # Thumb chain 1..4
    for k, ratio in enumerate((0.25, 0.50, 0.75, 1.0), start=1):
        pts[k] = [0.38 - 0.03 * ratio, 0.72 - 0.14 * ratio, -0.02 * ratio]
    # Four fingers (5..8, 9..12, 13..16, 17..20)
    for mcp in (5, 9, 13, 17):
        bx = base_x[mcp]
        pts[mcp] = [bx, 0.56, 0.0]
        pts[mcp + 1] = [bx, 0.46, -0.01]
        pts[mcp + 2] = [bx, 0.39, -0.015]
        pts[mcp + 3] = [bx, 0.32, -0.02]
    if pinch_target is not None:
        # Bring thumb tip (4) and target fingertip together
        mid = 0.5 * (pts[4] + pts[pinch_target])
        pts[4] = mid.copy()
        pts[pinch_target] = mid.copy() + np.array([0.002, 0.002, 0.0])
    landmarks = [SimpleNamespace(x=float(p[0]), y=float(p[1]), z=float(p[2])) for p in pts]
    world = [[float(p[0] - 0.5), float(p[1] - 0.6), float(p[2])] for p in pts]
    return SimpleNamespace(landmarks=landmarks, world=world), pts


def test_extract_spatial_features_96_dims_and_pinch_sensitivity():
    hand_open, pts_open = _synthetic_hand(None)
    hand_idx, pts_idx = _synthetic_hand(8)
    hand_ring, pts_ring = _synthetic_hand(16)

    f_open = extract_spatial_features(hand_open.landmarks)
    f_idx = extract_spatial_features(pts_idx.tolist())
    f_ring = extract_spatial_features([{"x": p[0], "y": p[1], "z": p[2]} for p in pts_ring])

    assert f_open is not None and f_open.shape == (SPATIAL_FEATURES,)
    assert f_idx is not None and f_idx.shape == (96,)
    assert f_ring is not None and f_ring.shape == (96,)
    assert np.isfinite(f_open).all()

    # Feature 68 is thumb-index 3D pinch distance; Feature 70 is thumb-ring 3D pinch distance
    assert f_idx[68] < f_open[68] * 0.25
    assert f_ring[70] < f_open[70] * 0.25

    batch, valid = session_spatial_features([{"lm": pts_open.tolist()}, {"lm": None}])
    assert batch.shape == (2, 96)
    assert valid.tolist() == [True, False]


def test_train_save_load_spatial_mlp_and_predict(tmp_path):
    _, pts_open = _synthetic_hand(None)
    _, pts_idx = _synthetic_hand(8)
    _, pts_ring = _synthetic_hand(16)

    rng = np.random.default_rng(7)
    xs, ys = [], []
    for cls_id, base_pts in enumerate((pts_open, pts_idx, pts_ring)):
        for _ in range(30):
            noisy = base_pts + rng.normal(0.0, 0.004, size=base_pts.shape)
            vec = extract_spatial_features(noisy)
            assert vec is not None
            xs.append(vec)
            ys.append(cls_id)

    x_arr = np.stack(xs)
    y_arr = np.array(ys, dtype=np.int64)
    classes = ("none", "thumb_index", "thumb_ring")

    model, metrics = train_spatial_mlp(x_arr, y_arr, classes=classes, epochs=35, seed=7)
    assert metrics.train_accuracy >= 95.0
    assert metrics.test_accuracy >= 90.0
    assert model.feat_dim == SPATIAL_FEATURES
    assert model.feature_version == SPATIAL_FEATURE_VERSION

    model_gpu, metrics_gpu = train_spatial_mlp(x_arr, y_arr, classes=classes, epochs=35, seed=7, device="gpu")
    assert metrics_gpu.train_accuracy >= 95.0
    assert metrics_gpu.test_accuracy >= 90.0

    out_file = model.save(tmp_path / "spatial_mlp.json")
    reloaded = SpatialMlpModel.load(out_file)
    assert set(reloaded.specialists.keys()) == {"thumb_index", "thumb_ring"}
    assert reloaded.specialists["thumb_ring"].classes == ("none", "thumb_ring")
    pred = reloaded.predict(extract_spatial_features(pts_ring))
    assert pred["action"] == "thumb_ring"
    assert pred["confidence"] > 0.7


def test_gesture_gate_spatial_mlp_zero_lag_veto_and_fast_boost():
    _, pts_open = _synthetic_hand(None)
    _, pts_ring = _synthetic_hand(16)
    rng = np.random.default_rng(11)

    xs, ys = [], []
    for cls_id, base_pts in enumerate((pts_open, pts_ring)):
        for _ in range(25):
            vec = extract_spatial_features(base_pts + rng.normal(0.0, 0.003, size=base_pts.shape))
            xs.append(vec)
            ys.append(cls_id)
    sp_model, _ = train_spatial_mlp(
        np.stack(xs),
        np.array(ys, dtype=np.int64),
        classes=("none", "thumb_ring"),
        epochs=35,
    )

    info = {"classes": ["none", "thumb_ring"], "window": 15, "threshold": 0.7, "frames_in_a_row": 3}

    # Case 1: TCN falsely outputs 0.75 for thumb_ring on a single frame while hand is actually open ('none').
    # Spatial MLP sees 'none' (>= 0.80) with 0-frame lag and vetoes the false click.
    gate_veto = GestureGate(lambda _w: np.array([0.25, 0.75]), {**info, "frames_in_a_row": 1}, spatial_model=sp_model)
    hand_open, _ = _synthetic_hand(None)
    gate_veto.step(0.0, hand_open, 0.5625)
    res_veto = gate_veto.filter(0.0, SimpleNamespace(action="right_click", is_moving=False))
    assert res_veto.action == "none"
    assert gate_veto.spatial_vetoed >= 1

    # Case 2: TCN is still warming up (0.50 < 0.70 threshold, and only 1 frame instead of k=3),
    # but Spatial MLP is >0.85 confident on frame 1 that the hand is pinching thumb_ring -> instant 0-lag confirmation!
    gate_boost = GestureGate(lambda _w: np.array([0.50, 0.50]), info, spatial_model=sp_model)
    hand_ring, _ = _synthetic_hand(16)
    gate_boost.step(0.0, hand_ring, 0.5625)
    res_boost = gate_boost.filter(0.0, SimpleNamespace(action="right_click", is_moving=False))
    assert res_boost.action == "right_click"
    assert gate_boost.spatial_boosted >= 1


def test_blend_tcn_and_spatial_probs_batch_and_single():
    classes = ["none", "thumb_index", "thumb_ring"]
    tcn_p = np.array([0.40, 0.05, 0.55])
    sp_p = np.array([0.02, 0.02, 0.96])
    blended = blend_tcn_and_spatial_probs(tcn_p, classes, sp_p, classes)
    assert blended.shape == (3,)
    assert np.isclose(blended.sum(), 1.0)
    assert blended[2] > tcn_p[2]


def test_extract_record_handles_numpy_array_landmarks():
    from pathlib import Path
    from src.capture_dataset import _extract_record

    _, lm_pts = _synthetic_hand(8)
    arr = np.asarray(lm_pts, dtype=np.float32)
    frames = [
        {"t": 0.0, "lm": arr, "world": arr.copy(), "label": {"phase": "do", "target": "thumb_index"}},
        {"t": 0.033, "lm": arr.copy(), "world": arr.copy(), "label": {"phase": "do", "target": "thumb_index"}},
    ]
    rec = _extract_record(Path("synthetic.jsonl.gz"), {"posture": 0, "complete": True}, frames, slim_frames=True)
    assert rec["spatial_features"].shape == (2, SPATIAL_FEATURES)
    assert np.any(rec["spatial_features"][0] != 0.0)


def test_stage1_group_leave_out_prevents_test_group_and_specialist_leakage():
    """Verify group leave-out holds out entire posture groups from both base MLP and specialists,
    and reports random_test_accuracy + leakage_delta_pct side-by-side."""
    from src.spatial_mlp import _resolve_train_test_masks

    _, pts_open = _synthetic_hand(None)
    _, pts_idx = _synthetic_hand(8)
    _, pts_mid = _synthetic_hand(12)
    rng = np.random.default_rng(123)

    xs, ys, gs = [], [], []
    for group_id in (0, 1, 2):
        # Group 2 has slightly shifted posture offset to simulate an unseen camera/posture group
        group_offset = np.array([0.04, -0.03, 0.02], dtype=np.float64) if group_id == 2 else 0.0
        for cls_id, base_pts in enumerate((pts_open, pts_idx, pts_mid)):
            for _ in range(15):
                noisy = base_pts + group_offset + rng.normal(0.0, 0.004, size=base_pts.shape)
                xs.append(extract_spatial_features(noisy))
                ys.append(cls_id)
                gs.append(group_id)

    x_arr = np.stack(xs)
    y_arr = np.array(ys, dtype=np.int64)
    g_arr = np.array(gs, dtype=np.int64)

    tr_mask, te_mask, mode = _resolve_train_test_masks(y_arr, groups=g_arr, split_mode="group_leave_out")
    assert mode == "group_leave_out"
    assert not np.any(tr_mask & te_mask)
    # Group 2 should be completely held out into test_mask
    assert np.all(te_mask[g_arr == 2])
    assert not np.any(tr_mask[g_arr == 2])

    model, metrics = train_spatial_mlp(
        x_arr,
        y_arr,
        classes=("none", "thumb_index", "thumb_middle"),
        epochs=25,
        groups=g_arr,
        device="cpu",
    )
    assert metrics.split_mode == "group_leave_out"
    assert metrics.random_test_accuracy is not None
    assert metrics.leakage_delta_pct is not None
    assert "random_test_accuracy" in model.metadata
    assert "leakage_delta_pct" in model.metadata


def test_stage1_evaluation_wilson_ci_parameterized_splits_and_latency():
    """Verify Wilson 95% CI, parameterized condition/session/person/camera splits, latency, and deferred summary."""
    from src.evaluation import (
        DEFERRED_TECHNIQUES_SUMMARY,
        build_group_leave_out_folds,
        measure_session_reaction_latency,
        run_fixture_regressions,
        wilson_ci_95,
    )

    # 1. Wilson 95% CI
    lo, hi = wilson_ci_95(90, 100)
    assert 0.82 < lo < 0.90 < hi < 0.95
    assert wilson_ci_95(0, 0) == (0.0, 0.0)

    # 2. Deferred techniques one-line adoption criteria
    for key in ("Soft-DTW", "TS2Vec", "BADGE", "K-Center Greedy"):
        assert key in DEFERRED_TECHNIQUES_SUMMARY
        assert len(DEFERRED_TECHNIQUES_SUMMARY[key]) > 20

    # 3. Parameterized splits: single-user/single-camera reports degenerate=True, multi-user/camera activates folds
    s1 = {
        "path": "s1.jsonl.gz",
        "meta": {"person_id": "alice", "camera": {"id": "cam_a"}},
        "cond": np.array([0, 0, 1, 1]),
        "y": np.array([0, 1, 0, 1]),
        "frames": [{}, {}, {}, {}],
    }
    s2 = {
        "path": "s2.jsonl.gz",
        "meta": {"person_id": "alice", "camera": {"id": "cam_a"}},
        "cond": np.array([0, 0, 1, 1]),
        "y": np.array([0, 1, 0, 1]),
        "frames": [{}, {}, {}, {}],
    }
    single_person_plan = build_group_leave_out_folds([s1, s2], split_mode="person")
    assert single_person_plan["degenerate"] is True
    assert single_person_plan["num_folds"] == 0

    s3 = {
        "path": "s3.jsonl.gz",
        "meta": {"person_id": "bob", "camera": {"id": "cam_b"}},
        "cond": np.array([0, 0, 1, 1]),
        "y": np.array([0, 1, 0, 1]),
        "frames": [{}, {}, {}, {}],
    }
    multi_person_plan = build_group_leave_out_folds([s1, s3], split_mode="person")
    assert multi_person_plan["degenerate"] is False
    assert multi_person_plan["num_folds"] == 2
    assert {f["fold_id"] for f in multi_person_plan["folds"]} == {"alice", "bob"}

    multi_cam_plan = build_group_leave_out_folds([s1, s3], split_mode="camera")
    assert multi_cam_plan["degenerate"] is False
    assert multi_cam_plan["num_folds"] == 2
    assert {f["fold_id"] for f in multi_cam_plan["folds"]} == {"cam_a", "cam_b"}

    # 4. Reaction latency measurement (first contact -> approval in ms and frames)
    frames = [
        {"t": 1.000, "label": {"task": "thumb_index", "phase": "rest", "set": 1, "rep": 1}},
        {"t": 1.033, "label": {"task": "thumb_index", "phase": "do", "set": 1, "rep": 1}},  # first contact (idx 1)
        {"t": 1.066, "label": {"task": "thumb_index", "phase": "do", "set": 1, "rep": 1}},  # contact frame 2 (idx 2)
        {"t": 1.100, "label": {"task": "thumb_index", "phase": "do", "set": 1, "rep": 1}},  # contact frame 3 -> k=3 approval (idx 3)
        {"t": 1.133, "label": {"task": "thumb_index", "phase": "rest", "set": 1, "rep": 1}},
    ]
    session = {
        "frames": frames,
        "classes": ["none", "thumb_index"],
        "y": np.array([0, 1, 1, 1, 0], dtype=np.int64),
        "cond": np.array([0, 0, 0, 0, 0], dtype=np.int64),
        "segments": np.array([0, 0, 0, 0, 0], dtype=np.int64),
    }
    probs = np.array([
        [0.95, 0.05],
        [0.10, 0.90],
        [0.10, 0.90],
        [0.10, 0.90],
        [0.95, 0.05],
    ], dtype=np.float64)
    lat = measure_session_reaction_latency(session, [0], prob=probs, thr=0.7, k=3)
    assert "thumb_index" in lat
    assert lat["thumb_index"]["count"] == 1
    assert np.isclose(lat["thumb_index"]["mean_ms"], 67.0, atol=0.2)
    assert lat["thumb_index"]["mean_frames"] == 3.0

    # 5. Fixture regression replay
    fx_res = run_fixture_regressions()
    assert fx_res["all_passed"] is True
    assert fx_res["passed_fixtures"] == 3


def test_stage2_3_quality_score_gate_and_weak_labels():
    """Verify Stage 2 per-frame input quality score, GestureGate quality withholding, and Stage 3 weak-label preservation."""
    from types import SimpleNamespace
    from src.data_quality import compute_frame_quality_score, compute_sequence_quality_scores
    from src.gesture_gate import GestureGate

    _, base_pts = _synthetic_hand(8)
    # 1. Normal frame inside screen bounds with small motion has high quality (~1.0)
    fq_ok = compute_frame_quality_score(base_pts, base_pts + 0.002, dt=0.033, confidence=0.98)
    assert fq_ok.score >= 0.90
    assert fq_ok.reason == "ok"

    # 2. Sudden joint jump + scale spike + low confidence right before tracking loss has low quality (<0.45)
    jumped_pts = base_pts * 1.45 + np.array([0.16, -0.14, 0.0])
    fq_bad = compute_frame_quality_score(
        jumped_pts,
        base_pts,
        dt=0.033,
        confidence=0.60,
        near_tracking_loss=True,
    )
    assert fq_bad.score < 0.45
    assert fq_bad.reason in ("joint_jump", "scale_spike", "low_confidence", "near_tracking_loss")

    # 3. Sequence quality scores downweight frames within +/- 2 frames of a missing-hand dropout
    seq = [
        {"t": 0.00, "lm": base_pts.tolist(), "score": 0.98},
        {"t": 0.03, "lm": base_pts.tolist(), "score": 0.98},
        {"t": 0.06, "lm": None, "score": 0.0},
        {"t": 0.10, "lm": base_pts.tolist(), "score": 0.98},
        {"t": 0.13, "lm": base_pts.tolist(), "score": 0.98},
        {"t": 0.16, "lm": base_pts.tolist(), "score": 0.98},
        {"t": 0.20, "lm": base_pts.tolist(), "score": 0.98},
    ]
    q_scores, s_weights, q_details = compute_sequence_quality_scores(seq, loss_window=2)
    assert q_scores[2] == 0.0 and s_weights[2] == 0.0
    assert q_details[1].near_tracking_loss is True and s_weights[1] <= 0.25
    assert q_details[3].near_tracking_loss is True and s_weights[3] <= 0.25
    assert q_details[6].near_tracking_loss is False and s_weights[6] >= 0.90

    # 4. GestureGate withholds click/scroll approval when frame quality is below quality_threshold
    classes = ["none", "thumb_index", "thumb_ring"]
    gate = GestureGate(
        predict=lambda w: np.array([0.05, 0.05, 0.90]),
        info={"classes": classes, "window": 15, "threshold": 0.7, "frames_in_a_row": 1},
        config={"gate_quality_threshold": 0.45, "quality_gate_enabled": True},
        spatial_model=None,
    )
    gate._probs.append((1.0, np.array([0.05, 0.05, 0.90])))
    gate.last_quality_score = 0.25
    gate.last_quality_reason = "joint_jump"
    res = SimpleNamespace(action="right_click", is_moving=True, active_gesture="thumb_ring_pinch")
    filtered = gate.filter(1.0, res)
    assert filtered.action == "none"
    assert gate.quality_withheld == 1
    assert "품질 보류" in gate.last_note


def test_stage6_augmentation_ablation_defaults_and_invariants():
    """Verify Stage 6 augmentation defaults (only winning augmentations enabled) and 96D feature recomputation."""
    from src.offline_augment import STAGE6_AUGMENTATION_DEFAULTS, augment_spatial_features_96, recompute_spatial_96

    # Winning augmentations enabled, harmful augmentations disabled by default
    assert STAGE6_AUGMENTATION_DEFAULTS["perspective_fov"] is True
    assert STAGE6_AUGMENTATION_DEFAULTS["z_noise"] is True
    assert STAGE6_AUGMENTATION_DEFAULTS["finger_length_ratio"] is True
    assert STAGE6_AUGMENTATION_DEFAULTS["temporal_speed_warp"] is True
    assert STAGE6_AUGMENTATION_DEFAULTS["hand_scale"] is False
    assert STAGE6_AUGMENTATION_DEFAULTS["horizontal_flip"] is False

    # Recomputing 96D spatial features from landmarks matches extract_spatial_features
    _, pts = _synthetic_hand(8)
    feat_single = extract_spatial_features(pts)
    feat_batch = recompute_spatial_96(pts[None, :, :])[0]
    assert np.allclose(feat_single, feat_batch, atol=1e-5)

    # Augmenting a batch of 96D features preserves shape, finite values, and active pinch contact
    batch_x = np.repeat(feat_single[None, :], 20, axis=0)
    aug_x = augment_spatial_features_96(batch_x, seed=42, prob=1.0)
    assert aug_x.shape == (20, 96)
    assert np.all(np.isfinite(aug_x))
    assert not np.allclose(batch_x, aug_x)
    # Index pinch distance (dim 68) stays close because pinch contact offset is preserved during finger ratio scaling
    assert np.max(aug_x[:, 68]) < 0.25


def test_stage4_5_supcon_prototypes_frozen_encoder_and_ood_veto():
    """Verify Stage 4 SupCon encoder, class prototypes, frozen encoder flag, and Stage 5 Mahalanobis OOD veto."""
    from types import SimpleNamespace
    from src.gesture_gate import GestureGate

    classes = ("none", "thumb_index", "thumb_ring")
    rng = np.random.default_rng(123)
    _, neutral_pts = _synthetic_hand(None)
    _, index_pts = _synthetic_hand(8)
    _, ring_pts = _synthetic_hand(16)
    f0 = extract_spatial_features(neutral_pts)
    f1 = extract_spatial_features(index_pts)
    f2 = extract_spatial_features(ring_pts)

    xs, ys, gs = [], [], []
    for g_id in range(3):
        for _ in range(15):
            xs.append(f0 + rng.normal(0.0, 0.015, size=f0.shape))
            ys.append(0)
            gs.append(g_id)
            xs.append(f1 + rng.normal(0.0, 0.015, size=f1.shape))
            ys.append(1)
            gs.append(g_id)
            xs.append(f2 + rng.normal(0.0, 0.015, size=f2.shape))
            ys.append(2)
            gs.append(g_id)

    model, _ = train_spatial_mlp(
        np.asarray(xs),
        np.asarray(ys),
        classes=classes,
        groups=np.asarray(gs),
        hidden_units=32,
        epochs=25,
        seed=42,
        supcon_weight=0.22,
    )
    assert model.encoder_frozen is True
    assert model.prototypes is not None and model.prototypes.shape == (3, 32)
    assert "thumb_index" in model.ood_precisions and "thumb_ring" in model.ood_precisions
    assert "thumb_index" in model.ood_thresholds and "thumb_ring" in model.ood_thresholds

    # Prototype similarity and probability work in pure NumPy
    sims = model.prototype_similarity(f1)
    proto_p = model.predict_prototype_proba(f1)
    assert sims.shape == (3,)
    assert proto_p.shape == (3,) and np.isclose(np.sum(proto_p), 1.0)
    assert int(np.argmax(proto_p)) == 1

    # Roundtrip serialization preserves prototypes, OOD stats, and encoder_frozen
    reloaded = SpatialMlpModel.from_dict(model.to_dict())
    assert reloaded.encoder_frozen is True
    assert np.allclose(reloaded.prototypes, model.prototypes, atol=1e-4)

    # In-distribution sample is not OOD; far out-of-distribution sample is flagged OOD and vetoed by GestureGate
    id_ood = reloaded.ood_score(f2, label="thumb_ring", ratio_limit=1.35)
    assert id_ood["is_ood"] is False

    far_ood_vec = f2.copy()
    far_ood_vec[:63] += 3.5  # Extreme unregistered hand geometry
    ood_res = reloaded.ood_score(far_ood_vec, label="thumb_ring", ratio_limit=1.35)
    assert ood_res["is_ood"] is True
    assert ood_res["ratio"] > 1.35

    gate = GestureGate(
        predict=lambda w: np.array([0.10, 0.05, 0.85]),
        info={"classes": list(classes), "window": 15, "threshold": 0.7, "frames_in_a_row": 1},
        config={"ood_gate_enabled": True, "ood_ratio_limit": 1.35},
        spatial_model=reloaded,
    )
    gate._probs.append((1.0, np.array([0.10, 0.05, 0.85])))
    gate.last_tcn = np.array([0.10, 0.05, 0.85])
    gate.last_spatial = np.array([0.20, 0.10, 0.70])
    gate.last_spatial_vec = far_ood_vec
    res = SimpleNamespace(action="right_click", is_moving=True, active_gesture="thumb_ring_pinch")
    filtered = gate.filter(1.0, res)
    assert filtered.action == "none"
    assert gate.ood_vetoed == 1
    assert gate.last_rejection_reason.startswith("ood_mahalanobis:")


def test_stage7_policy_temperature_calibration_and_negative_candidates(tmp_path):
    """Verify Stage 7 temperature scaling ECE reduction, risk-differentiated per-gesture policies,
    calibrated confidence & rejection reason UI status, and verifiable negative-candidate logging."""
    from src.failure_log import FailureRecorder
    from src.gesture_gate import (
        DEFAULT_GESTURE_FRAMES,
        DEFAULT_GESTURE_HOLD_SECONDS,
        DEFAULT_GESTURE_OOD_LIMITS,
        DEFAULT_GESTURE_THRESHOLDS,
        GestureGate,
    )
    from src.spatial_mlp import (
        apply_temperature_scaling,
        compute_calibration_metrics,
        fit_temperature_scaling,
    )

    # 1. Temperature scaling preserves argmax while reducing ECE on overconfident predictions
    rng = np.random.default_rng(77)
    n = 300
    y_true = rng.integers(0, 3, size=n)
    raw_logits = rng.normal(0.0, 1.0, size=(n, 3))
    raw_logits[np.arange(n), y_true] += 1.25  # ~70% accuracy
    # Make probabilities artificially overconfident (sharp temperature)
    sharp_logits = raw_logits * 2.5
    sharp_logits -= np.max(sharp_logits, axis=1, keepdims=True)
    probs = np.exp(sharp_logits) / np.sum(np.exp(sharp_logits), axis=1, keepdims=True)

    best_t, cal_info = fit_temperature_scaling(probs, y_true)
    cal_probs = apply_temperature_scaling(probs, best_t)
    assert best_t > 1.0
    assert np.array_equal(np.argmax(probs, axis=1), np.argmax(cal_probs, axis=1))
    assert cal_info["after"]["ece"] < cal_info["before"]["ece"]
    assert cal_info["ece_reduction_pct"] > 0.0
    metrics_after = compute_calibration_metrics(cal_probs, y_true)
    assert np.isclose(metrics_after["ece"], cal_info["after"]["ece"], atol=1e-5)

    # 2. Risk-differentiated per-gesture policies: low-risk vs high-risk hold duration & rejection reason
    assert DEFAULT_GESTURE_THRESHOLDS["thumb_ring"] < DEFAULT_GESTURE_THRESHOLDS["rock_sign"]
    assert DEFAULT_GESTURE_FRAMES["thumb_ring"] < DEFAULT_GESTURE_FRAMES["rock_sign"]
    assert DEFAULT_GESTURE_OOD_LIMITS["palm_side"] < DEFAULT_GESTURE_OOD_LIMITS["thumb_index"]
    assert DEFAULT_GESTURE_HOLD_SECONDS["thumb_ring"] == 0.0
    assert DEFAULT_GESTURE_HOLD_SECONDS["palm_side"] >= 0.10

    classes = ["none", "thumb_ring", "palm_side"]
    gate = GestureGate(
        predict=lambda w: np.array([0.05, 0.05, 0.90]),
        info={"classes": classes, "window": 15, "threshold": 0.7, "frames_in_a_row": 3},
        spatial_model=None,
    )
    # High-risk `palm_side` requires k=4 frames AND hold >= 0.10s
    for idx in range(4):
        # 4 frames spaced only 0.01s apart (total 0.03s < 0.10s hold requirement)
        gate._probs.append((1.0 + idx * 0.01, np.array([0.05, 0.05, 0.90])))
    gate.last = np.array([0.05, 0.05, 0.90])
    assert gate.confirmed("palm_side", 1.03) is False
    assert gate.last_rejection_reason.startswith("hold_duration:")
    st = gate.status_summary()
    assert st["calibrated_confidence"] > 0.80
    assert "유지 시간 미달" in st["rejection_reason_ko"]

    # Once held for >= 0.10s across 4 frames, `palm_side` is confirmed
    gate._probs.clear()
    for idx in range(4):
        gate._probs.append((2.0 + idx * 0.04, np.array([0.05, 0.05, 0.90])))
    assert gate.confirmed("palm_side", 2.12) is True
    assert gate.last_rejection_reason == ""

    # 3. Verifiable negative candidate label logging (never auto-retrained)
    rec = FailureRecorder(tmp_path / "failures")
    pts = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]
    rec.record(10.0, landmarks=pts, score=0.92, moving=True, action="show_desktop")
    out_path = rec.record_negative_candidate(
        10.1,
        source="f_key",
        suspected_gesture="palm_side",
        calibrated_confidence=0.88,
        note="Accidental show_desktop marked with F key",
    )
    assert out_path is not None and out_path.is_file()
    candidates = rec.load_negative_candidates()
    assert len(candidates) == 1
    assert candidates[0]["suspected_action"] == "show_desktop"
    assert candidates[0]["suspected_gesture"] == "palm_side"
    assert candidates[0]["candidate_target_label"] == "none"
    assert candidates[0]["verified"] is False
    assert candidates[0]["auto_retrain"] is False


def test_stage8_onboarding_custom_registration_and_runtime_ema(tmp_path):
    """Verify Stage 8-1 onboarding (hand size/lighting warnings + blended user prototypes + profile persistence),
    Stage 8-2 3-5 rep custom gesture registration (proximity warning + 30s neutral false-fire auto-thresholding),
    and Stage 8-3 shadow mode + bounded EMA prototype adaptation with rollback."""
    from src.calibration import ONBOARDING_STEPS, CalibrationSession
    from src.gesture_gate import GestureGate
    from src.profiles import apply_profile_adaptation_to_model, save_profile_adaptation

    classes = ("none", "thumb_index", "thumb_ring")
    rng = np.random.default_rng(2026)
    _, neutral_pts = _synthetic_hand(None)
    _, index_pts = _synthetic_hand(8)
    _, ring_pts = _synthetic_hand(16)
    _, pinky_pts = _synthetic_hand(20)
    f0 = extract_spatial_features(neutral_pts)
    f1 = extract_spatial_features(index_pts)
    f2 = extract_spatial_features(ring_pts)
    f_custom = extract_spatial_features(pinky_pts)

    xs, ys, gs = [], [], []
    for g_id in range(3):
        for _ in range(18):
            xs.append(f0 + rng.normal(0.0, 0.015, size=f0.shape))
            ys.append(0)
            gs.append(g_id)
            xs.append(f1 + rng.normal(0.0, 0.015, size=f1.shape))
            ys.append(1)
            gs.append(g_id)
            xs.append(f2 + rng.normal(0.0, 0.015, size=f2.shape))
            ys.append(2)
            gs.append(g_id)

    model, _ = train_spatial_mlp(
        np.asarray(xs),
        np.asarray(ys),
        classes=classes,
        groups=np.asarray(gs),
        hidden_units=32,
        epochs=25,
        seed=42,
        supcon_weight=0.22,
    )
    w1_orig = model.w1.copy()
    b1_orig = model.b1.copy()

    # 1. Stage 8-1: Onboarding steps include 30s neutral movement and hand size / lighting warnings
    step_keys = [s.key for s in ONBOARDING_STEPS]
    assert "open_hand" in step_keys and "neutral_move_30s" in step_keys
    sess = CalibrationSession(steps=ONBOARDING_STEPS[:1])
    tiny_lm = [SimpleNamespace(x=0.50 + 0.01 * (i % 3), y=0.50 + 0.01 * (i // 3), z=0.0) for i in range(21)]
    sess.feed(0.0, tiny_lm, hand_score=0.55)
    for k in range(1, 20):
        sess.feed(1.5 + k * 0.4, tiny_lm, hand_score=0.55)
    cal_res = sess.finish({}, spatial_model=model)
    assert cal_res.hand_size_status == "too_small"
    assert cal_res.lighting_status == "low_light_or_unstable"

    # Blended user onboarding prototypes keep encoder strictly frozen (`w1`, `b1` unchanged)
    user_f1 = np.repeat(f1[None, :], 12, axis=0) + rng.normal(0.0, 0.01, size=(12, f1.shape[0]))
    user_f0 = np.repeat(f0[None, :], 30, axis=0) + rng.normal(0.0, 0.01, size=(30, f0.shape[0]))
    onboard_res = model.apply_user_onboarding_prototypes(
        {"thumb_index": user_f1},
        neutral_features=user_f0,
        blend_beta=0.35,
    )
    assert onboard_res["encoder_frozen"] is True
    assert np.allclose(model.w1, w1_orig) and np.allclose(model.b1, b1_orig)
    assert "thumb_index" in onboard_res["updated_classes"]

    # Profile save & reload overlays onboarding prototypes and custom gestures onto a fresh model
    fresh_model = SpatialMlpModel.from_dict(model.to_dict())
    fresh_model.rollback_prototypes()

    # 2. Stage 8-2: Custom gesture registration from 3 reps on frozen encoder
    custom_reps = [
        np.repeat(f_custom[None, :], 8, axis=0) + rng.normal(0.0, 0.008, size=(8, f_custom.shape[0]))
        for _ in range(3)
    ]
    neutral_30s = np.repeat(f0[None, :], 120, axis=0) + rng.normal(0.0, 0.012, size=(120, f0.shape[0]))
    reg_info = model.register_custom_gesture(
        "thumb_pinky_custom",
        custom_reps,
        neutral_features=neutral_30s,
        min_reps=3,
        max_reps=5,
    )
    assert reg_info["label"] == "thumb_pinky_custom"
    assert reg_info["reps_used"] == 3
    assert reg_info["neutral_misfires_after_tuning"] == 0
    assert "thumb_pinky_custom" in model.classes
    assert np.allclose(model.w1, w1_orig)

    # Registering a duplicate of `thumb_index` triggers prototype proximity warning
    dup_reps = [
        np.repeat(f1[None, :], 8, axis=0) + rng.normal(0.0, 0.005, size=(8, f1.shape[0]))
        for _ in range(3)
    ]
    dup_info = model.register_custom_gesture(
        "dup_index",
        dup_reps,
        neutral_features=neutral_30s,
        proximity_warn_cosine=0.80,
    )
    assert dup_info["closest_class"] == "thumb_index"
    assert dup_info["proximity_warning"] != ""

    # Persist to user profile and reload onto fresh_model
    save_profile_adaptation(
        tmp_path,
        "tester",
        onboarding_adaptation=onboard_res,
        custom_gesture=reg_info,
    )
    applied_prof = apply_profile_adaptation_to_model(tmp_path, "tester", fresh_model)
    assert applied_prof["applied"] is True
    assert "thumb_pinky_custom" in fresh_model.classes

    # GestureGate integration for custom gestures
    gate = GestureGate(
        predict=lambda w: np.array([0.80, 0.10, 0.10]),
        info={"classes": list(classes), "window": 15, "threshold": 0.7, "frames_in_a_row": 2},
        config={"runtime_adaptation_enabled": False, "shadow_adaptation_enabled": True},
        spatial_model=fresh_model,
    )
    assert "thumb_pinky_custom" in gate.classes

    # 3. Stage 8-3: Shadow mode logs without mutating prototypes; active bounded EMA updates and rolls back cleanly
    proto_before_ema = fresh_model.prototypes.copy()
    fresh_model.shadow_mode = True
    sh_res = fresh_model.adapt_prototype_ema(
        "thumb_index",
        user_f1[:6],
        calibrated_confidence=0.95,
        quality_score=0.92,
        ood_ratio=0.50,
    )
    assert sh_res["accepted"] is True and sh_res["applied"] is False and sh_res["shadow_mode"] is True
    assert np.allclose(fresh_model.prototypes, proto_before_ema)

    fresh_model.shadow_mode = False
    live_res = fresh_model.adapt_prototype_ema(
        "thumb_index",
        user_f1[:6] + 0.05,
        calibrated_confidence=0.95,
        quality_score=0.92,
        ood_ratio=0.50,
        ema_alpha=0.15,
    )
    assert live_res["accepted"] is True and live_res["applied"] is True
    assert not np.allclose(fresh_model.prototypes, proto_before_ema)

    # Cancelled or low-confidence executions are rejected
    rej_cancel = fresh_model.adapt_prototype_ema(
        "thumb_index", user_f1[:6], calibrated_confidence=0.95, quality_score=0.92, ood_ratio=0.50, user_cancelled=True
    )
    assert rej_cancel["accepted"] is False and rej_cancel["reason"] == "user_cancelled"

    # Rollback restores exact pre-EMA prototypes
    assert fresh_model.rollback_prototypes() is True
    assert np.allclose(fresh_model.prototypes, proto_before_ema)






