# Stage 2 & 3 Report: Input Quality Gating & Guided Label Refinement

**Date:** 2026-11-08  
**Stage:** `2·3 (입력 품질 점수화/학습 가중치 & 가이드 녹화 전이 구간 약한 라벨 정제/Confident Learning)`  
**Evaluation Harness Artifact:** `models/stage2_3_eval_report.json`, `models/confident_learning_candidates.json`

---

## 1. Stage 2: Forensic Analysis of `lost_mid_frame` Failures & Per-Frame Input Quality Score

### 1.1 Forensic Analysis of 200 Field Failure Logs (`field-logs/2026-11-04-home-pc-phone/failures/`)
We analyzed all 200 field failure logs (`analyze_lost_mid_frame_logs()` in `src/data_quality.py`), of which **102 / 200 (51.0%)** are `lost_mid_frame` tracking loss events (`lost_ms`: median `63.0 ms`, p90 `550.7 ms`, max `1998.0 ms`).

| Root Cause / Diagnostic Signal in `lost_mid_frame` (n=102) | Count | Share (%) | Key Finding |
|---|---:|---:|---|
| **Handedness Filter Drop (`raw_hands_during_loss > 0`)** | 90 | **88.2%** | Hand was still detected by MediaPipe, but angle/self-occlusion flipped left/right handedness classification and dropped the control hand. |
| **Camera Frame / FPS Drop (`camera_drops_in_window > 0`)** | 29 | **28.4%** | Camera frame delivery stalled (`dt_ms` spike up to `78~250 ms`) immediately before tracking loss. |
| **Rapid Hand Size Change (`|size_change_0.4s - 1.0| > 20%`)** | 28 | **27.5%** | Rapid depth/angle change foreshortened or inflated the 2D hand bounding box (`min=0.53x`, `max=1.65x`). |
| **Low Detector Confidence (`score_min_0.4s < 0.85`)** | 18 | **17.6%** | Tracker confidence degraded prior to dropout (`p10=0.801`, `min=0.754`). |

Comparing the **3 frames immediately before tracking loss (`pre_loss_3f`, n=234)** and the **first re-entry frame (`post_reentry_1f`, n=83)** against normal tracking frames (`normal`, n=5,178):

| Diagnostic Metric | Normal Frames (n=5,178) | Pre-Loss 3 Frames (n=234) | Post-Loss Re-entry 1 Frame (n=83) | Shift vs. Normal |
|---|---:|---:|---:|---|
| **Max 2D Joint Jump (`joint_jump`)** | med `0.0055` (p90 `0.0257`) | med `0.0219` (p90 `0.0531`) | med `0.0782` (p90 `0.2762`) | **4.0× higher pre-loss, 14.2× higher on re-entry** |
| **Palm Scale Ratio (`scale_ratio`)** | med `1.0087` (p90 `1.0543`) | med `1.0376` (p90 `1.1593`) | med `1.1003` (p90 `1.4799`) | **+10.5%p p90 scale spike pre-loss, +42.6%p on re-entry** |
| **Detector Confidence (`score`)** | med `0.9810` (p10 `0.9190`) | med `0.9615` (p10 `0.8650`) | — | **p10 drops from `0.919` to `0.865`** |
| **Computed Input Quality Score (`[0, 1]`)** | med `1.0000` (p10 `1.0000`) | med `0.7850` (p10 `0.6368`, min `0.2683`) | med `0.4759` (p10 `0.0000`, min `0.0000`) | Separates unstable pre/post-loss frames from normal motion |

### 1.2 Per-Frame Input Quality Score (`compute_frame_quality_score`) & `GestureGate` Withholding
- **Definition (`src/data_quality.py`)**: `compute_frame_quality_score()` combines 4 signals into a bounded `[0.0, 1.0]` score (`FrameQualityScore`):
  1. `joint_jump`: penalized when max 2D landmark jump exceeds `0.048` (severe penalty above `0.135`).
  2. `scale_ratio`: penalized when frame-to-frame palm size ratio exceeds `1.14` or absolute palm/span is outside anatomical bounds.
  3. `confidence`: penalized when detector confidence drops below `0.85`.
  4. `edge_margin`: penalized when any landmark approaches within `0.020` of the `[0, 1]` screen border.
  5. `near_tracking_loss`: additional penalty during the 2 frames before/after a missing-hand dropout or frame gap (`dt > 0.12 s`).
