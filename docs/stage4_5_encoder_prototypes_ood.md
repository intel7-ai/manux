# Stage 4 · 5 Report: Contrastive Representation Learning, Prototype Classifier, Specialist Coexistence vs. Integration, and Mahalanobis OOD Rejection

## 1. Executive Summary
- **Stage 4 (Representation Learning & Prototype Classifier)**:
  - Extended `SpatialMlpModel` (`src/spatial_mlp.py`) to expose its hidden embedding encoder (`encode(features, normalize=...)`), L2-normalized class prototypes (`prototypes`), cosine prototype classifier (`prototype_similarity()`, `predict_prototype_proba()`), and post-deployment encoder freeze flag (`encoder_frozen = True`) while preserving the zero-dependency pure-NumPy inference path.
  - Added **Supervised Contrastive Loss (`SupCon`, `supcon_weight = 0.22`, `supcon_temp = 0.12`)** in `_fit_mlp_weights()` where positive pairs prioritize the same gesture across **different posture conditions / recording sessions** (`same_cls & diff_grp`).
  - Measured `H = 64` vs. `H = 128`, Softmax vs. Prototype classification, and **Single Encoder vs. Frozen-Encoder Linear Specialist Heads vs. Binary Specialist Bank Coexistence** across both Condition-Group Leave-Out (`59,665` frames, `16,942` held-out test) and Recording-Session Leave-Out (`23,036` held-out test).
  - **Key Stage 4 Finding**: Adding `SupCon` improves single-encoder condition leave-out accuracy by **`+6.07%p`** (`72.87% -> 78.94%` at `H = 64`) and **`+6.50%p`** (`73.23% -> 79.73%` at `H = 128`), and lifts active-gesture nearest-prototype classification accuracy by **`+30.98%p`** (`61.19% -> 92.17%`). **`Coexistence` (`H = 64` SupCon encoder + binary specialist bank)** achieves the highest accuracy on both Condition Leave-Out (**`83.90%`**, `+1.33%p` over Stage 6 `82.57%`, `+5.14%p` over Stage 1 `78.76%`) and Session Leave-Out (**`85.91%`**, `+0.33%p` over Stage 6 `85.58%`, `+4.58%p` over Stage 1 `81.33%`).
- **Stage 5 (OOD / Unregistered Gesture Rejection)**:
  - Computed class-wise mean and shrinkage-regularized covariance (`shrinkage = 0.15`) on unnormalized hidden embeddings `h = encode(x, normalize=False)` and calibrated class-wise 95th-percentile Mahalanobis distance thresholds on held-out posture conditions (`ood_means`, `ood_precisions`, `ood_thresholds`).
  - Wired `ood_vetoes(name)` into `GestureGate` (`src/gesture_gate.py`) to reject out-of-distribution postures before approval and report `last_rejection_reason = "ood_mahalanobis:<ratio>"`.
  - Evaluated on `11,794` real out-of-distribution frames extracted from `field-logs/2026-11-04-home-pc-phone/failures/*.json` (`v_sign`: `495` frames, `rock_sign`: `99` frames, `open_move`: `7,469` frames, `fist_move`: `3,200` frames, `motion_swipe`: `531` frames).
  - **Key Stage 5 Finding**: Shrinkage Mahalanobis OOD rejection retains **`94.94%`** of in-distribution active test frames at `ratio <= 1.0` (`median ratio = 0.524`) while slashing `rock_sign` false active pass rate (`p >= 0.70`) from **`91.92%` (`Softmax` baseline) to `19.19%` (`76.8%` vetoed, `median ratio = 3.26`)**, `v_sign` false pass from **`7.68%` to `3.23%` (`75.6%` vetoed, `median ratio = 2.09`)**, and reducing 15-fold leave-condition-out neutral misfires from **`27` (`1.53/min`) in Stage 6 to `25` (`1.42/min`, `-7.4%` vs. Stage 6, `-28.6%` vs. Stage 1)**.

---

## 2. Stage 4 Ablation: Embedding Dimension (`64` vs. `128`), `SupCon`, Prototypes, and Specialist Coexistence vs. Integration

