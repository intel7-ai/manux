"""5-Finger anatomical kinematics, self-occlusion detection, and temporal pinch-context calibration.

Humans always have 5 fingers with consistent relative bone lengths and joint constraints.
When the hand tilts or turns sideways toward the camera, self-occlusion causes MediaPipe to:
1. Foreshorten or collapse hidden distal finger bones (PIP->DIP->TIP),
2. Jitter occluded fingertips across consecutive frames, and
3. Falsely separate the thumb tip (#4) and pinched fingertip (#8/#12/#16/#20) in the middle of a held pinch.

This module provides:
- ``reconstruct_hand_sequence``: Fast vectorized bidirectional (past + future context) 5-finger
  anatomical and pinch-continuity calibration for recorded sequences before AI training/evaluation.
- ``CausalFingerTracker``: Real-time (past-context only, 0-lag) 5-finger kinematic stabilizer for
  live camera inference.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# 5 finger kinematic chains from wrist (0): (wrist, mcp/cmc, pip/mcp, dip/ip, tip)
FINGER_CHAINS: Tuple[Tuple[int, int, int, int, int], ...] = (
    (0, 1, 2, 3, 4),     # Thumb
    (0, 5, 6, 7, 8),     # Index
    (0, 9, 10, 11, 12),  # Middle
    (0, 13, 14, 15, 16), # Ring
    (0, 17, 18, 19, 20), # Pinky
)

_PARENTS = np.array(
    [
        [0, 1, 2, 3],
        [0, 5, 6, 7],
        [0, 9, 10, 11],
        [0, 13, 14, 15],
        [0, 17, 18, 19],
    ],
    dtype=np.int64,
)
_CHILDREN = np.array(
    [
        [1, 2, 3, 4],
        [5, 6, 7, 8],
        [9, 10, 11, 12],
        [13, 14, 15, 16],
        [17, 18, 19, 20],
    ],
    dtype=np.int64,
)

PINCH_TARGET_TO_FINGER: Dict[str, Tuple[int, int]] = {
    "thumb_index": (1, 8),
    "thumb_middle": (2, 12),
    "thumb_ring": (3, 16),
    "thumb_pinky": (4, 20),
}

# Anatomical fallback bone lengths relative to palm size (|p9 - p0|) for each finger's 4 segments:
DEFAULT_BONE_RATIOS = np.array(
    [
        [0.36, 0.32, 0.25, 0.20],  # Thumb (0->1->2->3->4)
        [1.02, 0.42, 0.26, 0.19],  # Index (0->5->6->7->8)
        [1.00, 0.46, 0.29, 0.20],  # Middle (0->9->10->11->12)
        [0.95, 0.43, 0.27, 0.19],  # Ring (0->13->14->15->16)
        [0.88, 0.34, 0.21, 0.17],  # Pinky (0->17->18->19->20)
    ],
    dtype=np.float64,
)


def _as_pts(raw: Any) -> Optional[np.ndarray]:
    if raw is None:
        return None
    if isinstance(raw, np.ndarray):
        if raw.shape == (21, 3) and np.isfinite(raw).all():
            return raw.astype(np.float64, copy=True)
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 21:
        p0 = raw[0]
        if hasattr(p0, "x") and hasattr(p0, "y"):
            try:
                arr = np.array([[float(p.x), float(p.y), float(getattr(p, "z", 0.0))] for p in raw], dtype=np.float64)
                if arr.shape == (21, 3) and np.isfinite(arr).all():
                    return arr
            except (TypeError, ValueError, AttributeError):
                return None
            return None
        try:
            arr = np.asarray(raw, dtype=np.float64)
            if arr.shape == (21, 3) and np.isfinite(arr).all():
                return arr
        except (TypeError, ValueError):
            return None
        return None
    try:
        arr = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if arr.shape != (21, 3) or not np.isfinite(arr).all():
        return None
    return arr


def _palm_frame(pts: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray, float]]:
    """Single-frame intrinsic palm basis (origin p0, 3x3 basis [x, y, z], palm_scale)."""
    origin = pts[0].copy()
    w = pts - origin
    y_vec = w[9]
    scale = float(np.linalg.norm(y_vec))
    if scale < 1e-5:
        return None
    y_axis = y_vec / scale
    x_vec = w[5] - w[17]
    x_vec = x_vec - float(np.dot(x_vec, y_axis)) * y_axis
    nx = float(np.linalg.norm(x_vec))
    if nx < 1e-5:
        return None
    x_axis = x_vec / nx
    z_axis = np.cross(x_axis, y_axis)
    nz = float(np.linalg.norm(z_axis))
    if nz < 1e-5:
        return None
    z_axis = z_axis / nz
    basis = np.stack([x_axis, y_axis, z_axis], axis=1)
    return origin, basis, scale


def _palm_frame_batch(pts_batch: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Vectorized palm basis for (m, 21, 3) landmarks.

    Returns (origins (m,1,3), bases (m,3,3), scales (m,1,1), local_pts (m,21,3)) or None if any frame is degenerate.
    """
    origins = pts_batch[:, 0:1, :]
    w = pts_batch - origins
    y_vec = w[:, 9, :]
    scales = np.linalg.norm(y_vec, axis=1)
    if np.any(scales < 1e-5):
        return None
    y_axis = y_vec / scales[:, None]
    x_vec = w[:, 5, :] - w[:, 17, :]
    x_vec = x_vec - np.sum(x_vec * y_axis, axis=1, keepdims=True) * y_axis
    nx = np.linalg.norm(x_vec, axis=1)
    if np.any(nx < 1e-5):
        return None
    x_axis = x_vec / nx[:, None]
    z_axis = np.cross(x_axis, y_axis)
    nz = np.linalg.norm(z_axis, axis=1)
    if np.any(nz < 1e-5):
        return None
    z_axis = z_axis / nz[:, None]
    bases = np.stack([x_axis, y_axis, z_axis], axis=2)  # (m, 3, 3)
    scales_3d = scales[:, None, None]
    local_pts = (w @ bases) / scales_3d
    return origins, bases, scales_3d, local_pts


