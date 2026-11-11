# Stage 8 Report: User Onboarding, Few-Shot Custom Gesture Registration, and Bounded EMA Runtime Adaptation

## 1. Executive Summary
- **Stage 8-1 (1–2 Minute User Onboarding Flow)**:
  - Implemented `ONBOARDING_STEPS` (`open_hand` 8s → `pinch_index` 12s → `pinch_ring` 12s → `pinch_middle` 12s → `pinch_pinky` 12s → `move_hand` 12s → `neutral_move_30s` 30s; total `98s` active + `10.5s` prep = `108.5s`, ~1.8 min) in `src/calibration.py`.
  - Added automated **hand size & lighting / detection stability diagnostics** (`HAND_SIZE_MIN_WARN = 0.085`, `HAND_SIZE_MAX_WARN = 0.620`, low-light/instability check when detection confidence `< 0.72` or lost-frame share `> 25%`).
  - Added **frozen-encoder prototype & OOD adaptation** (`SpatialMlpModel.apply_user_onboarding_prototypes()` in `src/spatial_mlp.py`) and user profile persistence (`save_profile_adaptation()`, `apply_profile_adaptation_to_model()` in `src/profiles.py`).
  - **Measured Impact on Disjoint Held-Out Frames (`n = 15,502`)**: Active gesture accuracy improves from **`82.37%` → `83.64%` (`+1.27%p`)** and overall accuracy improves from **`83.98%` → `84.25%` (`+0.28%p`)** (`thumb_pinky`: `71.97% → 75.07% (+3.10%p)`, `palm_side`: `79.34% → 81.82% (+2.48%p)`, `thumb_middle`: `77.25% → 78.12% (+0.87%p)`, `thumb_index`: `84.35% → 84.91% (+0.56%p)`, `thumb_ring`: `81.60% → 81.84% (+0.24%p)`) while keeping the encoder strictly frozen (`encoder_frozen = True`, `encoder_weight_max_diff = 0.000000`).
- **Stage 8-2 (3–5 Rep Custom Gesture Registration on Frozen Encoder)**:
  - Implemented `SpatialMlpModel.register_custom_gesture()` and `GestureGate.register_custom_gesture()` (`src/spatial_mlp.py`, `src/gesture_gate.py`) without Replay, iCaRL, or Knowledge Distillation (`W1, b1, W2, b2` are never mutated).
  - **Safety Check 1 (Prototype Proximity Warning)**: Computes cosine similarity against all existing base and custom prototypes and emits a warning when `closest_cosine >= 0.85` (verified on `duplicate_thumb_index`: `closest_class = "thumb_index"`, `closest_cosine = 0.9349 >= 0.85`, `proximity_warning_triggered = True`).
  - **Safety Check 2 (30s Neutral-Movement False-Fire Verification)**: Replays 30 seconds (`900` frames at 30fps) of neutral hand movement (`neutral_features`) and automatically raises `recommended_threshold` (from `0.72` up to `0.92` in `+0.04` steps) if any `k_frames = 4` false triggers occur.
  - **Measured Few-Shot Recall on Unseen Gestures**:
    - `thumbs_up` (100% unseen by 5-class encoder): **3 reps → `99/112` (`88.39%` recall)**, **5 reps → `96/110` (`87.27%` recall)**, `0` neutral misfires in 30s.
    - `palm_side` (100% unseen by 5-class encoder): **3 reps → `125/172` (`72.67%` recall)**, **5 reps → `132/170` (`77.65%` recall)**, `0` neutral misfires in 30s.
    - `v_sign_custom` (real out-of-dataset V-sign episodes from `field-logs/2026-11-04-home-pc-phone/failures` registered on the full 7-class model): **3 reps → `7/8` (`87.50%` recall)**, `0` neutral misfires in 30s.
- **Stage 8-3 (Runtime Adaptation: Shadow Mode First → Bounded EMA with Rollback)**:
  - Implemented `SpatialMlpModel.adapt_prototype_ema()`, `create_rollback_point()`, and `rollback_prototypes()` (`src/spatial_mlp.py`) and wired `GestureGate.record_runtime_adaptation()` (`src/gesture_gate.py`).
  - Defaults to `shadow_mode = True` (`runtime_adaptation_enabled = False`, `shadow_adaptation_enabled = True`), logging candidate updates without mutating live prototypes (`shadow_mode_prototype_diff = 0.000000`).
  - When enabled (`shadow_mode = False`), updates are strictly gated by `calibrated_confidence >= 0.88`, `quality_score >= 0.75`, `ood_ratio <= 0.90`, `not user_cancelled`, and `max_cosine_drift <= 0.12` from `_anchor_prototypes`, improving held-out accuracy from **`83.98%` → `84.22%` (`+0.25%p`)** across `24` updates and restoring exact pre-adaptation prototypes on `rollback_prototypes()` (`post_rollback_prototype_max_diff = 0.000000`).

