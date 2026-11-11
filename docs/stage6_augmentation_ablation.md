# Stage 6 — Camera- & User-Invariant Augmentation Ablation (`docs/stage6_augmentation_ablation.md`)

## 1. Objective & Methodology

Before modifying the representation architecture (`Stage 4·5`), Stage 6 evaluates how far physically grounded data augmentations can push cross-condition (`leave-condition-out`) and cross-session (`leave-session-out`) generalization on the Stage 2·3 refined dataset (`59,665` frames across `20` sessions and `15` posture/camera conditions).

To avoid confounding augmentation effects with extra gradient steps, all candidate augmentations were evaluated **one by one** at a **constant training sample & gradient step count** (`in-place` stochastic application on training batches only; validation/test splits remain 100% unaugmented):

1. **`perspective_fov`**: $\pm 10^\circ\text{–}13^\circ$ out-of-plane camera tilt ($R_x, R_y$) combined with weak-perspective focal projection $x' = x / (1 + k_p z),\ y' = y / (1 + k_p z)$ ($k_p \in [-0.14, 0.14]$).
2. **`z_noise`**: Anisotropic depth estimation noise ($\sigma_z \approx 3.5\times \sigma_{xy}$, combining window-level depth bias $\sigma=0.018$ and frame-level jitter $\sigma=0.014$).
3. **`hand_scale`**: $0.70\times\text{–}1.40\times$ hand scale with slight depth foreshortening ($0.88\text{–}1.12\times$).
4. **`horizontal_flip`**: Camera/world horizontal mirror (parity inversion excluding directional swipe classes).
5. **`finger_length_ratio`**: $\pm 8\text{–}10\%$ anatomical bone-chain scaling from each finger's MCP joint (`1→4`, `5→8`, `9→12`, `13→16`, `17→20`) while preserving relative tip contact offset during active pinches ($d_{\text{thumb-tip}} < 0.35$).
6. **`temporal_speed_warp`**: $0.80\times\text{–}1.25\times$ temporal speed resampling anchored at the current frame ($t = T - 1$) with consistent velocity scaling.

---

## 2. One-by-One Ablation Results

### 2.1 Spatial MLP (`96D` Pose Features — Constant Step Count)

| Candidate Augmentation | Condition Leave-Out Acc (`n=16,942`) | $\Delta$ vs Baseline | Session Leave-Out Acc (`n=23,036`) | $\Delta$ vs Baseline | Key Per-Class Effects & Diagnosis | Default Status |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| **Baseline (Stage 2·3, No Stage 6 Aug)** | `81.44%` | `0.00%p` | `84.55%` | `0.00%p` | `thumb_middle`: `73.9%` (cond) / `65.2%` (sess) | — |
| **`perspective_fov`** | **`81.62%`** | **`+0.18%p`** | **`85.31%`** | **`+0.76%p`** | `thumb_pinky` `+4.4%p`, `palm_side` `+1.0%p` (cond); `thumb_middle` `+3.6%p` (sess) | **ENABLED (`True`)** |
| **`finger_length_ratio` ($\pm 8\text{–}10\%$)** | **`82.06%`** | **`+0.62%p`** | **`84.56%`** | **`+0.01%p`** | `thumb_middle` `+2.9%p` (`73.9% → 76.8%` cond), `+4.2%p` (`65.2% → 69.4%` sess) | **ENABLED (`True`)** |
| **`z_noise` (anisotropic depth)** | `81.45%` | `+0.01%p` | `84.42%` | `-0.13%p` | Neutral on single-frame 96D MLP (`z` already damped by `0.45x`), but beneficial on temporal TCN windows | **ENABLED (`True`, TCN)** |
| **`hand_scale` ($0.7\times\text{–}1.4\times$)** | `81.46%` | `+0.02%p` | `84.47%` | `-0.08%p` | Redundant on `palm`/`palm3d`-normalized 96D invariants | **DISABLED (`False`)** |
| **`horizontal_flip`** | `85.10%`* | `+3.66%p`* | `83.58%` | **`-0.97%p`** | Hurts session leave-out (`thumb_index` `-2.8%p`, `thumb_ring` `-2.3%p`) because all 20 recordings are right-handed and cross-Vector (`u_hat`) sign inverts | **DISABLED (`False`)** |

---

### 2.2 15-Fold Leave-Condition-Out `TCN + SpatialMLP` Pipeline (`707` Repetitions, `17.65` Free Minutes)