- **Runtime Gate Integration (`src/gesture_gate.py`)**:
  - `GestureGate.step()` computes `last_quality_score` and `last_quality_reason` on every frame and tracks a 2-frame post-loss cooldown (`_post_loss_frames_remaining`).
  - When `quality_vetoes()` is active (`last_quality_score < quality_threshold`, default `0.45`), `GestureGate.confirmed()` withholds click/scroll approval (`quality_withheld += 1`, `last_rejection_reason = "low_quality:<reason>"`) and downweights single-frame Spatial MLP blending (`q_factor = 0.45`) when quality is degraded (`< 0.60`).
- **Training Sample Downweighting (`src/capture_dataset.py`, `scripts/train_tcn.py`, `src/spatial_mlp.py`)**:
  - `compute_sequence_quality_scores()` assigns per-frame `sample_weights` (`<= 0.20` within 2 frames of tracking loss, `0.0` when combined with velocity spikes `> 0.65`, and `<= 0.35` on isolated velocity spikes `> 1.10`).
  - Wired into both `train_tcn.train(..., sample_weights=...)` and `train_spatial_mlp(..., weights_all=...)`.

---

## 2. Stage 3: Guided Recording Transition Refinement & Confident Learning

### 2.1 Non-Destructive Weak-Label Refinement (`refine_guided_transition_labels` in `src/capture_dataset.py`)
In guided recordings, users react to the `"do"` prompt with human reaction delay at the start and often release the pinch early before `"do"` ends. Across the 20 primary recordings (`32,341` initial `do` positive frames):
- Raw `do` frames contained substantial unpinched onset/release tails (`kept_unpinched(>0.48)` = `1,909` frames across the 4 pinch classes; `competing_closer` = `2,079` frames where an adjacent finger was closer to the thumb than the scheduled target finger).
- `refine_guided_transition_labels()`:
  - **Preserves all raw labels** in `frame["label"]["raw_target"]` and `session["raw_y"]`.
  - Uses normalized 3D thumb-to-fingertip pinch distance (`spatial_features[:, 68:72]`) and competing-finger clearance within each contiguous `do` repetition to relabel unpinched onset/release boundary frames as `refined_target = "transition"` (`y = -1`), while **guaranteeing that the core contact interval (`max(5, len // 3)` closest frames) of every valid repetition is preserved**.
  - Downweights ambiguous mid-stretch frames (`sample_weights *= 0.35`) and slightly boosts clean core contact frames (`* 1.15`).
- **Refinement Summary across 20 Sessions**:
  - Raw `do` positive frames: **32,341**
  - Relabeled as `transition` (`y = -1`, raw label preserved): **4,198 (13.0%)**
  - Downweighted ambiguous mid-rep frames: **1,379 (4.3%)**
  - Retained clean `do` positive frames: **28,143 (87.0%)**

### 2.2 Confident Learning Candidate Report (`models/confident_learning_candidates.json`)
Using 5-fold group cross-validation out-of-sample probabilities (`find_confident_learning_candidates` in `src/data_quality.py`), we computed per-class self-confidence thresholds (`t_c`), the $7 \times 7$ Confident Joint matrix, and ranked label-error / high-loss candidates **without automatically deleting any samples**:
- **Total evaluated frames**: `59,713`
- **Flagged candidates**: `1,986` (`3.33%` of dataset; top 250 saved with full frame index, timestamp, recording name, confidence margin, cross-entropy loss, and input quality score in `models/confident_learning_candidates.json`).
- **Breakdown by Issue Kind**:
  - `unpinched_or_missed_execution` (`given != none`, `suggested == none`): **812**
  - `lingering_gesture_in_neutral` (`given == none`, `suggested != none`): **603**
  - `adjacent_finger_confusion` (e.g., `thumb_middle <-> thumb_ring`, `thumb_index <-> thumb_middle`): **564**
  - `low_input_quality_artifact` (`quality_score < 0.55`): **7**

---

## 3. Measured Before vs. After Results (Stage 1 Baseline vs. Stage 2·3)

All metrics below were measured by running `scripts/eval_harness.py` under identical 15-condition leave-group-out evaluation (`models/stage1_eval_report.json` vs. `models/stage2_3_eval_report.json`).

### 3.1 `SpatialMlpModel` Generalization Accuracy (No Leakage)

