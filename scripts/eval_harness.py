"""Stage 1 Single-Command Evaluation & Regression Harness (`scripts/eval_harness.py`).

Replays `recordings/*.jsonl.gz` and `tests/fixtures/*.json` in a single command to verify:
1. `SpatialMlpModel` random 80/20 frame split vs. condition-group / recording-session leave-out split
   side-by-side (quantifying adjacent-frame leakage and specialist leakage).
2. Per-gesture repetition recognition rate with sample counts and 95% Wilson confidence intervals
   (for Rules, Leave-Group-Out Zero-Shot TCN+SpatialMLP, and Deployed Hybrid Pipeline).
3. Neutral-movement (`free` / half-fist moving) false fires per minute (`misfires_per_min`) and total neutral minutes.
4. Reaction latency (`first contact -> approval`) in milliseconds (`ms`) and frames (`frames`).
5. Parameterized group leave-out splitting (`condition`, `session`, `person`, `camera`) and honest
   reporting of single-user / single-camera dataset limitations.
6. Fixture replay regression checks (`tests/fixtures/*.json`) across `GestureEngine` and `GestureGate`.

Usage:
    .venv\\Scripts\\python.exe scripts/eval_harness.py [--split-mode condition|session|person|camera] [--update-spatial-model]
"""
from __future__ import annotations

import argparse
from collections import Counter
import glob
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import train_tcn
from src.evaluation import (
    DEFERRED_TECHNIQUES_SUMMARY,
    SUPPORTED_SPLIT_MODES,
    aggregate_latency_rows,
    build_group_leave_out_folds,
    dataset_diversity_report,
    run_fixture_regressions,
    wilson_ci_95,
)
from src.gesture_catalog import BY_KEY, CLASSES, TASKS
from src.gesture_gate import (
    DEFAULT_GESTURE_FRAMES,
    DEFAULT_GESTURE_HOLD_SECONDS,
    DEFAULT_GESTURE_OOD_LIMITS,
    DEFAULT_GESTURE_THRESHOLDS,
    load_predictor,
)
from src.recorder import finished_recordings
from src.spatial_mlp import (
    SpatialMlpModel,
    blend_tcn_and_spatial_probs,
    compose_spatial_mlp,
    ensure_session_spatial_features,
    fit_temperature_scaling,
    pick_spatial_from_sessions,
    train_spatial_mlp,
)
from src.training_center import filter_selected_recording_paths


def _measure_legacy_leaked_spatial_mlp(
    x_all: np.ndarray,
    y_all: np.ndarray,
    classes: Sequence[str],
    *,
    device: str = "auto",
    seed: int = 42,
) -> Dict[str, Any]:
    """Reproduce the pre-Stage-1 random 80/20 split + full-array specialist training to quantify exact leakage."""
    from src.spatial_mlp import _fit_mlp_weights, _resolve_train_test_masks, train_binary_spatial_specialist

    x_arr = np.asarray(x_all, dtype=np.float64)
    y_arr = np.asarray(y_all, dtype=np.int64)
    num_classes = len(classes)
    counts = np.bincount(y_arr, minlength=num_classes).astype(np.float64)
    cls_w = (counts.sum() / np.maximum(counts, 1.0)) ** 0.5
    cls_w = cls_w / np.maximum(cls_w.mean(), 1e-8)
    sample_weights = cls_w[y_arr]

    r_train_m, r_test_m, _ = _resolve_train_test_masks(y_arr, groups=None, split_mode="random", seed=seed)
    w1, b1, w2, b2 = _fit_mlp_weights(
        x_arr[r_train_m],
        y_arr[r_train_m],
        sample_weights[r_train_m],
        num_classes=num_classes,
        hidden_units=64,
        epochs=45,
        batch_size=128,
        lr=0.003,
        l2=1e-4,
        seed=seed,
        device=device,
    )
    # 1. Base MLP only on random 80/20 split (adjacent-frame leakage, no specialist leakage)
    base_only_model = SpatialMlpModel(w1, b1, w2, b2, classes=classes)
    base_random_preds = np.argmax(base_only_model.predict_proba(x_arr[r_test_m]), axis=1)
    base_random_acc = round(float(np.mean(base_random_preds == y_arr[r_test_m]) * 100.0), 2)

    # 2. Train-only specialists on random 80/20 split (adjacent-frame leakage, fixed specialist split)
    clean_specs: Dict[str, SpatialMlpModel] = {}
    leaked_specs: Dict[str, SpatialMlpModel] = {}
    for c_idx, cls_name in enumerate(classes):
        if cls_name == "none":
            continue
        if int(np.sum(y_arr[r_train_m] == c_idx)) < 1:
            continue
        sp_clean, _ = train_binary_spatial_specialist(
            x_arr,
            y_arr,
            classes=classes,
            target_label=cls_name,
            hidden_units=48,
            epochs=38,
            seed=seed + c_idx + 1,
            device=device,
            split_mode="random",
            precomputed_split=(r_train_m, r_test_m),
        )
        clean_specs[cls_name] = sp_clean

        # Legacy bug reproduction: specialist shuffled with `seed + c_idx + 1` on full x_arr
        sp_leaked, _ = train_binary_spatial_specialist(
            x_arr,
            y_arr,
            classes=classes,
            target_label=cls_name,
            hidden_units=48,
            epochs=38,
            seed=seed + c_idx + 1,
            device=device,
            split_mode="random",
            precomputed_split=None,
        )
        leaked_specs[cls_name] = sp_leaked

    clean_random_model = SpatialMlpModel(w1, b1, w2, b2, classes=classes, specialists=clean_specs)
    clean_random_preds = np.argmax(clean_random_model.predict_proba(x_arr[r_test_m]), axis=1)
    clean_random_acc = round(float(np.mean(clean_random_preds == y_arr[r_test_m]) * 100.0), 2)

    leaked_random_model = SpatialMlpModel(w1, b1, w2, b2, classes=classes, specialists=leaked_specs)
    leaked_random_preds = np.argmax(leaked_random_model.predict_proba(x_arr[r_test_m]), axis=1)
    leaked_random_acc = round(float(np.mean(leaked_random_preds == y_arr[r_test_m]) * 100.0), 2)

    return {
        "legacy_random_80_20_with_specialist_leak_acc": leaked_random_acc,
        "random_80_20_train_only_specialists_acc": clean_random_acc,
        "random_80_20_base_only_acc": base_random_acc,
    }


