"""Shared data and event metrics for MANUX TCN training and evaluation."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

import numpy as np

from manux.data.guided import NONE, RESERVED
from manux.data.recording import read
from manux.data.rule_replay import rule_action
from manux.features.tcn_gate import LEGACY_GESTURES
from manux.perception.local3d import frame_features, windows

LEGACY_RULES = {"scroll_grab": "pinch.scroll", "right_click": "pinch.thumb_ring",
                "left_click": "pinch.thumb_middle", "drag_hold": "pinch.drag",
                "task_switcher": "pinch.thumb_pinky", "show_desktop": "pinch.thumb_tucked_open"}


@dataclass(frozen=True)
class Segment:
    person: str
    posture: str
    frames: list[dict]
    features: np.ndarray

    def labels(self) -> list[str | None]:
        return [(f.get("label") or {}).get("target") for f in self.frames]


def load(paths: list[Path]) -> list[Segment]:
    segments = []
    for path in paths:
        recording = read(path)
        if not recording.complete or recording.bad_lines:
            raise ValueError(f"{path.name}: incomplete or corrupt recording")
        person = recording.meta.get("person") or recording.meta.get("pc")
        group = hashlib.sha256(str(person).encode()).hexdigest()[:12] if person else ""
        width, height, _ = recording.frame_size()
        for posture, frames in groupby(recording.frames, _posture):
            batch = list(frames)
            segments.append(Segment(group, posture, batch, frame_features(batch, height / width)))
    return segments


def _posture(frame: dict) -> str:
    label = frame.get("label") or {}
    posture = label.get("condition_index")
    return str(posture) if posture is not None else str(label.get("condition") or "")


def class_mapping(segments: list[Segment], classes: list[str]) -> dict[str, str]:
    mapping = {}
    for segment in segments:
        for frame in segment.frames:
            label = frame.get("label") or {}
            name, gesture = label.get("target"), label.get("gesture")
            if name not in classes or not gesture:
                continue
            if mapping.setdefault(name, gesture) != gesture:
                raise ValueError(f"conflicting gesture IDs for class {name!r}")
    for name in classes:
        if name in LEGACY_GESTURES:
            mapping.setdefault(name, LEGACY_GESTURES[name])
    missing = set(classes) - {NONE} - mapping.keys()
    if missing:
        raise ValueError(f"recordings must declare a gesture ID for {', '.join(sorted(missing))}")
    return mapping


def class_names(segments: list[Segment]) -> list[str]:
    return sorted({name for s in segments for name in s.labels() if name and name not in RESERVED})


def groups(segments: list[Segment], axis: str) -> dict[str, list[Segment]]:
    grouped = defaultdict(list)
    for segment in segments:
        value = getattr(segment, axis)
        if value:
            grouped[value].append(segment)
    return dict(grouped)


def samples(segments: list[Segment], classes: list[str], length: int) -> tuple[np.ndarray, np.ndarray]:
    lookup = {name: i for i, name in enumerate(classes)}
    inputs, targets = [], []
    for segment in segments:
        y = np.array([lookup.get(name, -1) for name in segment.labels()], dtype=np.int64)
        keep = (y >= 0) & (segment.features[:, -1] == 1)
        if keep.any():
            inputs.append(windows(segment.features, length)[keep])
            targets.append(y[keep])
    if not inputs:
        raise ValueError("no labeled frames with valid world landmarks")
    return np.concatenate(inputs), np.concatenate(targets)


def predicted_events(probabilities: np.ndarray, classes: list[str], threshold: float, count: int):
    last = None
    run = 0
    for i, scores in enumerate(probabilities):
        best = int(scores.argmax())
        label = classes[best] if scores[best] >= threshold else None
        if label != last:
            run = 0
            last = label
        run += 1
        if run == count and label not in (None, NONE):
            yield i, label


def evaluate(segments, classes, mapping, infer, length, threshold, count):
    """Score gesture attempts and neutral misfires with one event per consecutive prediction run."""
    attempts = {name: {"attempts": 0, "tcn_hits": 0, "rule_hits": 0} for name in classes if name != NONE}
    by_gesture = {gesture: name for name, gesture in mapping.items()}
    tcn_misfires = rule_misfires = 0
    neutral_seconds = 0.0
    for segment in segments:
        labels = segment.labels()
        probabilities = infer(windows(segment.features, length))
        model = dict(predicted_events(probabilities, classes, threshold, count))
        rule = {}
        last = None
        for i, frame in enumerate(segment.frames):
            action = rule_action(frame)
            recorded = frame.get("rule") or {}
            gesture = recorded.get("gesture") or LEGACY_RULES.get(action, action)
            name = by_gesture.get(gesture)
            if name and name != last:
                rule[i] = name
            last = name
        for label, group in groupby(range(len(labels)), key=labels.__getitem__):
            indices = list(group)
            if label == NONE:
                tcn_misfires += sum(i in model for i in indices)
                rule_misfires += sum(i in rule for i in indices)
                neutral_seconds += sum(max(0.0, float(segment.frames[j].get("t", j / 30)) -
                                           float(segment.frames[i].get("t", i / 30)))
                                       for i, j in zip(indices, indices[1:]))
            elif label in attempts:
                row = attempts[label]
                row["attempts"] += 1
                row["tcn_hits"] += any(model.get(i) == label for i in indices)
                row["rule_hits"] += any(rule.get(i) == label for i in indices)
    return {"per_gesture": attempts, "neutral_seconds": round(neutral_seconds, 3),
            "tcn_misfires_per_minute": tcn_misfires * 60 / neutral_seconds if neutral_seconds else None,
            "rule_misfires_per_minute": rule_misfires * 60 / neutral_seconds if neutral_seconds else None}
