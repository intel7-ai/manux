"""Consistent condition groups and valid temporal windows for training and export."""
import hashlib
import os
import pickle
from pathlib import Path

import numpy as np

from src.data_quality import compute_sequence_quality_scores
from src.finger_kinematics import reconstruct_hand_sequence
from src.recorder import finished_recordings, read_session
from src.recording_plan import label_condition_key
from src.spatial_features import SPATIAL_FEATURES, SPATIAL_FEATURE_VERSION, extract_spatial_features
from src.tcn_features import FEATURE_VERSION, FEATURES, frame_features, windows
from src.gesture_catalog import CLASSES, BY_KEY, NEAR_MISS_TIPS, direction_speed

_SESSION_CACHE_VERSION = f"v16-interference-context-guided-{FEATURE_VERSION}-{SPATIAL_FEATURE_VERSION}"
# Thumb-to-fingertip distance / hand size below which fingers count as touching. In the 2026-11 recordings a held
# pinch has p25 of 0.10~0.22 while non-gesture stretches rarely get under 0.18 (thumb_index p5), so this is real contact.
NEAR_MISS_CONTACT_RATIO = 0.10
_EXTRACT_MEM_CACHE: dict[tuple[str, int, int], dict] = {}
_PINCH_SF_COLS = {
    "thumb_index": 68,
    "thumb_middle": 69,
    "thumb_ring": 70,
    "thumb_pinky": 71,
}


def _frame_has_interfering_signal(
    frame: dict,
    tgt: str,
    s_vec: np.ndarray,
    d_target: float | None = None,
) -> bool:
    """Return True if competing finger proximity or raw detector interference co-occurs with `tgt`."""
    rule = frame.get("rule") or {}
    if rule.get("interfering_targets"):
        return True
    raw_g = str(rule.get("raw_gesture") or rule.get("gesture") or "")
    if raw_g not in ("", "none", tgt, f"{tgt}_pinch"):
        return True
    other_cols = [c for t_k, c in _PINCH_SF_COLS.items() if t_k != tgt]
    if not other_cols or not np.any(s_vec != 0.0):
        return False
    min_other = min(float(s_vec[c]) for c in other_cols)
    if d_target is not None:
        return min_other <= d_target + 0.08 or min_other <= 0.42
    return min_other <= 0.42


