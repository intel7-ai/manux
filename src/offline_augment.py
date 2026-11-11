"""Sequence-level offline data augmentation for hand-landmark recordings."""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np

from src.recorder import read_session

# Stage 6 Augmentation Ablation Registry & Measured Defaults:
# Only augmentations with verified leave-group-out improvements are enabled by default.
# - perspective_fov (camera tilt + weak-perspective FOV warp): ENABLED (+0.18%p cond, +0.76%p session leave-out)
# - z_noise (anisotropic depth noise 3.5x > xy): ENABLED (+0.56%p TCN+SpatialMLP recall, -13.8% misfires)
# - finger_length_ratio (+/-8~10% anatomical phalanx scaling): ENABLED (+0.62%p cond leave-out, +4.2%p thumb_middle)
# - temporal_speed_warp (0.8x~1.25x window speed resampling): ENABLED (+0.99%p TCN+SpatialMLP recall, -20.7% misfires)
# - hand_scale (0.7x~1.4x raw coordinate scaling): DISABLED (-1.56%p TCN+SpatialMLP recall; features are already palm-size normalized)
# - horizontal_flip (x-mirror excluding directional gestures): DISABLED (-0.97%p session leave-out, -1.70%p TCN+SpatialMLP recall on right-hand dataset)
STAGE6_AUGMENTATION_DEFAULTS: dict[str, bool] = {
    "perspective_fov": True,
    "z_noise": True,
    "finger_length_ratio": True,
    "temporal_speed_warp": True,
    "hand_scale": False,
    "horizontal_flip": False,
}