def _to_local(pts: np.ndarray, origin: np.ndarray, basis: np.ndarray, scale: float) -> np.ndarray:
    return ((pts - origin) @ basis) / scale


def _from_local(local: np.ndarray, origin: np.ndarray, basis: np.ndarray, scale: float) -> np.ndarray:
    return origin + (local * scale) @ basis.T


def _compute_bone_lengths(local_pts: np.ndarray) -> np.ndarray:
    """Compute (5, 4) normalized bone lengths in palm-local coordinates for one frame."""
    diffs = local_pts[_CHILDREN] - local_pts[_PARENTS]
    return np.linalg.norm(diffs, axis=-1)


def _compute_bone_lengths_batch(local_batch: np.ndarray) -> np.ndarray:
    """Compute (m, 5, 4) normalized bone lengths in palm-local coordinates."""
    diffs = local_batch[:, _CHILDREN, :] - local_batch[:, _PARENTS, :]
    return np.linalg.norm(diffs, axis=-1)


def _enforce_finger_bone_lengths(
    local_pts: np.ndarray,
    f_idx: int,
    target_lengths: np.ndarray,
    *,
    start_seg: int = 1,
    blend: float = 0.85,
) -> None:
    """In-place forward kinematic bone-length projection along finger `f_idx` starting from `start_seg`."""
    chain = FINGER_CHAINS[f_idx]
    dirs = []
    for s_idx in range(start_seg, 4):
        a, b = chain[s_idx], chain[s_idx + 1]
        vec = local_pts[b] - local_pts[a]
        norm = float(np.linalg.norm(vec))
        dirs.append((s_idx, vec, norm))
    for s_idx, vec, norm in dirs:
        a, b = chain[s_idx], chain[s_idx + 1]
        ref_len = float(target_lengths[f_idx, s_idx])
        if norm > 1e-5 and ref_len > 1e-4:
            new_len = (1.0 - blend) * norm + blend * ref_len
            local_pts[b] = local_pts[a] + (vec / norm) * new_len