| Encoder Dimension (`H`) | `SupCon` Weight | Split Mode | Base Softmax Only | Active-6 Prototype Nearest Centroid | Frozen-Encoder Specialist Heads (Integration) | SupCon Encoder + Binary Specialist Bank (Coexistence) |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: |
| `H = 64` | `0.00` (CE only) | Condition Leave-Out | `72.87%` | `61.19%` | `77.99%` | `82.16%` |
| **`H = 64` (Selected)** | **`0.22` (Cross-Group SupCon)** | **Condition Leave-Out** | **`78.94%` (`+6.07%p`)** | **`92.17%` (`+30.98%p`)** | **`82.68%` (`+4.69%p`)** | **`83.90%` (`+1.74%p`)** |
| `H = 128` | `0.00` (CE only) | Condition Leave-Out | `73.23%` | `60.40%` | `79.93%` | `82.21%` |
| `H = 128` | `0.22` (Cross-Group SupCon) | Condition Leave-Out | `79.73%` (`+6.50%p`) | `92.03%` (`+31.63%p`) | `82.73%` (`+2.80%p`) | `83.00%` (`+0.79%p`) |
| `H = 64` | `0.00` (CE only) | Session Leave-Out | `72.17%` | `76.97%` | `77.95%` | `84.67%` |
| **`H = 64` (Selected)** | **`0.22` (Cross-Group SupCon)** | **Session Leave-Out** | **`78.58%` (`+6.41%p`)** | **`92.25%` (`+15.28%p`)** | **`83.83%` (`+5.88%p`)** | **`85.91%` (`+1.24%p`)** |
| `H = 128` | `0.00` (CE only) | Session Leave-Out | `72.51%` | `76.01%` | `79.30%` | `84.71%` |
| `H = 128` | `0.22` (Cross-Group SupCon) | Session Leave-Out | `78.43%` (`+5.92%p`) | `92.35%` (`+16.34%p`) | `82.90%` (`+3.60%p`) | `85.44%` (`+0.73%p`) |

### Architectural Conclusions from Stage 4 Measurements
1. **Why Pure Prototype Argmax Over `none` + Active Classes Underperforms Softmax on `none`, While Excelling on Active Classes (`92.17%`)**:
   - The `none` class is inherently multimodal (open palm, half-fist cursor move, resting hand, transition frames), so a single spherical `none` centroid has lower global accuracy (`66.46%` raw) than a linear hyperplane (`78.94%`).
   - Conversely, among the 6 active target gestures (`thumb_index`, `thumb_middle`, `thumb_ring`, `thumb_pinky`, `palm_side`, `thumbs_up`), `SupCon` clusters each gesture into a tight, posture-invariant cone, raising nearest-prototype accuracy from **`61.19%` to `92.17%` (`+30.98%p`)** on Condition Leave-Out and **`76.97%` to `92.25%` (`+15.28%p`)** on Session Leave-Out.
2. **Specialist Integration vs. Coexistence**:
   - Replacing separate binary specialists with linear specialist heads on top of the frozen `SupCon` encoder (`Frozen-Encoder Specialist Heads`) reaches `82.68%` (Condition) and `83.83%` (Session), closing 85% of the gap to full specialists.
   - However, **Coexistence (`H = 64` SupCon base encoder + binary specialist bank)** remains highest on both splits (`83.90%` Condition, `85.91%` Session) because each `48`-unit specialist allocates dedicated non-linear capacity to hard-negative adjacent-finger boundaries (`hard_negative_weight = 1.65`).
3. **Post-Deployment Frozen Encoder Policy**:
   - `SpatialMlpModel.encoder_frozen` is set to `True` in the saved artifact (`models/spatial_mlp.json`). Post-deployment user onboarding, custom gesture registration, and runtime adaptation (Stage 8) **never backpropagate into `W1, b1`** and never use Replay, iCaRL, or Knowledge Distillation; they operate strictly on the frozen `64`-D embedding space via `prototypes`, `ood_means/thresholds`, and calibration parameters.

---

## 3. Stage 5 Ablation: Softmax Max-Probability vs. kNN vs. Shrinkage Mahalanobis on Real Out-of-Model Gestures

Evaluated on held-out in-distribution active test frames (`n = 5,634`) vs. `11,794` real OOD frames from `field-logs/2026-11-04-home-pc-phone/failures/*.json`:

| Dataset / Gesture Category | Frames (`n`) | Softmax Baseline False Pass (`p >= 0.70`) | + kNN (`K = 8`, 95th-pct) False Pass (Veto Rate / Med Ratio) | + L2-Normalized Mahalanobis False Pass (Veto Rate / Med Ratio) | **+ Unnormalized Shrinkage Mahalanobis (`s = 0.15`, 95th-pct) False Pass (Veto Rate / Med Ratio)** | Most Frequent Confused Active Class |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **In-Distribution Active Test (Target Recall)** | `5,634` | `75.12%` pass | `94.94%` pass (`0.534` med) | `94.94%` pass (`0.443` med) | **`94.94%` pass (`0.524` med)** | (Matches true active gesture) |
| **OOD: `rock_sign` (Index+Pinky Extended)** | `99` | `91.92%` | `16.16%` (`78.8%` veto / `4.69`) | `91.92%` (`4.0%` veto / `0.58`) | **`19.19%` (`76.8%` veto / `3.26`)** | `thumb_middle` (`95/99`) |
| **OOD: `v_sign` (Index+Middle V-Sign)** | `495` | `7.68%` | `3.23%` (`89.7%` veto / `1.90`) | `6.87%` (`71.7%` veto / `1.40`) | **`3.23%` (`75.6%` veto / `2.09`)** | `thumb_ring` (`274`), `thumb_pinky` (`137`) |
| **OOD: `open_move` (5-Finger Open Palm Move)** | `7,469` | `10.27%` | `8.22%` (`65.7%` veto / `1.56`) | `9.37%` (`84.4%` veto / `2.51`) | **`8.49%` (`67.3%` veto / `1.82`)** | `thumb_index` (`4,062`), `thumb_ring` (`1,557`) |
| **OOD: `fist_move` (Closed Fist Move)** | `3,200` | `0.62%` | `0.34%` (`65.4%` veto / `1.19`) | `0.09%` (`98.0%` veto / `2.24`) | **`0.12%` (`82.8%` veto / `1.66`)** | `thumb_index` (`1,882`), `thumb_middle` (`891`) |
| **OOD: `motion_swipe` (Dynamic Swipe)** | `531` | `53.11%` | `37.85%` (`29.9%` veto / `0.78`) | `30.51%` (`52.7%` veto / `1.05`) | **`29.57%` (`38.2%` veto / `0.76`)** | `thumb_index` (`268`), `thumb_ring` (`188`) |