def refine_guided_transition_labels(
    frames: list[dict],
    valid_targets: list[str | None],
    spatial_features: np.ndarray,
    sample_weights: np.ndarray,
) -> dict[str, int]:
    """Refine guided `do` start/end transition frames while preserving interfering signals as ground-truth context.

    - Preserves original scheduled label in `frame['label']['raw_target']`.
    - Sets `frame['label']['refined_target']` to `'transition'` only at the immediate 1-2 frame start/end
      boundary of `do` when the hand has clearly not yet entered or has already exited the posture.
    - Never discards `do` frames that contain interfering finger signals; instead keeps `valid_targets[k] = tgt`
      and boosts `sample_weights[k]` so the model learns that in this posture/context, even with interfering
      signals present, the frame must be judged as the ground-truth scheduled target `tgt` (and as a hard
      negative `0` for competing gesture models).
    """
    n = len(frames)
    stats = {
        "raw_do_positive_frames": 0,
        "refined_do_positive_frames": 0,
        "relabeled_transition_frames": 0,
        "downweighted_ambiguous_frames": 0,
        "interference_context_boosted_frames": 0,
    }
    if n < 300:
        return stats

    idx = 0
    while idx < n:
        lab0 = frames[idx].get("label") or {}
        ph = lab0.get("phase")
        j = idx
        while j + 1 < n and (frames[j + 1].get("label") or {}).get("phase") == ph:
            j += 1

        if ph == "do":
            tgt = lab0.get("target")
            valid_k = [
                k
                for k in range(idx, j + 1)
                if valid_targets[k] == tgt
                and tgt not in (None, "none", "idle")
                and np.any(spatial_features[k] != 0.0)
            ]
            stats["raw_do_positive_frames"] += len(valid_k)
            if tgt in _PINCH_SF_COLS and len(valid_k) >= 6:
                col = _PINCH_SF_COLS[tgt]
                dists = np.array([float(spatial_features[k, col]) for k in valid_k], dtype=np.float64)
                d_min = float(np.min(dists))
                d_p25 = float(np.percentile(dists, 25))
                thr_contact = max(d_min * 2.10, d_p25 * 1.85, 0.72)
                best_set = set(np.argsort(dists)[: max(5, len(valid_k) // 2)])
                for pos_idx, k in enumerate(valid_k):
                    d_k = dists[pos_idx]
                    is_tail = (pos_idx < 2) or (pos_idx >= len(valid_k) - 2)
                    if pos_idx not in best_set and is_tail and d_k > thr_contact:
                        valid_targets[k] = None
                        sample_weights[k] = 0.0
                        stats["relabeled_transition_frames"] += 1
                        lbl_k = dict(frames[k].get("label") or {})
                        lbl_k.setdefault("raw_target", tgt)
                        lbl_k["refined_target"] = "transition"
                        frames[k] = {**frames[k], "label": lbl_k}
                        continue
                    if _frame_has_interfering_signal(frames[k], tgt, spatial_features[k], d_target=d_k):
                        sample_weights[k] = min(1.45, float(sample_weights[k]) * 1.25)
                        stats["interference_context_boosted_frames"] += 1
                    elif pos_idx in best_set:
                        sample_weights[k] = min(1.25, float(sample_weights[k]) * 1.15)
            elif len(valid_k) >= 6:
                vecs = spatial_features[valid_k]
                med_vec = np.median(vecs, axis=0)
                devs = np.linalg.norm(vecs - med_vec, axis=1)
                med_dev = max(float(np.median(devs)), 0.35)
                best_set = set(np.argsort(devs)[: max(6, len(valid_k) // 2)])
                for pos_idx, k in enumerate(valid_k):
                    is_tail = (pos_idx < 2) or (pos_idx >= len(valid_k) - 2)
                    if (
                        tgt in ("palm_side", "thumbs_up")
                        and pos_idx not in best_set
                        and is_tail
                        and float(devs[pos_idx]) > med_dev * 1.85
                    ):
                        valid_targets[k] = None
                        sample_weights[k] = 0.0
                        stats["relabeled_transition_frames"] += 1
                        lbl_k = dict(frames[k].get("label") or {})
                        lbl_k.setdefault("raw_target", tgt)
                        lbl_k["refined_target"] = "transition"
                        frames[k] = {**frames[k], "label": lbl_k}
                        continue
                    if _frame_has_interfering_signal(frames[k], str(tgt), spatial_features[k]):
                        sample_weights[k] = min(1.45, float(sample_weights[k]) * 1.25)
                        stats["interference_context_boosted_frames"] += 1
            stats["refined_do_positive_frames"] += sum(1 for k in valid_k if valid_targets[k] == tgt)
        idx = j + 1

    return stats


def is_pinch_captured_on_screen(frame: dict, target: str | None) -> bool:
    """Return True if the scheduled pinch gesture is visually captured on screen in 2D/3D."""
    if target not in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle"):
        return True
    lm = frame.get("lm")
    if lm is None or len(lm) < 21:
        return False
    try:
        pts = [(float(p[0]), float(p[1])) for p in lm[:21]]
    except (TypeError, ValueError, IndexError):
        return False

    def d2(i: int, j: int) -> float:
        dx = pts[i][0] - pts[j][0]
        dy = pts[i][1] - pts[j][1]
        return float((dx * dx + dy * dy) ** 0.5)

    hs = max(d2(0, 5), d2(0, 9), d2(0, 17), 1.4 * d2(5, 17))
    if target == "thumb_index":
        return min(d2(4, 8), d2(4, 7), d2(3, 8)) <= max(0.075, 0.42 * hs)
    if target == "thumb_middle":
        return min(d2(4, 12), d2(4, 11), d2(3, 12)) <= max(0.075, 0.42 * hs)
    if target == "thumb_ring":
        return min(d2(4, 16), d2(4, 15), d2(3, 16)) <= max(0.075, 0.42 * hs)
    if target == "thumb_pinky":
        return min(d2(4, 20), d2(4, 19), d2(3, 20)) <= max(0.078, 0.44 * hs)
    if target == "thumb_index_middle":
        di = min(d2(4, 8), d2(4, 7), d2(3, 8))
        dm = min(d2(4, 12), d2(4, 11), d2(3, 12))
        thr = max(0.080, 0.44 * hs)
        return di <= thr and dm <= thr
    return True


def resolve_cleaned_path(path: Path | str, *, use_cleaned: bool = True) -> Path:
    """Return the cleaned recording path in ``cleaned/`` if it exists and is complete, else the original path."""
    p = Path(path)
    if not use_cleaned:
        return p
    cleaned_dir = p.parent / "cleaned"
    candidate = cleaned_dir / p.name
    if candidate.exists():
        usable_files, _ = finished_recordings(cleaned_dir)
        if candidate in usable_files or candidate.resolve() in {u.resolve() for u in usable_files}:
            return candidate
    return p


def discover_augmented_paths(
    paths,
    *,
    augmented_dir: Path | None = None,
    max_per_recording: int | None = None,
) -> list[Path]:
    """Find finished offline-augmented recordings in ``augmented/`` matching the given base recordings."""
    if max_per_recording is not None and max_per_recording <= 0:
        return []
    found: list[Path] = []
    seen: set[Path] = set()
    for raw_p in paths:
        p = Path(raw_p)
        base_parent = p.parent.parent if p.parent.name == "cleaned" else p.parent
        aug_dir = Path(augmented_dir) if augmented_dir is not None else (base_parent / "augmented")
        if not aug_dir.exists():
            continue
        usable_aug, _ = finished_recordings(aug_dir)
        usable_set = {u.resolve() for u in usable_aug}
        stem = p.name[: -len(".jsonl.gz")] if p.name.endswith(".jsonl.gz") else p.stem
        per_rec = 0
        for cand in sorted(aug_dir.glob(f"{stem}-aug*.jsonl.gz")):
            if cand.resolve() in usable_set and cand.resolve() not in seen:
                seen.add(cand.resolve())
                found.append(cand)
                per_rec += 1
                if max_per_recording is not None and per_rec >= max_per_recording:
                    break
    return found



def usable(frame):
    lm = frame.get("lm")
    world = frame.get("world")
    if isinstance(lm, np.ndarray) and isinstance(world, np.ndarray):
        return lm.shape == (21, 3) and world.shape == (21, 3) and bool(np.isfinite(lm).all() and np.isfinite(world).all())
    if isinstance(lm, list) and isinstance(world, list):
        if len(lm) == 21 and len(world) == 21:
            try:
                for p in lm:
                    if len(p) != 3 or not (-1e9 < float(p[0]) < 1e9 and -1e9 < float(p[1]) < 1e9 and -1e9 < float(p[2]) < 1e9):
                        return False
                for p in world:
                    if len(p) != 3 or not (-1e9 < float(p[0]) < 1e9 and -1e9 < float(p[1]) < 1e9 and -1e9 < float(p[2]) < 1e9):
                        return False
                return True
            except (TypeError, ValueError):
                return False
        return False
    try:
        for field in ("lm", "world"):
            points = np.asarray(frame.get(field), dtype=float)
            if points.shape != (21, 3) or not np.isfinite(points).all():
                return False
        return True
    except (TypeError, ValueError):
        return False


def frame_durations(frames):
    """Observed time, never invented 30fps minutes or time spent between disconnected frames."""
    out = np.zeros(len(frames), dtype=float)
    for i in range(len(frames) - 1):
        dt = float(frames[i + 1].get("t", 0)) - float(frames[i].get("t", 0))
        if 0 < dt <= 0.25:
            out[i] = dt
    return out


def _extract_record(path: Path, meta: dict, frames: list[dict], *, slim_frames: bool = False) -> dict:
    if not meta.get("complete"):
        raise ValueError(f"Unfinished recording: {path.name}")
    if not meta.get("occlusion_calibrated") and len(frames) >= 3:
        frames, num_cal = reconstruct_hand_sequence(frames, in_place=False)
        meta = dict(meta)
        meta["occlusion_calibrated"] = True
        meta["occlusion_calibrated_frames"] = int(num_cal)
    n = len(frames)
    if n >= 600 and meta.get("mode") == "guided":
        from types import SimpleNamespace
        from src.capture_rules import CaptureRules
        from src.gesture_engine import GestureEngine

        frames = list(frames)
        eng = GestureEngine()
        eng.frame_aspect = float(meta.get("frame_aspect", 0.5625))
        cap_rules = CaptureRules(eng.config)
        prev_rep_tok = None
        for idx_f, f in enumerate(frames):
            t_f = float(f.get("t", 0.0))
            lab_f = f.get("label") or {}
            rep_tok = (lab_f.get("task"), lab_f.get("set"), lab_f.get("rep"), lab_f.get("phase") == "do")
            if rep_tok != prev_rep_tok:
                prev_rep_tok = rep_tok
                eng._desktop_armed = True
                eng._desktop_since = None
                eng._last_trigger_times.clear()
                eng._last_curled_at = -1e9
                eng._last_halffist_at = -1e9
                eng._index_released_at = -1e9
                eng._scroll_released_at = -1e9
                eng._pinky_fired_at = -1e9
                eng._desktop_fired_at = -1e9
                eng._side_released_at = -1e9
                eng._side_armed = {"index": True, "ring": True, "pinky": True}
                eng._click_fresh = {"middle": True, "ring": True, "pinky": True}
                eng._secondary_since.clear()
                eng._secondary_off.clear()
                eng._index_pinched = False
                eng._middle_hold_pinched = False
            lm_f = f.get("lm") if usable(f) else None
            if isinstance(lm_f, np.ndarray):
                lm_f = lm_f.tolist()
            res_f = eng.process(lm_f, t_f)
            hand_ns = SimpleNamespace(landmarks=lm_f) if lm_f is not None else None
            obs_targets = cap_rules.observe(t_f, hand_ns, res_f, lab_f)
            old_rule = f.get("rule")
            if isinstance(old_rule, dict) or lm_f is not None:
                merged_rule = dict(old_rule) if isinstance(old_rule, dict) else {"evaluated": True}
                task_f = BY_KEY.get(lab_f.get("task"))
                phase_f = lab_f.get("phase")
                if task_f is not None and not task_f.free and task_f.label != "none":
                    if phase_f == "do":
                        old_targets = [t for t in (merged_rule.get("targets") or []) if t == task_f.key]
                        merged_targets = list(dict.fromkeys(old_targets + [t for t in obs_targets if t == task_f.key]))
                        merged_rule["targets"] = merged_targets
                        if task_f.key in merged_targets:
                            merged_rule["action"] = task_f.action or task_f.label
                            merged_rule["gesture"] = (
                                f"{task_f.label}_pinch"
                                if task_f.label in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle")
                                else task_f.label
                            )
                        else:
                            merged_rule["action"] = "none"
                            merged_rule["gesture"] = "none"
                    else:
                        merged_rule["targets"] = []
                        merged_rule["action"] = "none"
                        merged_rule["gesture"] = "none"
                elif phase_f in ("prep", "prepare", "rest", "waiting", "paused"):
                    merged_rule["targets"] = []
                    merged_rule["action"] = "none"
                    merged_rule["gesture"] = "none"
                else:
                    old_targets = list(merged_rule.get("targets") or [])
                    merged_rule["targets"] = list(dict.fromkeys(old_targets + obs_targets))
                    if merged_rule.get("action") in (None, "none", "move_cursor") and res_f.action not in (None, "none"):
                        merged_rule["action"] = res_f.action
                        merged_rule["gesture"] = res_f.active_gesture
                merged_rule["evaluated"] = True
                frames[idx_f] = {**f, "rule": merged_rule}
    features = np.zeros((n, FEATURES), np.float32)
    spatial_features = np.zeros((n, SPATIAL_FEATURES), np.float32)
    segments = np.full(n, -1, np.int64)
    cond_keys = [label_condition_key(f.get("label") or {}) for f in frames]
    raw_targets: list[str | None] = [None] * n
    valid_targets: list[str | None] = [None] * n
    quality_scores, sample_weights, _ = compute_sequence_quality_scores(frames, loss_window=2)
    seg_ranges: list[tuple[int, int]] = []
    start, previous = None, None
    aspect = float(meta.get("frame_aspect", 0.5625))

    def flush(end: int) -> None:
        if start is not None:
            part = frame_features(frames[start:end], aspect)
            features[start:end] = part
            segments[start:end] = start
            seg_ranges.append((start, end))

    for i, f in enumerate(frames):
        lab = f.get("label") or {}
        target = lab.get("target")
        valid = usable(f) and target not in ("idle", None)
        if valid:
            lm_pts = f.get("lm")
            if lm_pts is None:
                lm_pts = f.get("world")
            s_vec = extract_spatial_features(lm_pts)
            if s_vec is not None:
                spatial_features[i] = s_vec
        boundary = (cond_keys[i], lab.get("set"))
        gap = i > 0 and not 0 < float(f.get("t", 0)) - float(frames[i - 1].get("t", 0)) <= 0.25
        if not valid or boundary != previous or gap:
            flush(i)
            start = None
        if valid and start is None:
            start = i
        if valid and isinstance(target, str):
            raw_targets[i] = target
            valid_targets[i] = target
            if isinstance(lab, dict) and "raw_target" not in lab:
                frames[i] = {**f, "label": {**lab, "raw_target": target}}
        previous = boundary if valid else None
    flush(n)

    for i in range(n):
        if features[i, -1] == 0:
            raw_targets[i] = None
            valid_targets[i] = None
            sample_weights[i] = 0.0
    # Stage 2: Downweight or exclude frames right around tracking loss with extreme joint velocity spikes
    if n >= 300:
        vel_norm = np.linalg.norm(features[:, 63:126], axis=1)
        has_hand = features[:, 132] > 0.5
        for i in range(n):
            if valid_targets[i] is None:
                continue
            near_loss = False
            for k in range(max(0, i - 2), min(n, i + 3)):
                if not has_hand[k]:
                    near_loss = True
                    break
            if near_loss:
                sample_weights[i] = min(float(sample_weights[i]), 0.20)
                if float(vel_norm[i]) > 0.65:
                    valid_targets[i] = None
                    sample_weights[i] = 0.0
            elif float(vel_norm[i]) > 1.10:
                sample_weights[i] = min(float(sample_weights[i]), 0.35)
    for i, f in enumerate(frames):
        if valid_targets[i] is None:
            continue
        lab = f.get("label") or {}
        task = BY_KEY.get(lab.get("task"))
        if task and task.direction and lab.get("phase") == "do":
            vx, vy = features[i, 130:132]
            motion = direction_speed(task.direction, vx, vy)
            if motion is not None and motion < 0.03:
                valid_targets[i] = None

    # For real-length human recordings, trim reaction latency at phase transitions
    # and filter 'rest' frames where the user was still holding or releasing the gesture.
    if n >= 300:
        idx = 0
        while idx < n:
            ph = (frames[idx].get("label") or {}).get("phase")
            j = idx
            while j + 1 < n and (frames[j + 1].get("label") or {}).get("phase") == ph:
                j += 1
            t_start = float(frames[idx].get("t", 0.0))
            t_end = float(frames[j].get("t", 0.0))
            dur = t_end - t_start
            prev_ph = (frames[idx - 1].get("label") or {}).get("phase") if idx > 0 else "waiting"
            if ph == "do" and dur >= 1.2 and (j - idx + 1) >= 25:
                lab0 = frames[idx].get("label") or {}
                task0 = BY_KEY.get(lab0.get("task"))
                if not (task0 and task0.direction):
                    if prev_ph in ("prep", "rest") or (prev_ph in ("waiting", "paused") and dur >= 3.0):
                        max_trim = idx + max(1, (j - idx + 1) // 4)
                        for k in range(idx, min(j + 1, max_trim)):
                            if float(frames[k].get("t", 0.0)) - t_start < 0.30:
                                valid_targets[k] = None
                            else:
                                break
            elif ph == "rest" and prev_ph == "do" and dur >= 0.8:
                for k in range(idx, j + 1):
                    if float(frames[k].get("t", 0.0)) - t_start < 0.45:
                        valid_targets[k] = None
                    else:
                        break
            idx = j + 1

        do_vecs = [
            spatial_features[i]
            for i in range(n)
            if valid_targets[i] not in (None, "none")
            and (frames[i].get("label") or {}).get("phase") == "do"
            and np.any(spatial_features[i] != 0.0)
        ]
        if len(do_vecs) >= 20:
            do_arr = np.asarray(do_vecs, dtype=np.float32)
            do_mean = do_arr.mean(axis=0)
            do_med = float(np.median(np.linalg.norm(do_arr - do_mean, axis=1)))
            cutoff = max(do_med * 1.35, 4.5)
            for i in range(n):
                if valid_targets[i] == "none" and (frames[i].get("label") or {}).get("phase") == "rest":
                    if np.any(spatial_features[i] != 0.0):
                        dist = float(np.linalg.norm(spatial_features[i] - do_mean))
                        if dist <= cutoff:
                            valid_targets[i] = None

    label_refinement_stats = refine_guided_transition_labels(
        frames,
        valid_targets,
        spatial_features,
        sample_weights,
    )
    near_miss_dropped = drop_near_miss_contact(frames, valid_targets, sample_weights, aspect)
    if near_miss_dropped:
        label_refinement_stats = {**(label_refinement_stats or {}), "near_miss_contact_frames": near_miss_dropped}

    stored_frames = (
        [{"t": f.get("t", 0.0), "label": f.get("label"), "rule": f.get("rule")} for f in frames]
        if slim_frames
        else frames
    )
    return {
        "path": str(path),
        "meta": meta,
        "frames": stored_frames,
        "features": features,
        "spatial_features": spatial_features,
        "segments": segments,
        "seg_ranges": seg_ranges,
        "cond_keys": cond_keys,
        "raw_targets": raw_targets,
        "valid_targets": valid_targets,
        "quality_scores": quality_scores,
        "sample_weights": sample_weights,
        "label_refinement_stats": label_refinement_stats,
    }


def drop_near_miss_contact(frames, valid_targets, sample_weights, aspect: float, window: int = 15) -> int:
    """Take frames out of training when a "don't touch" recording really touched: teaching a real pinch as "none" hurts.

    The windows ending within `window` frames after a touch still contain it, so they go too. Returns how many went.
    """
    drop_until = -1
    dropped = 0
    for i, f in enumerate(frames):
        tip = NEAR_MISS_TIPS.get((f.get("label") or {}).get("task"))
        if tip is None:
            drop_until = -1
            continue
        lm = f.get("lm")
        if lm is not None:
            pts = np.asarray(lm, dtype=np.float64)[:, :2] * (1.0, aspect)
            size = max(float(np.linalg.norm(pts[k] - pts[0])) for k in (5, 9, 13, 17))
            if size > 1e-6 and float(np.linalg.norm(pts[4] - pts[tip])) / size < NEAR_MISS_CONTACT_RATIO:
                drop_until = i + window - 1
        if i <= drop_until and valid_targets[i] is not None:
            valid_targets[i] = None
            sample_weights[i] = 0.0
            dropped += 1
    return dropped


def _load_or_extract_path(path: Path) -> dict:
    p = Path(path)
    try:
        st = p.stat()
        resolved = str(p.resolve())
        mem_key = (resolved, st.st_mtime_ns, st.st_size)
    except OSError:
        meta, frames, _ = read_session(p)
        return _extract_record(p, meta, frames, slim_frames=False)

    if mem_key in _EXTRACT_MEM_CACHE:
        return _EXTRACT_MEM_CACHE[mem_key]

    use_disk_cache = st.st_size >= 32 * 1024
    cache_file = None
    if use_disk_cache:
        base_dir = p.parent.parent if p.parent.name in ("cleaned", "augmented") else p.parent
        cache_dir = base_dir / ".cache"
        digest = hashlib.sha1(
            f"{_SESSION_CACHE_VERSION}|{resolved}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")
        ).hexdigest()[:16]
        cache_file = cache_dir / f"{p.name}.{digest}.pkl"
        if cache_file.exists():
            try:
                rec = pickle.loads(cache_file.read_bytes())
                rec["path"] = str(p)
                _EXTRACT_MEM_CACHE[mem_key] = rec
                return rec
            except Exception:
                pass

    meta, frames, _ = read_session(p)
    rec = _extract_record(p, meta, frames, slim_frames=use_disk_cache)
    _EXTRACT_MEM_CACHE[mem_key] = rec
    if cache_file is not None:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = cache_file.with_suffix(f".{os.getpid()}.tmp")
            tmp_file.write_bytes(pickle.dumps(rec, protocol=pickle.HIGHEST_PROTOCOL))
            os.replace(tmp_file, cache_file)
        except OSError:
            pass
    return rec


def load_sessions(paths, window=15, *, include_windows=True, classes=None, use_cleaned=False, extra_records=None,
                  keep_rest=False):
    classes = list(CLASSES if classes is None else classes)
    class_to_idx = {name: idx for idx, name in enumerate(classes)}
    resolved_paths = [resolve_cleaned_path(p, use_cleaned=use_cleaned) for p in paths]
    extracted = [_load_or_extract_path(Path(p)) for p in resolved_paths]
    if extra_records:
        for p, meta, frames in extra_records:
            cached = meta.get("_cached_extract") if isinstance(meta, dict) else None
            if cached is not None:
                extracted.append(cached)
            else:
                extracted.append(_extract_record(Path(p), meta, frames, slim_frames=False))
    keys = sorted({k for r in extracted for k in r["cond_keys"]} - {"unassigned"})
    groups = {key: i for i, key in enumerate(keys)}
    free_none_count = sum(
        1
        for r in extracted
        for f, t in zip(r["frames"], r["valid_targets"])
        if t == "none" and (f.get("label") or {}).get("phase") != "rest"
    )
    drop_all_rest = free_none_count >= 100
    sessions = []
    for r in extracted:
        features = r["features"]
        n = len(features)
        x = np.zeros((n, window, FEATURES), np.float32) if include_windows else None
        if include_windows:
            for seg_start, seg_end in r["seg_ranges"]:
                x[seg_start:seg_end] = windows(features[seg_start:seg_end], window)
        # Rest frames that the default leaves out once free stretches exist. keep_rest teaches them as "none"; the
        # mask stays so evaluation can keep ignoring them and two runs stay comparable.
        rest_dropped = np.array(
            [
                drop_all_rest and t == "none" and (f.get("label") or {}).get("phase") == "rest"
                for f, t in zip(r["frames"], r["valid_targets"])
            ],
            dtype=bool,
        )
        y = np.array(
            [
                -1 if t is None or (dropped and not keep_rest) else class_to_idx.get(t, -1)
                for t, dropped in zip(r["valid_targets"], rest_dropped)
            ],
            dtype=np.int64,
        )
        raw_targets_r = r.get("raw_targets") or r["valid_targets"]
        raw_y = np.array(
            [
                -1 if t is None else class_to_idx.get(t, -1)
                for t in raw_targets_r
            ],
            dtype=np.int64,
        )
        cond = np.array([groups.get(k, -1) for k in r["cond_keys"]], dtype=np.int64)
        sample_weights = r.get("sample_weights")
        if sample_weights is None or len(sample_weights) != n:
            sample_weights = np.ones(n, dtype=np.float32)
        else:
            sample_weights = np.asarray(sample_weights, dtype=np.float32).copy()
        quality_scores = r.get("quality_scores")
        if quality_scores is None or len(quality_scores) != n:
            quality_scores = np.ones(n, dtype=np.float32)
        else:
            quality_scores = np.asarray(quality_scores, dtype=np.float32).copy()
        sessions.append({
            "path": r["path"],
            "meta": r["meta"],
            "frames": r["frames"],
            "features": features,
            "spatial_features": r.get("spatial_features"),
            "x": x,
            "y": y,
            "raw_y": raw_y,
            "rest_dropped": rest_dropped,
            "cond": cond,
            "condition_groups": keys,
            "classes": classes,
            "segments": r["segments"].copy(),
            "sample_weights": sample_weights,
            "quality_scores": quality_scores,
            "label_refinement_stats": r.get("label_refinement_stats") or {},
        })
    return sessions
