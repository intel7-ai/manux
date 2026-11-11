"""Guided recordings -> samples. Labels come from the recording's schedule, so they are `guided_scheduled`, not user-confirmed:
the first training data of the encoder and the first replay memory, kept apart from what a person checked."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from src.gesture_catalog import BY_KEY, NEAR_MISS_TIPS
from src.online.samples import GUIDED_SCHEDULED, SampleStore
from src.pinch_report import read_recording
from src.tcn_features import FEATURE_VERSION

CONTEXT_FRAMES = 15        # frames before a repetition: the first windows need a past
FREE_SECONDS = 3.0         # free phases (movement, "almost touching") are cut into pieces this long
MIN_HAND_SHARE = .5
NEAR_CONTACT = .10         # thumb-finger distance / hand size that counts as touching (drop_near_miss_contact)


def _touches(frames: Sequence[Dict[str, Any]], tip: int, aspect: float) -> bool:
    for f in frames:
        lm = f.get("lm")
        if not lm:
            continue
        p = np.asarray(lm, float)[:, :2] * (1.0, aspect)
        size = np.linalg.norm(p[9] - p[0])
        if size > 0 and np.linalg.norm(p[4] - p[tip]) / size < NEAR_CONTACT:
            return True
    return False


def _pieces(frames: List[Dict[str, Any]]) -> Dict[tuple, List[int]]:
    """(kind, set, rep or start second) -> frame indexes. `do` repetitions and pieces of free phases."""
    out: Dict[tuple, List[int]] = {}
    for i, f in enumerate(frames):
        lab = f.get("label") or {}
        task = BY_KEY.get(lab.get("task"))
        if task is None:
            continue
        if not task.free and lab.get("phase") == "do":
            out.setdefault(("do", lab.get("task"), lab.get("set"), lab.get("rep")), []).append(i)
        elif task.free and lab.get("phase") == "free":
            out.setdefault(("free", lab.get("task"), lab.get("set"), int(float(f["t"]) // FREE_SECONDS)), []).append(i)
    return out


def import_recording(store: SampleStore, path: Path | str) -> Dict[str, int]:
    """Adds the repetitions of one recording; samples already stored are left alone (their ids come from the recording)."""
    path = Path(path)
    meta, frames = read_recording(path)
    frames = [f for f in frames if "t" in f]
    session = path.name.split(".")[0]
    aspect = float(meta.get("frame_aspect") or 720 / 1280)
    camera = str((meta.get("camera") or {}).get("note") or "")
    counts = {"added": 0, "skipped_duplicate": 0, "skipped_hand": 0, "skipped_contact": 0}
    for key, indexes in _pieces(frames).items():
        kind, task_key, set_id, rep = key
        task = BY_KEY[task_key]
        part = frames[max(0, indexes[0] - CONTEXT_FRAMES): indexes[-1] + 1]
        do_frames = [frames[i] for i in indexes]
        share = sum(1 for f in do_frames if f.get("lm") and f.get("world")) / len(do_frames)
        if share < MIN_HAND_SHARE:
            counts["skipped_hand"] += 1
            continue
        tip = NEAR_MISS_TIPS.get(task_key)
        if tip is not None and _touches(do_frames, tip, aspect):
            counts["skipped_contact"] += 1          # it was meant to almost touch: this piece did touch, so it is not "none"
            continue
        lab = frames[indexes[0]].get("label") or {}
        sample_id = hashlib.sha1(repr((session, key)).encode()).hexdigest()[:24]
        added = store.add_frames(
            part, label=task.label, label_source=GUIDED_SCHEDULED, session=session, sample_id=sample_id,
            approved=False, handedness=str(meta.get("control_hand") or ""), preprocessing_version=FEATURE_VERSION,
            task=task_key, condition=str(lab.get("condition_key") or lab.get("condition") or lab.get("posture") or ""),
            pc=str(meta.get("pc") or ""), camera=camera, aspect=aspect, quality_score=share,
            extra={"kind": kind, "set": set_id, "rep": rep, "context_frames": indexes[0] - max(0, indexes[0] - CONTEXT_FRAMES)},
        )
        counts["added" if added is not None else "skipped_duplicate"] += 1
    return counts


def import_recordings(store: SampleStore, paths: Sequence[Path | str], progress: Optional[Any] = None) -> Dict[str, int]:
    total = {"recordings": 0, "added": 0, "skipped_duplicate": 0, "skipped_hand": 0, "skipped_contact": 0}
    for path in paths:
        counts = import_recording(store, path)
        total["recordings"] += 1
        for key, value in counts.items():
            total[key] += value
        if progress is not None:
            progress(Path(path).name, counts)
    return total
