"""Consistent condition groups and valid temporal windows for training and export."""
from pathlib import Path

import numpy as np

from src.recorder import read_session
from src.recording_plan import label_condition_key
from src.tcn_features import temporal_inputs
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


def training_selection(tasks):
    """Tasks sharing one hand pose (click/long-press drag) must share a model route."""
    keys = list(dict.fromkeys(tasks))
    unknown = set(keys) - set(BY_KEY)
    if unknown:
        raise ValueError("알 수 없는 학습 동작: " + ", ".join(sorted(unknown)))
    labels = {BY_KEY[key].label for key in keys} - {"none"}
    if not labels:
        raise ValueError("학습할 동작을 하나 이상 선택하세요. 중립·휴식 구간은 자동으로 포함됩니다.")
    return ([key for key, task in BY_KEY.items() if task.label in labels],
            [name for name in CLASSES if name == "none" or name in labels])


def load_sessions(paths, window=15, *, include_windows=True, classes=None, selected_tasks=None):
    classes = list(CLASSES if classes is None else classes)
    if selected_tasks is not None:
        selected_tasks, classes = training_selection(selected_tasks)
    records = [(Path(p), *read_session(Path(p))[:2]) for p in paths]
    keys = sorted({label_condition_key(f.get("label") or {}) for _, _, frames in records for f in frames} - {"unassigned"})
    groups = {key: i for i, key in enumerate(keys)}
    sessions = []
    for path, meta, frames in records:
        if not meta.get("complete"):
            raise ValueError(f"Unfinished recording: {path.name}")
        n = len(frames)
        features, x, segments = temporal_inputs(frames, float(meta.get("frame_aspect", 0.5625)),
                                                window, include_windows=include_windows)
        y = np.full(n, -1, np.int64)
        cond = np.array([groups.get(label_condition_key(f.get("label") or {}), -1) for f in frames], np.int64)
        for i, f in enumerate(frames):
            lab = f.get("label") or {}
            target = lab.get("target")
            if target in classes:
                y[i] = classes.index(target)
            elif selected_tasks is not None and target in CLASSES:
                # Other labelled gestures are hard negatives for this specialist,
                # never extra output classes or changes to those gestures' models.
                y[i] = classes.index("none")
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