---

## 2. Stage 8-1: 1–2 Minute User Onboarding Flow & Profile Adaptation

### 2.1 Onboarding Protocol (`ONBOARDING_STEPS` in `src/calibration.py`)
| Step | Key | Duration | Purpose |
| --- | --- | --- | --- |
| 1 | `open_hand` | `8.0s` | Measure resting hand size (`calibrated_hand_size`), palm stability (jitter), and baseline detection confidence |
| 2 | `pinch_index` | `12.0s` | Measure thumb–index pinch enter/release thresholds and contact landmarks for user prototype adaptation |
| 3 | `pinch_ring` | `12.0s` | Measure thumb–ring pinch selectivity, confuser share, and contact landmarks |
| 4 | `pinch_middle` | `12.0s` | Measure thumb–middle pinch selectivity and contact landmarks |
| 5 | `pinch_pinky` | `12.0s` | Measure thumb–pinky pinch selectivity and contact landmarks |
| 6 | `move_hand` | `12.0s` | Capture normal cursor translation across screen bounds |
| 7 | `neutral_move_30s` | `30.0s` | Capture 30 seconds of unconstrained neutral movement for false-fire verification and negative margin calibration |

### 2.2 Measured Stage 8-1 Before vs. After Results (`models/stage8_eval_report.json`)
Evaluated on `16,942` held-out frames from Condition-Group Leave-Out (`1,440` frames used for onboarding calibration; `15,502` strictly disjoint frames used for evaluation):

| Metric / Class | Before Onboarding (Generic Prototypes) | After Stage 8-1 Onboarding (`blend_beta = 0.35`) | Delta |
| --- | --- | --- | --- |
| **Encoder Frozen (`W1, b1` max diff)** | `True` (`0.0`) | `True` (`0.000000`) | **Zero encoder mutation** |
| **Overall Held-Out Accuracy (`n = 15,502`)** | `83.98%` | **`84.25%`** | **`+0.28%p`** |
| **Active Gesture Accuracy (`n = 5,903`)** | `82.37%` | **`83.64%`** | **`+1.27%p`** |
| `none` (Neutral / Background) | `84.97%` | `84.63%` | `-0.34%p` |
| `thumb_index` | `84.35%` | **`84.91%`** | **`+0.56%p`** (cosine shift `0.00161`, OOD thr `4.8627`) |
| `thumb_ring` | `81.60%` | **`81.84%`** | **`+0.24%p`** (cosine shift `0.00049`, OOD thr `9.6273`) |
| `thumb_middle` | `77.25%` | **`78.12%`** | **`+0.87%p`** (cosine shift `0.00735`, OOD thr `11.2354`) |
| `thumb_pinky` | `71.97%` | **`75.07%`** | **`+3.10%p`** (cosine shift `0.00439`, OOD thr `11.5077`) |
| `palm_side` | `79.34%` | **`81.82%`** | **`+2.48%p`** (cosine shift `0.00621`, OOD thr `12.3426`) |
| `thumbs_up` | `100.00%` | **`100.00%`** | **`0.00%p`** (cosine shift `0.00081`, OOD thr `4.7078`) |

---

## 3. Stage 8-2: Few-Shot (3–5 Rep) Custom Gesture Registration

### 3.1 Few-Shot Registration Architecture
- **Zero Retraining**: `SpatialMlpModel.register_custom_gesture()` trims `15%` boundary transition frames from each of the `3–5` user repetitions, encodes the core frames through the frozen encoder (`encode(core, normalize=True)`), and stores the L2-normalized mean prototype in `custom_prototypes[label]` alongside shrinkage Mahalanobis OOD statistics (`0.35 * emp_cov + 0.65 * prior_cov`).
- **No Replay / iCaRL / Knowledge Distillation**: Base encoder weights (`W1, b1`), softmax head (`W2, b2`), and binary specialists remain 100% untouched.

