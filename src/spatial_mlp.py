"""Lightweight Spatial MLP (Multi-Layer Perceptron) classifier & fast trainer.

Designed for instantaneous single-frame posture classification (`window = 1`, 0-frame lag)
in pure NumPy without requiring PyTorch or ONNX runtime overhead.
Plugs directly into `GestureGate` as a 0-lag posture guard and hybrid ensemble partner with TCN.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from src.spatial_features import SPATIAL_FEATURES, SPATIAL_FEATURE_VERSION, extract_spatial_features

DEFAULT_SPATIAL_MLP_PATH = Path(__file__).resolve().parents[1] / "models" / "spatial_mlp.json"

STUDIO_CLASSES: Tuple[str, ...] = (
    "none",
    "thumb_middle",
    "thumb_ring",
    "thumb_index",
    "thumb_pinky",
    "palm_side",
    "thumbs_up",
    "v_sign",
)

# Static / pinch / sign postures where single-frame 96-dim geometry is highly discriminative
STATIC_POSTURE_LABELS = frozenset({
    "none",
    "thumb_index",
    "thumb_middle",
    "thumb_ring",
    "thumb_pinky",
    "thumb_index_middle",
    "palm_side",
    "v_sign",
    "rock_sign",
    "thumbs_up",
})


@dataclass(frozen=True)
class SpatialMlpMetrics:
    train_accuracy: float
    test_accuracy: float
    total_samples: int
    train_samples: int
    test_samples: int
    elapsed_ms: float
    per_class: Mapping[str, Mapping[str, Any]]
    trained_at: str
    random_test_accuracy: Optional[float] = None
    leakage_delta_pct: Optional[float] = None
    split_mode: str = "group_leave_out"


class SpatialMlpModel:
    """2-Layer Spatial MLP (`feat_dim -> hidden_units -> num_classes`) with LeakyReLU(0.05) + Softmax,
    plus optional per-gesture binary specialist MLPs (`self.specialists[label]`).
    """

    def __init__(
        self,
        w1: np.ndarray,
        b1: np.ndarray,
        w2: np.ndarray,
        b2: np.ndarray,
        classes: Sequence[str] = STUDIO_CLASSES,
        feature_version: str = SPATIAL_FEATURE_VERSION,
        metadata: Optional[Mapping[str, Any]] = None,
        specialists: Optional[Mapping[str, SpatialMlpModel]] = None,
        prototypes: Optional[np.ndarray] = None,
        ood_means: Optional[np.ndarray] = None,
        ood_precisions: Optional[Mapping[str, np.ndarray]] = None,
        ood_thresholds: Optional[Mapping[str, float]] = None,
        encoder_frozen: bool = True,
        temperature: float = 1.0,
        custom_prototypes: Optional[Mapping[str, np.ndarray]] = None,
        custom_ood_means: Optional[Mapping[str, np.ndarray]] = None,
        shadow_mode: bool = True,
    ) -> None:
        self.w1 = np.asarray(w1, dtype=np.float64)  # (H, D)
        self.b1 = np.asarray(b1, dtype=np.float64)  # (H,)
        self.w2 = np.asarray(w2, dtype=np.float64)  # (C_base, H)
        self.b2 = np.asarray(b2, dtype=np.float64)  # (C_base,)
        self.base_classes: Tuple[str, ...] = tuple(classes)
        self.specialists: Dict[str, SpatialMlpModel] = {}
        merged_classes = list(self.base_classes)
        if specialists:
            for lbl, spec in specialists.items():
                if isinstance(spec, SpatialMlpModel) and lbl != "none":
                    self.specialists[str(lbl)] = spec
                    if str(lbl) not in merged_classes:
                        merged_classes.append(str(lbl))
        self.custom_prototypes: Dict[str, np.ndarray] = {}
        if custom_prototypes:
            for lbl, proto in custom_prototypes.items():
                arr = np.asarray(proto, dtype=np.float64).reshape(-1)
                norm = max(float(np.linalg.norm(arr)), 1e-8)
                self.custom_prototypes[str(lbl)] = arr / norm
                if str(lbl) not in merged_classes:
                    merged_classes.append(str(lbl))
        self.classes: Tuple[str, ...] = tuple(merged_classes)
        self.feature_version = feature_version
        self.metadata: Dict[str, Any] = dict(metadata or {})
        self.prototypes: Optional[np.ndarray] = (
            np.asarray(prototypes, dtype=np.float64) if prototypes is not None else None
        )
        self._anchor_prototypes: Optional[np.ndarray] = (
            self.prototypes.copy() if self.prototypes is not None else None
        )
        self._anchor_custom_prototypes: Dict[str, np.ndarray] = {
            k: v.copy() for k, v in self.custom_prototypes.items()
        }
        self._prototypes_adapted: bool = bool(self.metadata.get("prototypes_adapted", False))
        self.ood_means: Optional[np.ndarray] = (
            np.asarray(ood_means, dtype=np.float64) if ood_means is not None else None
        )
        self.custom_ood_means: Dict[str, np.ndarray] = {
            str(k): np.asarray(v, dtype=np.float64).reshape(-1)
            for k, v in (custom_ood_means or {}).items()
        }
        self.ood_precisions: Dict[str, np.ndarray] = {
            str(k): np.asarray(v, dtype=np.float64)
            for k, v in (ood_precisions or {}).items()
        }
        self.ood_thresholds: Dict[str, float] = {
            str(k): float(v) for k, v in (ood_thresholds or {}).items()
        }
        self.encoder_frozen: bool = bool(encoder_frozen)
        self.temperature: float = max(float(temperature), 0.1)
        self.shadow_mode: bool = bool(shadow_mode)
        self.shadow_adaptation_log: List[Dict[str, Any]] = []
        self.rollback_stack: List[Dict[str, Any]] = []

    @property
    def feat_dim(self) -> int:
        return int(self.w1.shape[1])

    @property
    def hidden_units(self) -> int:
        return int(self.w1.shape[0])

    def encode(self, features: np.ndarray, *, normalize: bool = False) -> np.ndarray:
        """Project 96D spatial features into the hidden representation `h` (`(H,)` or `(N, H)`).

        Post-deployment `self.encoder_frozen` is True so user adaptation only updates
        prototypes, OOD statistics, or calibration parameters without backpropagating into `W1, b1`.
        """
        x = np.asarray(features, dtype=np.float64)
        single = x.ndim == 1
        if single:
            x = x[None, :]
        z1 = x @ self.w1.T + self.b1
        h = np.where(z1 > 0.0, z1, z1 * 0.05)
        if normalize:
            norms = np.maximum(np.linalg.norm(h, axis=1, keepdims=True), 1e-8)
            h = h / norms
        return h[0] if single else h

    def prototype_similarity(self, features: np.ndarray) -> np.ndarray:
        """Cosine similarity between L2-normalized hidden embeddings and class prototypes."""
        h_norm = self.encode(features, normalize=True)
        single = h_norm.ndim == 1
        if single:
            h_norm = h_norm[None, :]
        if self.prototypes is not None and self.prototypes.shape == (len(self.base_classes), self.hidden_units):
            protos = self.prototypes
        else:
            w2_norms = np.maximum(np.linalg.norm(self.w2, axis=1, keepdims=True), 1e-8)
            protos = self.w2 / w2_norms
        sims = h_norm @ protos.T
        return sims[0] if single else sims

    def predict_prototype_proba(self, features: np.ndarray, *, temperature: float = 0.08) -> np.ndarray:
        """Pure prototype-distance probability distribution over `self.base_classes`."""
        sims = self.prototype_similarity(features)
        single = sims.ndim == 1
        if single:
            sims = sims[None, :]
        logits = sims / max(float(temperature), 1e-4)
        logits = logits - np.max(logits, axis=1, keepdims=True)
        exp_l = np.exp(logits)
        probs = exp_l / np.sum(exp_l, axis=1, keepdims=True)
        return probs[0] if single else probs

    def ood_score(
        self,
        features: np.ndarray,
        label: Optional[str] = None,
        *,
        ratio_limit: float = 1.0,
    ) -> Dict[str, Any]:
        """Compute class-wise shrinkage Mahalanobis OOD distance and normalized ratio for a frame `(D,)`."""
        x = np.asarray(features, dtype=np.float64).reshape(-1)
        h = self.encode(x, normalize=False)
        if label is None:
            probs = self.predict_proba(x)
            active_indices = [i for i, c in enumerate(self.classes) if c != "none" and c in self.ood_precisions]
            if active_indices:
                best_i = max(active_indices, key=lambda idx: float(probs[idx]))
                label = self.classes[best_i]
            else:
                label = self.classes[int(np.argmax(probs))]
        if label in self.custom_ood_means and label in self.ood_precisions and label in self.ood_thresholds:
            mean_vec = self.custom_ood_means[label]
        elif (
            label in self.base_classes
            and self.ood_means is not None
            and label in self.ood_precisions
            and label in self.ood_thresholds
        ):
            c_idx = self.base_classes.index(label)
            mean_vec = self.ood_means[c_idx]
        else:
            return {
                "label": str(label),
                "mahalanobis_distance": 0.0,
                "threshold": 1.0,
                "ratio": 0.0,
                "is_ood": False,
            }
        diff = h - mean_vec
        prec = self.ood_precisions[label]
        dist = math.sqrt(max(float(diff @ prec @ diff), 0.0))
        thr = max(float(self.ood_thresholds.get(label, 1.0)), 1e-6)
        ratio = dist / thr
        return {
            "label": str(label),
            "mahalanobis_distance": round(dist, 4),
            "threshold": round(thr, 4),
            "ratio": round(ratio, 4),
            "is_ood": bool(ratio > float(ratio_limit)),
        }

    def _predict_base_proba(self, x_2d: np.ndarray) -> np.ndarray:
        z1 = x_2d @ self.w1.T + self.b1
        h = np.where(z1 > 0.0, z1, z1 * 0.05)
        logits = h @ self.w2.T + self.b2
        if (
            self._prototypes_adapted
            and self.prototypes is not None
            and self._anchor_prototypes is not None
            and self.prototypes.shape == (len(self.base_classes), self.hidden_units)
            and self._anchor_prototypes.shape == self.prototypes.shape
        ):
            h_norms = np.maximum(np.linalg.norm(h, axis=1, keepdims=True), 1e-8)
            h_norm = h / h_norms
            sim_new = h_norm @ self.prototypes.T
            sim_old = h_norm @ self._anchor_prototypes.T
            delta_sim = sim_new - sim_old
            # Only apply prototype adaptation shift when a frame is close to an adapted active prototype (sim_new >= 0.78)
            active_gate = (sim_new[:, 1:] >= 0.78) & (delta_sim[:, 1:] > 0.0)
            logits[:, 1:] = logits[:, 1:] + np.where(active_gate, delta_sim[:, 1:] / 0.025, 0.0)
        logits = logits - np.max(logits, axis=1, keepdims=True)
        exp_logits = np.exp(logits)
        return exp_logits / np.sum(exp_logits, axis=1, keepdims=True)

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Single feature vector `(D,)` or batch `(N, D)` -> probabilities `(C,)` or `(N, C)`."""
        x = np.asarray(features, dtype=np.float64)
        single = x.ndim == 1
        if single:
            x = x[None, :]
        base_probs = self._predict_base_proba(x)
        if not self.specialists and not self.custom_prototypes:
            return base_probs[0] if single else base_probs

        n_rows = x.shape[0]
        probs = np.zeros((n_rows, len(self.classes)), dtype=np.float64)
        probs[:, : len(self.base_classes)] = base_probs

        for label, spec_model in self.specialists.items():
            if label == "none" or label not in self.classes:
                continue
            ci = self.classes.index(label)
            spec_out = spec_model._predict_base_proba(x)
            if spec_out.shape[1] < 2:
                continue
            spec_pos = spec_out[:, 1]
            is_independent = bool(spec_model.metadata.get("independent")) or (ci >= len(self.base_classes))
            if is_independent:
                probs[:, ci] = spec_pos
            else:
                base_p = probs[:, ci]
                blended = 0.35 * base_p + 0.65 * spec_pos
                # Suppress competing-finger false positives when the binary specialist rejects the gesture
                veto_mask = (spec_pos < 0.18) & (base_p < 0.80)
                blended = np.where(veto_mask, blended * 0.35, blended)
                probs[:, ci] = blended

        if (
            self._prototypes_adapted
            and self.prototypes is not None
            and self._anchor_prototypes is not None
            and self.prototypes.shape == (len(self.base_classes), self.hidden_units)
        ):
            h_norm = self.encode(x, normalize=True)
            if h_norm.ndim == 1:
                h_norm = h_norm[None, :]
            sim_new = h_norm @ self.prototypes.T
            sim_old = h_norm @ self._anchor_prototypes.T
            delta_sim = sim_new - sim_old
            # Softmax over active gesture prototypes only (excluding multimodal 'none' index 0)
            active_sims = sim_new[:, 1:] / 0.05
            active_sims = active_sims - np.max(active_sims, axis=1, keepdims=True)
            active_proto_p = np.exp(active_sims)
            active_proto_p = active_proto_p / np.maximum(np.sum(active_proto_p, axis=1, keepdims=True), 1e-8)
            max_active_prob = np.max(probs[:, 1 : len(self.base_classes)], axis=1)
            for c_idx in range(1, len(self.base_classes)):
                if float(np.linalg.norm(self.prototypes[c_idx] - self._anchor_prototypes[c_idx])) > 1e-6:
                    # Only blend when the frame has high cosine similarity to the adapted prototype and passes specialist floor
                    eligible = (
                        ((max_active_prob >= 0.48) | ((sim_new[:, c_idx] >= 0.84) & (delta_sim[:, c_idx] >= 0.003)))
                        & (probs[:, c_idx] >= 0.22)
                        & (delta_sim[:, c_idx] >= 0.0)
                    )
                    boosted = np.maximum(
                        probs[:, c_idx],
                        0.68 * probs[:, c_idx] + 0.32 * active_proto_p[:, c_idx - 1] * np.maximum(max_active_prob, 0.54),
                    )
                    probs[:, c_idx] = np.where(eligible, boosted, probs[:, c_idx])

        if self.custom_prototypes:
            h_raw = self.encode(x, normalize=False)
            h_norms = np.maximum(np.linalg.norm(h_raw, axis=1, keepdims=True), 1e-8)
            h_norm = h_raw / h_norms
            if self.prototypes is not None and self.prototypes.shape == (len(self.base_classes), self.hidden_units):
                base_sims = h_norm @ self.prototypes.T
                if len(self.base_classes) > 1:
                    active_base_probs = probs[:, 1 : len(self.base_classes)]
                    weighted_active_sims = base_sims[:, 1:] * (0.72 + 0.28 * active_base_probs)
                    max_competing_sim = np.maximum(base_sims[:, 0] * 0.88, np.max(weighted_active_sims, axis=1))
                else:
                    max_competing_sim = base_sims[:, 0] * 0.88
            else:
                max_competing_sim = np.full(n_rows, 0.70, dtype=np.float64)
            for c_label, c_proto in self.custom_prototypes.items():
                if c_label == "none" or c_label not in self.classes:
                    continue
                ci = self.classes.index(c_label)
                sim_c = h_norm @ c_proto
                margin = sim_c - np.maximum(0.68, max_competing_sim - 0.015)
                c_prob = 1.0 / (1.0 + np.exp(-np.clip(margin / 0.035, -30.0, 30.0)))
                if c_label in self.custom_ood_means and c_label in self.ood_precisions:
                    diff = h_raw - self.custom_ood_means[c_label][None, :]
                    prec = self.ood_precisions[c_label]
                    d_maha = np.sqrt(np.maximum(np.sum((diff @ prec) * diff, axis=1), 0.0))
                    thr = max(float(self.ood_thresholds.get(c_label, 1.0)), 1e-6)
                    ratio = d_maha / thr
                    ood_damp = np.where(ratio <= 1.35, 1.0, np.exp(-1.2 * (ratio - 1.35)))
                    c_prob = c_prob * ood_damp
                probs[:, ci] = c_prob

        if "none" in self.classes:
            none_idx = self.classes.index("none")
            other_indices = [i for i, name in enumerate(self.classes) if name != "none"]
            if other_indices:
                other_sum = np.sum(probs[:, other_indices], axis=1)
                max_other = np.max(probs[:, other_indices], axis=1)
                probs[:, none_idx] = np.where(
                    other_sum <= 1.0,
                    1.0 - other_sum,
                    np.clip(1.0 - max_other, 0.0, 1.0),
                )
            else:
                row_sum = np.sum(probs, axis=1, keepdims=True)
                probs = np.where(row_sum > 1e-8, probs / row_sum, probs)
        else:
            row_sum = np.sum(probs, axis=1, keepdims=True)
            probs = np.where(row_sum > 1e-8, probs / row_sum, probs)
        return probs[0] if single else probs

    # ---------------------------------------------------------------- Stage 8: User Onboarding, Few-Shot Registration & Bounded EMA Adaptation
    def create_rollback_point(self, reason: str = "manual") -> Dict[str, Any]:
        """Save a reversible snapshot of current prototypes and OOD thresholds before user adaptation."""
        snap: Dict[str, Any] = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": str(reason),
            "classes": list(self.classes),
            "prototypes_adapted": bool(self._prototypes_adapted),
            "prototypes": np.round(self.prototypes, 6).tolist() if self.prototypes is not None else None,
            "custom_prototypes": {k: np.round(v, 6).tolist() for k, v in self.custom_prototypes.items()},
            "ood_means": np.round(self.ood_means, 5).tolist() if self.ood_means is not None else None,
            "custom_ood_means": {k: np.round(v, 5).tolist() for k, v in self.custom_ood_means.items()},
            "ood_thresholds": {k: round(float(v), 4) for k, v in self.ood_thresholds.items()},
        }
        self.rollback_stack.append(snap)
        if len(self.rollback_stack) > 10:
            self.rollback_stack.pop(0)
        return snap

    def rollback_prototypes(self, snapshot: Optional[Mapping[str, Any]] = None) -> bool:
        """Revert prototypes, custom gestures, and OOD thresholds to `snapshot` or the most recent rollback point."""
        snap = dict(snapshot) if snapshot is not None else (self.rollback_stack.pop() if self.rollback_stack else None)
        if not snap:
            return False
        if snap.get("prototypes") is not None:
            self.prototypes = np.asarray(snap["prototypes"], dtype=np.float64)
        self._prototypes_adapted = bool(snap.get("prototypes_adapted", False))
        self.metadata["prototypes_adapted"] = self._prototypes_adapted
        raw_custom = snap.get("custom_prototypes") or {}
        self.custom_prototypes = {
            str(k): np.asarray(v, dtype=np.float64).reshape(-1) for k, v in raw_custom.items()
        }
        if snap.get("ood_means") is not None:
            self.ood_means = np.asarray(snap["ood_means"], dtype=np.float64)
        raw_c_means = snap.get("custom_ood_means") or {}
        self.custom_ood_means = {
            str(k): np.asarray(v, dtype=np.float64).reshape(-1) for k, v in raw_c_means.items()
        }
        if isinstance(snap.get("ood_thresholds"), Mapping):
            self.ood_thresholds = {str(k): float(v) for k, v in snap["ood_thresholds"].items()}
        merged = list(self.base_classes)
        for lbl in self.specialists:
            if lbl not in merged:
                merged.append(lbl)
        for lbl in self.custom_prototypes:
            if lbl not in merged:
                merged.append(lbl)
        self.classes = tuple(merged)
        return True

    def apply_user_onboarding_prototypes(
        self,
        labeled_features: Mapping[str, np.ndarray],
        neutral_features: Optional[np.ndarray] = None,
        *,
        blend_beta: float = 0.35,
        ood_percentile: float = 95.0,
    ) -> Dict[str, Any]:
        """Stage 8-1: Adapt class prototypes and OOD thresholds using 1–2 min user onboarding data
        while keeping the base encoder (`W1, b1`) strictly frozen (`encoder_frozen=True`).
        """
        if not self.encoder_frozen:
            raise RuntimeError("Stage 8 onboarding requires encoder_frozen=True")
        self.create_rollback_point("pre_onboarding")
        if self.prototypes is None or self.prototypes.shape != (len(self.base_classes), self.hidden_units):
            w2_norms = np.maximum(np.linalg.norm(self.w2, axis=1, keepdims=True), 1e-8)
            self.prototypes = self.w2 / w2_norms
        if self._anchor_prototypes is None or self._anchor_prototypes.shape != self.prototypes.shape:
            self._anchor_prototypes = self.prototypes.copy()
        if self.ood_means is None or self.ood_means.shape != (len(self.base_classes), self.hidden_units):
            self.ood_means = self.prototypes.copy()

        beta = float(np.clip(blend_beta, 0.05, 0.85))
        combined_inputs: Dict[str, np.ndarray] = {}
        for k, v in labeled_features.items():
            arr = np.asarray(v, dtype=np.float64)
            if arr.ndim == 1 and arr.size == self.feat_dim:
                arr = arr[None, :]
            if arr.ndim == 2 and len(arr) > 0 and arr.shape[1] == self.feat_dim:
                combined_inputs[str(k)] = arr
        if neutral_features is not None:
            n_arr = np.asarray(neutral_features, dtype=np.float64)
            if n_arr.ndim == 1 and n_arr.size == self.feat_dim:
                n_arr = n_arr[None, :]
            if n_arr.ndim == 2 and len(n_arr) > 0 and n_arr.shape[1] == self.feat_dim:
                if "none" in combined_inputs:
                    combined_inputs["none"] = np.concatenate([combined_inputs["none"], n_arr], axis=0)
                else:
                    combined_inputs["none"] = n_arr

        updated_classes: List[str] = []
        cosine_shifts: Dict[str, float] = {}
        for cls_name, feats in combined_inputs.items():
            if cls_name not in self.base_classes:
                continue
            c_idx = self.base_classes.index(cls_name)
            h_raw = self.encode(feats, normalize=False)
            h_norm = self.encode(feats, normalize=True)
            if h_raw.ndim == 1:
                h_raw = h_raw[None, :]
                h_norm = h_norm[None, :]
            u_proto = np.mean(h_norm, axis=0)
            u_proto = u_proto / max(float(np.linalg.norm(u_proto)), 1e-8)
            old_proto = self.prototypes[c_idx].copy()
            new_proto = (1.0 - beta) * old_proto + beta * u_proto
            new_proto = new_proto / max(float(np.linalg.norm(new_proto)), 1e-8)
            self.prototypes[c_idx] = new_proto
            cosine_shifts[cls_name] = round(1.0 - float(old_proto @ new_proto), 5)

            u_mean = np.mean(h_raw, axis=0)
            self.ood_means[c_idx] = (1.0 - beta) * self.ood_means[c_idx] + beta * u_mean
            if cls_name in self.ood_precisions:
                diff = h_raw - self.ood_means[c_idx][None, :]
                prec = self.ood_precisions[cls_name]
                d_maha = np.sqrt(np.maximum(np.sum((diff @ prec) * diff, axis=1), 0.0))
                user_p95 = float(np.percentile(d_maha, ood_percentile)) * 1.05
                old_thr = float(self.ood_thresholds.get(cls_name, user_p95))
                self.ood_thresholds[cls_name] = round(max(old_thr * 0.85, min(old_thr * 1.35, user_p95)), 4)
            updated_classes.append(cls_name)

        self._prototypes_adapted = True
        self.metadata["prototypes_adapted"] = True
        return {
            "encoder_frozen": self.encoder_frozen,
            "blend_beta": round(beta, 3),
            "updated_classes": updated_classes,
            "prototype_cosine_shifts": cosine_shifts,
            "user_ood_thresholds": {k: round(float(v), 4) for k, v in self.ood_thresholds.items()},
            "user_prototypes": {
                self.base_classes[i]: np.round(self.prototypes[i], 6).tolist()
                for i in range(len(self.base_classes))
            },
        }

    def register_custom_gesture(
        self,
        label: str,
        rep_feature_sequences: Sequence[np.ndarray],
        neutral_features: Optional[np.ndarray] = None,
        *,
        proximity_warn_cosine: float = 0.85,
        min_reps: int = 3,
        max_reps: int = 5,
        k_frames: int = 4,
        base_threshold: float = 0.72,
    ) -> Dict[str, Any]:
        """Stage 8-2: Few-shot custom gesture registration from 3–5 repetitions on the frozen encoder.

        - Never mutates `W1, b1, W2, b2` (`encoder_frozen=True`, no Replay, iCaRL, or Knowledge Distillation).
        - Emits a prototype proximity warning if cosine similarity to an existing prototype >= `proximity_warn_cosine`.
        - Replays 30s neutral movement frames (`neutral_features`) to verify zero false fires (or auto-tightens threshold).
        """
        if not self.encoder_frozen:
            raise RuntimeError("Custom gesture registration requires encoder_frozen=True")
        clean_label = str(label or "").strip()
        if not clean_label or clean_label == "none":
            raise ValueError("Custom gesture label must be a non-empty string other than 'none'")
        if len(rep_feature_sequences) < int(min_reps):
            raise ValueError(f"At least {min_reps} repetitions required (got {len(rep_feature_sequences)})")

        self.create_rollback_point(f"pre_register_{clean_label}")
        rep_means_norm: List[np.ndarray] = []
        core_frames_list: List[np.ndarray] = []
        for rep in list(rep_feature_sequences)[: max(int(max_reps), len(rep_feature_sequences))]:
            arr = np.asarray(rep, dtype=np.float64)
            if arr.ndim == 1:
                arr = arr[None, :]
            if arr.ndim != 2 or arr.shape[1] != self.feat_dim or len(arr) == 0:
                continue
            # Trim boundary transition frames when rep has >= 5 frames
            if len(arr) >= 5:
                trim = max(1, int(len(arr) * 0.15))
                core = arr[trim : len(arr) - trim]
            else:
                core = arr
            h_rep_norm = self.encode(core, normalize=True)
            if h_rep_norm.ndim == 1:
                h_rep_norm = h_rep_norm[None, :]
            r_mean = np.mean(h_rep_norm, axis=0)
            r_mean = r_mean / max(float(np.linalg.norm(r_mean)), 1e-8)
            rep_means_norm.append(r_mean)
            core_frames_list.append(core)

        if len(rep_means_norm) < int(min_reps):
            raise ValueError(f"Valid repetitions ({len(rep_means_norm)}) below minimum ({min_reps})")

        all_core_x = np.concatenate(core_frames_list, axis=0)
        h_raw_all = self.encode(all_core_x, normalize=False)
        h_norm_all = self.encode(all_core_x, normalize=True)
        if h_raw_all.ndim == 1:
            h_raw_all = h_raw_all[None, :]
            h_norm_all = h_norm_all[None, :]

        custom_proto = np.mean(np.stack(rep_means_norm, axis=0), axis=0)
        custom_proto = custom_proto / max(float(np.linalg.norm(custom_proto)), 1e-8)

        # Inter-repetition consistency
        rep_sims = [float(r @ custom_proto) for r in rep_means_norm]
        rep_consistency = float(np.mean(rep_sims))

        # Safety Check 1: Prototype proximity against all existing base & custom prototypes
        closest_class = ""
        closest_cosine = -1.0
        if self.prototypes is not None and self.prototypes.shape == (len(self.base_classes), self.hidden_units):
            for idx, b_cls in enumerate(self.base_classes):
                if b_cls == clean_label:
                    continue
                sim = float(custom_proto @ self.prototypes[idx])
                if sim > closest_cosine:
                    closest_cosine = sim
                    closest_class = b_cls
        for c_cls, c_p in self.custom_prototypes.items():
            if c_cls == clean_label:
                continue
            sim = float(custom_proto @ c_p)
            if sim > closest_cosine:
                closest_cosine = sim
                closest_class = c_cls

        proximity_warning = ""
        if closest_cosine >= float(proximity_warn_cosine):
            proximity_warning = (
                f"기존 동작 '{closest_class}'와 프로토타입 유사도가 높습니다 "
                f"(cos={closest_cosine:.3f} >= {proximity_warn_cosine:.2f}): 혼동 위험이 있으니 다른 손모양을 권장합니다."
            )

        # Fit shrinkage Mahalanobis OOD statistics for the custom gesture
        c_mean_raw = np.mean(h_raw_all, axis=0)
        diff = h_raw_all - c_mean_raw[None, :]
        emp_cov = (diff.T @ diff) / max(len(h_raw_all) - 1, 1)
        if self.ood_precisions:
            avg_prec = np.mean(np.stack(list(self.ood_precisions.values()), axis=0), axis=0)
            prior_cov = np.linalg.pinv(avg_prec)
        else:
            prior_cov = np.eye(self.hidden_units, dtype=np.float64)
        # Strong shrinkage toward global covariance since few-shot has only 3-5 reps
        shrunk_cov = 0.35 * emp_cov + 0.65 * prior_cov + 1e-4 * np.eye(self.hidden_units, dtype=np.float64)
        c_prec = np.linalg.pinv(shrunk_cov)
        d_maha = np.sqrt(np.maximum(np.sum((diff @ c_prec) * diff, axis=1), 0.0))
        prior_thr = float(np.median(list(self.ood_thresholds.values()))) if self.ood_thresholds else 6.5
        c_thr = max(float(np.percentile(d_maha, 95.0)) * 1.25, prior_thr * 0.95, 5.5)

        self.custom_prototypes[clean_label] = custom_proto
        self._anchor_custom_prototypes[clean_label] = custom_proto.copy()
        self.custom_ood_means[clean_label] = c_mean_raw
        self.ood_precisions[clean_label] = c_prec
        self.ood_thresholds[clean_label] = round(c_thr, 4)
        if clean_label not in self.classes:
            self.classes = tuple(list(self.classes) + [clean_label])

        # Safety Check 2: 30s neutral-movement false-fire check
        neutral_misfires_default = 0
        neutral_misfires_tuned = 0
        rec_threshold = float(base_threshold)
        neutral_warning = ""
        if neutral_features is not None:
            n_arr = np.asarray(neutral_features, dtype=np.float64)
            if n_arr.ndim == 1 and n_arr.size == self.feat_dim:
                n_arr = n_arr[None, :]
            if n_arr.ndim == 2 and len(n_arr) > 0 and n_arr.shape[1] == self.feat_dim:
                n_probs = self.predict_proba(n_arr)
                c_idx = self.classes.index(clean_label)
                c_p_seq = n_probs[:, c_idx]

                def _count_runs(p_seq: np.ndarray, thr_val: float, k_req: int) -> int:
                    runs_cnt, cur = 0, 0
                    for val in p_seq:
                        if val >= thr_val:
                            cur += 1
                            if cur == k_req:
                                runs_cnt += 1
                        else:
                            cur = 0
                    return runs_cnt

                neutral_misfires_default = _count_runs(c_p_seq, rec_threshold, int(k_frames))
                neutral_misfires_tuned = neutral_misfires_default
                while neutral_misfires_tuned > 0 and rec_threshold < 0.92:
                    rec_threshold = round(min(0.92, rec_threshold + 0.04), 2)
                    neutral_misfires_tuned = _count_runs(c_p_seq, rec_threshold, int(k_frames))
                if neutral_misfires_default > 0:
                    neutral_warning = (
                        f"30초 평소 이동 중 기본 임계값({base_threshold:.2f})에서 오발화 {neutral_misfires_default}회 감지됨 "
                        f"→ 권장 임계값 {rec_threshold:.2f} (조정 후 오발화 {neutral_misfires_tuned}회)"
                    )

        return {
            "label": clean_label,
            "encoder_frozen": self.encoder_frozen,
            "reps_used": len(rep_means_norm),
            "frames_used": int(len(all_core_x)),
            "rep_consistency_cosine": round(rep_consistency, 4),
            "closest_class": closest_class,
            "closest_cosine": round(closest_cosine, 4),
            "proximity_warning": proximity_warning,
            "neutral_misfires_at_default": int(neutral_misfires_default),
            "neutral_misfires_after_tuning": int(neutral_misfires_tuned),
            "recommended_threshold": round(rec_threshold, 2),
            "neutral_check_passed": bool(neutral_misfires_tuned == 0),
            "neutral_warning": neutral_warning,
            "ood_threshold": round(c_thr, 4),
            "prototype": np.round(custom_proto, 6).tolist(),
        }

    def adapt_prototype_ema(
        self,
        label: str,
        rep_features: np.ndarray,
        *,
        calibrated_confidence: float,
        quality_score: float,
        ood_ratio: float,
        user_cancelled: bool = False,
        ema_alpha: float = 0.04,
        min_confidence: float = 0.88,
        min_quality: float = 0.72,
        max_ood_ratio: float = 0.90,
        min_anchor_cosine: float = 0.92,
    ) -> Dict[str, Any]:
        """Stage 8-3: Bounded EMA runtime prototype adaptation with shadow-mode logging and drift cap."""
        if not self.encoder_frozen:
            raise RuntimeError("Runtime prototype adaptation requires encoder_frozen=True")
        lbl = str(label or "").strip()
        if user_cancelled:
            return {"accepted": False, "applied": False, "reason": "user_cancelled", "label": lbl}
        if not lbl or lbl == "none":
            return {"accepted": False, "applied": False, "reason": "invalid_label", "label": lbl}
        if float(calibrated_confidence) < float(min_confidence):
            return {"accepted": False, "applied": False, "reason": "low_confidence", "label": lbl}
        if float(quality_score) < float(min_quality):
            return {"accepted": False, "applied": False, "reason": "low_quality", "label": lbl}
        if float(ood_ratio) > float(max_ood_ratio):
            return {"accepted": False, "applied": False, "reason": "ood_ratio_too_high", "label": lbl}

        arr = np.asarray(rep_features, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[None, :]
        h_norm = self.encode(arr, normalize=True)
        if h_norm.ndim == 1:
            h_norm = h_norm[None, :]
        obs_proto = np.mean(h_norm, axis=0)
        obs_proto = obs_proto / max(float(np.linalg.norm(obs_proto)), 1e-8)

        is_custom = lbl in self.custom_prototypes
        if is_custom:
            cur_proto = self.custom_prototypes[lbl]
            anchor_proto = self._anchor_custom_prototypes.get(lbl, cur_proto)
        elif lbl in self.base_classes and self.prototypes is not None:
            c_idx = self.base_classes.index(lbl)
            cur_proto = self.prototypes[c_idx]
            anchor_proto = (
                self._anchor_prototypes[c_idx]
                if self._anchor_prototypes is not None
                else cur_proto
            )
        else:
            return {"accepted": False, "applied": False, "reason": "unknown_class", "label": lbl}

        alpha = float(np.clip(ema_alpha, 0.005, 0.25))
        cand_proto = (1.0 - alpha) * cur_proto + alpha * obs_proto
        cand_proto = cand_proto / max(float(np.linalg.norm(cand_proto)), 1e-8)
        anchor_cos = float(cand_proto @ anchor_proto)
        step_cos = float(cand_proto @ cur_proto)

        if anchor_cos < float(min_anchor_cosine):
            entry = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "label": lbl,
                "accepted": False,
                "applied": False,
                "shadow_mode": self.shadow_mode,
                "reason": "drift_cap_exceeded",
                "anchor_cosine": round(anchor_cos, 5),
                "min_anchor_cosine": float(min_anchor_cosine),
            }
            self.shadow_adaptation_log.append(entry)
            return entry

        applied = False
        if not self.shadow_mode:
            if not self.rollback_stack:
                self.create_rollback_point("pre_ema_adaptation")
            if is_custom:
                self.custom_prototypes[lbl] = cand_proto
            else:
                self.prototypes[self.base_classes.index(lbl)] = cand_proto
                self._prototypes_adapted = True
                self.metadata["prototypes_adapted"] = True
            applied = True

        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "label": lbl,
            "accepted": True,
            "applied": applied,
            "shadow_mode": self.shadow_mode,
            "reason": "shadow_logged" if self.shadow_mode else "ema_updated",
            "ema_alpha": round(alpha, 4),
            "step_cosine": round(step_cos, 6),
            "anchor_cosine": round(anchor_cos, 5),
            "calibrated_confidence": round(float(calibrated_confidence), 4),
            "quality_score": round(float(quality_score), 4),
            "ood_ratio": round(float(ood_ratio), 4),
        }
        self.shadow_adaptation_log.append(entry)
        return entry

    def predict_calibrated_proba(
        self,
        features: np.ndarray,
        *,
        temperature: Optional[float] = None,
    ) -> np.ndarray:
        """Return temperature-scaled probability distribution (`T = self.temperature`)."""
        probs = self.predict_proba(features)
        t = self.temperature if temperature is None else float(temperature)
        return apply_temperature_scaling(probs, t)

    def predict(self, features: np.ndarray) -> Dict[str, Any]:
        """Predict top class, raw confidence, calibrated confidence, and probability map for a single frame `(D,)`."""
        probs = self.predict_proba(features)
        cal_probs = apply_temperature_scaling(probs, self.temperature)
        best_idx = int(np.argmax(probs))
        return {
            "action": self.classes[best_idx],
            "confidence": float(probs[best_idx]),
            "calibrated_confidence": float(cal_probs[best_idx]),
            "temperature": round(float(self.temperature), 4),
            "probabilities": {cls: float(probs[i]) for i, cls in enumerate(self.classes)},
            "calibrated_probabilities": {cls: float(cal_probs[i]) for i, cls in enumerate(self.classes)},
        }

    def __call__(self, window: np.ndarray) -> np.ndarray:
        """Accepts `(D,)`, `(W, D)`, or `(1, W, D)` and predicts on the latest frame."""
        arr = np.asarray(window, dtype=np.float64)
        if arr.ndim == 3:
            arr = arr[0, -1]
        elif arr.ndim == 2:
            arr = arr[-1]
        return self.predict_proba(arr)

    def to_dict(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = {
            "schema": "manux-spatial-mlp/1",
            "feature_version": self.feature_version,
            "feat_dim": self.feat_dim,
            "hidden_units": self.hidden_units,
            "encoder_frozen": self.encoder_frozen,
            "temperature": round(float(self.temperature), 4),
            "classes": list(self.base_classes),
            "metadata": self.metadata,
            "weights": {
                "W1": np.round(self.w1, 6).tolist(),
                "b1": np.round(self.b1, 6).tolist(),
                "W2": np.round(self.w2, 6).tolist(),
                "b2": np.round(self.b2, 6).tolist(),
            },
        }
        if self.prototypes is not None:
            doc["prototypes"] = np.round(self.prototypes, 6).tolist()
        if self.custom_prototypes:
            doc["custom_prototypes"] = {
                k: np.round(v, 6).tolist() for k, v in self.custom_prototypes.items()
            }
        if self.ood_means is not None and self.ood_precisions and self.ood_thresholds:
            ood_payload: Dict[str, Any] = {
                "means": np.round(self.ood_means, 5).tolist(),
                "thresholds": {k: round(float(v), 4) for k, v in self.ood_thresholds.items()},
                "precisions": {
                    k: np.round(v, 4).tolist() for k, v in self.ood_precisions.items()
                },
            }
            if self.custom_ood_means:
                ood_payload["custom_means"] = {
                    k: np.round(v, 5).tolist() for k, v in self.custom_ood_means.items()
                }
            doc["ood"] = ood_payload
        if self.specialists:
            doc["specialists"] = {
                lbl: spec.to_dict() for lbl, spec in self.specialists.items()
            }
        return doc

    def save(self, path: Path = DEFAULT_SPATIAL_MLP_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> SpatialMlpModel:
        if not isinstance(doc, Mapping) or doc.get("schema") != "manux-spatial-mlp/1":
            raise ValueError("invalid spatial MLP document")
        weights = doc["weights"]
        raw_specs = doc.get("specialists") or {}
        parsed_specs: Dict[str, SpatialMlpModel] = {}
        if isinstance(raw_specs, Mapping):
            for lbl, sdoc in raw_specs.items():
                if isinstance(sdoc, Mapping) and sdoc.get("schema") == "manux-spatial-mlp/1":
                    try:
                        parsed_specs[str(lbl)] = cls.from_dict(sdoc)
                    except Exception:
                        pass
        raw_protos = doc.get("prototypes")
        raw_custom_protos = doc.get("custom_prototypes") if isinstance(doc.get("custom_prototypes"), Mapping) else {}
        ood_doc = doc.get("ood") if isinstance(doc.get("ood"), Mapping) else {}
        raw_ood_means = ood_doc.get("means")
        raw_custom_means = ood_doc.get("custom_means") if isinstance(ood_doc.get("custom_means"), Mapping) else {}
        raw_ood_precs = ood_doc.get("precisions") if isinstance(ood_doc.get("precisions"), Mapping) else {}
        raw_ood_thrs = ood_doc.get("thresholds") if isinstance(ood_doc.get("thresholds"), Mapping) else {}
        meta = dict(doc.get("metadata") or {})
        raw_temp = doc.get("temperature", meta.get("temperature", 1.0))
        return cls(
            w1=np.asarray(weights["W1"], dtype=np.float64),
            b1=np.asarray(weights["b1"], dtype=np.float64),
            w2=np.asarray(weights["W2"], dtype=np.float64),
            b2=np.asarray(weights["b2"], dtype=np.float64),
            classes=tuple(doc["classes"]),
            feature_version=str(doc.get("feature_version", SPATIAL_FEATURE_VERSION)),
            metadata=meta,
            specialists=parsed_specs,
            prototypes=np.asarray(raw_protos, dtype=np.float64) if raw_protos is not None else None,
            ood_means=np.asarray(raw_ood_means, dtype=np.float64) if raw_ood_means is not None else None,
            ood_precisions={str(k): np.asarray(v, dtype=np.float64) for k, v in raw_ood_precs.items()},
            ood_thresholds={str(k): float(v) for k, v in raw_ood_thrs.items()},
            encoder_frozen=bool(doc.get("encoder_frozen", True)),
            temperature=float(raw_temp or 1.0),
            custom_prototypes={str(k): np.asarray(v, dtype=np.float64) for k, v in raw_custom_protos.items()},
            custom_ood_means={str(k): np.asarray(v, dtype=np.float64) for k, v in raw_custom_means.items()},
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_SPATIAL_MLP_PATH) -> SpatialMlpModel:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(doc)


def _resolve_train_test_masks(
    y_arr: np.ndarray,
    *,
    groups: Optional[np.ndarray] = None,
    split_mode: str = "group_leave_out",
    seed: int = 42,
    precomputed_split: Optional[Tuple[np.ndarray, np.ndarray]] = None,
) -> Tuple[np.ndarray, np.ndarray, str]:
    """Return `(train_mask, test_mask, effective_split_mode)` in original sample order.

    - `group_leave_out`: When `groups` has >= 2 unique groups, holds out whole group(s) per class
      so zero frames from held-out posture/session groups leak into training. When `groups` is
      unavailable or single-group for a class, falls back to contiguous temporal block holdout
      (last 20% of contiguous label runs) so adjacent 30fps frames never straddle train/test.
    - `random`: Legacy 80/20 random frame permutation split (used only for leakage quantification).
    """
    n_samples = len(y_arr)
    if precomputed_split is not None:
        tr_m, te_m = precomputed_split
        return np.asarray(tr_m, dtype=bool), np.asarray(te_m, dtype=bool), split_mode
    if n_samples < 10:
        all_true = np.ones(n_samples, dtype=bool)
        return all_true, all_true.copy(), "small_all"

    mode = str(split_mode or "group_leave_out").strip().lower()
    rng = np.random.default_rng(seed)
    if mode == "random":
        perm = rng.permutation(n_samples)
        split = max(1, min(n_samples - 1, int(n_samples * 0.8)))
        train_mask = np.zeros(n_samples, dtype=bool)
        test_mask = np.zeros(n_samples, dtype=bool)
        train_mask[perm[:split]] = True
        test_mask[perm[split:]] = True
        return train_mask, test_mask, "random"

    train_mask = np.ones(n_samples, dtype=bool)
    test_mask = np.zeros(n_samples, dtype=bool)
    used_group_split = False

    grp_arr: Optional[np.ndarray] = None
    if groups is not None and len(groups) == n_samples:
        grp_arr = np.asarray(groups)

    unique_classes = np.unique(y_arr)
    for c in unique_classes:
        cls_idx = np.flatnonzero(y_arr == c)
        n_cls = len(cls_idx)
        if n_cls < 5:
            continue
        if grp_arr is not None:
            cls_groups = grp_arr[cls_idx]
            uniq_g = []
            for g in cls_groups:
                if g not in uniq_g and (not isinstance(g, (int, np.integer)) or int(g) >= 0):
                    uniq_g.append(g)
            if len(uniq_g) >= 2:
                # Hold out the last group(s) up to ~20-35% of class samples (at least 1 group)
                held_g = []
                held_count = 0
                for g in reversed(uniq_g):
                    g_cnt = int(np.sum(cls_groups == g))
                    if not held_g or (held_count + g_cnt <= max(int(n_cls * 0.30), 1) and len(held_g) < len(uniq_g) - 1):
                        held_g.append(g)
                        held_count += g_cnt
                    else:
                        break
                in_held = np.isin(cls_groups, held_g)
                if np.any(in_held) and not np.all(in_held):
                    test_idx = cls_idx[in_held]
                    train_mask[test_idx] = False
                    test_mask[test_idx] = True
                    used_group_split = True
                    continue

        # Contiguous temporal block holdout (last 20% of class samples in temporal order)
        split_c = max(1, min(n_cls - 1, int(n_cls * 0.8)))
        test_idx = cls_idx[split_c:]
        train_mask[test_idx] = False
        test_mask[test_idx] = True

    if not np.any(test_mask) or not np.any(train_mask):
        split = max(1, min(n_samples - 1, int(n_samples * 0.8)))
        train_mask = np.zeros(n_samples, dtype=bool)
        test_mask = np.zeros(n_samples, dtype=bool)
        train_mask[:split] = True
        test_mask[split:] = True
        return train_mask, test_mask, "block_leave_out"

    return train_mask, test_mask, ("group_leave_out" if used_group_split else "block_leave_out")


def _fit_mlp_weights(
    x_train: np.ndarray,
    y_train: np.ndarray,
    w_train: np.ndarray,
    *,
    num_classes: int,
    hidden_units: int,
    epochs: int,
    batch_size: int,
    lr: float,
    l2: float,
    seed: int,
    device: Optional[str] = None,
    supcon_weight: float = 0.22,
    supcon_temp: float = 0.12,
    groups_train: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fit 2-layer LeakyReLU(0.05) + Softmax weights with Supervised Contrastive Loss (`SupCon`)."""
    n_train, feat_dim = x_train.shape
    rng = np.random.default_rng(seed)
    w1 = rng.standard_normal((hidden_units, feat_dim)) * math.sqrt(2.0 / feat_dim)
    b1 = np.full(hidden_units, 0.01, dtype=np.float64)
    w2 = rng.standard_normal((num_classes, hidden_units)) * math.sqrt(2.0 / hidden_units)
    b2 = np.zeros(num_classes, dtype=np.float64)

    sc_w = float(supcon_weight) if num_classes > 2 else 0.0
    g_ids: Optional[np.ndarray] = None
    if sc_w > 0.0 and groups_train is not None and len(groups_train) == n_train:
        g_arr = np.asarray(groups_train)
        if np.issubdtype(g_arr.dtype, np.integer):
            g_ids = g_arr.astype(np.int64, copy=False)
        else:
            _, g_inv = np.unique(g_arr.astype(str), return_inverse=True)
            g_ids = g_inv.astype(np.int64)

    req_dev = str(device or "auto").strip().lower()
    use_cuda = False
    if req_dev != "cpu":
        try:
            import torch
            if torch.cuda.is_available() and (req_dev in ("gpu", "cuda") or n_train >= 256):
                use_cuda = True
        except Exception:
            use_cuda = False

    if use_cuda:
        import torch
        t_dev = torch.device("cuda")
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

        tw1 = torch.tensor(w1, dtype=torch.float32, device=t_dev, requires_grad=True)
        tb1 = torch.tensor(b1, dtype=torch.float32, device=t_dev, requires_grad=True)
        tw2 = torch.tensor(w2, dtype=torch.float32, device=t_dev, requires_grad=True)
        tb2 = torch.tensor(b2, dtype=torch.float32, device=t_dev, requires_grad=True)

        tx_train = torch.from_numpy(np.ascontiguousarray(x_train, dtype=np.float32)).to(t_dev)
        ty_train = torch.from_numpy(np.ascontiguousarray(y_train, dtype=np.int64)).to(t_dev)
        tw_train = torch.from_numpy(np.ascontiguousarray(w_train, dtype=np.float32)).to(t_dev)
        tg_train = (
            torch.from_numpy(np.ascontiguousarray(g_ids, dtype=np.int64)).to(t_dev)
            if g_ids is not None
            else torch.zeros(n_train, dtype=torch.int64, device=t_dev)
        )

        use_large_bs = n_train >= 4096 and batch_size <= 256
        eff_batch_size = 2048 if use_large_bs else min(batch_size, max(4, n_train // 8))
        eff_lr = lr * 1.5 if use_large_bs else lr
        opt = torch.optim.Adam([tw1, tb1, tw2, tb2], lr=eff_lr)
        sc_t = max(float(supcon_temp), 0.04)
        max_sub = 256
        eye_full = torch.eye(max_sub, device=t_dev, dtype=torch.bool) if sc_w > 0.0 else None

        for ep_i in range(max(1, epochs)):
            ep_perm = torch.randperm(n_train, device=t_dev)
            x_ep = tx_train[ep_perm]
            y_ep = ty_train[ep_perm]
            w_ep = tw_train[ep_perm]
            g_ep = tg_train[ep_perm] if sc_w > 0.0 else None

            for b_start in range(0, n_train, eff_batch_size):
                xb = x_ep[b_start : b_start + eff_batch_size]
                yb = y_ep[b_start : b_start + eff_batch_size]
                wb = w_ep[b_start : b_start + eff_batch_size]

                opt.zero_grad(set_to_none=True)
                z1 = xb @ tw1.t() + tb1
                h = torch.nn.functional.leaky_relu(z1, negative_slope=0.05)
                logits = h @ tw2.t() + tb2
                per_sample = torch.nn.functional.cross_entropy(logits, yb, reduction="none")
                reg = 0.5 * l2 * (torch.sum(tw1 * tw1) + torch.sum(tw2 * tw2))
                loss = torch.mean(per_sample * wb) + reg

                if sc_w > 0.0 and (ep_i % 2 == 0) and len(xb) >= 32 and g_ep is not None and eye_full is not None:
                    gb = g_ep[b_start : b_start + eff_batch_size]
                    sub_n = min(max_sub, len(xb))
                    hs = torch.nn.functional.normalize(h[:sub_n], dim=1, eps=1e-8)
                    ys = yb[:sub_n]
                    gs = gb[:sub_n]
                    sim = (hs @ hs.t()) / sc_t
                    sim = sim - torch.max(sim, dim=1, keepdim=True)[0].detach()
                    eye = eye_full[:sub_n, :sub_n]
                    not_eye_f = (~eye).float()
                    same_cls = (ys.unsqueeze(0) == ys.unsqueeze(1)) & (~eye)
                    diff_grp = gs.unsqueeze(0) != gs.unsqueeze(1)
                    pos_mask = same_cls & diff_grp
                    has_cross = pos_mask.sum(dim=1, keepdim=True) > 0
                    pos_mask = torch.where(has_cross, pos_mask, same_cls)
                    exp_sim = torch.exp(sim) * not_eye_f
                    log_prob = sim - torch.log(exp_sim.sum(dim=1, keepdim=True) + 1e-8)
                    pos_cnt = pos_mask.sum(dim=1)
                    valid_f = ((pos_cnt > 0) & (ys > 0)).float()
                    per_anchor = -((log_prob * pos_mask.float()).sum(dim=1) / torch.clamp(pos_cnt.float(), min=1.0))
                    sc_loss = (per_anchor * valid_f).sum() / torch.clamp(valid_f.sum(), min=1.0)
                    loss = loss + (sc_w * 1.5) * sc_loss

                loss.backward()
                opt.step()

        w1 = tw1.detach().cpu().numpy().astype(np.float64)
        b1 = tb1.detach().cpu().numpy().astype(np.float64)
        w2 = tw2.detach().cpu().numpy().astype(np.float64)
        b2 = tb2.detach().cpu().numpy().astype(np.float64)
    else:
        mw1, vw1 = np.zeros_like(w1), np.zeros_like(w1)
        mb1, vb1 = np.zeros_like(b1), np.zeros_like(b1)
        mw2, vw2 = np.zeros_like(w2), np.zeros_like(w2)
        mb2, vb2 = np.zeros_like(b2), np.zeros_like(b2)

        beta1, beta2, eps = 0.9, 0.999, 1e-8
        step = 0
        eff_batch_size = min(batch_size, max(4, n_train // 8))

        for _ in range(max(1, epochs)):
            ep_perm = rng.permutation(n_train)
            x_ep = x_train[ep_perm]
            y_ep = y_train[ep_perm]
            w_ep = w_train[ep_perm]

            for b_start in range(0, n_train, eff_batch_size):
                xb = x_ep[b_start : b_start + eff_batch_size]
                yb = y_ep[b_start : b_start + eff_batch_size]
                wb = w_ep[b_start : b_start + eff_batch_size]
                b_len = len(xb)
                step += 1

                z1 = xb @ w1.T + b1
                h = np.where(z1 > 0.0, z1, z1 * 0.05)
                logits = h @ w2.T + b2
                logits = logits - np.max(logits, axis=1, keepdims=True)
                exp_l = np.exp(logits)
                probs = exp_l / np.sum(exp_l, axis=1, keepdims=True)

                d_logits = probs.copy()
                d_logits[np.arange(b_len), yb] -= 1.0
                d_logits *= (wb[:, None] / b_len)

                gw2 = d_logits.T @ h + l2 * w2
                gb2 = np.sum(d_logits, axis=0)

                dh = d_logits @ w2
                dz1 = np.where(z1 > 0.0, dh, dh * 0.05)
                gw1 = dz1.T @ xb + l2 * w1
                gb1 = np.sum(dz1, axis=0)

                b1_corr = 1.0 - beta1 ** step
                b2_corr = 1.0 - beta2 ** step

                mw1 = beta1 * mw1 + (1.0 - beta1) * gw1
                vw1 = beta2 * vw1 + (1.0 - beta2) * (gw1 * gw1)
                w1 -= lr * (mw1 / b1_corr) / (np.sqrt(vw1 / b2_corr) + eps)

                mb1 = beta1 * mb1 + (1.0 - beta1) * gb1
                vb1 = beta2 * vb1 + (1.0 - beta2) * (gb1 * gb1)
                b1 -= lr * (mb1 / b1_corr) / (np.sqrt(vb1 / b2_corr) + eps)

                mw2 = beta1 * mw2 + (1.0 - beta1) * gw2
                vw2 = beta2 * vw2 + (1.0 - beta2) * (gw2 * gw2)
                w2 -= lr * (mw2 / b1_corr) / (np.sqrt(vw2 / b2_corr) + eps)

                mb2 = beta1 * mb2 + (1.0 - beta1) * gb2
                vb2 = beta2 * vb2 + (1.0 - beta2) * (gb2 * gb2)
                b2 -= lr * (mb2 / b1_corr) / (np.sqrt(vb2 / b2_corr) + eps)

    return w1, b1, w2, b2


def train_binary_spatial_specialist(
    x_all: np.ndarray,
    y_all: np.ndarray,
    classes: Sequence[str],
    target_label: str,
    *,
    weights_all: Optional[np.ndarray] = None,
    hidden_units: int = 48,
    epochs: int = 45,
    batch_size: int = 128,
    lr: float = 0.003,
    l2: float = 1e-4,
    seed: int = 42,
    hard_negative_weight: float = 1.65,
    independent: bool = False,
    feature_version: str = SPATIAL_FEATURE_VERSION,
    device: Optional[str] = None,
    groups: Optional[np.ndarray] = None,
    split_mode: str = "group_leave_out",
    precomputed_split: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    apply_stage6_augment: bool = True,
) -> Tuple[SpatialMlpModel, SpatialMlpMetrics]:
    """Train a dedicated binary Spatial MLP (`['none', target_label]`) for a single gesture.

    Competing gesture postures (`label != 'none'` and `label != target_label`) receive
    `hard_negative_weight` so adjacent finger pinches are sharply separated.
    """
    cls_list = list(classes)
    if target_label not in cls_list or target_label == "none":
        raise ValueError(f"유효하지 않은 대상 동작: {target_label}")
    target_idx = cls_list.index(target_label)
    none_idx = cls_list.index("none") if "none" in cls_list else -1

    y_orig = np.asarray(y_all, dtype=np.int64)
    y_bin = (y_orig == target_idx).astype(np.int64)

    counts = np.bincount(y_bin, minlength=2).astype(np.float64)
    cls_w = (counts.sum() / np.maximum(counts, 1.0)) ** 0.5
    cls_w = cls_w / np.maximum(cls_w.mean(), 1e-8)
    sample_weights = cls_w[y_bin].copy()

    # Emphasize competing non-neutral gestures as hard negatives
    competing_mask = (y_orig != target_idx) & (y_orig != none_idx) & (y_orig >= 0)
    if np.any(competing_mask):
        sample_weights[competing_mask] *= float(hard_negative_weight)
    if weights_all is not None and len(weights_all) == len(sample_weights):
        sample_weights *= np.asarray(weights_all, dtype=np.float64)

    spec_model, spec_metrics = train_spatial_mlp(
        x_all,
        y_bin,
        weights_all=sample_weights,
        classes=("none", target_label),
        hidden_units=hidden_units,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
        l2=l2,
        seed=seed,
        class_balance=False,
        feature_version=feature_version,
        device=device,
        train_specialists=False,
        groups=groups,
        split_mode=split_mode,
        precomputed_split=precomputed_split,
        compute_random_comparison=False,
        apply_stage6_augment=apply_stage6_augment,
    )
    spec_model.metadata["target"] = target_label
    spec_model.metadata["model_kind"] = "spatial_binary_specialist"
    spec_model.metadata["independent"] = bool(independent)
    return spec_model, spec_metrics


def train_spatial_mlp(
    x_all: np.ndarray,
    y_all: np.ndarray,
    weights_all: Optional[np.ndarray] = None,
    *,
    classes: Sequence[str] = STUDIO_CLASSES,
    hidden_units: int = 64,
    epochs: int = 45,
    batch_size: int = 128,
    lr: float = 0.003,
    l2: float = 1e-4,
    seed: int = 42,
    class_balance: bool = True,
    feature_version: str = SPATIAL_FEATURE_VERSION,
    device: Optional[str] = None,
    train_specialists: bool = True,
    groups: Optional[np.ndarray] = None,
    split_mode: str = "group_leave_out",
    precomputed_split: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    compute_random_comparison: bool = True,
    apply_stage6_augment: bool = True,
    supcon_weight: float = 0.22,
    supcon_temp: float = 0.12,
    ood_shrinkage: float = 0.15,
    ood_percentile: float = 95.0,
) -> Tuple[SpatialMlpModel, SpatialMlpMetrics]:
    """Train a 2-layer Spatial MLP + per-gesture binary specialist MLPs using CUDA or vectorized NumPy Adam.

    By default (`split_mode='group_leave_out'`), uses group-based / contiguous-block leave-out splitting
    and ensures specialists evaluated on `x_test` never train on `x_test`. Also computes legacy 80/20
    random-frame split accuracy (`random_test_accuracy`) side-by-side to quantify adjacent-frame leakage.
    """
    t0 = time.perf_counter()
    x_arr = np.asarray(x_all, dtype=np.float64)
    y_arr = np.asarray(y_all, dtype=np.int64)
    n_samples, feat_dim = x_arr.shape
    num_classes = len(classes)
    if n_samples < 2:
        raise ValueError("학습하려면 최소 2개 이상의 표본이 필요합니다.")

    if class_balance:
        counts = np.bincount(y_arr, minlength=num_classes).astype(np.float64)
        cls_w = (counts.sum() / np.maximum(counts, 1.0)) ** 0.5
        cls_w = cls_w / np.maximum(cls_w.mean(), 1e-8)
        sample_weights = cls_w[y_arr].copy()
        if weights_all is not None and len(weights_all) == n_samples:
            sample_weights *= np.asarray(weights_all, dtype=np.float64)
    elif weights_all is not None:
        sample_weights = np.asarray(weights_all, dtype=np.float64)
    else:
        sample_weights = np.ones(n_samples, dtype=np.float64)

    train_mask, test_mask, eff_split_mode = _resolve_train_test_masks(
        y_arr,
        groups=groups,
        split_mode=split_mode,
        seed=seed,
        precomputed_split=precomputed_split,
    )
    x_train_raw, x_test = x_arr[train_mask], x_arr[test_mask]
    y_train, y_test = y_arr[train_mask], y_arr[test_mask]
    w_train = sample_weights[train_mask]
    g_train = np.asarray(groups)[train_mask] if (groups is not None and len(groups) == n_samples) else None

    x_train = x_train_raw
    if apply_stage6_augment and feat_dim == 96 and len(x_train) >= 16:
        from src.offline_augment import augment_spatial_features_96
        x_train = augment_spatial_features_96(x_train_raw, seed=seed)

    w1, b1, w2, b2 = _fit_mlp_weights(
        x_train,
        y_train,
        w_train,
        num_classes=num_classes,
        hidden_units=hidden_units,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
        l2=l2,
        seed=seed,
        device=device,
        supcon_weight=supcon_weight if num_classes > 2 else 0.0,
        supcon_temp=supcon_temp,
        groups_train=g_train,
    )

    spec_models: Dict[str, SpatialMlpModel] = {}
    spec_meta_summary: Dict[str, Any] = {}
    if train_specialists and num_classes > 2 and "none" in classes:
        spec_hidden = min(48, max(24, hidden_units))
        spec_epochs = max(20, int(epochs * 0.85))
        for c_idx, cls_name in enumerate(classes):
            if cls_name == "none":
                continue
            if int(np.sum(y_train == c_idx)) < 1 or int(np.sum(y_train != c_idx)) < 1:
                continue
            try:
                sp_spec, sp_spec_m = train_binary_spatial_specialist(
                    x_arr,
                    y_arr,
                    classes=classes,
                    target_label=cls_name,
                    weights_all=weights_all,
                    hidden_units=spec_hidden,
                    epochs=spec_epochs,
                    batch_size=batch_size,
                    lr=lr,
                    l2=l2,
                    seed=seed + c_idx + 1,
                    independent=False,
                    feature_version=feature_version,
                    device=device,
                    groups=groups,
                    split_mode=eff_split_mode,
                    precomputed_split=(train_mask, test_mask),
                )
                spec_models[cls_name] = sp_spec
                spec_meta_summary[cls_name] = {
                    "hidden_units": sp_spec.hidden_units,
                    "train_accuracy": sp_spec_m.train_accuracy,
                    "test_accuracy": sp_spec_m.test_accuracy,
                    "split_mode": sp_spec_m.split_mode,
                }
            except Exception:
                pass

    temp_model = SpatialMlpModel(
        w1, b1, w2, b2,
        classes=classes,
        feature_version=feature_version,
        specialists=spec_models,
    )
    train_probs = temp_model.predict_proba(x_train_raw)
    test_probs = temp_model.predict_proba(x_test)
    all_probs = temp_model.predict_proba(x_arr)
    train_preds = np.argmax(train_probs, axis=1)
    test_preds = np.argmax(test_probs, axis=1)
    all_preds = np.argmax(all_probs, axis=1)

    # Fit validation-split temperature scaling (monotonic in probability space; preserves argmax accuracy)
    cal_temp = 1.0
    cal_summary: Dict[str, Any] = {}
    if len(x_test) >= 16 and num_classes >= 2:
        cal_temp, cal_summary = fit_temperature_scaling(test_probs, y_test)

    # Fit L2-normalized class prototypes and shrinkage Mahalanobis OOD statistics on the frozen encoder
    h_tr = temp_model.encode(x_train_raw, normalize=False)
    hn_tr = temp_model.encode(x_train_raw, normalize=True)
    h_te = temp_model.encode(x_test, normalize=False)
    h_dim = int(w1.shape[0])
    prototypes = np.zeros((num_classes, h_dim), dtype=np.float64)
    ood_means = np.zeros((num_classes, h_dim), dtype=np.float64)
    ood_precisions: Dict[str, np.ndarray] = {}
    ood_thresholds: Dict[str, float] = {}
    shrink = float(np.clip(ood_shrinkage, 0.01, 0.90))
    pct = float(np.clip(ood_percentile, 50.0, 99.9))

    for c_idx, cls_name in enumerate(classes):
        tr_c = y_train == c_idx
        if np.any(tr_c):
            proto_c = np.mean(hn_tr[tr_c], axis=0)
            p_norm = float(np.linalg.norm(proto_c))
            prototypes[c_idx] = proto_c / max(p_norm, 1e-8)
            ood_means[c_idx] = np.mean(h_tr[tr_c], axis=0)
            if cls_name != "none" and int(np.sum(tr_c)) >= 8:
                hc = h_tr[tr_c]
                cov = np.cov(hc, rowvar=False)
                tr_avg = max(float(np.trace(cov)) / max(h_dim, 1), 1e-4)
                cov_s = (1.0 - shrink) * cov + shrink * tr_avg * np.eye(h_dim, dtype=np.float64)
                prec = np.linalg.pinv(cov_s, rcond=1e-4)
                ood_precisions[str(cls_name)] = prec
                val_c = (y_test == c_idx) & (test_preds == c_idx)
                hc_val = h_te[val_c] if int(np.sum(val_c)) >= 8 else hc
                diff_val = hc_val - ood_means[c_idx]
                dists = np.sqrt(np.maximum(np.sum((diff_val @ prec) * diff_val, axis=1), 0.0))
                ood_thresholds[str(cls_name)] = float(max(np.percentile(dists, pct), 1e-4))
        else:
            w_row = w2[c_idx]
            prototypes[c_idx] = w_row / max(float(np.linalg.norm(w_row)), 1e-8)

    train_acc = float(np.mean(train_preds == y_train) * 100.0)
    test_acc = float(np.mean(test_preds == y_test) * 100.0)

    random_test_acc: Optional[float] = None
    leakage_delta_pct: Optional[float] = None
    if eff_split_mode == "random":
        random_test_acc = round(test_acc, 2)
        leakage_delta_pct = 0.0
    elif compute_random_comparison and n_samples >= 10:
        r_train_m, r_test_m, _ = _resolve_train_test_masks(
            y_arr, groups=None, split_mode="random", seed=seed
        )
        rw1, rb1, rw2, rb2 = _fit_mlp_weights(
            x_arr[r_train_m],
            y_arr[r_train_m],
            sample_weights[r_train_m],
            num_classes=num_classes,
            hidden_units=hidden_units,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            l2=l2,
            seed=seed,
            device=device,
        )
        r_model = SpatialMlpModel(
            rw1, rb1, rw2, rb2,
            classes=classes,
            feature_version=feature_version,
            specialists=spec_models,
        )
        r_preds = np.argmax(r_model.predict_proba(x_arr[r_test_m]), axis=1)
        random_test_acc = round(float(np.mean(r_preds == y_arr[r_test_m]) * 100.0), 2)
        leakage_delta_pct = round(random_test_acc - round(test_acc, 2), 2)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    trained_at = time.strftime("%Y-%m-%d %H:%M:%S")

    per_class: Dict[str, Dict[str, Any]] = {}
    for c_idx, cls_name in enumerate(classes):
        mask = y_arr == c_idx
        total_c = int(np.sum(mask))
        correct_c = int(np.sum(all_preds[mask] == c_idx)) if total_c > 0 else 0
        test_mask_c = y_test == c_idx
        test_total_c = int(np.sum(test_mask_c))
        test_correct_c = int(np.sum(test_preds[test_mask_c] == c_idx)) if test_total_c > 0 else 0
        per_class[cls_name] = {
            "total": total_c,
            "correct": correct_c,
            "accuracy": round((correct_c / total_c) * 100.0, 1) if total_c > 0 else None,
            "test_total": test_total_c,
            "test_correct": test_correct_c,
            "test_accuracy": round((test_correct_c / test_total_c) * 100.0, 1) if test_total_c > 0 else None,
        }

    metrics = SpatialMlpMetrics(
        train_accuracy=round(train_acc, 2),
        test_accuracy=round(test_acc, 2),
        total_samples=int(n_samples),
        train_samples=int(len(x_train_raw)),
        test_samples=int(len(x_test)),
        elapsed_ms=round(elapsed_ms, 1),
        per_class=per_class,
        trained_at=trained_at,
        random_test_accuracy=random_test_acc,
        leakage_delta_pct=leakage_delta_pct,
        split_mode=eff_split_mode,
    )

    model = SpatialMlpModel(
        w1, b1, w2, b2,
        classes=classes,
        feature_version=feature_version,
        metadata={
            "train_accuracy": metrics.train_accuracy,
            "test_accuracy": metrics.test_accuracy,
            "random_test_accuracy": metrics.random_test_accuracy,
            "leakage_delta_pct": metrics.leakage_delta_pct,
            "split_mode": metrics.split_mode,
            "total_samples": metrics.total_samples,
            "train_samples": metrics.train_samples,
            "test_samples": metrics.test_samples,
            "elapsed_ms": metrics.elapsed_ms,
            "per_class": per_class,
            "specialists": spec_meta_summary,
            "supcon_weight": float(supcon_weight if num_classes > 2 else 0.0),
            "supcon_temp": float(supcon_temp),
            "ood_shrinkage": float(shrink),
            "ood_percentile": float(pct),
            "encoder_frozen": True,
            "temperature": round(float(cal_temp), 4),
            "calibration": cal_summary,
            "trained_at": trained_at,
        },
        specialists=spec_models,
        prototypes=prototypes,
        ood_means=ood_means,
        ood_precisions=ood_precisions,
        ood_thresholds=ood_thresholds,
        encoder_frozen=True,
        temperature=cal_temp,
    )
    return model, metrics


def compose_spatial_mlp(
    model_path: Path,
    base_spatial: Optional[SpatialMlpModel] = None,
) -> Optional[SpatialMlpModel]:
    """Compose `base_spatial` (`models/spatial_mlp.json`) with per-gesture specialist MLPs in `models/gestures/`."""
    model_path = Path(model_path)
    if base_spatial is None:
        sp_candidate = model_path.parent / "spatial_mlp.json"
        if sp_candidate.is_file():
            try:
                base_spatial = SpatialMlpModel.load(sp_candidate)
            except Exception:
                base_spatial = None

    bank_dir = model_path.parent / "gestures"
    bank_specs: Dict[str, SpatialMlpModel] = {}
    if (bank_dir / "manifest.json").is_file():
        try:
            from src.gesture_models import read_bank
            entries = read_bank(bank_dir)
            for label, entry in entries.items():
                card_path = bank_dir / entry["card"]
                if not card_path.is_file():
                    continue
                card_doc = json.loads(card_path.read_text(encoding="utf-8"))
                sp_doc = card_doc.get("spatial_mlp")
                if isinstance(sp_doc, Mapping) and sp_doc.get("schema") == "manux-spatial-mlp/1":
                    spec_m = SpatialMlpModel.from_dict(sp_doc)
                    spec_m.metadata["independent"] = True
                    spec_m.metadata["target"] = label
                    bank_specs[label] = spec_m
        except Exception:
            pass

    if base_spatial is None and not bank_specs:
        return None

    if base_spatial is None:
        # Build a neutral base wrapper so the bank specialists can still run independently
        first_spec = next(iter(bank_specs.values()))
        f_dim = first_spec.feat_dim
        h_dim = first_spec.hidden_units
        w1 = np.zeros((h_dim, f_dim), dtype=np.float64)
        b1 = np.zeros(h_dim, dtype=np.float64)
        w2 = np.zeros((1, h_dim), dtype=np.float64)
        b2 = np.array([1.0], dtype=np.float64)
        return SpatialMlpModel(
            w1, b1, w2, b2,
            classes=("none",),
            feature_version=first_spec.feature_version,
            metadata={"composed_from_bank": True},
            specialists=bank_specs,
        )

    if not bank_specs:
        return base_spatial

    merged_specs = dict(base_spatial.specialists)
    merged_specs.update(bank_specs)
    return SpatialMlpModel(
        base_spatial.w1.copy(),
        base_spatial.b1.copy(),
        base_spatial.w2.copy(),
        base_spatial.b2.copy(),
        classes=base_spatial.base_classes,
        feature_version=base_spatial.feature_version,
        metadata={
            **base_spatial.metadata,
            "independent_spatial_gestures": sorted(bank_specs.keys()),
        },
        specialists=merged_specs,
        prototypes=base_spatial.prototypes.copy() if base_spatial.prototypes is not None else None,
        ood_means=base_spatial.ood_means.copy() if base_spatial.ood_means is not None else None,
        ood_precisions={k: v.copy() for k, v in base_spatial.ood_precisions.items()},
        ood_thresholds=dict(base_spatial.ood_thresholds),
        encoder_frozen=base_spatial.encoder_frozen,
        temperature=base_spatial.temperature,
    )


def ensure_session_spatial_features(session: Dict[str, Any]) -> np.ndarray:
    """Attach and return `(N, 96)` `spatial_features` on a session dict loaded by `capture_dataset`."""
    if "spatial_features" in session and isinstance(session["spatial_features"], np.ndarray):
        return session["spatial_features"]
    frames = session.get("frames") or []
    n = len(frames)
    s_feats = np.zeros((n, SPATIAL_FEATURES), dtype=np.float32)
    # If frames were slimmed in disk cache, reload raw landmarks from session["path"] if available
    need_reload = n > 0 and "lm" not in frames[0] and "world" not in frames[0]
    raw_frames = frames
    if need_reload and session.get("path") and Path(session["path"]).is_file():
        try:
            from src.recorder import read_session
            _, raw_frames, _ = read_session(Path(session["path"]))
        except Exception:
            raw_frames = frames
    for i, f in enumerate(raw_frames[:n]):
        lm = f.get("lm") if isinstance(f, Mapping) else None
        if lm is None and isinstance(f, Mapping):
            lm = f.get("world")
        vec = extract_spatial_features(lm)
        if vec is not None:
            s_feats[i] = vec
    session["spatial_features"] = s_feats
    return s_feats


def pick_spatial_from_sessions(
    sessions: Sequence[Dict[str, Any]],
    conds: Optional[Sequence[int]] = None,
    *,
    return_groups: bool = False,
    return_weights: bool = False,
    group_by: str = "condition",
):
    """Extract `(x_spatial, y)` (and optionally `groups` and/or `weights`) from sessions."""
    xs: List[np.ndarray] = []
    ys: List[np.ndarray] = []
    gs: List[np.ndarray] = []
    ws: List[np.ndarray] = []
    g_mode = str(group_by or "condition").strip().lower()
    for s_idx, s in enumerate(sessions):
        s_feats = ensure_session_spatial_features(s)
        valid_vec = np.any(s_feats != 0.0, axis=1)
        m = (s["y"] >= 0) & valid_vec
        if conds is not None:
            in_cond = np.isin(s["cond"], conds)
            if (s.get("meta") or {}).get("mode") == "free":
                in_cond = in_cond | ((s["cond"] == -1) & (s["y"] == 0))
            m = m & in_cond
        if np.any(m):
            xs.append(s_feats[m])
            ys.append(s["y"][m])
            if return_weights:
                sw = s.get("sample_weights")
                if sw is not None and len(sw) == len(s["y"]):
                    ws.append(np.asarray(sw[m], dtype=np.float64))
                else:
                    ws.append(np.ones(int(np.sum(m)), dtype=np.float64))
            if return_groups:
                if g_mode == "session":
                    sid = str(Path(s.get("path", f"session-{s_idx}")).name)
                    gs.append(np.full(int(np.sum(m)), sid, dtype=object))
                elif g_mode == "person":
                    meta = s.get("meta") or {}
                    pid = str(meta.get("person") or meta.get("user") or meta.get("subject") or meta.get("pc") or "default_user")
                    gs.append(np.full(int(np.sum(m)), pid, dtype=object))
                elif g_mode == "camera":
                    meta = s.get("meta") or {}
                    cam = meta.get("camera") or {}
                    cid = str(cam.get("id") or cam.get("note") or cam.get("source") or "default_camera")
                    gs.append(np.full(int(np.sum(m)), cid, dtype=object))
                else:
                    gs.append(s["cond"][m].astype(np.int64))
    if not xs:
        empty_x = np.zeros((0, SPATIAL_FEATURES), dtype=np.float32)
        empty_y = np.zeros((0,), dtype=np.int64)
        empty_g = np.zeros((0,), dtype=np.int64)
        empty_w = np.zeros((0,), dtype=np.float64)
        if return_groups and return_weights:
            return empty_x, empty_y, empty_g, empty_w
        if return_groups:
            return empty_x, empty_y, empty_g
        if return_weights:
            return empty_x, empty_y, empty_w
        return empty_x, empty_y
    x_cat = np.concatenate(xs, axis=0)
    y_cat = np.concatenate(ys, axis=0)
    if return_groups and return_weights:
        return x_cat, y_cat, np.concatenate(gs, axis=0), np.concatenate(ws, axis=0)
    if return_groups:
        return x_cat, y_cat, np.concatenate(gs, axis=0)
    if return_weights:
        return x_cat, y_cat, np.concatenate(ws, axis=0)
    return x_cat, y_cat


def blend_tcn_and_spatial_probs(
    tcn_probs: np.ndarray,
    tcn_classes: Sequence[str],
    spatial_probs: Optional[np.ndarray],
    spatial_classes: Sequence[str],
    *,
    alpha: float = 0.40,
    veto_threshold: float = 0.80,
    veto_target_cap: float = 0.15,
    boost_threshold: float = 0.85,
) -> np.ndarray:
    """Combine TCN temporal probabilities with 0-lag 96-dim Spatial MLP posture probabilities.

    Supports both 1D `(C,)` single-frame and 2D `(N, C)` batch probability arrays.
    - For static/pinch gestures present in both models, blends TCN and Spatial MLP (`(1-alpha)*TCN + alpha*MLP`).
    - When Spatial MLP is strongly confident in `"none"` (`>= veto_threshold`) while target is `< veto_target_cap`
      and TCN is below `0.88`, suppresses false-positive pinch spikes during half-fist cursor movement.
    - When Spatial MLP is strongly confident in a static posture (`>= boost_threshold`) and TCN is already
      moderately active (`>= 0.35`), boosts the combined probability for faster response.
    """
    out = np.asarray(tcn_probs, dtype=np.float64).copy()
    if spatial_probs is None:
        return out
    sp = np.asarray(spatial_probs, dtype=np.float64)
    single = out.ndim == 1
    if single:
        out = out[None, :]
        sp = sp[None, :] if sp.ndim == 1 else sp

    tcn_map = {name: i for i, name in enumerate(tcn_classes)}
    sp_map = {name: i for i, name in enumerate(spatial_classes)}
    none_tcn = tcn_map.get("none")
    none_sp = sp_map.get("none")

    for cls_name in STATIC_POSTURE_LABELS:
        if cls_name == "none":
            continue
        ti = tcn_map.get(cls_name)
        si = sp_map.get(cls_name)
        if ti is None or si is None:
            continue
        p_t = out[:, ti]
        p_s = sp[:, si]
        blended = (1.0 - alpha) * p_t + alpha * p_s

        # 0-lag fast confirmation boost when both agree
        boost_mask = (p_s >= boost_threshold) & (p_t >= 0.35)
        blended = np.where(boost_mask, np.maximum(blended, 0.78 * p_s + 0.22 * p_t), blended)

        # Veto suppression when Spatial MLP is confident the hand is in neutral/moving posture ('none')
        if none_sp is not None:
            p_none_s = sp[:, none_sp]
            veto_mask = (p_none_s >= veto_threshold) & (p_s < veto_target_cap) & (p_t < 0.88)
            blended = np.where(veto_mask, blended * 0.35, blended)

        out[:, ti] = blended

    if none_tcn is not None:
        other_Indices = [i for i, name in enumerate(tcn_classes) if name != "none"]
        if other_Indices:
            other_sum = np.sum(out[:, other_Indices], axis=1)
            max_other = np.max(out[:, other_Indices], axis=1)
            out[:, none_tcn] = np.where(
                other_sum <= 1.0,
                1.0 - other_sum,
                np.clip(1.0 - max_other, 0.0, 1.0),
            )
        else:
            row_sum = np.sum(out, axis=1, keepdims=True)
            out = np.where(row_sum > 1e-8, out / row_sum, out)

    return out[0] if single else out


def apply_temperature_scaling(probs: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    """Apply post-hoc temperature scaling (`T > 0`) in log-probability space while preserving argmax."""
    p = np.asarray(probs, dtype=np.float64)
    t = max(float(temperature), 0.1)
    if abs(t - 1.0) < 1e-6:
        return p.copy()
    single = p.ndim == 1
    if single:
        p = p[None, :]
    log_p = np.log(np.clip(p, 1e-8, 1.0)) / t
    log_p = log_p - np.max(log_p, axis=1, keepdims=True)
    exp_p = np.exp(log_p)
    out = exp_p / np.maximum(np.sum(exp_p, axis=1, keepdims=True), 1e-8)
    return out[0] if single else out


def compute_calibration_metrics(
    probs: np.ndarray,
    y_true: np.ndarray,
    *,
    n_bins: int = 15,
) -> Dict[str, float]:
    """Compute Expected Calibration Error (ECE), Maximum Calibration Error (MCE), NLL, and Brier score."""
    p = np.asarray(probs, dtype=np.float64)
    y = np.asarray(y_true, dtype=np.int64)
    n = len(y)
    if n == 0 or p.ndim != 2 or p.shape[0] != n:
        return {
            "ece": 0.0,
            "mce": 0.0,
            "nll": 0.0,
            "brier": 0.0,
            "mean_confidence": 0.0,
            "accuracy": 0.0,
        }
    conf = np.max(p, axis=1)
    pred = np.argmax(p, axis=1)
    acc = (pred == y).astype(np.float64)
    bins = np.linspace(0.0, 1.0, max(2, int(n_bins)) + 1)
    ece = 0.0
    mce = 0.0
    for i in range(len(bins) - 1):
        if i == 0:
            mask = (conf >= bins[i]) & (conf <= bins[i + 1])
        else:
            mask = (conf > bins[i]) & (conf <= bins[i + 1])
        cnt = int(np.sum(mask))
        if cnt > 0:
            diff = abs(float(np.mean(acc[mask])) - float(np.mean(conf[mask])))
            ece += (cnt / n) * diff
            mce = max(mce, diff)
    idx = np.clip(y, 0, p.shape[1] - 1)
    nll = float(-np.mean(np.log(np.clip(p[np.arange(n), idx], 1e-8, 1.0))))
    onehot = np.zeros_like(p)
    onehot[np.arange(n), idx] = 1.0
    brier = float(np.mean(np.sum((p - onehot) ** 2, axis=1)))
    return {
        "ece": round(float(ece), 5),
        "mce": round(float(mce), 5),
        "nll": round(float(nll), 5),
        "brier": round(float(brier), 5),
        "mean_confidence": round(float(np.mean(conf)), 4),
        "accuracy": round(float(np.mean(acc)), 4),
    }


def fit_temperature_scaling(
    probs: np.ndarray,
    y_true: np.ndarray,
    *,
    t_min: float = 0.40,
    t_max: float = 2.50,
    steps: int = 211,
    n_bins: int = 15,
) -> Tuple[float, Dict[str, Any]]:
    """Find optimal post-hoc temperature `T` on validation probabilities to minimize ECE."""
    before = compute_calibration_metrics(probs, y_true, n_bins=n_bins)
    if len(y_true) == 0:
        return 1.0, {"temperature": 1.0, "before": before, "after": before, "ece_reduction_pct": 0.0}
    best_t = 1.0
    best_ece = before["ece"]
    best_nll = before["nll"]
    for tc in np.linspace(float(t_min), float(t_max), max(11, int(steps))):
        p_cal = apply_temperature_scaling(probs, float(tc))
        st = compute_calibration_metrics(p_cal, y_true, n_bins=n_bins)
        if (st["ece"], st["nll"]) < (best_ece, best_nll):
            best_ece = st["ece"]
            best_nll = st["nll"]
            best_t = round(float(tc), 3)
    after = compute_calibration_metrics(apply_temperature_scaling(probs, best_t), y_true, n_bins=n_bins)
    red_pct = (
        round((before["ece"] - after["ece"]) / max(before["ece"], 1e-8) * 100.0, 2)
        if before["ece"] > 0.0
        else 0.0
    )
    return best_t, {
        "temperature": best_t,
        "before": before,
        "after": after,
        "ece_reduction_pct": red_pct,
    }