def evaluate_spatial_mlp_leave_out(
    sessions: Sequence[Dict[str, Any]],
    classes: Sequence[str],
    *,
    device: str = "auto",
    update_model_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare SpatialMlpModel random 80/20 split vs. condition-group and session leave-out splits."""
    conds = sorted({int(c) for s in sessions for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
    xs_cond, ys_cond, gs_cond, ws_cond = pick_spatial_from_sessions(
        sessions, conds, return_groups=True, return_weights=True, group_by="condition"
    )
    _, _, gs_sess = pick_spatial_from_sessions(
        sessions, conds, return_groups=True, group_by="session"
    )

    legacy_info = _measure_legacy_leaked_spatial_mlp(xs_cond, ys_cond, classes, device=device)

    # 1. Condition-group leave-out split
    cond_model, cond_metrics = train_spatial_mlp(
        xs_cond,
        ys_cond,
        weights_all=ws_cond,
        classes=classes,
        device=device,
        groups=gs_cond,
        split_mode="group_leave_out",
        compute_random_comparison=True,
    )

    # 2. Recording-session leave-out split
    _, sess_metrics = train_spatial_mlp(
        xs_cond,
        ys_cond,
        weights_all=ws_cond,
        classes=classes,
        device=device,
        groups=gs_sess,
        split_mode="group_leave_out",
        compute_random_comparison=False,
    )

    if update_model_path is not None:
        update_model_path.parent.mkdir(parents=True, exist_ok=True)
        cond_model.metadata["legacy_random_80_20_acc"] = legacy_info["legacy_random_80_20_with_specialist_leak_acc"]
        cond_model.metadata["random_test_accuracy"] = legacy_info["random_80_20_train_only_specialists_acc"]
        cond_model.metadata["session_leave_out_accuracy"] = sess_metrics.test_accuracy
        cond_model.metadata["leakage_delta_pct"] = round(
            legacy_info["legacy_random_80_20_with_specialist_leak_acc"] - cond_metrics.test_accuracy, 2
        )
        cond_model.save(update_model_path)

    return {
        "total_samples": cond_metrics.total_samples,
        "condition_leave_out": {
            "train_samples": cond_metrics.train_samples,
            "test_samples": cond_metrics.test_samples,
            "train_accuracy": cond_metrics.train_accuracy,
            "test_accuracy": cond_metrics.test_accuracy,
            "per_class": cond_metrics.per_class,
            "temperature": cond_model.temperature,
            "calibration": cond_model.metadata.get("calibration") or {},
        },
        "session_leave_out": {
            "train_samples": sess_metrics.train_samples,
            "test_samples": sess_metrics.test_samples,
            "train_accuracy": sess_metrics.train_accuracy,
            "test_accuracy": sess_metrics.test_accuracy,
            "per_class": sess_metrics.per_class,
        },
        "random_80_20": legacy_info,
        "adjacent_frame_and_specialist_leakage_pct": round(
            legacy_info["legacy_random_80_20_with_specialist_leak_acc"] - cond_metrics.test_accuracy, 2
        ),
        "adjacent_frame_only_leakage_pct": round(
            legacy_info["random_80_20_train_only_specialists_acc"] - cond_metrics.test_accuracy, 2
        ),
        "_cond_model": cond_model,
        "_xs_cond": xs_cond,
        "_ys_cond": ys_cond,
        "_gs_cond": gs_cond,
        "_ws_cond": ws_cond,
    }


def _extract_contiguous_runs(
    x_arr: np.ndarray, y_arr: np.ndarray, target_idx: int, min_len: int = 4, target_rep_len: int = 25
) -> List[np.ndarray]:
    """Split contiguous runs where `y_arr == target_idx` into individual repetition arrays.
    When transition (`y == -1`) frames between repetitions were filtered out, subdivide long contiguous blocks
    into ~`target_rep_len`-frame repetitions."""
    mask = np.asarray(y_arr == target_idx, dtype=bool)
    raw_runs: List[np.ndarray] = []
    start = None
    for i, val in enumerate(mask):
        if val and start is None:
            start = i
        elif not val and start is not None:
            if i - start >= min_len:
                raw_runs.append(x_arr[start:i])
            start = None
    if start is not None and len(mask) - start >= min_len:
        raw_runs.append(x_arr[start:])

    runs: List[np.ndarray] = []
    for r in raw_runs:
        if len(r) >= target_rep_len * 2:
            n_chunks = max(2, len(r) // target_rep_len)
            for sub in np.array_split(r, n_chunks):
                if len(sub) >= min_len:
                    runs.append(sub)
        else:
            runs.append(r)
    return runs


def evaluate_stage8_adaptation(
    cond_model: SpatialMlpModel,
    xs_cond: np.ndarray,
    ys_cond: np.ndarray,
    gs_cond: np.ndarray,
    ws_cond: np.ndarray,
    classes: Sequence[str],
    *,
    device: str = "auto",
) -> Dict[str, Any]:
    """Quantitatively evaluate Stage 8-1 (User Onboarding Prototype Adaptation),
    Stage 8-2 (3–5 Rep Custom Gesture Registration with Proximity & Neutral Safety Checks),
    and Stage 8-3 (Shadow Mode & Bounded EMA Adaptation with Rollback) on real held-out data.
    """
    from src.spatial_mlp import _resolve_train_test_masks

    _, test_mask, _ = _resolve_train_test_masks(ys_cond, groups=gs_cond, split_mode="group_leave_out", seed=42)
    x_test_all = xs_cond[test_mask]
    y_test_all = ys_cond[test_mask]

    # ------------------------------------------------------------------
    # Stage 8-1: 1-2 Min User Onboarding Prototype & OOD Adaptation
    # Split held-out test set into:
    # - Onboarding calibration subset (first 20% of each class in held-out conditions, ~3 reps + 30s neutral move)
    # - Disjoint held-out evaluation subset (remaining 80% of held-out conditions)
    # ------------------------------------------------------------------
    onboard_mask = np.zeros(len(y_test_all), dtype=bool)
    eval_mask = np.zeros(len(y_test_all), dtype=bool)
    labeled_onboard: Dict[str, np.ndarray] = {}
    neutral_onboard: Optional[np.ndarray] = None

    for c_idx, cls_name in enumerate(classes):
        idx_c = np.flatnonzero(y_test_all == c_idx)
        if len(idx_c) < 10:
            eval_mask[idx_c] = True
            continue
        n_cal = max(15, min(900 if cls_name == "none" else 90, int(len(idx_c) * 0.20)))
        cal_pos = np.unique(np.linspace(0, len(idx_c) - 1, n_cal, dtype=int))
        cal_idx = idx_c[cal_pos]
        ev_idx = np.setdiff1d(idx_c, cal_idx, assume_unique=True)
        onboard_mask[cal_idx] = True
        eval_mask[ev_idx] = True
        if cls_name == "none":
            neutral_onboard = x_test_all[cal_idx]
        else:
            labeled_onboard[cls_name] = x_test_all[cal_idx]

    x_eval = x_test_all[eval_mask]
    y_eval = y_test_all[eval_mask]

    model_s8 = SpatialMlpModel.from_dict(cond_model.to_dict())
    w1_before = model_s8.w1.copy()
    b1_before = model_s8.b1.copy()

    probs_before = model_s8.predict_proba(x_eval)
    preds_before = np.argmax(probs_before, axis=1)
    acc_before = float(np.mean(preds_before == y_eval) * 100.0)
    active_mask = y_eval > 0
    active_acc_before = float(np.mean(preds_before[active_mask] == y_eval[active_mask]) * 100.0)

    per_class_before: Dict[str, float] = {}
    for c_idx, cls_name in enumerate(classes):
        m_c = y_eval == c_idx
        if np.any(m_c):
            per_class_before[cls_name] = round(float(np.mean(preds_before[m_c] == c_idx) * 100.0), 2)

    onboard_info = model_s8.apply_user_onboarding_prototypes(
        labeled_onboard,
        neutral_features=neutral_onboard,
        blend_beta=0.35,
    )
    probs_after = model_s8.predict_proba(x_eval)
    preds_after = np.argmax(probs_after, axis=1)
    acc_after = float(np.mean(preds_after == y_eval) * 100.0)
    active_acc_after = float(np.mean(preds_after[active_mask] == y_eval[active_mask]) * 100.0)

    per_class_after: Dict[str, float] = {}
    for c_idx, cls_name in enumerate(classes):
        m_c = y_eval == c_idx
        if np.any(m_c):
            per_class_after[cls_name] = round(float(np.mean(preds_after[m_c] == c_idx) * 100.0), 2)

    encoder_max_diff = float(
        max(np.max(np.abs(model_s8.w1 - w1_before)), np.max(np.abs(model_s8.b1 - b1_before)))
    )

    # ------------------------------------------------------------------
    # Stage 8-2: Few-Shot (3-Rep and 5-Rep) Custom Gesture Registration on Frozen Encoder
    # Train a 5-class base encoder (excluding `thumbs_up` and `palm_side` so they are 100% unseen custom gestures)
    # and register them from ONLY 3 reps and 5 reps without mutating W1, b1, W2, b2.
    # Also register real `v_sign` episodes from `field-logs/2026-11-04-home-pc-phone/failures/*-toggle_sign.json`
    # on the full 7-class model.
    # ------------------------------------------------------------------
    unseen_targets = ["thumbs_up", "palm_side"]
    base5_classes = [c for c in classes if c not in unseen_targets]
    keep_mask = np.isin(ys_cond, [classes.index(c) for c in base5_classes])
    xs_b5 = xs_cond[keep_mask]
    ys_b5_raw = ys_cond[keep_mask]
    gs_b5 = gs_cond[keep_mask]
    ws_b5 = ws_cond[keep_mask]
    ys_b5 = np.zeros_like(ys_b5_raw)
    for new_i, c_name in enumerate(base5_classes):
        ys_b5[ys_b5_raw == classes.index(c_name)] = new_i

    base5_model, _ = train_spatial_mlp(
        xs_b5,
        ys_b5,
        weights_all=ws_b5,
        classes=base5_classes,
        device=device,
        groups=gs_b5,
        split_mode="group_leave_out",
        compute_random_comparison=False,
    )
    # 30 seconds of neutral movement (`900` frames at 30fps) from held-out `none` frames
    neutral_30s = x_test_all[y_test_all == 0][:900]
    base5_test_mask = test_mask[keep_mask]
    x_b5_test = xs_b5[base5_test_mask]
    y_b5_test = ys_b5[base5_test_mask]
    base5_acc_before = float(np.mean(np.argmax(base5_model.predict_proba(x_b5_test), axis=1) == y_b5_test) * 100.0)

    few_shot_results: Dict[str, Any] = {}
    for target_lbl in unseen_targets:
        if target_lbl not in classes:
            continue
        t_idx = classes.index(target_lbl)
        all_runs = _extract_contiguous_runs(xs_cond, ys_cond, t_idx, min_len=6)
        if len(all_runs) < 8:
            continue
        for n_reps in (3, 5):
            m_copy = SpatialMlpModel.from_dict(base5_model.to_dict())
            reg_indices = [int(i * (len(all_runs) - 1) / max(n_reps, 1)) for i in range(n_reps)]
            reg_set = set(reg_indices)
            reg_runs = [all_runs[i] for i in reg_indices]
            eval_runs = [r for i, r in enumerate(all_runs) if i not in reg_set]
            reg_info = m_copy.register_custom_gesture(
                target_lbl,
                reg_runs,
                neutral_features=neutral_30s,
                min_reps=3,
                max_reps=5,
                k_frames=4,
                base_threshold=0.72,
            )
            c_idx_new = m_copy.classes.index(target_lbl)
            rec_thr = float(reg_info["recommended_threshold"])
            recognised_reps = 0
            frame_hits = 0
            frame_total = 0
            for r_seq in eval_runs:
                p_seq = m_copy.predict_proba(r_seq)
                c_probs = p_seq[:, c_idx_new]
                top_cls = np.argmax(p_seq, axis=1)
                frame_hits += int(np.sum(top_cls == c_idx_new))
                frame_total += len(r_seq)
                # Confirmed if >= 4 consecutive frames exceed recommended threshold or top-1 for >= 4 frames
                run_len = 0
                confirmed = False
                for pv, tc in zip(c_probs, top_cls):
                    if pv >= rec_thr or tc == c_idx_new:
                        run_len += 1
                        if run_len >= 4:
                            confirmed = True
                            break
                    else:
                        run_len = 0
                if confirmed:
                    recognised_reps += 1

            # Verify zero degradation on existing base5 classes when evaluated on base5 features
            b5_probs_after = m_copy.predict_proba(x_b5_test)[:, : len(base5_classes)]
            base5_acc_after = float(np.mean(np.argmax(b5_probs_after, axis=1) == y_b5_test) * 100.0)

            few_shot_results[f"{target_lbl}_{n_reps}reps"] = {
                "gesture": target_lbl,
                "reps_registered": n_reps,
                "held_out_reps_recognised": recognised_reps,
                "held_out_reps_total": len(eval_runs),
                "held_out_rep_recall_pct": round(recognised_reps / max(len(eval_runs), 1) * 100.0, 2),
                "held_out_frame_accuracy_pct": round(frame_hits / max(frame_total, 1) * 100.0, 2),
                "rep_consistency_cosine": reg_info["rep_consistency_cosine"],
                "closest_existing_class": reg_info["closest_class"],
                "closest_cosine": reg_info["closest_cosine"],
                "proximity_warning_triggered": bool(reg_info["proximity_warning"]),
                "neutral_30s_misfires_default": reg_info["neutral_misfires_at_default"],
                "neutral_30s_misfires_after_tuning": reg_info["neutral_misfires_after_tuning"],
                "recommended_threshold": reg_info["recommended_threshold"],
                "base_classes_acc_before_pct": round(base5_acc_before, 2),
                "base_classes_acc_after_pct": round(base5_acc_after, 2),
                "encoder_frozen": m_copy.encoder_frozen,
            }

    # Also test registering real out-of-dataset `v_sign` episodes from `field-logs` on the full 7-class model
    v_sign_runs: List[np.ndarray] = []
    from src.spatial_features import extract_spatial_features
    for fpath in sorted((ROOT / "field-logs" / "2026-11-04-home-pc-phone" / "failures").glob("*-toggle_sign.json")):
        try:
            fdoc = json.loads(fpath.read_text(encoding="utf-8"))
            if "V 손 모양" not in str(fdoc.get("note", "")):
                continue
            vecs = []
            for fr in fdoc.get("frames") or []:
                lm = fr.get("landmarks") or fr.get("lm")
                if lm and len(lm) >= 21:
                    v = extract_spatial_features(lm)
                    if v is not None:
                        vecs.append(v)
            if len(vecs) >= 6:
                v_sign_runs.append(np.asarray(vecs, dtype=np.float64))
        except Exception:
            pass

    if len(v_sign_runs) >= 5:
        m_vsign = SpatialMlpModel.from_dict(cond_model.to_dict())
        reg_v = m_vsign.register_custom_gesture(
            "v_sign_custom",
            v_sign_runs[:3],
            neutral_features=neutral_30s,
            min_reps=3,
            max_reps=5,
        )
        v_idx = m_vsign.classes.index("v_sign_custom")
        v_eval_runs = v_sign_runs[3:]
        v_rec = 0
        for r_seq in v_eval_runs:
            p_seq = m_vsign.predict_proba(r_seq)
            if int(np.sum(np.argmax(p_seq, axis=1) == v_idx)) >= 3:
                v_rec += 1
        few_shot_results["v_sign_field_3reps"] = {
            "gesture": "v_sign_custom",
            "reps_registered": 3,
            "held_out_reps_recognised": v_rec,
            "held_out_reps_total": len(v_eval_runs),
            "held_out_rep_recall_pct": round(v_rec / max(len(v_eval_runs), 1) * 100.0, 2),
            "rep_consistency_cosine": reg_v["rep_consistency_cosine"],
            "closest_existing_class": reg_v["closest_class"],
            "closest_cosine": reg_v["closest_cosine"],
            "proximity_warning_triggered": bool(reg_v["proximity_warning"]),
            "neutral_30s_misfires_default": reg_v["neutral_misfires_at_default"],
            "neutral_30s_misfires_after_tuning": reg_v["neutral_misfires_after_tuning"],
            "recommended_threshold": reg_v["recommended_threshold"],
        }

    # Deliberate duplicate gesture registration test to verify proximity warning triggers (`cos >= 0.85`)
    idx_runs = _extract_contiguous_runs(xs_cond, ys_cond, classes.index("thumb_index"), min_len=6)
    m_dup = SpatialMlpModel.from_dict(cond_model.to_dict())
    dup_reg = m_dup.register_custom_gesture(
        "duplicate_thumb_index",
        idx_runs[:3],
        neutral_features=neutral_30s,
        proximity_warn_cosine=0.85,
    )

    # ------------------------------------------------------------------
    # Stage 8-3: Shadow Mode & Bounded EMA Runtime Adaptation with Rollback
    # ------------------------------------------------------------------
    model_ema = SpatialMlpModel.from_dict(cond_model.to_dict())
    proto_initial = model_ema.prototypes.copy()

    # 1. Shadow mode (`shadow_mode=True`): logs candidate updates without mutating prototypes
    model_ema.shadow_mode = True
    ti_idx = classes.index("thumb_index")
    ti_sample = x_eval[y_eval == ti_idx][:15]
    shadow_entry = model_ema.adapt_prototype_ema(
        "thumb_index",
        ti_sample,
        calibrated_confidence=0.94,
        quality_score=0.91,
        ood_ratio=0.52,
    )
    shadow_diff = float(np.max(np.abs(model_ema.prototypes - proto_initial)))

    # 2. Active bounded EMA mode (`shadow_mode=False`) across high-confidence held-out executions
    model_ema.shadow_mode = False
    ema_accepted = 0
    for c_idx, cls_name in enumerate(classes):
        if cls_name == "none":
            continue
        c_frames = x_test_all[onboard_mask & (y_test_all == c_idx)]
        if len(c_frames) >= 5:
            for chunk in np.array_split(c_frames, min(4, len(c_frames) // 5)):
                res_e = model_ema.adapt_prototype_ema(
                    cls_name,
                    chunk,
                    calibrated_confidence=0.93,
                    quality_score=0.88,
                    ood_ratio=0.58,
                    ema_alpha=0.08,
                )
                if res_e.get("applied"):
                    ema_accepted += 1

    ema_preds = np.argmax(model_ema.predict_proba(x_eval), axis=1)
    ema_acc_after = float(np.mean(ema_preds == y_eval) * 100.0)

    # 3. Verify safety rejections (user_cancelled, low_confidence, drift_cap_exceeded)
    rej_cancel = model_ema.adapt_prototype_ema(
        "thumb_index", ti_sample, calibrated_confidence=0.95, quality_score=0.90, ood_ratio=0.50, user_cancelled=True
    )
    rej_low_conf = model_ema.adapt_prototype_ema(
        "thumb_index", ti_sample, calibrated_confidence=0.65, quality_score=0.90, ood_ratio=0.50
    )

    # 4. Rollback verification: restore pre-EMA prototypes and verify 0.0 difference
    rolled_back = model_ema.rollback_prototypes()
    rollback_max_diff = float(np.max(np.abs(model_ema.prototypes - proto_initial)))

    return {
        "stage8_1_onboarding": {
            "eval_frames": int(len(y_eval)),
            "onboard_calibration_frames": int(np.sum(onboard_mask)),
            "encoder_frozen": bool(model_s8.encoder_frozen),
            "encoder_weight_max_diff": encoder_max_diff,
            "overall_accuracy_before_pct": round(acc_before, 2),
            "overall_accuracy_after_pct": round(acc_after, 2),
            "overall_delta_pct": round(acc_after - acc_before, 2),
            "active_gesture_accuracy_before_pct": round(active_acc_before, 2),
            "active_gesture_accuracy_after_pct": round(active_acc_after, 2),
            "active_gesture_delta_pct": round(active_acc_after - active_acc_before, 2),
            "per_class_before_pct": per_class_before,
            "per_class_after_pct": per_class_after,
            "prototype_cosine_shifts": onboard_info["prototype_cosine_shifts"],
            "user_ood_thresholds": onboard_info["user_ood_thresholds"],
        },
        "stage8_2_custom_registration": {
            "few_shot_evaluations": few_shot_results,
            "duplicate_proximity_check": {
                "registered_label": dup_reg["label"],
                "closest_class": dup_reg["closest_class"],
                "closest_cosine": dup_reg["closest_cosine"],
                "proximity_warning_triggered": bool(dup_reg["proximity_warning"]),
                "warning_text": dup_reg["proximity_warning"],
            },
        },
        "stage8_3_runtime_ema_and_rollback": {
            "shadow_mode_logged": bool(shadow_entry.get("accepted") and not shadow_entry.get("applied")),
            "shadow_mode_prototype_diff": shadow_diff,
            "live_ema_updates_applied": ema_accepted,
            "accuracy_before_ema_pct": round(acc_before, 2),
            "accuracy_after_ema_pct": round(ema_acc_after, 2),
            "ema_delta_pct": round(ema_acc_after - acc_before, 2),
            "cancelled_rejected": not bool(rej_cancel.get("accepted")),
            "low_confidence_rejected": not bool(rej_low_conf.get("accepted")),
            "rollback_succeeded": bool(rolled_back),
            "post_rollback_prototype_max_diff": rollback_max_diff,
        },
    }


def evaluate_deployed_and_leave_out_pipeline(
    eval_sessions: Sequence[Dict[str, Any]],
    classes: Sequence[str],
    *,
    model_path: Path,
    split_mode: str = "condition",
    run_leave_out_training: bool = True,
    epochs: int = 15,
    window_stride: int = 2,
    device: str = "auto",
    thr: float = 0.7,
    k: int = 3,
) -> Dict[str, Any]:
    """Evaluate deployed TCN+Specialist+SpatialMLP pipeline and leave-group-out folds across all sessions."""
    conds = sorted({int(c) for s in eval_sessions for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
    fold_plan = build_group_leave_out_folds(eval_sessions, split_mode=split_mode)

    # 1. Evaluate deployed model artifact (`models/gesture_tcn.onnx` + specialist bank + `models/spatial_mlp.json`)
    deployed_rows: List[Dict[str, Any]] = []
    if model_path.is_file():
        pred_fn, pred_info = load_predictor(model_path)
        pred_classes = list(pred_info.get("classes") or classes)
        sp_path = model_path.parent / "spatial_mlp.json"
        base_sp = SpatialMlpModel.load(sp_path) if sp_path.is_file() else None
        sp_model = compose_spatial_mlp(model_path, base_sp)

        for s in eval_sessions:
            # Batch-evaluate deployed predictor on session windows `s["x"]`
            x_win = s["x"]
            n_frames = len(x_win)
            probs = np.zeros((n_frames, len(classes)), dtype=np.float64)
            # Fast batch evaluation through ONNX / composed predictor
            for i in range(n_frames):
                if s["y"][i] < 0:
                    continue
                p_vec = pred_fn(x_win[i : i + 1])
                for c_idx, cname in enumerate(classes):
                    if cname in pred_classes:
                        probs[i, c_idx] = float(p_vec[pred_classes.index(cname)])
            if sp_model is not None:
                s_feats = ensure_session_spatial_features(s)
                if len(s_feats) == len(probs):
                    sp_prob = sp_model.predict_proba(s_feats)
                    probs = blend_tcn_and_spatial_probs(probs, classes, sp_prob, sp_model.classes)

            s_conds = sorted({int(c) for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
            for c in s_conds:
                rule_ev = train_tcn.evaluate(s, [c])
                tcn_ev = train_tcn.evaluate(s, [c], probs, thr, k)
                deployed_rows.append({
                    "posture": int(c),
                    "session": Path(s["path"]).name,
                    "rules": rule_ev,
                    "tcn": tcn_ev,
                    "free_minutes": round(train_tcn.free_minutes(s, [c]), 2),
                })

    # 2. Evaluate leave-group-out folds so every gesture has honest unseen-group zero-shot metrics
    held_out_rows: List[Dict[str, Any]] = []
    evaluated_folds: List[Any] = []
    ho_probs_list: List[np.ndarray] = []
    ho_y_list: List[np.ndarray] = []
    if run_leave_out_training and not fold_plan["degenerate"]:
        for fold_spec in fold_plan["folds"]:
            fold_id = fold_spec["fold_id"]
            train_sess = [eval_sessions[i] for i in fold_spec["train_session_indices"]]
            test_sess = [eval_sessions[i] for i in fold_spec["test_session_indices"]]
            train_conds = fold_spec["train_conds"]
            test_conds = fold_spec["test_conds"]

            x_tr, y_tr, w_tr = train_tcn.pick_with_weights(train_sess, train_conds)
            if len(y_tr) < 10 or 0 not in set(y_tr.tolist()):
                continue
            evaluated_folds.append(fold_id)
            fold_model = train_tcn.train(
                x_tr,
                y_tr,
                epochs,
                seed=42,
                stage=f"eval_fold_{fold_id}",
                classes=classes,
                window_stride=window_stride,
                device=device,
                sample_weights=w_tr,
            )
            xs_f, ys_f, gs_f, ws_f = pick_spatial_from_sessions(
                train_sess, train_conds, return_groups=True, return_weights=True
            )
            sp_fold = None
            if len(ys_f) >= 10:
                try:
                    sp_fold, _ = train_spatial_mlp(
                        xs_f,
                        ys_f,
                        weights_all=ws_f,
                        classes=classes,
                        device=device,
                        groups=gs_f,
                        compute_random_comparison=False,
                    )
                except Exception:
                    sp_fold = None

            for s in test_sess:
                prob = train_tcn.predict(fold_model, s["x"])
                if sp_fold is not None:
                    s_feats = ensure_session_spatial_features(s)
                    if len(s_feats) == len(prob):
                        sp_prob = sp_fold.predict_proba(s_feats)
                        prob = blend_tcn_and_spatial_probs(prob, classes, sp_prob, sp_fold.classes)
                m_valid = (s["y"] >= 0) & np.isin(s["cond"], test_conds)
                if np.any(m_valid):
                    ho_probs_list.append(prob[m_valid])
                    ho_y_list.append(s["y"][m_valid])
                rule_ev = train_tcn.evaluate(s, test_conds)
                tcn_ev = train_tcn.evaluate(s, test_conds, prob, thr, k)
                held_out_rows.append({
                    "posture": fold_id if isinstance(fold_id, int) else -1,
                    "fold_id": fold_id,
                    "session": Path(s["path"]).name,
                    "rules": rule_ev,
                    "tcn": tcn_ev,
                    "free_minutes": round(train_tcn.free_minutes(s, test_conds), 2),
                })

    hybrid_calibration: Dict[str, Any] = {}
    if ho_probs_list:
        all_ho_p = np.concatenate(ho_probs_list, axis=0)
        all_ho_y = np.concatenate(ho_y_list, axis=0)
        _, hybrid_calibration = fit_temperature_scaling(all_ho_p, all_ho_y)

    # Aggregate per-gesture metrics
    xa_eval, ya_eval = train_tcn.pick(eval_sessions, conds)
    windows_per_class = dict(Counter(classes[i] for i in ya_eval))
    per_gesture_summary: Dict[str, Any] = {}

    for label in classes:
        keys = [t.key for t in TASKS if t.label == label]
        if label == "none":
            dep_mins = sum(r.get("free_minutes", 0.0) for r in deployed_rows)
            dep_mis = sum(sum(r["tcn"]["misfires"].values()) for r in deployed_rows)
            dep_rule_mis = sum(sum(r["rules"]["misfires"].values()) for r in deployed_rows)

            ho_mins = sum(r.get("free_minutes", 0.0) for r in held_out_rows)
            ho_mis = sum(sum(r["tcn"]["misfires"].values()) for r in held_out_rows)
            ho_rule_mis = sum(sum(r["rules"]["misfires"].values()) for r in held_out_rows)

            per_gesture_summary[label] = {
                "windows": int(windows_per_class.get(label, 0)),
                "free_minutes_deployed": round(dep_mins, 2),
                "deployed_misfires": dep_mis,
                "deployed_misfires_per_min": round(dep_mis / dep_mins, 2) if dep_mins > 0 else 0.0,
                "rules_misfires": dep_rule_mis,
                "rules_misfires_per_min": round(dep_rule_mis / dep_mins, 2) if dep_mins > 0 else 0.0,
                "leave_out_free_minutes": round(ho_mins, 2),
                "leave_out_misfires": ho_mis,
                "leave_out_misfires_per_min": round(ho_mis / ho_mins, 2) if ho_mins > 0 else None,
                "leave_out_rules_misfires_per_min": round(ho_rule_mis / ho_mins, 2) if ho_mins > 0 else None,
            }
            continue

        dep_a = sum(r["tcn"]["reps"].get(k_name, (0, 0))[0] for r in deployed_rows for k_name in keys)
        dep_b = sum(r["tcn"]["reps"].get(k_name, (0, 0))[1] for r in deployed_rows for k_name in keys)
        rule_a = sum(r["rules"]["reps"].get(k_name, (0, 0))[0] for r in deployed_rows for k_name in keys)
        rule_b = sum(r["rules"]["reps"].get(k_name, (0, 0))[1] for r in deployed_rows for k_name in keys)

        ho_a = sum(r["tcn"]["reps"].get(k_name, (0, 0))[0] for r in held_out_rows for k_name in keys)
        ho_b = sum(r["tcn"]["reps"].get(k_name, (0, 0))[1] for r in held_out_rows for k_name in keys)

        per_gesture_summary[label] = {
            "windows": int(windows_per_class.get(label, 0)),
            "leave_out_recognised": ho_a,
            "leave_out_total": ho_b,
            "leave_out_rate": round(ho_a / ho_b, 4) if ho_b > 0 else None,
            "leave_out_ci95": list(wilson_ci_95(ho_a, ho_b)) if ho_b > 0 else None,
            "deployed_recognised": dep_a,
            "deployed_total": dep_b,
            "deployed_rate": round(dep_a / dep_b, 4) if dep_b > 0 else None,
            "deployed_ci95": list(wilson_ci_95(dep_a, dep_b)) if dep_b > 0 else None,
            "rules_recognised": rule_a,
            "rules_total": rule_b,
            "rules_rate": round(rule_a / rule_b, 4) if rule_b > 0 else None,
            "rules_ci95": list(wilson_ci_95(rule_a, rule_b)) if rule_b > 0 else None,
            "leave_out_reaction_latency": aggregate_latency_rows(held_out_rows, keys, source="tcn"),
            "deployed_reaction_latency": aggregate_latency_rows(deployed_rows, keys, source="tcn"),
            "rules_reaction_latency": aggregate_latency_rows(deployed_rows, keys, source="rules"),
        }

    return {
        "split_mode": split_mode,
        "evaluated_folds": evaluated_folds,
        "fold_plan": {
            "num_folds": fold_plan["num_folds"],
            "degenerate": fold_plan["degenerate"],
            "degenerate_reason": fold_plan["degenerate_reason"],
        },
        "gesture_policy": {
            "thresholds": dict(DEFAULT_GESTURE_THRESHOLDS),
            "frames": dict(DEFAULT_GESTURE_FRAMES),
            "ood_ratio_limits": dict(DEFAULT_GESTURE_OOD_LIMITS),
            "hold_seconds": dict(DEFAULT_GESTURE_HOLD_SECONDS),
            "spatial_alpha": 0.40,
            "veto_threshold": 0.80,
            "veto_target_cap": 0.15,
            "boost_threshold": 0.85,
        },
        "hybrid_calibration": hybrid_calibration,
        "per_gesture": per_gesture_summary,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Stage 1 Single-Command Evaluation & Regression Harness")
    ap.add_argument("--recordings", default=str(ROOT / "recordings" / "*-guided.jsonl.gz"))
    ap.add_argument("--model", type=Path, default=ROOT / "models" / "gesture_tcn.onnx")
    ap.add_argument("--split-mode", choices=SUPPORTED_SPLIT_MODES, default="condition")
    ap.add_argument("--epochs", type=int, default=15, help="epochs per leave-out fold evaluation")
    ap.add_argument("--window-stride", type=int, default=2)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--skip-leave-out-train", action="store_true", help="skip training leave-out TCN folds")
    ap.add_argument("--update-spatial-model", action="store_true", help="update models/spatial_mlp.json with group leave-out split")
    ap.add_argument("--output-json", type=Path, default=ROOT / "models" / "stage1_eval_report.json")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    t_start = time.perf_counter()

    print("=" * 88)
    print("Stage 1 Evaluation & Regression Harness (recordings/*.jsonl.gz + tests/fixtures/*.json)")
    print("=" * 88)

    # 1. Fixture replay regression test
    fx_res = run_fixture_regressions(model_path=args.model)
    print(f"\n[1/4] Fixture Regressions: {fx_res['passed_fixtures']}/{fx_res['total_fixtures']} passed (gate_loaded={fx_res['gate_loaded']})")
    for chk in fx_res["checks"]:
        status = "PASS" if chk["passed"] else "FAIL"
        print(f"  - [{status}] {chk['fixture']} ({chk['frames']} frames): {chk['invariant']}")

    # 2. Load recorded sessions and inspect diversity / split modes
    paths = sorted(glob.glob(args.recordings))
    folders = sorted({str(Path(p).parent) for p in paths})
    usable = {str(p) for f in folders for p in finished_recordings(Path(f))[0]}
    paths = [p for p in paths if str(Path(p)) in usable]
    paths = filter_selected_recording_paths(paths)
    if not paths:
        print("No valid recordings found.")
        return 1

    dummy_args = train_tcn.parse_args(["--mode", "fast", "--device", args.device])
    _, eval_sessions, opts = train_tcn.load_training_sessions(paths, dummy_args, window=train_tcn.WINDOW)
    present = {int(y) for s in eval_sessions for y in s["y"] if y >= 0}
    classes = [name for i, name in enumerate(CLASSES) if i in present]
    for s in eval_sessions:
        original = s["y"].copy()
        for c, name in enumerate(classes):
            s["y"][original == CLASSES.index(name)] = c
        s["classes"] = classes

    diversity = dataset_diversity_report(eval_sessions)
    print(
        f"\n[2/4] Dataset Diversity ({diversity['num_sessions']} sessions, "
        f"{diversity['num_conditions']} conditions, {diversity['num_persons']} person(s), "
        f"{diversity['num_cameras']} camera setup(s))"
    )
    for lim in diversity["limitations"]:
        print(f"  * [한계 명시] {lim}")

    # 3. SpatialMlpModel Leakage Quantification (Random 80/20 vs Group Leave-Out)
    sp_update_path = (args.model.parent / "spatial_mlp.json") if args.update_spatial_model else None
    sp_eval = evaluate_spatial_mlp_leave_out(
        eval_sessions,
        classes,
        device=opts["effective_device"],
        update_model_path=sp_update_path,
    )
    cond_model = sp_eval.pop("_cond_model")
    xs_cond = sp_eval.pop("_xs_cond")
    ys_cond = sp_eval.pop("_ys_cond")
    gs_cond = sp_eval.pop("_gs_cond")
    ws_cond = sp_eval.pop("_ws_cond")

    stage8_eval = evaluate_stage8_adaptation(
        cond_model,
        xs_cond,
        ys_cond,
        gs_cond,
        ws_cond,
        classes,
        device=opts["effective_device"],
    )
    s8_1 = stage8_eval["stage8_1_onboarding"]
    s8_2 = stage8_eval["stage8_2_custom_registration"]
    s8_3 = stage8_eval["stage8_3_runtime_ema_and_rollback"]
    print(f"\n[3b/4] Stage 8 Onboarding, Few-Shot Registration & Bounded EMA Adaptation")
    print(
        f"  - Stage 8-1 Onboarding (n={s8_1['eval_frames']} held-out frames) : "
        f"Overall {s8_1['overall_accuracy_before_pct']:.2f}% -> {s8_1['overall_accuracy_after_pct']:.2f}% "
        f"({s8_1['overall_delta_pct']:+.2f}%p) | Active Gestures {s8_1['active_gesture_accuracy_before_pct']:.2f}% -> "
        f"{s8_1['active_gesture_accuracy_after_pct']:.2f}% ({s8_1['active_gesture_delta_pct']:+.2f}%p) | "
        f"encoder_frozen={s8_1['encoder_frozen']} (max_diff={s8_1['encoder_weight_max_diff']:.6f})"
    )
    for k_reg, v_reg in s8_2["few_shot_evaluations"].items():
        print(
            f"  - Stage 8-2 Custom Reg [{k_reg:18s}] : "
            f"Recall {v_reg['held_out_reps_recognised']}/{v_reg['held_out_reps_total']} ({v_reg['held_out_rep_recall_pct']:.1f}%) | "
            f"30s Neutral Misfires {v_reg['neutral_30s_misfires_default']} -> {v_reg['neutral_30s_misfires_after_tuning']} "
            f"(thr={v_reg['recommended_threshold']:.2f})"
        )
    print(
        f"  - Stage 8-3 Bounded EMA & Rollback            : "
        f"Shadow diff={s8_3['shadow_mode_prototype_diff']:.6f} | "
        f"Live EMA {s8_3['accuracy_before_ema_pct']:.2f}% -> {s8_3['accuracy_after_ema_pct']:.2f}% ({s8_3['ema_delta_pct']:+.2f}%p) | "
        f"Rollback diff={s8_3['post_rollback_prototype_max_diff']:.6f}"
    )
    print(f"\n[3/4] SpatialMlpModel Split Comparison ({sp_eval['total_samples']} frames across {len(classes)} classes)")
    print(
        f"  - Before (Random 80/20 Frame Split + Specialist Leakage) : "
        f"{sp_eval['random_80_20']['legacy_random_80_20_with_specialist_leak_acc']:.2f}%"
    )
    print(
        f"  - Random 80/20 Frame Split (Train-Only Specialists)      : "
        f"{sp_eval['random_80_20']['random_80_20_train_only_specialists_acc']:.2f}%"
    )
    print(
        f"  - After  (Condition-Group Leave-Out Split, No Leakage)   : "
        f"{sp_eval['condition_leave_out']['test_accuracy']:.2f}% "
        f"(test n={sp_eval['condition_leave_out']['test_samples']})"
    )
    print(
        f"  - After  (Recording-Session Leave-Out Split, No Leakage) : "
        f"{sp_eval['session_leave_out']['test_accuracy']:.2f}% "
        f"(test n={sp_eval['session_leave_out']['test_samples']})"
    )
    print(
        f"  - Quantified Leakage (Legacy Random vs Condition Leave-Out): "
        f"+{sp_eval['adjacent_frame_and_specialist_leakage_pct']:.2f}%p overestimation removed"
    )

    # 4. Full Pipeline Per-Gesture Metrics (Success Rate + 95% CI, False Fires/min, Reaction Latency)
    pipe_eval = evaluate_deployed_and_leave_out_pipeline(
        eval_sessions,
        classes,
        model_path=args.model,
        split_mode=args.split_mode,
        run_leave_out_training=not args.skip_leave_out_train,
        epochs=args.epochs,
        window_stride=args.window_stride,
        device=opts["effective_device"],
    )
    print(
        f"\n[4/4] Per-Gesture Practical Metrics (split_mode={pipe_eval['split_mode']}, "
        f"evaluated_folds={len(pipe_eval['evaluated_folds'])})"
    )
    pg = pipe_eval["per_gesture"]
    none_info = pg.get("none") or {}
    print(
        f"  * Neutral Movement (`none`): {none_info.get('free_minutes_deployed', 0):.2f} min total | "
        f"Deployed Misfires: {none_info.get('deployed_misfires', 0)} ({none_info.get('deployed_misfires_per_min', 0):.2f}/min) | "
        f"Leave-Out Misfires: {none_info.get('leave_out_misfires', 0)} ({none_info.get('leave_out_misfires_per_min')}/min) | "
        f"Rules Misfires: {none_info.get('rules_misfires', 0)} ({none_info.get('rules_misfires_per_min', 0):.2f}/min)"
    )
    for label in classes:
        if label == "none":
            continue
        row = pg[label]
        lo_str = (
            f"{row['leave_out_recognised']}/{row['leave_out_total']} "
            f"({row['leave_out_rate'] * 100:.1f}%, 95% CI [{row['leave_out_ci95'][0] * 100:.1f}%, {row['leave_out_ci95'][1] * 100:.1f}%])"
            if row["leave_out_total"]
            else "N/A"
        )
        dep_str = (
            f"{row['deployed_recognised']}/{row['deployed_total']} "
            f"({row['deployed_rate'] * 100:.1f}%, 95% CI [{row['deployed_ci95'][0] * 100:.1f}%, {row['deployed_ci95'][1] * 100:.1f}%])"
            if row["deployed_total"]
            else "N/A"
        )
        rl_str = (
            f"{row['rules_recognised']}/{row['rules_total']} ({row['rules_rate'] * 100:.1f}%)"
            if row["rules_total"]
            else "N/A"
        )
        lat = row.get("deployed_reaction_latency") or row.get("leave_out_reaction_latency") or {}
        lat_str = (
            f"mean {lat.get('mean_ms')}ms ({lat.get('mean_frames')}f), "
            f"median {lat.get('median_ms')}ms ({lat.get('median_frames')}f), "
            f"p90 {lat.get('p90_ms')}ms ({lat.get('p90_frames')}f)"
            if lat
            else "N/A"
        )
        print(f"  - {label:14s} | Leave-Out: {lo_str} | Deployed: {dep_str} | Rules: {rl_str} | Latency: {lat_str}")

    report_doc = {
        "schema": "manux-stage1-eval-report/1",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.perf_counter() - t_start, 2),
        "fixture_regressions": fx_res,
        "dataset_diversity": diversity,
        "spatial_mlp_leakage_comparison": sp_eval,
        "stage8_adaptation": stage8_eval,
        "pipeline_evaluation": pipe_eval,
        "deferred_techniques": DEFERRED_TECHNIQUES_SUMMARY,
    }
    if args.output_json and str(args.output_json).strip() not in ("", "."):
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved Stage 1 evaluation report to: {args.output_json}")

    return 0 if fx_res["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