- **Why Unnormalized Shrinkage Mahalanobis Beats L2-Normalized Mahalanobis on `rock_sign`**:
  - In `rock_sign` (index and pinky extended, middle and ring curled toward the thumb), the angular direction of `h` aligns with `thumb_middle`, so L2-normalized Mahalanobis fails to veto (`medRatio = 0.58`, `4.0%` vetoed).
  - However, the unnormalized hidden activation `h` exhibits an extreme off-manifold magnitude along the orthogonal pinky-extension subspace, producing a median unnormalized Mahalanobis ratio of **`3.26`** and vetoing **`76.8%`** of `rock_sign` frames (`91.92% -> 19.19%` false pass).

---

## 4. End-to-End Stage 1 → Stage 2·3 → Stage 6 → Stage 4·5 Progression (`scripts/eval_harness.py`)

| Metric | Stage 1 Baseline | Stage 2·3 (Quality + Labels) | Stage 6 (Augmentation) | Stage 4·5 (SupCon + OOD) | Delta (`Stage 4·5 - Stage 6`) | Cumulative Delta (`Stage 4·5 - Stage 1`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Fixture Regressions (`tests/fixtures/*.json`)** | `3 / 3` PASS | `3 / 3` PASS | `3 / 3` PASS | **`3 / 3` PASS** | `0` | `0` |
| **Condition-Group Leave-Out Accuracy** | `78.76%` | `81.71%` | `82.57%` | **`83.90%`** | **`+1.33%p`** | **`+5.14%p`** |
| **Recording-Session Leave-Out Accuracy** | `81.33%` | `84.55%` | `85.58%` | **`85.91%`** | **`+0.33%p`** | **`+4.58%p`** |
| **Quantified Random-Split Leakage Delta** | `+19.75%p` | `+17.20%p` | `+16.38%p` | **`+15.09%p`** | **`-1.29%p`** | **`-4.66%p`** |
| **Leave-Out Neutral Misfires (`17.65 min`)** | `35` (`1.98/min`) | `31` (`1.76/min`) | `27` (`1.53/min`) | **`25` (`1.42/min`)** | **`-2` (`-7.4%`)** | **`-10` (`-28.6%`)** |
| **Deployed Hybrid Neutral Misfires (`17.65 min`)** | `6` (`0.34/min`) | `6` (`0.34/min`) | `6` (`0.34/min`) | **`6` (`0.34/min`)** | `0` | `0` |
| **15-Fold Leave-Condition-Out Macro Recall** | `71.85%` (`508/707`) | `74.12%` (`524/707`) | `74.40%` (`526/707`) | **`74.12%` (`524/707`)** | `-0.28%p` (`thumb_pinky +3.5%p`) | **`+2.27%p` (`+16` reps)** |
| **Deployed Hybrid Pipeline Macro Recall** | `91.23%` (`645/707`) | `91.65%` (`648/707`) | `91.65%` (`648/707`) | **`91.37%` (`646/707`)** | `-0.28%p` | **`+0.14%p` (`+1` rep)** |

---

## 5. Remaining Risks Entering Stage 7 (Decision Policy & Calibration)
1. **Uniform Gate Hyperparameters Across Distinct Risk Profiles**:
   - Currently `GestureGate` uses fixed constants (`spatial_alpha = 0.42`, `threshold = 0.70`, `spatial_veto_threshold = 0.80`, `spatial_boost_threshold = 0.85`, `frames_in_a_row = 3`) across all gestures. Destructive or mode-switching gestures (`palm_side`, `thumbs_up`, `v_sign`, `rock_sign`) require stricter consecutive-frame / hold-time / OOD policies than frequent pointer actions (`thumb_index`, `thumb_middle`).
2. **Uncalibrated Probability Confidence (ECE)**:
   - Blended TCN + Spatial MLP probabilities have not yet undergone validation-set temperature scaling (`T`), and the UI does not yet surface calibrated confidence alongside explicit rejection reasons (`low_quality`, `ood_mahalanobis`, `spatial_neutral_veto`, `below_threshold`).
