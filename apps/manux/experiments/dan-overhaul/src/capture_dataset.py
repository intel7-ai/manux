"""Consistent condition groups and valid temporal windows for training and export."""
import hashlib
import os
import pickle
from pathlib import Path

import numpy as np

from src.recorder import finished_recordings, read_session
from src.recording_plan import label_condition_key
from src.tcn_features import FEATURE_VERSION, FEATURES, frame_features, windows
from src.gesture_catalog import CLASSES, BY_KEY

_SESSION_CACHE_VERSION = f"v1-{FEATURE_VERSION}"
_EXTRACT_MEM_CACHE: dict[tuple[str, int, int], dict] = {}


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


def discover_augmented_paths(paths, *, augmented_dir: Path | None = None) -> list[Path]:
    """Find finished offline-augmented recordings in ``augmented/`` matching the given base recordings."""
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
        for cand in sorted(aug_dir.glob(f"{stem}-aug*.jsonl.gz")):
            if cand.resolve() in usable_set and cand.resolve() not in seen:
                seen.add(cand.resolve())
                found.append(cand)
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
    n = len(frames)
    features = np.zeros((n, FEATURES), np.float32)
    segments = np.full(n, -1, np.int64)
    cond_keys = [label_condition_key(f.get("label") or {}) for f in frames]
    valid_targets: list[str | None] = [None] * n
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
        boundary = (cond_keys[i], lab.get("set"))
        gap = i > 0 and not 0 < float(f.get("t", 0)) - float(frames[i - 1].get("t", 0)) <= 0.25
        if not valid or boundary != previous or gap:
            flush(i)
            start = None
        if valid and start is None:
            start = i
        if valid and isinstance(target, str):
            valid_targets[i] = target
        previous = boundary if valid else None
    flush(n)

    for i in range(n):
        if features[i, -1] == 0:
            valid_targets[i] = None
    for i, f in enumerate(frames):
        if valid_targets[i] is None:
            continue
        lab = f.get("label") or {}
        task = BY_KEY.get(lab.get("task"))
        if task and task.direction and lab.get("phase") == "do":
            vx, vy = features[i, 130:132]
            motion = {"left": -vx, "right": vx, "down": vy}[task.direction]
            if motion < 0.03:
                valid_targets[i] = None

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
        "segments": segments,
        "seg_ranges": seg_ranges,
        "cond_keys": cond_keys,
        "valid_targets": valid_targets,
    }


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


def load_sessions(paths, window=15, *, include_windows=True, classes=None, use_cleaned=False, extra_records=None):
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
    sessions = []
    for r in extracted:
        features = r["features"]
        n = len(features)
        x = np.zeros((n, window, FEATURES), np.float32) if include_windows else None
        if include_windows:
            for seg_start, seg_end in r["seg_ranges"]:
                x[seg_start:seg_end] = windows(features[seg_start:seg_end], window)
        y = np.array(
            [class_to_idx.get(t, -1) if t is not None else -1 for t in r["valid_targets"]],
            dtype=np.int64,
        )
        cond = np.array([groups.get(k, -1) for k in r["cond_keys"]], dtype=np.int64)
        sessions.append({
            "path": r["path"],
            "meta": r["meta"],
            "frames": r["frames"],
            "features": features,
            "x": x,
            "y": y,
            "cond": cond,
            "condition_groups": keys,
            "classes": classes,
            "segments": r["segments"].copy(),
        })
    return sessions