def _pull_chain_to_tip(
    local_pts: np.ndarray,
    f_idx: int,
    desired_tip: np.ndarray,
    target_lengths: np.ndarray,
) -> None:
    """1-pass FABRIK-style inverse kinematics for a single finger chain anchored at MCP (chain[1])."""
    chain = FINGER_CHAINS[f_idx]
    anchor = local_pts[chain[1]].copy()
    local_pts[chain[4]] = desired_tip
    for s_idx in (3, 2):
        child = chain[s_idx + 1]
        parent = chain[s_idx]
        vec = local_pts[parent] - local_pts[child]
        norm = float(np.linalg.norm(vec))
        ref_len = float(target_lengths[f_idx, s_idx])
        if norm > 1e-5:
            local_pts[parent] = local_pts[child] + (vec / norm) * ref_len
    local_pts[chain[1]] = anchor
    for s_idx in (1, 2, 3):
        parent = chain[s_idx]
        child = chain[s_idx + 1]
        vec = local_pts[child] - local_pts[parent]
        norm = float(np.linalg.norm(vec))
        ref_len = float(target_lengths[f_idx, s_idx])
        if norm > 1e-5:
            local_pts[child] = local_pts[parent] + (vec / norm) * ref_len


def _reconstruct_contiguous_run(
    frames: List[Dict[str, Any]],
    indices: List[int],
    ref_bones_lm: np.ndarray,
    ref_bones_w: np.ndarray,
    *,
    max_occlusion_gap: int = 12,
) -> int:
    """Bidirectionally reconstruct occluded fingers and stabilize pinch continuity on a contiguous run."""
    m = len(indices)
    if m < 3:
        return 0

    try:
        lm_batch = np.asarray([frames[idx]["lm"] for idx in indices], dtype=np.float64)
        if lm_batch.shape != (m, 21, 3) or not np.isfinite(lm_batch).all():
            return 0
    except (TypeError, ValueError, KeyError):
        return 0

    pf_batch = _palm_frame_batch(lm_batch)
    if pf_batch is None:
        return 0
    origins_lm, bases_lm, scales_lm, loc = pf_batch
    bones = _compute_bone_lengths_batch(loc)  # (m, 5, 4)

    # Local reference bone lengths within this contiguous run (adapts to segment camera aspect/angle)
    seg_ref_bones = np.median(bones, axis=0)
    seg_ref_bones = np.where(
        (seg_ref_bones >= 0.65 * ref_bones_lm) & (seg_ref_bones <= 1.45 * ref_bones_lm),
        0.60 * seg_ref_bones + 0.40 * ref_bones_lm,
        ref_bones_lm,
    )
    max_ref_bones = np.maximum(seg_ref_bones, DEFAULT_BONE_RATIOS)

    # 1. Detect per-frame, per-finger severe self-occlusion / anatomical collapse
    ratios_low = bones[:, :, 1:] / np.maximum(seg_ref_bones[None, :, 1:], 1e-4)
    ratios_high = bones[:, :, 1:] / np.maximum(max_ref_bones[None, :, 1:], 1e-4)
    bone_bad = np.any((ratios_low < 0.58) | (ratios_high > 1.48), axis=2)  # (m, 5)

    # Detect local fingertip impulse spikes (vectorized 1-frame & 2-frame neighbor checks)
    spike_bad = np.zeros((m, 5), dtype=bool)
    for f_idx, chain in enumerate(FINGER_CHAINS):
        mcp, pip, dip, tip = chain[1], chain[2], chain[3], chain[4]
        if m >= 3:
            mid_tip = 0.5 * (loc[:-2, tip] + loc[2:, tip])
            dev_tip = np.linalg.norm(loc[1:-1, tip] - mid_tip, axis=1)
            span_tip = np.linalg.norm(loc[2:, tip] - loc[:-2, tip], axis=1)
            mid_dip = 0.5 * (loc[:-2, dip] + loc[2:, dip])
            dev_dip = np.linalg.norm(loc[1:-1, dip] - mid_dip, axis=1)
            spike_1 = ((dev_tip > 0.20) & (dev_tip > 1.35 * span_tip)) | ((dev_dip > 0.16) & (dev_dip > 1.35 * span_tip))
            spike_bad[1:-1, f_idx] |= spike_1
        if m >= 5:
            # 2-frame occlusion spike where both k and k+1 jumped while k-1 and k+2 stayed put
            mid2_tip = 0.5 * (loc[:-3, tip] + loc[3:, tip])
            span2_tip = np.linalg.norm(loc[3:, tip] - loc[:-3, tip], axis=1)
            dev2_a = np.linalg.norm(loc[1:-2, tip] - mid2_tip, axis=1)
            dev2_b = np.linalg.norm(loc[2:-1, tip] - mid2_tip, axis=1)
            pip_vec = loc[:, pip] - loc[:, mcp]
            pip_shift_a = np.linalg.norm(pip_vec[1:-2] - 0.5 * (pip_vec[:-3] + pip_vec[3:]), axis=1)
            pip_shift_b = np.linalg.norm(pip_vec[2:-1] - 0.5 * (pip_vec[:-3] + pip_vec[3:]), axis=1)
            spike_2a = (dev2_a > 0.20) & (dev2_a > 1.40 * span2_tip) & (pip_shift_a <= 0.08)
            spike_2b = (dev2_b > 0.20) & (dev2_b > 1.40 * span2_tip) & (pip_shift_b <= 0.08)
            spike_bad[1:-2, f_idx] |= spike_2a
            spike_bad[2:-1, f_idx] |= spike_2b

    occluded = bone_bad | spike_bad  # (m, 5)
    # Never mark thumb or a pinching finger as bone-collapsed/spiked on frames where they are already in 3D+2D pinch contact
    thumb_xy = lm_batch[:, 4, :2]
    thumb_ip_xy = lm_batch[:, 3, :2]
    for f_idx in range(1, 5):
        tip_i = FINGER_CHAINS[f_idx][4]
        dip_i = FINGER_CHAINS[f_idx][3]
        d_tip = np.linalg.norm(thumb_xy - lm_batch[:, tip_i, :2], axis=1)
        d_dip = np.linalg.norm(thumb_xy - lm_batch[:, dip_i, :2], axis=1)
        d_ip = np.linalg.norm(thumb_ip_xy - lm_batch[:, tip_i, :2], axis=1)
        d_loc_3d = np.minimum(
            np.linalg.norm(loc[:, 4] - loc[:, tip_i], axis=1),
            np.linalg.norm(loc[:, 4] - loc[:, dip_i], axis=1),
        )
        in_valid_pinch = (d_loc_3d <= 0.28) & (np.minimum(np.minimum(d_tip, d_dip), d_ip) <= 0.058)
        occluded[in_valid_pinch, 0] = False
        occluded[in_valid_pinch, f_idx] = False
    modified_frames = np.zeros(m, dtype=bool)

    # 2. Bidirectional context interpolation in palm-local space for occluded fingers
    if np.any(occluded):
        for f_idx, chain in enumerate(FINGER_CHAINS):
            if not np.any(occluded[:, f_idx]):
                continue
            k = 0
            while k < m:
                if not occluded[k, f_idx]:
                    k += 1
                    continue
                end_k = k
                while end_k + 1 < m and occluded[end_k + 1, f_idx]:
                    end_k += 1
                gap = end_k - k + 1
                left = k - 1
                right = end_k + 1
                if gap <= max_occlusion_gap and (left >= 0 or right < m):
                    mcp = chain[1]
                    for cur in range(k, end_k + 1):
                        if left >= 0 and right < m:
                            alpha = (cur - left) / float(right - left)
                            for j_idx in chain[2:]:
                                off_l = loc[left, j_idx] - loc[left, mcp]
                                off_r = loc[right, j_idx] - loc[right, mcp]
                                interp_pos = loc[cur, mcp] + (1.0 - alpha) * off_l + alpha * off_r
                                loc[cur, j_idx] = 0.20 * loc[cur, j_idx] + 0.80 * interp_pos
                        elif left >= 0:
                            for j_idx in chain[2:]:
                                off_l = loc[left, j_idx] - loc[left, mcp]
                                loc[cur, j_idx] = 0.30 * loc[cur, j_idx] + 0.70 * (loc[cur, mcp] + off_l)
                        elif right < m:
                            for j_idx in chain[2:]:
                                off_r = loc[right, j_idx] - loc[right, mcp]
                                loc[cur, j_idx] = 0.30 * loc[cur, j_idx] + 0.70 * (loc[cur, mcp] + off_r)
                        _enforce_finger_bone_lengths(loc[cur], f_idx, seg_ref_bones, start_seg=1, blend=0.88)
                        modified_frames[cur] = True
                k = end_k + 1

    # 3. Pinch-Context Continuity Calibration
    lab0 = frames[indices[0]].get("label") or {}
    phase = lab0.get("phase")
    target = str(lab0.get("target") or "")
    pinch_candidates = (
        [PINCH_TARGET_TO_FINGER[target]]
        if target in PINCH_TARGET_TO_FINGER
        else list(PINCH_TARGET_TO_FINGER.values())
    )

    for f_idx, tip_idx in pinch_candidates:
        chain = FINGER_CHAINS[f_idx]
        mcp_idx, pip_idx = chain[1], chain[2]
        pinch_dists = np.linalg.norm(loc[:, 4] - loc[:, tip_idx], axis=1)
        is_explicit_do = (phase == "do" and target in PINCH_TARGET_TO_FINGER and PINCH_TARGET_TO_FINGER[target][1] == tip_idx)
        contact_thresh = 0.30 if is_explicit_do else 0.22
        in_contact = pinch_dists <= contact_thresh
        if not np.any(in_contact):
            continue

        ref_contact_dist = float(np.clip(np.percentile(pinch_dists[in_contact], 35), 0.06, 0.22))
        break_thresh = max(contact_thresh, ref_contact_dist * 1.35)
        broken_indices = np.flatnonzero(pinch_dists > break_thresh)
        if len(broken_indices) == 0:
            continue

        pip_vecs = loc[:, pip_idx] - loc[:, mcp_idx]
        ref_pip_vec = np.median(pip_vecs[in_contact], axis=0)

        for k in broken_indices:
            win_l = max(0, int(k) - 10)
            win_r = min(m, int(k) + 11)
            prev_contact = bool(np.any(in_contact[win_l:k])) if k > win_l else False
            next_contact = bool(np.any(in_contact[k + 1:win_r])) if k + 1 < win_r else False
            if not (prev_contact or next_contact):
                continue

            pip_vec_shift = float(np.linalg.norm(pip_vecs[k] - ref_pip_vec))
            knuckle_still_curled = pip_vec_shift <= 0.10
            finger_was_occluded = bool(occluded[k, 0] or occluded[k, f_idx])

            if knuckle_still_curled and finger_was_occluded and (
                (prev_contact and next_contact)
                or (is_explicit_do and (prev_contact or next_contact))
            ):
                t_tip = loc[k, 4]
                f_tip = loc[k, tip_idx]
                diff = f_tip - t_tip
                cur_dist = float(np.linalg.norm(diff))
                if cur_dist > 1e-5:
                    w_thumb = 0.65 if occluded[k, 0] and not occluded[k, f_idx] else (
                        0.35 if occluded[k, f_idx] and not occluded[k, 0] else 0.50
                    )
                    midpoint = (1.0 - w_thumb) * t_tip + w_thumb * f_tip
                    unit = diff / cur_dist
                    desired_dist = min(cur_dist, ref_contact_dist * 1.10)
                    _pull_chain_to_tip(loc[k], 0, midpoint - unit * (desired_dist * w_thumb), seg_ref_bones)
                    _pull_chain_to_tip(loc[k], f_idx, midpoint + unit * (desired_dist * (1.0 - w_thumb)), seg_ref_bones)
                    modified_frames[k] = True

    if not np.any(modified_frames):
        return 0

    # 4. Write only modified frames back to `lm` and `world` (vectorized)
    mod_ks = np.flatnonzero(modified_frames)
    new_lm_mod = np.round(
        origins_lm[mod_ks] + (loc[mod_ks] * scales_lm[mod_ks]) @ np.swapaxes(bases_lm[mod_ks], 1, 2),
        5,
    )
    for m_i, k in enumerate(mod_ks):
        idx = indices[int(k)]
        frames[idx]["lm"] = new_lm_mod[m_i].tolist()
        w_pts = _as_pts(frames[idx].get("world"))
        if w_pts is not None:
            wpf = _palm_frame(w_pts)
            if wpf is not None:
                new_w = _from_local(loc[k], *wpf)
                frames[idx]["world"] = np.round(new_w, 5).tolist()

    return int(len(mod_ks))