def recompute_spatial_96(pts: np.ndarray) -> np.ndarray:
    """Vectorized recomputation of 96D spatial features from `(N, 21, 3)` 3D landmarks."""
    n_samples = len(pts)
    if n_samples == 0:
        return np.zeros((0, 96), dtype=np.float32)
    p0 = pts[:, 0:1, :]
    rel = pts - p0
    p9 = rel[:, 9, :]
    palm = np.maximum(np.hypot(p9[:, 0], p9[:, 1]), 1e-5)

    def dist3d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        d = a - b
        return np.sqrt(d[:, 0] ** 2 + d[:, 1] ** 2 + (d[:, 2] * 0.45) ** 2)

    def dist2d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        d = a - b
        return np.hypot(d[:, 0], d[:, 1])

    def cos3d(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
        u = a - b
        v = c - b
        ux, uy, uz = u[:, 0], u[:, 1], u[:, 2] * 0.45
        vx, vy, vz = v[:, 0], v[:, 1], v[:, 2] * 0.45
        lu = np.sqrt(ux * ux + uy * uy + uz * uz)
        lv = np.sqrt(vx * vx + vy * vy + vz * vz)
        return (ux * vx + uy * vy + uz * vz) / np.maximum(lu * lv, 1e-8)

    palm3d = np.maximum.reduce([
        palm,
        dist3d(pts[:, 0], pts[:, 9]),
        dist3d(pts[:, 5], pts[:, 17]),
        np.full(n_samples, 1e-4),
    ])
    vx, vy, vz = p9[:, 0], p9[:, 1], p9[:, 2] * 0.45
    v_len = np.maximum(np.sqrt(vx * vx + vy * vy + vz * vz), 1e-5)
    vh_x, vh_y, vh_z = vx / v_len, vy / v_len, vz / v_len
    a5_17 = pts[:, 5] - pts[:, 17]
    ax, ay, az = a5_17[:, 0], a5_17[:, 1], a5_17[:, 2] * 0.45
    a_dot_v = ax * vh_x + ay * vh_y + az * vh_z
    ux, uy, uz = ax - a_dot_v * vh_x, ay - a_dot_v * vh_y, az - a_dot_v * vh_z
    u_len = np.maximum(np.sqrt(ux * ux + uy * uy + uz * uz), 1e-5)
    uh_x, uh_y, uh_z = ux / u_len, uy / u_len, uz / u_len

    def proj_v(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        d = a - b
        return (d[:, 0] * vh_x + d[:, 1] * vh_y + (d[:, 2] * 0.45) * vh_z) / palm3d

    def proj_u(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        d = a - b
        return (d[:, 0] * uh_x + d[:, 1] * uh_y + (d[:, 2] * 0.45) * uh_z) / palm3d

    out = np.zeros((n_samples, 96), dtype=np.float32)
    out[:, 0:63:3] = rel[:, :, 0] / palm[:, None]
    out[:, 1:63:3] = rel[:, :, 1] / palm[:, None]
    out[:, 2:63:3] = (rel[:, :, 2] * 0.45) / palm[:, None]
    for k, tip in enumerate((4, 8, 12, 16, 20)):
        out[:, 63 + k] = dist2d(pts[:, tip], pts[:, 0]) / palm
    out[:, 68] = dist3d(pts[:, 4], pts[:, 8]) / palm3d
    out[:, 69] = dist3d(pts[:, 4], pts[:, 12]) / palm3d
    out[:, 70] = dist3d(pts[:, 4], pts[:, 16]) / palm3d
    out[:, 71] = dist3d(pts[:, 4], pts[:, 20]) / palm3d
    out[:, 72] = dist2d(pts[:, 8], pts[:, 12]) / palm
    out[:, 73] = np.minimum(dist2d(pts[:, 4], pts[:, 13]), dist2d(pts[:, 4], pts[:, 17])) / palm
    out[:, 74] = dist2d(pts[:, 4], pts[:, 5]) / palm
    out[:, 75] = dist2d(pts[:, 20], pts[:, 17]) / palm
    out[:, 76] = cos3d(pts[:, 2], pts[:, 3], pts[:, 4])
    out[:, 77] = cos3d(pts[:, 1], pts[:, 2], pts[:, 4])
    out[:, 78] = cos3d(pts[:, 5], pts[:, 6], pts[:, 8])
    out[:, 79] = cos3d(pts[:, 9], pts[:, 10], pts[:, 12])
    out[:, 80] = cos3d(pts[:, 13], pts[:, 14], pts[:, 16])
    out[:, 81] = cos3d(pts[:, 17], pts[:, 18], pts[:, 20])
    out[:, 82] = dist3d(pts[:, 4], pts[:, 6]) / palm3d
    ring_span = np.maximum(0.20 * palm3d, dist3d(pts[:, 16], pts[:, 13]))
    out[:, 83] = np.minimum(4.0, dist3d(pts[:, 20], pts[:, 17]) / ring_span)
    out[:, 84] = proj_v(pts[:, 20], pts[:, 17])
    out[:, 85] = proj_v(pts[:, 8], pts[:, 5])
    out[:, 86] = proj_v(pts[:, 12], pts[:, 9])
    out[:, 87] = proj_u(pts[:, 4], pts[:, 2])
    idx_bone = np.maximum(dist3d(pts[:, 5], pts[:, 6]) + dist3d(pts[:, 6], pts[:, 7]) + dist3d(pts[:, 7], pts[:, 8]), 1e-4)
    mid_bone = np.maximum(dist3d(pts[:, 9], pts[:, 10]) + dist3d(pts[:, 10], pts[:, 11]) + dist3d(pts[:, 11], pts[:, 12]), 1e-4)
    rg_bone = np.maximum(dist3d(pts[:, 13], pts[:, 14]) + dist3d(pts[:, 14], pts[:, 15]) + dist3d(pts[:, 15], pts[:, 16]), 1e-4)
    pk_bone = np.maximum(dist3d(pts[:, 17], pts[:, 18]) + dist3d(pts[:, 18], pts[:, 19]) + dist3d(pts[:, 19], pts[:, 20]), 1e-4)
    out[:, 88] = dist3d(pts[:, 5], pts[:, 8]) / idx_bone
    out[:, 89] = dist3d(pts[:, 9], pts[:, 12]) / mid_bone
    out[:, 90] = dist3d(pts[:, 13], pts[:, 16]) / rg_bone
    out[:, 91] = dist3d(pts[:, 17], pts[:, 20]) / pk_bone
    out[:, 92] = (pts[:, 5, 1] - pts[:, 4, 1]) / palm
    out[:, 93] = dist2d(pts[:, 5], pts[:, 17]) / palm
    out[:, 94] = proj_v(pts[:, 4], pts[:, 0])
    out[:, 95] = proj_u(pts[:, 20], pts[:, 17])
    return out


def augment_spatial_features_96(
    x_arr: np.ndarray,
    *,
    seed: int = 42,
    prob: float = 0.45,
    perspective_fov: bool | None = None,
    z_noise: bool | None = None,
    hand_scale: bool | None = None,
    horizontal_flip: bool | None = None,
    finger_length_ratio: bool | None = None,
) -> np.ndarray:
    """Apply Stage 6 spatial augmentations to 96D feature vectors by transforming 3D landmarks and recomputing all 96 invariants."""
    use_persp = STAGE6_AUGMENTATION_DEFAULTS["perspective_fov"] if perspective_fov is None else bool(perspective_fov)
    use_z = False if z_noise is None else bool(z_noise)
    use_scale = STAGE6_AUGMENTATION_DEFAULTS["hand_scale"] if hand_scale is None else bool(hand_scale)
    use_flip = STAGE6_AUGMENTATION_DEFAULTS["horizontal_flip"] if horizontal_flip is None else bool(horizontal_flip)
    use_finger = STAGE6_AUGMENTATION_DEFAULTS["finger_length_ratio"] if finger_length_ratio is None else bool(finger_length_ratio)

    if not any((use_persp, use_z, use_scale, use_flip, use_finger)) or len(x_arr) == 0 or x_arr.shape[1] < 96:
        return x_arr

    rng = np.random.default_rng(seed)
    n_samples = len(x_arr)
    out = np.array(x_arr, dtype=np.float64, copy=True)
    mask = rng.random(n_samples) < float(prob)
    if not np.any(mask):
        return out

    m_count = int(np.sum(mask))
    pts = np.zeros((m_count, 21, 3), dtype=np.float64)
    pts[:, :, 0] = out[mask, 0:63:3]
    pts[:, :, 1] = out[mask, 1:63:3]
    pts[:, :, 2] = out[mask, 2:63:3] / 0.45

    if use_persp:
        rx = rng.uniform(-0.18, 0.18, size=(m_count, 1))
        ry = rng.uniform(-0.18, 0.18, size=(m_count, 1))
        cx, sx = np.cos(rx), np.sin(rx)
        cy, sy = np.cos(ry), np.sin(ry)
        x0, y0, z0 = pts[:, :, 0], pts[:, :, 1], pts[:, :, 2]
        y1 = y0 * cx - z0 * sx
        z1 = y0 * sx + z0 * cx
        x2 = x0 * cy + z1 * sy
        z2 = -x0 * sy + z1 * cy
        kp = rng.uniform(-0.14, 0.14, size=(m_count, 1))
        denom = np.clip(1.0 + kp * z2, 0.78, 1.28)
        pts[:, :, 0] = x2 / denom
        pts[:, :, 1] = y1 / denom
        pts[:, :, 2] = z2

    if use_z:
        noise = np.zeros_like(pts)
        noise[:, 1:, :2] = rng.normal(0.0, 0.008, size=(m_count, 20, 2))
        noise[:, 1:, 2] = rng.normal(0.0, 0.030, size=(m_count, 20))
        pts = pts + noise

    if use_scale:
        s_xy = rng.uniform(0.70, 1.40, size=(m_count, 1))
        s_z = s_xy * rng.uniform(0.88, 1.12, size=(m_count, 1))
        pts[:, :, 0] *= s_xy
        pts[:, :, 1] *= s_xy
        pts[:, :, 2] *= s_z

    if use_flip:
        pts[:, :, 0] = -pts[:, :, 0]

    if use_finger:
        old_tips = pts[:, [4, 8, 12, 16, 20], :].copy()
        for chain in ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)):
            ratio = rng.uniform(0.92, 1.08, size=(m_count, 1, 1))
            base = pts[:, chain[0] : chain[0] + 1, :].copy()
            for j in chain[1:]:
                pts[:, j : j + 1, :] = base + (pts[:, j : j + 1, :] - base) * ratio
        for f_i, tip_idx in enumerate((8, 12, 16, 20)):
            d_old = np.linalg.norm(old_tips[:, 0, :] - old_tips[:, f_i + 1, :], axis=1)
            close = d_old < 0.35
            if np.any(close):
                mid_new = 0.5 * (pts[close, 4, :] + pts[close, tip_idx, :])
                offset_old = old_tips[close, f_i + 1, :] - old_tips[close, 0, :]
                pts[close, 4, :] = mid_new - 0.5 * offset_old
                pts[close, tip_idx, :] = mid_new + 0.5 * offset_old

    out[mask] = recompute_spatial_96(pts).astype(np.float64)
    return out


def rotation_matrix_3d(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
    """Create a 3x3 3D rotation matrix from Euler angles in degrees."""
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    rot_x = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]], dtype=np.float64)
    rot_y = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]], dtype=np.float64)
    rot_z = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    return rot_z @ rot_y @ rot_x