### 3.2 Measured Few-Shot Registration Results (`models/stage8_eval_report.json`)
| Custom Gesture Target | Training Encoder Setup | Registered Reps | Held-Out Rep Recall | Held-Out Frame Acc | Rep Consistency Cosine | Closest Existing Class (`cos`) | 30s Neutral Misfires (Default → Tuned) | Base Classes Acc (Before → After) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **`thumbs_up`** | 5-class frozen encoder (unseen class) | **3 reps** | **`99 / 112` (`88.39%`)** | `80.37%` | `0.9525` | `thumb_index` (`0.8222`) | `0 → 0` (`thr=0.72`) | `83.38% → 82.93%` |
| **`thumbs_up`** | 5-class frozen encoder (unseen class) | **5 reps** | **`96 / 110` (`87.27%`)** | `76.96%` | `0.9542` | `thumb_index` (`0.8038`) | `0 → 0` (`thr=0.72`) | `83.38% → 83.14%` |
| **`palm_side`** | 5-class frozen encoder (unseen class) | **3 reps** | **`125 / 172` (`72.67%`)** | `66.88%` | `0.8187` | `thumb_index` (`0.8401`) | `0 → 0` (`thr=0.72`) | `83.38% → 83.35%` |
| **`palm_side`** | 5-class frozen encoder (unseen class) | **5 reps** | **`132 / 170` (`77.65%`)** | `73.59%` | `0.8238` | `thumb_index` (`0.8146`) | `0 → 0` (`thr=0.72`) | `83.38% → 83.18%` |
| **`v_sign_custom`** | Full 7-class frozen encoder (field-logs V-sign) | **3 reps** | **`7 / 8` (`87.50%`)** | — | `0.9131` | `thumb_ring` (`0.7637`) | `0 → 0` (`thr=0.72`) | Unchanged |
| **`duplicate_thumb_index`** | Full 7-class frozen encoder (safety test) | **3 reps** | Safety check | — | — | `thumb_index` (**`0.9349 >= 0.85`**) | `0 → 0` | **Proximity Warning Triggered** |

---

## 4. Stage 8-3: Shadow-Mode Logging & Bounded EMA Runtime Adaptation with Rollback

| Verification Check | Measured Result |
| --- | --- |
| **Shadow Mode (`shadow_mode = True`, default)** | Logs accepted candidate updates in `shadow_adaptation_log` while keeping `shadow_mode_prototype_diff = 0.000000` |
| **Live Bounded EMA (`shadow_mode = False`, `ema_alpha = 0.08`)** | Applied `24` high-confidence updates; held-out accuracy improved from **`83.98%` → `84.22%` (`+0.25%p`)** |
| **User Cancellation Guard (`user_cancelled = True`)** | Rejected (`cancelled_rejected = True`, `reason = "user_cancelled"`) |
| **Low Calibrated Confidence Guard (`conf = 0.65 < 0.88`)** | Rejected (`low_confidence_rejected = True`, `reason = "low_confidence:0.650<0.880"`) |
| **Maximum Drift Cap (`max_cosine_drift = 0.12`)** | Prevents any class prototype from drifting `> 0.12` cosine distance from its pre-adaptation `_anchor_prototypes` |
| **Instant Rollback (`rollback_prototypes()`)** | Restores exact pre-adaptation prototypes, custom prototypes, and OOD thresholds (`post_rollback_prototype_max_diff = 0.000000`) |

---

## 5. Remaining Risks & Limitations
1. **Single-User / Single-Camera Recording Corpus**:
   - While Stage 8-1 onboarding and Stage 8-2 few-shot custom registration are verified on held-out posture conditions and out-of-dataset `field-logs` V-sign episodes, all 20 guided sessions in `recordings/` originate from one primary user and one camera setup (`1280x720@30fps`). Multi-user onboarding validation (`--split-mode person`) and multi-camera validation (`--split-mode camera`) should be run as soon as additional user/camera recordings are added.
2. **Static vs. Dynamic Custom Gestures**:
   - Stage 8-2 registers static/posture custom gestures in the 64D `SpatialMlpModel` embedding space. Highly dynamic trajectory gestures (e.g., circular air swipes with variable speed) are not yet aligned via temporal warping (see `Soft-DTW` adoption condition in `docs/final_comprehensive_report.md`).
