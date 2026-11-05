"""Consistent condition groups and valid temporal windows for training and export."""
from pathlib import Path

import numpy as np

from src.recorder import read_session
from src.recording_plan import label_condition_key
from src.tcn_features import FEATURES, frame_features, windows
from src.gesture_catalog import CLASSES, BY_KEY


def usable(frame):
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


def load_sessions(paths, window=15, *, include_windows=True, classes=None):
    classes = list(CLASSES if classes is None else classes)
    records = [(Path(p), *read_session(Path(p))[:2]) for p in paths]
    keys = sorted({label_condition_key(f.get("label") or {}) for _, _, frames in records for f in frames} - {"unassigned"})
    groups = {key: i for i, key in enumerate(keys)}
    sessions = []
    for path, meta, frames in records:
        if not meta.get("complete"):
            raise ValueError(f"Unfinished recording: {path.name}")
        n = len(frames)
        features = np.zeros((n, FEATURES), np.float32)
        x = np.zeros((n, window, FEATURES), np.float32) if include_windows else None
        y = np.full(n, -1, np.int64)
        segments = np.full(n, -1, np.int64)
        cond = np.array([groups.get(label_condition_key(f.get("label") or {}), -1) for f in frames], np.int64)
        start, previous = None, None

        def flush(end):
            if start is not None:
                part = frame_features(frames[start:end], float(meta.get("frame_aspect", 0.5625)))
                features[start:end] = part
                segments[start:end] = start
                if include_windows:
                    x[start:end] = windows(part, window)

        for i, f in enumerate(frames):
            lab = f.get("label") or {}
            valid = usable(f) and lab.get("target") not in ("idle", None)
            boundary = (cond[i], lab.get("set"))
            gap = i > 0 and not 0 < float(f.get("t", 0)) - float(frames[i - 1].get("t", 0)) <= 0.25
            if not valid or boundary != previous or gap:
                flush(i)
                start = None
            if valid and start is None:
                start = i
            if valid and lab.get("target") in classes:
                y[i] = classes.index(lab["target"])
            previous = boundary if valid else None
        flush(n)
        y[features[:, -1] == 0] = -1  # degenerate 3D hand frames are not labelled training examples
        # A swipe's waiting/returning frames must not teach a direction it did not move in.
        for i, f in enumerate(frames):
            task = BY_KEY.get((f.get("label") or {}).get("task"))
            if task and task.direction and (f.get("label") or {}).get("phase") == "do":
                vx, vy = features[i, 130:132]
                motion = {"left": -vx, "right": vx, "down": vy}[task.direction]
                if motion < .03:
                    y[i] = -1
        sessions.append({"path": str(path), "meta": meta, "frames": frames, "features": features,
                         "x": x, "y": y, "cond": cond, "condition_groups": keys,
                         "classes": classes, "segments": segments})
    return sessions