def reconstruct_hand_sequence(
    frames: Sequence[Dict[str, Any]],
    *,
    in_place: bool = False,
    max_occlusion_gap: int = 12,
) -> Tuple[List[Dict[str, Any]], int]:
    """Calibrate a sequence of recorded frames using 5-finger anatomical constraints and temporal context."""
    out_frames: List[Dict[str, Any]] = (
        list(frames)
        if in_place
        else [dict(f, label=dict(f["label"]) if isinstance(f.get("label"), dict) else f.get("label")) for f in frames]
    )
    n = len(out_frames)
    if n < 3:
        return out_frames, 0

    # Sample up to 256 frames across the recording to estimate baseline anatomical bone ratios quickly
    step = max(1, n // 256)
    sample_lms = []
    sample_ws = []
    for i in range(0, n, step):
        lm_pts = _as_pts(out_frames[i].get("lm"))
        if lm_pts is not None:
            sample_lms.append(lm_pts)
        w_pts = _as_pts(out_frames[i].get("world"))
        if w_pts is not None:
            sample_ws.append(w_pts)

    ref_bones_lm = DEFAULT_BONE_RATIOS.copy()
    if sample_lms:
        pf_s = _palm_frame_batch(np.stack(sample_lms, axis=0))
        if pf_s is not None:
            b_s = _compute_bone_lengths_batch(pf_s[3])
            med_b = np.median(b_s, axis=0)
            ref_bones_lm = np.clip(med_b, 0.45 * DEFAULT_BONE_RATIOS, 1.65 * DEFAULT_BONE_RATIOS)

    ref_bones_w = ref_bones_lm.copy()
    if sample_ws:
        pf_w = _palm_frame_batch(np.stack(sample_ws, axis=0))
        if pf_w is not None:
            b_w = _compute_bone_lengths_batch(pf_w[3])
            med_w = np.median(b_w, axis=0)
            ref_bones_w = np.clip(med_w, 0.45 * DEFAULT_BONE_RATIOS, 1.65 * DEFAULT_BONE_RATIOS)

    total_calibrated = 0
    run: List[int] = []

    def _run_key(idx: int) -> Tuple[Any, Any, Any]:
        lab = out_frames[idx].get("label") or {}
        return (lab.get("phase"), lab.get("target"), lab.get("set"))

    for i in range(n):
        f = out_frames[i]
        raw_lm = f.get("lm")
        has_valid_lm = isinstance(raw_lm, (list, np.ndarray)) and len(raw_lm) == 21
        if not has_valid_lm:
            if len(run) >= 3:
                total_calibrated += _reconstruct_contiguous_run(
                    out_frames, run, ref_bones_lm, ref_bones_w, max_occlusion_gap=max_occlusion_gap
                )
            run = []
            continue

        if run:
            prev_i = run[-1]
            dt = float(out_frames[i].get("t", 0.0)) - float(out_frames[prev_i].get("t", 0.0))
            if _run_key(i) != _run_key(prev_i) or not (0.0 <= dt <= 0.25):
                if len(run) >= 3:
                    total_calibrated += _reconstruct_contiguous_run(
                        out_frames, run, ref_bones_lm, ref_bones_w, max_occlusion_gap=max_occlusion_gap
                    )
                run = []
        run.append(i)

    if len(run) >= 3:
        total_calibrated += _reconstruct_contiguous_run(
            out_frames, run, ref_bones_lm, ref_bones_w, max_occlusion_gap=max_occlusion_gap
        )

    return out_frames, total_calibrated


class CausalFingerTracker:
    """Real-time 0-lag 5-finger kinematic tracker and pinch-context stabilizer for live camera frames."""

    def __init__(self, max_hold_s: float = 0.25) -> None:
        self.max_hold_s = float(max_hold_s)
        self._ref_bones_lm = DEFAULT_BONE_RATIOS.copy()
        self._ref_bones_w = DEFAULT_BONE_RATIOS.copy()
        self._prev_local_lm: Optional[np.ndarray] = None
        self._prev_t: Optional[float] = None
        self._active_pinch_finger: Optional[int] = None
        self._active_pinch_dist: float = 0.14
        self._active_pinch_until: float = 0.0
        self._occ_run_counts = np.zeros(5, dtype=np.int32)
        self.occluded_fingers_last_frame: int = 0

    def reset(self) -> None:
        self._prev_local_lm = None
        self._prev_t = None
        self._active_pinch_finger = None
        self._active_pinch_until = 0.0
        self._occ_run_counts.fill(0)
        self.occluded_fingers_last_frame = 0

    def step(
        self,
        now: float,
        lm_raw: Any,
        world_raw: Any,
    ) -> Tuple[Optional[List[List[float]]], Optional[List[List[float]]]]:
        lm_pts = _as_pts(lm_raw)
        w_pts = _as_pts(world_raw)
        self.occluded_fingers_last_frame = 0
        if lm_pts is None:
            self.reset()
            return lm_raw, world_raw

        pf = _palm_frame(lm_pts)
        if pf is None:
            return lm_raw, world_raw

        loc_lm = _to_local(lm_pts, *pf)
        raw_loc_lm = loc_lm.copy()
        bones_lm = _compute_bone_lengths(loc_lm)
        dt = (now - self._prev_t) if (self._prev_t is not None and now > self._prev_t) else 999.0

        modified = False
        occluded_mask = np.zeros(5, dtype=bool)

        if self._prev_local_lm is None or dt > self.max_hold_s:
            self._ref_bones_lm = np.clip(bones_lm, 0.35 * DEFAULT_BONE_RATIOS, 1.65 * DEFAULT_BONE_RATIOS)
            self._occ_run_counts.fill(0)
        elif self._prev_local_lm is not None and dt <= self.max_hold_s:
            for f_idx, chain in enumerate(FINGER_CHAINS):
                ratios = bones_lm[f_idx, 1:] / np.maximum(self._ref_bones_lm[f_idx, 1:], 1e-4)
                is_collapsed = bool(np.any((ratios < 0.32) | (ratios > 2.10)))

                tip = chain[4]
                mcp = chain[1]
                tip_jump = float(np.linalg.norm(loc_lm[tip] - self._prev_local_lm[tip]))
                mcp_jump = float(np.linalg.norm(loc_lm[mcp] - self._prev_local_lm[mcp]))
                is_spike = tip_jump > 0.42 and mcp_jump < 0.08

                if (is_collapsed or is_spike) and self._occ_run_counts[f_idx] < 3:
                    self._occ_run_counts[f_idx] += 1
                    occluded_mask[f_idx] = True
                    self.occluded_fingers_last_frame += 1
                    decay = max(0.25, 0.65 - 0.18 * (self._occ_run_counts[f_idx] - 1))
                    for j_idx in chain[2:]:
                        prev_off = self._prev_local_lm[j_idx] - self._prev_local_lm[mcp]
                        pred = loc_lm[mcp] + prev_off
                        loc_lm[j_idx] = (1.0 - decay) * loc_lm[j_idx] + decay * pred
                    _enforce_finger_bone_lengths(loc_lm, f_idx, self._ref_bones_lm, start_seg=1, blend=0.70)
                    modified = True
                else:
                    self._occ_run_counts[f_idx] = 0
                    self._ref_bones_lm[f_idx] = np.clip(
                        0.88 * self._ref_bones_lm[f_idx] + 0.12 * bones_lm[f_idx],
                        0.30 * DEFAULT_BONE_RATIOS[f_idx],
                        1.80 * DEFAULT_BONE_RATIOS[f_idx],
                    )

            if (
                self._active_pinch_finger is not None
                and now <= self._active_pinch_until
                and (occluded_mask[0] or occluded_mask[self._active_pinch_finger])
            ):
                f_idx = self._active_pinch_finger
                chain = FINGER_CHAINS[f_idx]
                tip_idx = chain[4]
                mcp_idx, pip_idx = chain[1], chain[2]
                prev_pip_vec = self._prev_local_lm[pip_idx] - self._prev_local_lm[mcp_idx]
                cur_pip_vec = loc_lm[pip_idx] - loc_lm[mcp_idx]
                if float(np.linalg.norm(cur_pip_vec - prev_pip_vec)) <= 0.12:
                    t_tip = loc_lm[4]
                    f_tip = loc_lm[tip_idx]
                    diff = f_tip - t_tip
                    cur_dist = float(np.linalg.norm(diff))
                    if cur_dist > self._active_pinch_dist * 1.35 and cur_dist > 1e-5:
                        midpoint = 0.5 * (t_tip + f_tip)
                        unit = diff / cur_dist
                        target_d = self._active_pinch_dist * 1.08
                        _pull_chain_to_tip(loc_lm, 0, midpoint - 0.5 * target_d * unit, self._ref_bones_lm)
                        _pull_chain_to_tip(loc_lm, f_idx, midpoint + 0.5 * target_d * unit, self._ref_bones_lm)
                        modified = True

        best_f = None
        best_d = 999.0
        for f_idx, tip_idx in PINCH_TARGET_TO_FINGER.values():
            d = float(np.linalg.norm(loc_lm[4] - loc_lm[tip_idx]))
            if d < best_d:
                best_d = d
                best_f = f_idx
        if best_f is not None and best_d <= 0.20 and not occluded_mask[0] and not occluded_mask[best_f]:
            self._active_pinch_finger = best_f
            self._active_pinch_dist = 0.7 * self._active_pinch_dist + 0.3 * best_d
            self._active_pinch_until = now + self.max_hold_s

        # Blend with raw local coordinates so _prev_local_lm never freezes indefinitely
        self._prev_local_lm = 0.50 * loc_lm + 0.50 * raw_loc_lm if modified else raw_loc_lm
        self._prev_t = now

        if not modified:
            return lm_raw, world_raw

        new_lm = _from_local(loc_lm, *pf)
        out_lm = new_lm.tolist()
        out_w = world_raw
        if w_pts is not None:
            wpf = _palm_frame(w_pts)
            if wpf is not None:
                w_loc = loc_lm.copy()
                for f_idx in range(5):
                    _enforce_finger_bone_lengths(w_loc, f_idx, self._ref_bones_w, start_seg=1, blend=0.80)
                out_w = _from_local(w_loc, *wpf).tolist()
        return out_lm, out_w
