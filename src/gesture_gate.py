"""
Intent check with the trained gesture model (2026-11-05): the rules still decide, the TCN confirms.

models/gesture_tcn.onnx (scripts/train_tcn.py) looks at the last 0.5 s of the hand (src/tcn_features.py) every frame
and gives probabilities for none / thumb_index / thumb_ring. When the rules want to
- right click (thumb + ring): the click goes out only if the model also saw thumb_ring (probability >= threshold for
  `frames_in_a_row` frames within the last `lookback` s). Not yet? The click waits up to `gate_wait` s for it, then
  is dropped.
- start a scroll grab (thumb + index): the grab starts only once the model saw thumb_index; until then the cursor
  just stays put. Once allowed, the grab runs to its end.
Everything else (left click, cursor, the other gestures) is untouched: the model was trained on these two only.
In the held-out postures of the first recording this cut the misfires while moving with a half fist from 51 to 6
(docs/recording.md). Switch: the "손짓 확인 (TCN)" toggle on the Qt screen, or "tcn_gate" in the config.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np

from src.data_quality import FrameQualityScore, compute_frame_quality_score
from src.finger_kinematics import CausalFingerTracker
from src.gesture_catalog import NAMES
from src.spatial_features import extract_spatial_features
from src.spatial_mlp import (
    DEFAULT_SPATIAL_MLP_PATH,
    STATIC_POSTURE_LABELS,
    SpatialMlpModel,
    apply_temperature_scaling,
    blend_tcn_and_spatial_probs,
    compose_spatial_mlp,
)
from src.tcn_features import FEATURE_VERSION, frame_features, windows

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "gesture_tcn.onnx"
DEFAULT_SPATIAL_MODEL = DEFAULT_SPATIAL_MLP_PATH
DEFAULT_THRESHOLD = 0.7
GATED = {"right_click": "thumb_ring", "left_click": "thumb_middle", "scroll_grab": "thumb_index",
         "task_switcher": "thumb_pinky", "show_desktop": "palm_side"}

# Stage 7: Risk-differentiated per-gesture decision policies auto-tuned on 15-fold validation splits
DEFAULT_GESTURE_THRESHOLDS: Dict[str, float] = {
    "thumb_index": 0.62,         # scroll_grab: low-risk continuous action
    "thumb_middle": 0.68,        # left_click: standard point-and-click
    "thumb_ring": 0.58,          # right_click: anatomically coupled finger with low false-fire rate
    "thumb_index_middle": 0.68,  # drag_hold: compound 2-finger pinch
    "thumb_pinky": 0.73,         # task_switcher: medium-high cost window focus switch
    "palm_side": 0.65,           # show_desktop: high-cost destructive action (paired with hold + strict OOD)
    "thumbs_up": 0.73,           # mode/auxiliary posture
    "v_sign": 0.78,              # toggle mouse control: high cost
    "rock_sign": 0.78,           # close window: high-cost destructive action
}

DEFAULT_GESTURE_FRAMES: Dict[str, int] = {
    "thumb_index": 4,
    "thumb_middle": 4,
    "thumb_ring": 2,
    "thumb_index_middle": 4,
    "thumb_pinky": 4,
    "palm_side": 4,
    "thumbs_up": 4,
    "v_sign": 5,
    "rock_sign": 5,
}

DEFAULT_GESTURE_OOD_LIMITS: Dict[str, float] = {
    "thumb_index": 1.40,
    "thumb_middle": 1.35,
    "thumb_ring": 1.45,
    "thumb_index_middle": 1.35,
    "thumb_pinky": 1.25,
    "palm_side": 1.20,
    "thumbs_up": 1.20,
    "v_sign": 1.15,
    "rock_sign": 1.15,
}

DEFAULT_GESTURE_HOLD_SECONDS: Dict[str, float] = {
    "thumb_index": 0.0,
    "thumb_middle": 0.0,
    "thumb_ring": 0.0,
    "thumb_index_middle": 0.0,
    "thumb_pinky": 0.08,
    "palm_side": 0.10,
    "thumbs_up": 0.10,
    "v_sign": 0.15,
    "rock_sign": 0.15,
}


def load_spatial_mlp(spatial_path: Path = DEFAULT_SPATIAL_MODEL, *, compose_specialists: bool = True) -> Optional[SpatialMlpModel]:
    """Load `models/spatial_mlp.json` composed with per-gesture specialist MLPs if present."""
    try:
        p = Path(spatial_path)
        base = SpatialMlpModel.load(p) if p.is_file() else None
        if not compose_specialists:
            return base
        return compose_spatial_mlp(p.parent / "gesture_tcn.onnx", base)
    except Exception:
        pass
    return None


def load_predictor(model_path: Path = DEFAULT_MODEL) -> Tuple[Callable[[np.ndarray], np.ndarray], Dict[str, Any]]:
    """(window -> class probabilities, model info). Raises if the model or onnxruntime is missing."""
    from src.gesture_models import load_single, compose_predictor
    p = Path(model_path)
    sel_mode = "composite"
    sel_id = "composite:active"
    if p.resolve() == DEFAULT_MODEL.resolve():
        sel_file = p.parent / "active_onnx_selection.json"
        if sel_file.is_file():
            try:
                sel = json.loads(sel_file.read_text(encoding="utf-8"))
                sel_mode = str(sel.get("mode") or "composite")
                sel_id = str(sel.get("model_id") or "composite:active")
                custom_onnx = sel.get("onnx_path")
                if sel_mode == "single_onnx" and custom_onnx:
                    cand = Path(custom_onnx)
                    if not cand.is_absolute():
                        cand = DEFAULT_MODEL.parents[1] / cand
                    if cand.is_file() and cand.with_suffix(".json").is_file():
                        p = cand
                    else:
                        sel_mode = "composite"
            except Exception:
                sel_mode = "composite"

    predict, info = load_single(p, FEATURE_VERSION)
    if sel_mode == "composite":
        predict, info = compose_predictor(p, predict, info)
    info = dict(info)
    info["active_onnx_mode"] = sel_mode
    info["active_onnx_id"] = sel_id
    info["active_onnx_file"] = p.name
    spatial_candidate = Path(model_path).parent / "spatial_mlp.json"
    bank_manifest = Path(model_path).parent / "gestures" / "manifest.json"
    if sel_mode != "single_onnx" and (spatial_candidate.is_file() or bank_manifest.is_file()):
        info.setdefault("spatial_mlp_path", str(spatial_candidate))
        info["compose_spatial_specialists"] = (sel_mode == "composite")
    return predict, info


class GestureGate:
    def __init__(self, predict: Callable[[np.ndarray], np.ndarray], info: Dict[str, Any],
                 config: Optional[Dict[str, Any]] = None,
                 spatial_model: Optional[SpatialMlpModel] = None) -> None:
        cfg = config or {}
        self.predict = predict
        self.classes: List[str] = list(info["classes"])
        independent = set(info.get("independent_gestures") or [])
        self.disabled_gestures: set[str] = set(info.get("disabled_gestures") or []) - independent
        self.window = int(info["window"])
        self.threshold = float(cfg.get("gate_threshold", info.get("threshold", 0.7)))
        self.k = int(cfg.get("gate_frames", info.get("frames_in_a_row", 3)))
        use_policy_defaults = bool(cfg.get("use_gesture_policies", info.get("use_gesture_policies", True)))
        if "gate_threshold" in cfg:
            self.thresholds: Dict[str, float] = {}
        elif use_policy_defaults:
            self.thresholds = {**DEFAULT_GESTURE_THRESHOLDS, **(info.get("gesture_thresholds") or {})}
        else:
            self.thresholds = dict(info.get("gesture_thresholds") or {})
        if isinstance(cfg.get("gesture_thresholds"), dict):
            self.thresholds.update({str(k): float(v) for k, v in cfg["gesture_thresholds"].items()})

        if "gate_frames" in cfg or (self.k == 1 and "gesture_frames" not in info):
            self.frame_counts: Dict[str, int] = {}
        elif use_policy_defaults:
            self.frame_counts = {**DEFAULT_GESTURE_FRAMES, **(info.get("gesture_frames") or {})}
        else:
            self.frame_counts = dict(info.get("gesture_frames") or {})
        if isinstance(cfg.get("gesture_frames"), dict):
            self.frame_counts.update({str(k): int(v) for k, v in cfg["gesture_frames"].items()})

        if self.k == 1 and "gesture_hold_seconds" not in info and "gesture_hold_seconds" not in cfg:
            self.hold_seconds: Dict[str, float] = {}
        elif use_policy_defaults:
            self.hold_seconds = {**DEFAULT_GESTURE_HOLD_SECONDS, **(info.get("gesture_hold_seconds") or {})}
        else:
            self.hold_seconds = dict(info.get("gesture_hold_seconds") or {})
        if isinstance(cfg.get("gesture_hold_seconds"), dict):
            self.hold_seconds.update({str(k): float(v) for k, v in cfg["gesture_hold_seconds"].items()})

        self.lookback = float(cfg.get("gate_lookback", 0.5))
        self.wait = float(cfg.get("gate_wait", 0.3))
        self.spatial_alpha = float(cfg.get("spatial_mlp_alpha", info.get("spatial_mlp_alpha", 0.40)))
        self.spatial_veto_threshold = float(cfg.get("spatial_veto_threshold", info.get("spatial_veto_threshold", 0.80)))
        self.spatial_veto_target_cap = float(cfg.get("spatial_veto_target_cap", info.get("spatial_veto_target_cap", 0.15)))
        self.spatial_boost_threshold = float(cfg.get("spatial_boost_threshold", info.get("spatial_boost_threshold", 0.85)))
        self.temperature = float(cfg.get("gate_temperature", info.get("temperature", 0.81)))
        self.quality_threshold = float(cfg.get("gate_quality_threshold", info.get("quality_threshold", 0.45)))
        self.quality_gate_enabled = bool(cfg.get("quality_gate_enabled", True))
        self.ood_gate_enabled = bool(cfg.get("ood_gate_enabled", info.get("ood_gate_enabled", True)))
        self.ood_ratio_limit = float(cfg.get("ood_ratio_limit", info.get("ood_ratio_limit", 1.35)))
        self.ood_ratio_limits: Dict[str, float] = (
            {**DEFAULT_GESTURE_OOD_LIMITS, **(info.get("gesture_ood_ratio_limits") or {})}
            if use_policy_defaults
            else dict(info.get("gesture_ood_ratio_limits") or {})
        )
        if isinstance(cfg.get("gesture_ood_ratio_limits"), dict):
            self.ood_ratio_limits.update({str(k): float(v) for k, v in cfg["gesture_ood_ratio_limits"].items()})
        if spatial_model is None and cfg.get("spatial_mlp_enabled", True):
            sp_path = info.get("spatial_mlp_path")
            if sp_path:
                spatial_model = load_spatial_mlp(
                    Path(sp_path),
                    compose_specialists=bool(info.get("compose_spatial_specialists", True)),
                )
            elif isinstance(info.get("spatial_mlp"), dict) and info["spatial_mlp"].get("schema") == "manux-spatial-mlp/1":
                try:
                    spatial_model = SpatialMlpModel.from_dict(info["spatial_mlp"])
                except Exception:
                    spatial_model = None
        self.spatial_model: Optional[SpatialMlpModel] = spatial_model
        if self.spatial_model is not None and self.spatial_model.custom_prototypes:
            for c_lbl in self.spatial_model.custom_prototypes:
                if c_lbl not in self.classes:
                    self.classes.append(c_lbl)
        self.runtime_adaptation_enabled: bool = bool(cfg.get("runtime_adaptation_enabled", False))
        self.shadow_adaptation_enabled: bool = bool(cfg.get("shadow_adaptation_enabled", True))
        if self.spatial_model is not None:
            self.spatial_model.shadow_mode = not self.runtime_adaptation_enabled
        self._finger_tracker = CausalFingerTracker()
        self._frames: Deque[Dict[str, Any]] = deque(maxlen=self.window + 1)
        self._probs: Deque[Tuple[float, np.ndarray]] = deque(maxlen=60)
        self._spatial_probs: Deque[Tuple[float, np.ndarray]] = deque(maxlen=60)
        self.last: Optional[np.ndarray] = None
        self.last_calibrated: Optional[np.ndarray] = None
        self.last_tcn: Optional[np.ndarray] = None
        self.last_spatial: Optional[np.ndarray] = None
        self.last_spatial_vec: Optional[np.ndarray] = None
        self.last_ood: Optional[Dict[str, Any]] = None
        self._ood_cache: Dict[str, Dict[str, Any]] = {}
        self.last_quality: Optional[FrameQualityScore] = None
        self.last_quality_score: float = 1.0
        self.last_quality_reason: str = "ok"
        self.last_rejection_reason: str = ""
        self._prev_raw_lm: Optional[List[List[float]]] = None
        self._prev_hand_t: Optional[float] = None
        self._post_loss_frames_remaining: int = 0
        self._pending = None     # a held rule click (action, label, since)
        self._grab_ok = False
        self._continuous = None
        self.blocked = 0                                      # rule actions the model did not confirm
        self.passed = 0
        self.spatial_vetoed = 0
        self.spatial_boosted = 0
        self.quality_withheld = 0
        self.ood_vetoed = 0
        self.last_note = ""

    def reset(self) -> None:
        self._finger_tracker.reset()
        self._frames.clear()
        self._probs.clear()
        self._spatial_probs.clear()
        self.last = None
        self.last_calibrated = None
        self.last_tcn = None
        self.last_spatial = None
        self.last_spatial_vec = None
        self.last_ood = None
        self._ood_cache.clear()
        self.last_quality = None
        self.last_quality_score = 1.0
        self.last_quality_reason = "ok"
        self.last_rejection_reason = ""
        self._prev_raw_lm = None
        self._prev_hand_t = None
        self._post_loss_frames_remaining = 0
        self._pending = None
        self._grab_ok = False
        self._continuous = None

    # ---------------------------------------------------------------- model
    def step(self, now: float, hand: Any, aspect: float) -> Optional[np.ndarray]:
        lm = None
        if hand is None or not getattr(hand, "world", None):
            self._finger_tracker.reset()
            self._frames.append({"t": now})
            if self._prev_raw_lm is not None:
                self._post_loss_frames_remaining = 2
                self.last_quality = FrameQualityScore(
                    score=0.0,
                    reason="missing_hand",
                    confidence=0.0,
                    edge_margin=0.0,
                    near_tracking_loss=True,
                )
                self.last_quality_score = 0.0
                self.last_quality_reason = "missing_hand"
        else:
            raw_lm = [[p.x, p.y, p.z] for p in hand.landmarks]
            dt = (now - self._prev_hand_t) if (self._prev_hand_t is not None and now > self._prev_hand_t) else 0.033
            if dt > 0.12:
                self._post_loss_frames_remaining = max(self._post_loss_frames_remaining, 2)
            near_loss = self._post_loss_frames_remaining > 0
            hand_score = float(getattr(hand, "score", 1.0) or 1.0)
            fq = compute_frame_quality_score(
                raw_lm,
                self._prev_raw_lm,
                dt=dt,
                confidence=hand_score,
                near_tracking_loss=near_loss,
            )
            self.last_quality = fq
            self.last_quality_score = fq.score
            self.last_quality_reason = fq.reason
            if self._post_loss_frames_remaining > 0:
                self._post_loss_frames_remaining -= 1
            self._prev_raw_lm = raw_lm
            self._prev_hand_t = now
            lm, world = self._finger_tracker.step(now, raw_lm, hand.world)
            self._frames.append({"t": now, "world": world, "lm": lm})
        feats = frame_features(list(self._frames), aspect)
        raw_tcn_probs = np.asarray(self.predict(windows(feats, self.window)[-1:]), dtype=np.float64)
        if len(raw_tcn_probs) < len(self.classes):
            tcn_probs = np.zeros(len(self.classes), dtype=np.float64)
            tcn_probs[: len(raw_tcn_probs)] = raw_tcn_probs
        else:
            tcn_probs = raw_tcn_probs
        self.last_tcn = tcn_probs
        probs = tcn_probs
        if self.spatial_model is not None and lm is not None:
            s_vec = extract_spatial_features(lm)
            if s_vec is not None:
                self.last_spatial_vec = s_vec
                self._ood_cache.clear()
                sp_probs = self.spatial_model.predict_proba(s_vec)
                self.last_spatial = sp_probs
                self._spatial_probs.append((now, sp_probs))
                # If a finger was self-occluded or frame quality is degraded, trust temporal TCN context more than single-frame MLP
                q_factor = 0.45 if (self._finger_tracker.occluded_fingers_last_frame > 0 or self.last_quality_score < 0.60) else 1.0
                eff_alpha = self.spatial_alpha * q_factor
                probs = blend_tcn_and_spatial_probs(
                    tcn_probs,
                    self.classes,
                    sp_probs,
                    self.spatial_model.classes,
                    alpha=eff_alpha,
                    veto_threshold=self.spatial_veto_threshold,
                    veto_target_cap=self.spatial_veto_target_cap,
                    boost_threshold=self.spatial_boost_threshold,
                )
                for c_lbl in self.spatial_model.custom_prototypes:
                    if c_lbl in self.classes and c_lbl in self.spatial_model.classes:
                        probs[self.classes.index(c_lbl)] = float(sp_probs[self.spatial_model.classes.index(c_lbl)])
        self._probs.append((now, probs))
        self.last = probs
        self.last_calibrated = apply_temperature_scaling(probs, self.temperature)
        return probs

    def register_custom_gesture(
        self,
        label: str,
        rep_feature_sequences: List[np.ndarray],
        neutral_features: Optional[np.ndarray] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Stage 8-2: Register a 3–5 rep custom gesture on the frozen SpatialMLP encoder and wire it into the gate."""
        if self.spatial_model is None:
            raise RuntimeError("SpatialMlpModel is required for custom gesture registration")
        res = self.spatial_model.register_custom_gesture(
            label,
            rep_feature_sequences,
            neutral_features=neutral_features,
            **kwargs,
        )
        c_lbl = str(res["label"])
        if c_lbl not in self.classes:
            self.classes.append(c_lbl)
        self.thresholds[c_lbl] = float(res.get("recommended_threshold", 0.72))
        self.frame_counts.setdefault(c_lbl, int(kwargs.get("k_frames", 4)))
        self.ood_ratio_limits.setdefault(c_lbl, 1.30)
        return res

    def record_runtime_adaptation(self, label: str, *, user_cancelled: bool = False) -> Optional[Dict[str, Any]]:
        """Stage 8-3: Feed a confirmed execution into shadow/bounded-EMA prototype adaptation."""
        if self.spatial_model is None or self.last_spatial_vec is None:
            return None
        if not (self.shadow_adaptation_enabled or self.runtime_adaptation_enabled):
            return None
        self.spatial_model.shadow_mode = not self.runtime_adaptation_enabled
        ood_info = self._ood_cache.get(label) or self.spatial_model.ood_score(self.last_spatial_vec, label=label)
        return self.spatial_model.adapt_prototype_ema(
            label,
            self.last_spatial_vec,
            calibrated_confidence=self.calibrated_confidence(label),
            quality_score=self.last_quality_score,
            ood_ratio=float(ood_info.get("ratio", 0.0)),
            user_cancelled=user_cancelled,
        )

    def calibrated_confidence(self, label: Optional[str] = None) -> float:
        """Return temperature-scaled confidence (`0.0..1.0`) for `label` or the top predicted class."""
        p = self.last_calibrated
        if p is None and self.last is not None:
            p = apply_temperature_scaling(self.last, self.temperature)
        if p is None or len(p) == 0:
            return 0.0
        if label is not None and label in self.classes:
            return float(p[self.classes.index(label)])
        return float(np.max(p))

    def rejection_reason_ko(self) -> str:
        """Human-readable Korean explanation for why a gesture was withheld or rejected."""
        r = self.last_rejection_reason or ""
        if not r:
            return "정상 (승인)"
        if r.startswith("low_quality:"):
            return f"입력 품질 낮음 ({self.last_quality_reason}, {self.last_quality_score:.2f})"
        if r.startswith("ood_mahalanobis:"):
            ratio = float((self.last_ood or {}).get("ratio", 0.0))
            return f"미등록 손모양/OOD 의심 (거리비 {ratio:.2f})"
        if r == "spatial_neutral_veto":
            return "중립·이동 손모양 억제 (Spatial Veto)"
        if r.startswith("hold_duration:"):
            return "고위험 동작 유지 시간 미달"
        if r == "below_threshold":
            return "확신도 임계값 또는 연속 프레임 미달"
        return r

    def status_summary(self) -> Dict[str, Any]:
        """Compact diagnostic snapshot of calibrated confidence and gate rejection reasons for UI display."""
        top_cls = "none"
        p = self.last_calibrated if self.last_calibrated is not None else self.last
        if p is not None and len(p) == len(self.classes):
            top_cls = self.classes[int(np.argmax(p))]
        return {
            "top_gesture": top_cls,
            "top_gesture_ko": NAMES.get(top_cls, top_cls),
            "calibrated_confidence": round(self.calibrated_confidence(), 4),
            "temperature": round(self.temperature, 3),
            "quality_score": round(self.last_quality_score, 3),
            "quality_reason": self.last_quality_reason,
            "rejection_reason": self.last_rejection_reason,
            "rejection_reason_ko": self.rejection_reason_ko(),
            "passed": int(self.passed),
            "blocked": int(self.blocked),
            "quality_withheld": int(self.quality_withheld),
            "ood_vetoed": int(self.ood_vetoed),
            "spatial_vetoed": int(self.spatial_vetoed),
        }

    def quality_vetoes(self) -> bool:
        """True when current frame input quality is too low to approve a new click or scroll start."""
        return bool(self.quality_gate_enabled and self.last_quality_score < self.quality_threshold)

    def ood_vetoes(self, name: str) -> bool:
        """True when the hidden embedding has a class-wise shrinkage Mahalanobis distance exceeding the OOD percentile limit."""
        if not self.ood_gate_enabled or self.spatial_model is None or self.last_spatial_vec is None:
            return False
        is_known_posture = name in STATIC_POSTURE_LABELS or name in self.spatial_model.custom_prototypes
        if not is_known_posture or name not in self.spatial_model.ood_precisions:
            return False
        if self._finger_tracker.occluded_fingers_last_frame > 0:
            return False
        p_tcn = float(self.last_tcn[self.classes.index(name)]) if (self.last_tcn is not None and name in self.classes) else 0.0
        p_sp = float(self.last_spatial[self.spatial_model.classes.index(name)]) if (self.last_spatial is not None and name in self.spatial_model.classes) else 0.0
        if p_tcn >= 0.90 or p_sp >= 0.85:
            return False
        limit = float(self.ood_ratio_limits.get(name, self.ood_ratio_limit))
        ood_info = self._ood_cache.get(name)
        if ood_info is None:
            ood_info = self.spatial_model.ood_score(self.last_spatial_vec, label=name, ratio_limit=limit)
            self._ood_cache[name] = ood_info
        self.last_ood = ood_info
        return bool(ood_info.get("is_ood", False))

    def spatial_instant_confirmed(self, name: str) -> bool:
        """0-frame-lag fast confirmation when Spatial MLP is highly confident (`>= boost_threshold`)
        and TCN already shows emerging probability (`>= 0.45 * threshold`).
        Disabled for high-risk gestures that require an explicit hold duration (`hold_seconds > 0`)."""
        if float(self.hold_seconds.get(name, 0.0)) > 0.0:
            return False
        if self.quality_vetoes() or self.ood_vetoes(name):
            return False
        if self.spatial_model is None or self.last_spatial is None or self.last_tcn is None:
            return False
        if name not in STATIC_POSTURE_LABELS or name not in self.classes or name in self.disabled_gestures:
            return False
        if name not in self.spatial_model.classes:
            return False
        si = self.spatial_model.classes.index(name)
        ti = self.classes.index(name)
        thr = float(self.thresholds.get(name, self.threshold))
        return float(self.last_spatial[si]) >= self.spatial_boost_threshold and float(self.last_tcn[ti]) >= max(0.32, thr * 0.5)

    def spatial_vetoes(self, name: str) -> bool:
        """True when 0-lag Spatial MLP is confident the hand is in neutral/resting posture ('none')
        and NOT in the requested static posture `name`."""
        if self.spatial_model is None or self.last_spatial is None:
            return False
        is_known_posture = name in STATIC_POSTURE_LABELS or name in self.spatial_model.custom_prototypes
        if not is_known_posture or "none" not in self.spatial_model.classes:
            return False
        # Do not let a single self-occluded frame veto an active TCN temporal judgment
        if self._finger_tracker.occluded_fingers_last_frame > 0:
            return False
        none_idx = self.spatial_model.classes.index("none")
        p_none = float(self.last_spatial[none_idx])
        p_target = float(self.last_spatial[self.spatial_model.classes.index(name)]) if name in self.spatial_model.classes else 0.0
        p_tcn = float(self.last_tcn[self.classes.index(name)]) if (self.last_tcn is not None and name in self.classes) else 0.0
        return p_none >= self.spatial_veto_threshold and p_target < self.spatial_veto_target_cap and p_tcn < 0.88

    def confirmed(self, name: str, now: float) -> bool:
        """The model saw `name` for k frames in a row (and optional hold duration) within the last `lookback` s."""
        if name not in self.classes or name in self.disabled_gestures:
            return False
        if self.quality_vetoes():
            self.quality_withheld += 1
            self.last_rejection_reason = f"low_quality:{self.last_quality_reason}"
            return False
        if self.spatial_vetoes(name):
            self.spatial_vetoed += 1
            self.last_rejection_reason = "spatial_neutral_veto"
            return False
        if self.ood_vetoes(name):
            self.ood_vetoed += 1
            r_val = float((self.last_ood or {}).get("ratio", 0.0))
            self.last_rejection_reason = f"ood_mahalanobis:{r_val:.2f}"
            return False
        if self.spatial_instant_confirmed(name):
            self.spatial_boosted += 1
            self.last_rejection_reason = ""
            return True
        c = self.classes.index(name)
        threshold = float(self.thresholds.get(name, self.threshold))
        required = int(self.frame_counts.get(name, self.k))
        hold_req = float(self.hold_seconds.get(name, 0.0))
        run = 0
        run_start_t: Optional[float] = None
        reached_frames_but_short_hold = False
        for t, p in self._probs:
            if now - t > self.lookback:
                continue
            if p[c] >= threshold:
                if run == 0:
                    run_start_t = t
                run += 1
            else:
                run = 0
                run_start_t = None
            if run >= required:
                held = (t - run_start_t) if run_start_t is not None else 0.0
                if hold_req <= 0.0 or required <= 1 or held + 1e-6 >= hold_req:
                    self.last_rejection_reason = ""
                    return True
                reached_frames_but_short_hold = True
        self.last_rejection_reason = (
            f"hold_duration:{hold_req:.2f}s" if reached_frames_but_short_hold else "below_threshold"
        )
        return False

    def allows(self, name: str, now: float) -> bool:
        """Untrained gestures retain their existing rule behavior, including legacy 3-class models."""
        return name not in self.classes or name in self.disabled_gestures or self.confirmed(name, now)

    # ---------------------------------------------------------------- the check
    def filter(self, now: float, result: Any) -> Any:
        """Confirm only learned actions; continuous grabs need confirmation once at their start."""
        action = getattr(result, "action", None)
        label = GATED.get(action)
        if action == "drag_hold":
            label = "thumb_index_middle" if getattr(result, "active_gesture", "") == "thumb_index_middle_pinch" else "thumb_middle"
        continuous = action in ("scroll_grab", "drag_hold")
        if self._continuous != (action, label):
            self._continuous = None
        if action not in (None, "none", "move_cursor"):
            self._pending = None  # a new action supersedes a delayed click
        if label in self.classes and label not in self.disabled_gestures:
            if self._continuous == (action, label) or self.confirmed(label, now):
                if continuous:
                    self._continuous = (action, label)
                self.passed += 1
                conf_pct = int(round(self.calibrated_confidence(label) * 100.0))
                self.last_note = f"TCN 확인: {NAMES.get(label, label)} ({conf_pct}%)"
                return result
            if not continuous:
                self._pending = (action, label, now)
            result.action = "none"
            result.is_moving = False
            conf_pct = int(round(self.calibrated_confidence(label) * 100.0))
            if self.last_rejection_reason.startswith("low_quality:"):
                self.last_note = f"품질 보류 ({self.last_quality_score:.2f}): " + NAMES.get(label, label)
            elif self.last_rejection_reason.startswith("ood_mahalanobis:"):
                r_val = float((self.last_ood or {}).get("ratio", 0.0))
                self.last_note = f"미등록/OOD 보류 (거리비 {r_val:.2f}): " + NAMES.get(label, label)
            elif self.last_rejection_reason.startswith("hold_duration:"):
                self.last_note = f"TCN 유지 대기 ({conf_pct}%): " + NAMES.get(label, label)
            else:
                self.last_note = f"TCN 확인 대기 ({conf_pct}%): " + NAMES.get(label, label)
            return result
        if self._pending is not None:
            kind, pending_label, since = self._pending
            if now - since > self.wait:
                self._pending = None
                self.blocked += 1
                self.last_note = "TCN: 의도 확인 안 됨"
            elif self.confirmed(pending_label, now):
                self._pending = None
                self.passed += 1
                conf_pct = int(round(self.calibrated_confidence(pending_label) * 100.0))
                self.last_note = f"TCN 확인: {NAMES.get(pending_label, pending_label)} ({conf_pct}%)"
                if action in ("none", "move_cursor", None):
                    result.action = kind
        return result