| Evaluation Split | Stage 1 Baseline (`%`) | Stage 2·3 After (`%`) | Delta (`%p`) |
|---|---:|---:|---:|
| **Condition-Group Leave-Out Overall** | `78.76%` | **`81.71%`** | **`+2.95%p`** |
| **Recording-Session Leave-Out Overall** | `81.33%` | **`84.55%`** | **`+3.22%p`** |
| `none` (Condition Leave-Out) | `81.0%` | **`83.1%`** | `+2.1%p` |
| `thumb_index` (Condition Leave-Out) | `81.4%` | **`84.2%`** | `+2.8%p` |
| `thumb_middle` (Condition Leave-Out) | `71.0%` | **`74.1%`** | `+3.1%p` |
| `thumb_ring` (Condition Leave-Out) | `65.4%` | **`80.3%`** | **`+14.9%p`** |
| `thumb_pinky` (Condition Leave-Out) | `55.5%` | **`63.7%`** | **`+8.2%p`** |
| `palm_side` (Condition Leave-Out) | `77.9%` | **`77.7%`** | `-0.2%p` |
| `thumbs_up` (Condition Leave-Out) | `100.0%` | **`100.0%`** | `0.0%p` |

### 3.2 End-to-End `TCN + SpatialMLP` Event-Level Metrics (15 Held-Out Condition Folds)

| Metric / Gesture | Stage 1 Baseline | Stage 2·3 After | Measured Improvement |
|---|---|---|---|
| **Overall Leave-Out Repetition Recall (707 reps)** | `508 / 707` (`71.85%`) | **`524 / 707` (`74.12%`)** | **`+16 reps` (`+2.27%p`)** |
| **Neutral Movement False Fires (Leave-Out, 17.65m)** | `35` (`1.98 / min`) | **`31` (`1.76 / min`)** | **`-4 misfires` (`-11.4%`)** |
| **Neutral Movement False Fires (Deployed, 17.65m)** | `7` (`0.40 / min`) | **`6` (`0.34 / min`)** | **`-1 misfire` (`-14.3%`)** |
| `thumb_index` Leave-Out Recall (257 reps) | `228 / 257` (`88.7%`) | **`230 / 257` (`89.5%`)** | `+0.8%p` |
| `thumb_ring` Leave-Out Recall (89 reps) | `42 / 89` (`47.2%`) | **`45 / 89` (`50.6%`)** | **`+3.4%p`** (Latency `164.0ms → 156.4ms`) |
| `thumb_middle` Leave-Out Recall (126 reps) | `67 / 126` (`53.2%`) | **`67 / 126` (`53.2%`)** | `0.0%p` (Latency `282.4ms → 271.0ms`) |
| `thumb_pinky` Leave-Out Recall (85 reps) | `52 / 85` (`61.2%`) | **`59 / 85` (`69.4%`)** | **`+8.2%p`** (Latency `179.5ms → 170.7ms`) |
| `palm_side` Leave-Out Recall (90 reps) | `65 / 90` (`72.2%`) | **`67 / 90` (`74.4%`)** | **`+2.2%p`** (Latency `82.6ms → 76.2ms`) |
| `thumbs_up` Leave-Out Recall (60 reps) | `54 / 60` (`90.0%`) | **`56 / 60` (`93.3%`)** | **`+3.3%p`** |
| **Fixture Regressions (`tests/fixtures/*.json`)** | `3 / 3 PASS` | **`3 / 3 PASS`** | Zero regression |

---

## 4. Remaining Risks & Next Stage (`Stage 6: Augmentation Ablation`)
1. **Handedness Filter Drops in Tracker**: 88.2% of `lost_mid_frame` events occur when MediaPipe detects a hand (`raw_hands_during_loss > 0`) but flips the left/right handedness label under steep camera angles. While `GestureGate` now withholds false clicks during pre/post-loss frames, camera-angle/perspective robustness in the feature/model representation still limits `thumb_ring` (`50.6%`) and `thumb_middle` (`53.2%`) on unseen postures.
2. **Next Step (Stage 6)**: Systematically ablate individual 3D/camera/anatomical augmentations (`perspective_fov`, `z_noise`, `hand_scale`, `horizontal_flip` excluding directional gestures, `finger_length_ratio`, `temporal_speed_warp`) one by one before representation learning (Stage 4·5), enabling only augmentations with verified leave-out gains.