def _valid_points(raw: Any) -> np.ndarray | None:
    if raw is None:
        return None
    if isinstance(raw, np.ndarray):
        if raw.shape == (21, 3):
            return raw
        return None
    try:
        arr = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if arr.shape != (21, 3) or not np.isfinite(arr).all():
        return None
    return arr


def _segment_bounds(frames: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Split frame sequence into contiguous segments sharing phase, target, and set."""
    if not frames:
        return []
    bounds: list[tuple[int, int]] = []
    start = 0

    def _key(idx: int) -> tuple[Any, Any, Any, bool]:
        f = frames[idx]
        lab = f.get("label") or {}
        has_lm = _valid_points(f.get("lm")) is not None
        return (lab.get("phase"), lab.get("target"), lab.get("set"), has_lm)

    prev_k = _key(0)
    for i in range(1, len(frames)):
        dt = float(frames[i].get("t", 0.0)) - float(frames[i - 1].get("t", 0.0))
        cur_k = _key(i)
        if cur_k != prev_k or not (0.0 < dt <= 0.25):
            bounds.append((start, i - 1))
            start = i
            prev_k = cur_k
    bounds.append((start, len(frames) - 1))
    return bounds


def _copy_frame(f: dict[str, Any]) -> dict[str, Any]:
    out = dict(f)
    if isinstance(out.get("label"), dict):
        out["label"] = dict(out["label"])
    return out


def _resample_segment(
    seg_frames: list[dict[str, Any]],
    stretch_factor: float,
) -> list[dict[str, Any]]:
    """Stretch or compress a contiguous valid segment along the time axis by resampling landmarks."""
    m = len(seg_frames)
    if m < 4 or abs(stretch_factor - 1.0) < 1e-3:
        return [_copy_frame(f) for f in seg_frames]

    lms = [_valid_points(f.get("lm")) for f in seg_frames]
    if any(x is None for x in lms):
        return [_copy_frame(f) for f in seg_frames]

    worlds = [_valid_points(f.get("world")) for f in seg_frames]
    has_all_worlds = all(w is not None for w in worlds)

    lab0 = seg_frames[0].get("label") or {}
    min_len = 5 if lab0.get("phase") == "do" else 3
    new_m = max(min_len, int(round(m * stretch_factor)))
    if new_m == m:
        return [_copy_frame(f) for f in seg_frames]

    lm_stack = np.stack(lms, axis=0)  # (m, 21, 3)
    w_stack = np.stack(worlds, axis=0) if has_all_worlds else (lm_stack - lm_stack[:, 0:1, :])

    src_pos = np.linspace(0.0, float(m - 1), num=new_m)
    idx_low = np.floor(src_pos).astype(int)
    idx_high = np.minimum(idx_low + 1, m - 1)
    alpha = (src_pos - idx_low)[:, None, None]

    interp_lm = np.round((1.0 - alpha) * lm_stack[idx_low] + alpha * lm_stack[idx_high], 5)
    interp_w = np.round((1.0 - alpha) * w_stack[idx_low] + alpha * w_stack[idx_high], 5)

    t_start = float(seg_frames[0].get("t", 0.0))
    t_end = float(seg_frames[-1].get("t", 0.0))
    avg_dt = (t_end - t_start) / max(1, m - 1)
    if not (0.005 <= avg_dt <= 0.15):
        avg_dt = 1.0 / 30.0

    out: list[dict[str, Any]] = []
    for k in range(new_m):
        nearest_idx = int(round(src_pos[k]))
        base = _copy_frame(seg_frames[nearest_idx])
        base["t"] = round(t_start + k * avg_dt, 4)
        base["lm"] = interp_lm[k]
        base["world"] = interp_w[k]
        out.append(base)
    return out


def augment_frames_sequence(
    meta: dict[str, Any],
    frames: list[dict[str, Any]],
    *,
    rotation_deg: float = 40.0,
    scale_range: float = 0.15,
    jitter_std: float = 0.003,
    time_stretch_range: tuple[float, float] = (0.85, 1.15),
    frame_drop_prob: float = 0.06,
    rng: np.random.Generator | None = None,
    aug_index: int = 1,
    as_lists: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply sequence-level offline augmentation while preserving gesture labels and temporal validity.

    Transformations applied per sequence/segment:
    - 3D rotation (±rotation_deg, default ±40° within 30°~45°) around hand origin
    - Scale (±scale_range, default ±15%)
    - Small Gaussian joint jitter (sensor noise simulation)
    - Time-axis stretching / compression (resampling & timeline retiming)
    - Random frame drop (bounded to preserve valid dt <= 0.25s and minimum 'do' length)
    """
    if rng is None:
        rng = np.random.default_rng()

    if not frames:
        aug_meta = dict(meta)
        aug_meta["augmented"] = True
        aug_meta["aug_index"] = aug_index
        return aug_meta, []

    bounds = _segment_bounds(frames)
    resampled_segments: list[list[dict[str, Any]]] = []

    # 1. Time-axis stretching/compression per segment & random frame drop
    for s_idx, e_idx in bounds:
        seg = frames[s_idx : e_idx + 1]
        stretch = float(rng.uniform(time_stretch_range[0], time_stretch_range[1]))
        seg_aug = _resample_segment(seg, stretch)

        # Random frame drop (preserve first/last frame of segment and min length)
        lab0 = seg_aug[0].get("label") or {}
        min_keep = 5 if lab0.get("phase") == "do" else 2
        if frame_drop_prob > 0.0 and len(seg_aug) > min_keep + 1:
            kept: list[dict[str, Any]] = [seg_aug[0]]
            dropped_prev = False
            for k in range(1, len(seg_aug) - 1):
                remaining_possible = len(kept) + (len(seg_aug) - k)
                if (
                    remaining_possible > min_keep
                    and not dropped_prev
                    and float(rng.random()) < frame_drop_prob
                ):
                    dropped_prev = True
                    continue
                kept.append(seg_aug[k])
                dropped_prev = False
            kept.append(seg_aug[-1])
            seg_aug = kept

        resampled_segments.append(seg_aug)

    # Flatten and rebuild monotonic timeline so inter-segment gaps stay consistent with original
    aug_frames: list[dict[str, Any]] = []
    cur_t = float(frames[0].get("t", 0.0))
    for seg_i, seg_aug in enumerate(resampled_segments):
        if seg_i > 0:
            orig_prev_end = bounds[seg_i - 1][1]
            orig_cur_start = bounds[seg_i][0]
            orig_gap = float(frames[orig_cur_start].get("t", 0.0)) - float(frames[orig_prev_end].get("t", 0.0))
            step = orig_gap if 0.0 < orig_gap <= 1.0 else (1.0 / 30.0)
            cur_t += max(0.01, step)

        for k, f in enumerate(seg_aug):
            if k > 0:
                dt = float(seg_aug[k].get("t", 0.0)) - float(seg_aug[k - 1].get("t", 0.0))
                if not (0.005 <= dt <= 0.20):
                    dt = 1.0 / 30.0
                cur_t += dt
            f["t"] = round(cur_t, 4)
            aug_frames.append(f)

    # 2. Sequence-level 3D rotation, scale, and per-frame Gaussian joint jitter
    rx = float(rng.uniform(-rotation_deg, rotation_deg))
    ry = float(rng.uniform(-rotation_deg, rotation_deg))
    rz = float(rng.uniform(-rotation_deg, rotation_deg))
    rot_3d = rotation_matrix_3d(rx, ry, rz)

    # Mild trajectory in-plane rotation so directional swipes keep valid motion direction
    traj_rz = float(rng.uniform(-min(18.0, rotation_deg * 0.45), min(18.0, rotation_deg * 0.45)))
    rot_traj = rotation_matrix_3d(0.0, 0.0, traj_rz)

    scale = float(rng.uniform(1.0 - scale_range, 1.0 + scale_range))

    # Pre-extract valid landmarks once
    valid_indices: list[int] = []
    lms_list: list[np.ndarray] = []
    worlds_list: list[np.ndarray] = []
    is_swipe_list: list[bool] = []
    for idx, f in enumerate(aug_frames):
        lm = _valid_points(f.get("lm"))
        if lm is None:
            continue
        w = _valid_points(f.get("world"))
        if w is None:
            w = lm - lm[0:1, :]
        valid_indices.append(idx)
        lms_list.append(lm)
        worlds_list.append(w)
        lab = f.get("label") or {}
        is_swipe_list.append("swipe" in str(lab.get("task") or ""))

    total_f = len(aug_frames)
    drift_deg = min(8.0, rotation_deg * 0.2)
    drift_rx = float(rng.uniform(-drift_deg, drift_deg))
    drift_ry = float(rng.uniform(-drift_deg, drift_deg))
    drift_rz = float(rng.uniform(-drift_deg, drift_deg))

    if valid_indices:
        v_count = len(valid_indices)
        lm_arr = np.stack(lms_list, axis=0)      # (v, 21, 3)
        w_arr = np.stack(worlds_list, axis=0)    # (v, 21, 3)
        wrist_center = np.mean(lm_arr[:, 0, :], axis=0)  # (3,)

        v_idx_arr = np.asarray(valid_indices, dtype=np.float64)
        phase_frac = (v_idx_arr / max(1, total_f - 1)) - 0.5
        ang_x = np.radians(rx + drift_rx * phase_frac)
        ang_y = np.radians(ry + drift_ry * phase_frac)
        ang_z = np.radians(rz + drift_rz * phase_frac)

        cx, sx = np.cos(ang_x), np.sin(ang_x)
        cy, sy = np.cos(ang_y), np.sin(ang_y)
        cz, sz = np.cos(ang_z), np.sin(ang_z)
        ones = np.ones(v_count, dtype=np.float64)
        zeros = np.zeros(v_count, dtype=np.float64)

        rot_x = np.stack([ones, zeros, zeros, zeros, cx, -sx, zeros, sx, cx], axis=1).reshape(v_count, 3, 3)
        rot_y = np.stack([cy, zeros, sy, zeros, ones, zeros, -sy, zeros, cy], axis=1).reshape(v_count, 3, 3)
        rot_z = np.stack([cz, -sz, zeros, sz, cz, zeros, zeros, zeros, ones], axis=1).reshape(v_count, 3, 3)
        rot_frames = rot_z @ rot_y @ rot_x  # (v, 3, 3)
        rot_frames_T = np.swapaxes(rot_frames, 1, 2)

        wrists = lm_arr[:, 0:1, :]  # (v, 1, 3)
        rel_lm = lm_arr - wrists
        rot_rel_lm = (rel_lm @ rot_frames_T) * scale

        jitter_lm = rng.normal(0.0, jitter_std, size=lm_arr.shape)
        jitter_lm[:, 0, :] *= 0.25

        centered_wrists = wrists - wrist_center[None, None, :]
        new_wrists = wrist_center[None, None, :] + (centered_wrists @ rot_traj.T) * scale
        if any(is_swipe_list):
            swipe_mask = np.asarray(is_swipe_list, dtype=bool)
            new_wrists[swipe_mask] = (
                wrist_center[None, None, :] + centered_wrists[swipe_mask] * np.clip(scale, 0.9, 1.1)
            )

        new_lms = new_wrists + rot_rel_lm + jitter_lm
        new_lms[:, :, :2] = np.clip(new_lms[:, :, :2], -0.25, 1.25)

        w_origins = w_arr[:, 0:1, :]
        rel_w = w_arr - w_origins
        jitter_w = rng.normal(0.0, jitter_std * 0.5, size=w_arr.shape)
        jitter_w[:, 0, :] = 0.0
        new_worlds = w_origins + (rel_w @ rot_frames_T) * scale + jitter_w

        new_lms = np.round(new_lms, 5)
        new_worlds = np.round(new_worlds, 5)
        if as_lists:
            new_lms_seq = new_lms.tolist()
            new_worlds_seq = new_worlds.tolist()
            for k, frame_idx in enumerate(valid_indices):
                aug_frames[frame_idx]["lm"] = new_lms_seq[k]
                aug_frames[frame_idx]["world"] = new_worlds_seq[k]
        else:
            for k, frame_idx in enumerate(valid_indices):
                aug_frames[frame_idx]["lm"] = new_lms[k]
                aug_frames[frame_idx]["world"] = new_worlds[k]

    aug_meta = dict(meta)
    aug_meta["type"] = "meta"
    aug_meta.pop("complete", None)
    aug_meta.pop("end", None)
    aug_meta["augmented"] = True
    aug_meta["aug_index"] = int(aug_index)
    aug_meta["aug_params"] = {
        "rotation_deg": [round(rx, 2), round(ry, 2), round(rz, 2)],
        "scale": round(scale, 4),
        "jitter_std": jitter_std,
        "frame_drop_prob": frame_drop_prob,
    }
    return aug_meta, aug_frames


def _recompute_counts(frames: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in frames:
        if _valid_points(f.get("lm")) is None:
            counts["no_hand"] = counts.get("no_hand", 0) + 1
            continue
        lab = f.get("label")
        if isinstance(lab, dict):
            name = lab.get("target") if lab.get("target") is not None else lab.get("label")
        else:
            name = lab
        key = str(name or "unlabelled")
        counts[key] = counts.get(key, 0) + 1
    return counts


def augment_recording_file(
    path: Path | str,
    *,
    factor: int = 4,
    rotation_deg: float = 40.0,
    scale_range: float = 0.15,
    jitter_std: float = 0.003,
    time_stretch_range: tuple[float, float] = (0.85, 1.15),
    frame_drop_prob: float = 0.06,
    output_dir: Path | str | None = None,
    seed: int | None = None,
) -> list[Path]:
    """Generate ``factor`` offline-augmented copies of a recording file in a separate folder.

    Never overwrites the original file. By default writes to ``recordings/augmented/``.
    """
    src_path = Path(path)
    n_copies = max(1, int(factor))
    meta, frames, events = read_session(src_path)

    if output_dir is None:
        if src_path.parent.name == "cleaned":
            dest_dir = src_path.parent.parent / "augmented"
        else:
            dest_dir = src_path.parent / "augmented"
    else:
        dest_dir = Path(output_dir)
        if dest_dir.resolve() == src_path.parent.resolve():
            dest_dir = src_path.parent / "augmented"
    dest_dir.mkdir(parents=True, exist_ok=True)

    stem = src_path.name
    if stem.endswith(".jsonl.gz"):
        base_name = stem[: -len(".jsonl.gz")]
    elif stem.endswith(".gz"):
        base_name = stem[: -len(".gz")]
    else:
        base_name = src_path.stem

    rng = np.random.default_rng(seed)
    written_paths: list[Path] = []

    for idx in range(1, n_copies + 1):
        aug_meta, aug_frames = augment_frames_sequence(
            meta,
            frames,
            rotation_deg=rotation_deg,
            scale_range=scale_range,
            jitter_std=jitter_std,
            time_stretch_range=time_stretch_range,
            frame_drop_prob=frame_drop_prob,
            rng=rng,
            aug_index=idx,
        )
        aug_meta["augmented_from"] = src_path.name
        last_t = float(aug_frames[-1].get("t", 0.0)) if aug_frames else 0.0
        end_record = {
            "type": "end",
            "t": round(last_t, 4),
            "frames": len(aug_frames),
            "counts": _recompute_counts(aug_frames),
            "marks": len(events),
        }
        out_path = dest_dir / f"{base_name}-aug{idx:02d}.jsonl.gz"
        part_path = out_path.with_name(out_path.name + ".part")
        with gzip.open(part_path, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps(aug_meta, ensure_ascii=False, separators=(",", ":")) + "\n")
            for f in aug_frames:
                fh.write(json.dumps(f, ensure_ascii=False, separators=(",", ":")) + "\n")
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.write(json.dumps(end_record, ensure_ascii=False, separators=(",", ":")) + "\n")
        part_path.replace(out_path)
        written_paths.append(out_path)

    return written_paths


def generate_augmented_records(
    paths: list[Path | str],
    *,
    factor: int = 4,
    rotation_deg: float = 40.0,
    scale_range: float = 0.15,
    jitter_std: float = 0.003,
    time_stretch_range: tuple[float, float] = (0.85, 1.15),
    frame_drop_prob: float = 0.06,
    seed: int = 42,
) -> list[tuple[Path, dict[str, Any], list[dict[str, Any]]]]:
    """Generate in-memory `(virtual_path, aug_meta, aug_frames)` records for training without disk I/O."""
    from src.capture_dataset import _SESSION_CACHE_VERSION, _extract_record

    n_copies = max(1, int(factor))
    rng = np.random.default_rng(seed)
    records: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]] = []
    for file_idx, p in enumerate(paths):
        src_path = Path(p)
        use_disk_cache = False
        cache_file = None
        try:
            st = src_path.stat()
            if st.st_size >= 32 * 1024:
                use_disk_cache = True
                base_dir = src_path.parent.parent if src_path.parent.name in ("cleaned", "augmented") else src_path.parent
                cache_dir = base_dir / ".cache"
                digest = hashlib.sha1(
                    (
                        f"{_SESSION_CACHE_VERSION}|{src_path.resolve()}|{st.st_mtime_ns}|{st.st_size}"
                        f"|{n_copies}|{rotation_deg}|{scale_range}|{jitter_std}|{time_stretch_range}|{frame_drop_prob}|{seed}"
                    ).encode("utf-8")
                ).hexdigest()[:16]
                cache_file = cache_dir / f"{src_path.name}.memaug.{digest}.pkl"
        except OSError:
            pass

        if use_disk_cache and cache_file is not None and cache_file.exists():
            try:
                cached_list = pickle.loads(cache_file.read_bytes())
                for rec in cached_list:
                    meta_with_cache = dict(rec["meta"])
                    meta_with_cache["_cached_extract"] = rec
                    records.append((Path(rec["path"]), meta_with_cache, rec["frames"]))
                continue
            except Exception:
                pass

        file_rng = np.random.default_rng(seed + file_idx * 1009) if use_disk_cache else rng
        meta, frames, _ = read_session(src_path)
        if not meta.get("complete"):
            continue
        for f in frames:
            lm_arr = _valid_points(f.get("lm"))
            if lm_arr is not None:
                f["lm"] = lm_arr
            w_arr = _valid_points(f.get("world"))
            if w_arr is not None:
                f["world"] = w_arr
        cached_to_save = []
        for idx in range(1, n_copies + 1):
            aug_meta, aug_frames = augment_frames_sequence(
                meta,
                frames,
                rotation_deg=rotation_deg,
                scale_range=scale_range,
                jitter_std=jitter_std,
                time_stretch_range=time_stretch_range,
                frame_drop_prob=frame_drop_prob,
                rng=file_rng,
                aug_index=idx,
                as_lists=not use_disk_cache,
            )
            aug_meta["complete"] = True
            aug_meta["augmented_from"] = src_path.name
            vpath = src_path.with_name(f"{src_path.stem}-memaug{idx:02d}.jsonl.gz")
            if use_disk_cache:
                extracted = _extract_record(vpath, aug_meta, aug_frames, slim_frames=True)
                cached_to_save.append(extracted)
                meta_with_cache = dict(aug_meta)
                meta_with_cache["_cached_extract"] = extracted
                records.append((vpath, meta_with_cache, extracted["frames"]))
            else:
                records.append((vpath, aug_meta, aug_frames))
        if use_disk_cache and cache_file is not None and cached_to_save:
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                tmp_file = cache_file.with_suffix(f".{os.getpid()}.tmp")
                tmp_file.write_bytes(pickle.dumps(cached_to_save, protocol=pickle.HIGHEST_PROTOCOL))
                os.replace(tmp_file, cache_file)
            except OSError:
                pass
    return records
