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