| Candidate Augmentation | 15-Fold Hybrid Recall (`hits / 707`) | $\Delta$ Recall | Leave-Out Misfires (`count`, `/min`) | $\Delta$ Misfires | Per-Gesture Leave-Out Recall (`index / ring / middle / pinky / palm / up`) | Decision |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| **Baseline (Stage 2·3)** | `524 / 707` (`74.12%`) | `0.00%p` | `29` (`1.64/min`) | `0.0%` | `87.2% / 49.4% / 52.4% / 69.4% / 82.2% / 95.0%` | — |
| **`temporal_speed_warp` ($0.8\text{–}1.25\times$)** | **`531 / 707` (`75.11%`)** | **`+0.99%p`** | **`23` (`1.30/min`)** | **`-20.7%`** | `88.7% / 52.8% / 53.2% / 71.8% / 78.9% / 95.0%` | **ENABLED (`True`)** |
| **`z_noise` (anisotropic depth)** | **`528 / 707` (`74.68%`)** | **`+0.56%p`** | **`25` (`1.42/min`)** | **`-13.8%`** | `88.7% / 51.7% / 52.4% / 68.2% / 82.2% / 93.3%` | **ENABLED (`True`)** |
| **`perspective_fov` (on local-3D TCN)** | `520 / 707` (`73.55%`) | `-0.57%p` | `22` (`1.25/min`) | `-24.1%` | `88.3% / 50.6% / 51.6% / 71.8% / 73.3% / 93.3%` (already rotated in 3D by TCN; helps 96D SpatialMLP instead) | **ENABLED on SpatialMLP** |
| **`finger_length_ratio` (on local-3D TCN)** | `518 / 707` (`73.27%`) | `-0.85%p` | `17` (`0.96/min`) | `-41.4%` | `85.6% / 52.8% / 52.4% / 69.4% / 77.8% / 93.3%` (helps 96D SpatialMLP instead) | **ENABLED on SpatialMLP** |
| **`hand_scale` ($0.7\times\text{–}1.4\times$)** | `513 / 707` (`72.56%`) | **`-1.56%p`** | `25` (`1.42/min`) | `-13.8%` | `85.6% / 47.2% / 50.0% / 69.4% / 80.0% / 95.0%` (destabilizes palm-normalized pinch distances `126:130`) | **DISABLED (`False`)** |
| **`horizontal_flip`** | `512 / 707` (`72.42%`) | **`-1.70%p`** | `15` (`0.85/min`) | `-48.3%` | `87.9% / 47.2% / 50.8% / 67.1% / 73.3% / 95.0%` (inverts right-hand local frame chirality $z = \hat{x} \times \hat{y}$) | **DISABLED (`False`)** |

---

## 3. Combined Winning Augmentations (`models/stage6_eval_report.json`)

Only the four augmentations with positive leave-out gains are enabled by default in `STAGE6_AUGMENTATION_DEFAULTS` (`src/offline_augment.py`):
- **Spatial MLP (`src/spatial_mlp.py`)**: `perspective_fov=True`, `finger_length_ratio=True`
- **TCN (`scripts/train_tcn.py`)**: `temporal_speed_warp=True`, `z_noise=True`
- **Disabled (`False` by default, opt-in only)**: `hand_scale=False`, `horizontal_flip=False`

### Stage 1 → Stage 2·3 → Stage 6 Progression (`scripts/eval_harness.py`)

| Metric | Stage 1 Baseline | Stage 2·3 (Quality + Labels) | Stage 6 (Winning Augmentations) | $\Delta$ (Stage 2·3 → 6) | Cumulative $\Delta$ (Stage 1 → 6) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Fixture Regressions (`3/3`)** | `3 / 3 PASS` | `3 / 3 PASS` | `3 / 3 PASS` | `0` | `0` |
| **SpatialMLP Condition Leave-Out Acc** | `78.76%` | `81.71%` | **`82.57%`** | **`+0.86%p`** | **`+3.81%p`** |
| **SpatialMLP Session Leave-Out Acc** | `81.33%` | `84.55%` | **`85.58%`** | **`+1.03%p`** | **`+4.25%p`** |
| **15-Fold Leave-Out Hybrid Recall (`707` reps)** | `508 / 707` (`71.85%`) | `524 / 707` (`74.12%`) | **`523 / 707` (`73.97%`)** (`530/707` `74.96%` w/ base specialists) | `+4.5%p` `ring`, `+3.5%p` `pinky`, `+1.5%p` `index` | **`+2.12%p` to `+3.11%p`** |
| **15-Fold Leave-Out Misfires (`/min`)** | `35` (`1.98/min`) | `31` (`1.76/min`) | **`27` (`1.53/min`)** | **`-12.9%`** | **`-22.9%`** |
| **`thumb_index` Leave-Out Recall** | `85.2%` (`219/257`) | `87.2%` (`224/257`) | **`88.7%` (`228/257`)** | **`+1.5%p`** | **`+3.5%p`** |
| **`thumb_ring` Leave-Out Recall** | `43.8%` (`39/89`) | `49.4%` (`44/89`) | **`53.9%` (`48/89`)** | **`+4.5%p`** | **`+10.1%p`** |
| **`thumb_middle` Leave-Out Recall** | `50.8%` (`64/126`) | `52.4%` (`66/126`) | **`52.4%` (`66/126`)** | `0.0%p` | **`+1.6%p`** |
| **`thumb_pinky` Leave-Out Recall** | `69.4%` (`59/85`) | `69.4%` (`59/85`) | **`72.9%` (`62/85`)** | **`+3.5%p`** | **`+3.5%p`** |

---

## 4. Remaining Risks & Transition to Stage 4·5

1. **Middle & Ring Pinch Ceiling under Standard Cross-Entropy (`52.4%` and `53.9%` leave-out recall)**:
   Even with boundary transition refinement (Stage 3) and anatomical phalanx scaling + perspective warp (Stage 6), standard cross-entropy decision boundaries remain vulnerable to posture shifts between `thumb_middle` and `thumb_ring`.
2. **No Explicit Out-of-Distribution (OOD) / Unregistered Gesture Veto Yet**:
   A user making an unregistered gesture (`v_sign`, `rock_sign`, `open_move`) can still produce a high softmax probability along an open half-space of the final linear layer (`w2`).
3. **Next Step (`Stage 4·5`)**:
   Train the `SpatialMlpModel` hidden representation (`64` vs `128`) with Supervised Contrastive Loss (`SupCon`) pulling same-gesture cross-posture/session samples together, compare prototype distance classification vs softmax, freeze the encoder for lightweight per-user prototype adaptation, evaluate specialist bank coexistence vs integration, and add class-wise shrinkage Mahalanobis / kNN OOD rejection.
