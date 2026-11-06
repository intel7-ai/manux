"""NumPy 학습·추론만 origin/testing@431f9b0의 spatial_mlp에서 가져왔다.

평가용 변경: 33/96차원과 동적 클래스 허용, 내부 무작위 프레임 분할 제거.
Adam/LeakyReLU/가중치 초기화·역전파는 원본 유지. 학습 시간은 실행해 측정한다.
저장·로드는 #82와 통합한다. 명시적 validation_data만 계산하며 무작위 분할은 하지 않는다.
실제 독립 평가 지표는 tools/eval_judges.py가 계산한다.
"""
from __future__ import annotations

import math
import json
from pathlib import Path
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

STUDIO_CLASSES: Tuple[str, ...] = (
    "none",
    "thumb_middle",
    "thumb_ring",
    "thumb_index",
    "thumb_pinky",
    "palm_side",
    "thumbs_up",
    "v_sign",
    "thumb_middle_ring",
    "rock_sign",
)


@dataclass(frozen=True)
class SpatialMlpMetrics:
    train_accuracy: float
    test_accuracy: Optional[float]
    total_samples: int
    train_samples: int
    test_samples: int
    elapsed_ms: float
    per_class: Mapping[str, Mapping[str, Any]]
    trained_at: str


class SpatialMlpModel:
    """2-Layer Spatial MLP (`feat_dim -> hidden_units -> num_classes`) with LeakyReLU(0.05) + Softmax."""

    def __init__(
        self,
        w1: np.ndarray,
        b1: np.ndarray,
        w2: np.ndarray,
        b2: np.ndarray,
        classes: Sequence[str] = STUDIO_CLASSES,
        feature_version: str = "spatial-mlp-v1",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.w1 = np.asarray(w1, dtype=np.float64)  # (H, D)
        self.b1 = np.asarray(b1, dtype=np.float64)  # (H,)
        self.w2 = np.asarray(w2, dtype=np.float64)  # (C, H)
        self.b2 = np.asarray(b2, dtype=np.float64)  # (C,)
        self.classes: Tuple[str, ...] = tuple(classes)
        self.feature_version = feature_version
        self.metadata: Dict[str, Any] = dict(metadata or {})
        dimension = {"spatial-mlp-v1": 96, "pose-angles-v1": 33}.get(feature_version)
        if (self.w1.ndim != 2 or self.w1.shape[1] != dimension or not 1 <= self.w1.shape[0] <= 512
                or self.b1.shape != (self.w1.shape[0],) or self.w2.shape != (len(self.classes), self.w1.shape[0])
                or self.b2.shape != (len(self.classes),) or not self.classes or len(self.classes) > 32
                or len(set(self.classes)) != len(self.classes) or any(not isinstance(c, str) or not c for c in self.classes)
                or feature_version not in ("spatial-mlp-v1", "pose-angles-v1")
                or not all(np.isfinite(x).all() for x in (self.w1, self.b1, self.w2, self.b2))):
            raise ValueError("Spatial MLP 특징·클래스·가중치 형식이 맞지 않습니다.")

    @property
    def feat_dim(self) -> int:
        return int(self.w1.shape[1])

    @property
    def hidden_units(self) -> int:
        return int(self.w1.shape[0])

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Single feature vector `(D,)` or batch `(N, D)` -> probabilities `(C,)` or `(N, C)`."""
        x = np.asarray(features, dtype=np.float64)
        single = x.ndim == 1
        if single:
            x = x[None, :]
        z1 = x @ self.w1.T + self.b1
        h = np.where(z1 > 0.0, z1, z1 * 0.05)
        logits = h @ self.w2.T + self.b2
        logits = logits - np.max(logits, axis=1, keepdims=True)
        exp_logits = np.exp(logits)
        probs = exp_logits / np.sum(exp_logits, axis=1, keepdims=True)
        return probs[0] if single else probs

    def predict(self, features: np.ndarray) -> Dict[str, Any]:
        """Predict top class, confidence, and probability map for a single frame `(D,)`."""
        probs = self.predict_proba(features)
        best_idx = int(np.argmax(probs))
        return {
            "action": self.classes[best_idx],
            "confidence": float(probs[best_idx]),
            "probabilities": {cls: float(probs[i]) for i, cls in enumerate(self.classes)},
        }

    def __call__(self, window: np.ndarray) -> np.ndarray:
        """Compatible with MANUX `Predictor` protocol: accepts `(D,)` or `(W, D)` and uses latest frame."""
        arr = np.asarray(window, dtype=np.float64)
        if arr.ndim == 2:
            arr = arr[-1]
        return self.predict_proba(arr)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "manux-spatial-mlp/1",
            "feature_version": self.feature_version,
            "feat_dim": self.feat_dim,
            "hidden_units": self.hidden_units,
            "classes": list(self.classes),
            "metadata": self.metadata,
            "weights": {
                "W1": np.round(self.w1, 6).tolist(),
                "b1": np.round(self.b1, 6).tolist(),
                "W2": np.round(self.w2, 6).tolist(),
                "b2": np.round(self.b2, 6).tolist(),
            },
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> SpatialMlpModel:
        if not isinstance(doc, Mapping) or doc.get("schema") != "manux-spatial-mlp/1":
            raise ValueError("invalid spatial MLP document")
        weights = doc["weights"]
        return cls(
            w1=np.asarray(weights["W1"], dtype=np.float64),
            b1=np.asarray(weights["b1"], dtype=np.float64),
            w2=np.asarray(weights["W2"], dtype=np.float64),
            b2=np.asarray(weights["b2"], dtype=np.float64),
            classes=tuple(doc["classes"]),
            feature_version=str(doc.get("feature_version", "spatial-mlp-v1")),
            metadata=dict(doc.get("metadata") or {}),
        )

    @classmethod
    def load(cls, path: Path) -> SpatialMlpModel:
        if Path(path).stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Spatial MLP 파일이 너무 큽니다.")
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(doc)


def train_spatial_mlp(
    x_all: np.ndarray,
    y_all: np.ndarray,
    weights_all: Optional[np.ndarray] = None,
    *,
    classes: Sequence[str] = STUDIO_CLASSES,
    hidden_units: int = 64,
    epochs: int = 45,
    batch_size: int = 64,
    lr: float = 0.003,
    l2: float = 1e-4,
    seed: int = 42,
    feature_version: str = "spatial-mlp-v1",
    validation_data: Optional[Tuple[np.ndarray, np.ndarray]] = None,
) -> Tuple[SpatialMlpModel, SpatialMlpMetrics]:
    """NumPy Adam으로 외부에서 분리한 학습 표본 전체를 학습한다."""
    t0 = time.perf_counter()
    x_arr = np.asarray(x_all, dtype=np.float64)
    y_arr = np.asarray(y_all, dtype=np.int64)
    n_samples, feat_dim = x_arr.shape
    num_classes = len(classes)
    if n_samples < 2:
        raise ValueError("학습하려면 최소 2개 이상의 표본이 필요합니다.")
    if (y_arr.shape != (n_samples,) or not np.isfinite(x_arr).all()
            or not num_classes or (y_arr < 0).any() or (y_arr >= num_classes).any()):
        raise ValueError("유한한 특징과 범위 내 클래스 번호가 필요합니다.")

    sample_weights = (
        np.asarray(weights_all, dtype=np.float64)
        if weights_all is not None
        else np.ones(n_samples, dtype=np.float64)
    )
    if (sample_weights.shape != (n_samples,) or not np.isfinite(sample_weights).all()
            or (sample_weights <= 0).any()):
        raise ValueError("학습 가중치는 유한한 양수여야 합니다.")

    rng = np.random.default_rng(seed)
    perm = rng.permutation(n_samples)
    x_arr = x_arr[perm]
    y_arr = y_arr[perm]
    sample_weights = sample_weights[perm]

    # The caller splits recordings/sessions, never neighboring frames. Shuffling
    # changes optimization order only. No internal validation set is manufactured.
    x_train = x_arr
    y_train = y_arr
    w_train = sample_weights

    # He Initialization
    w1 = rng.standard_normal((hidden_units, feat_dim)) * math.sqrt(2.0 / feat_dim)
    b1 = np.full(hidden_units, 0.01, dtype=np.float64)
    w2 = rng.standard_normal((num_classes, hidden_units)) * math.sqrt(2.0 / hidden_units)
    b2 = np.zeros(num_classes, dtype=np.float64)

    # Adam Optimizer State
    mw1, vw1 = np.zeros_like(w1), np.zeros_like(w1)
    mb1, vb1 = np.zeros_like(b1), np.zeros_like(b1)
    mw2, vw2 = np.zeros_like(w2), np.zeros_like(w2)
    mb2, vb2 = np.zeros_like(b2), np.zeros_like(b2)

    beta1, beta2, eps = 0.9, 0.999, 1e-8
    step = 0
    n_train = len(x_train)
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

            # Forward pass (vectorized)
            z1 = xb @ w1.T + b1                              # (B, H)
            h = np.where(z1 > 0.0, z1, z1 * 0.05)            # (B, H)
            logits = h @ w2.T + b2                           # (B, C)
            logits = logits - np.max(logits, axis=1, keepdims=True)
            exp_l = np.exp(logits)
            probs = exp_l / np.sum(exp_l, axis=1, keepdims=True)  # (B, C)

            # Backward pass
            d_logits = probs.copy()
            d_logits[np.arange(b_len), yb] -= 1.0
            d_logits *= (wb[:, None] / b_len)                # (B, C)

            gw2 = d_logits.T @ h + l2 * w2                   # (C, H)
            gb2 = np.sum(d_logits, axis=0)                   # (C,)

            dh = d_logits @ w2                               # (B, H)
            dz1 = np.where(z1 > 0.0, dh, dh * 0.05)          # (B, H)
            gw1 = dz1.T @ xb + l2 * w1                       # (H, D)
            gb1 = np.sum(dz1, axis=0)                       # (H,)

            # Adam update
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

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    trained_at = time.strftime("%Y-%m-%d %H:%M:%S")

    temp_model = SpatialMlpModel(w1, b1, w2, b2, classes=classes, feature_version=feature_version)
    train_preds = np.argmax(temp_model.predict_proba(x_train), axis=1)
    all_preds = np.argmax(temp_model.predict_proba(x_arr), axis=1)

    train_acc = float(np.mean(train_preds == y_train) * 100.0)

    per_class: Dict[str, Dict[str, Any]] = {}
    for c_idx, cls_name in enumerate(classes):
        mask = y_arr == c_idx
        total_c = int(np.sum(mask))
        correct_c = int(np.sum(all_preds[mask] == c_idx)) if total_c > 0 else 0
        per_class[cls_name] = {
            "total": total_c,
            "correct": correct_c,
            "accuracy": round((correct_c / total_c) * 100.0, 1) if total_c > 0 else None,
        }

    test_acc = None
    test_samples = 0
    if validation_data is not None:
        vx, vy = validation_data
        test_samples = len(vy)
        test_acc = round(float(np.mean(np.argmax(temp_model.predict_proba(vx), axis=1) == vy) * 100), 2)

    metrics = SpatialMlpMetrics(
        train_accuracy=round(train_acc, 2),
        test_accuracy=test_acc,
        total_samples=int(n_samples),
        train_samples=int(len(x_train)),
        test_samples=test_samples,
        elapsed_ms=round(elapsed_ms, 1),
        per_class=per_class,
        trained_at=trained_at,
    )

    model = SpatialMlpModel(
        w1, b1, w2, b2,
        classes=classes,
        feature_version=feature_version,
        metadata={
            "train_accuracy": metrics.train_accuracy,
            "test_accuracy": metrics.test_accuracy,
            "total_samples": metrics.total_samples,
            "elapsed_ms": metrics.elapsed_ms,
            "per_class": per_class,
            "trained_at": trained_at,
        },
    )
    return model, metrics
