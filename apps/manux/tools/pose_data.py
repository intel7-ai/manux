"""Per-frame pose data for the pose-assist model: features, labels, independent segments and splits.

numpy only (no torch), so the splitting and the checks run in CI. `train_pose.py` adds the model.

The unit of independence is the **segment**: a run of consecutive labelled frames of one class in one
recording. Neighbouring frames of a run are nearly the same pose, so they must never land on both sides
of a train / evaluation split (the park branch's 97 % came from exactly that leak; segment-grouped it
was about 80-82 %). Splits here move whole segments, and a class with too few segments is reported.

    segments = pose_data.load_segments(paths, split="train")
    train, held_out = pose_data.split_segments(segments, eval_fraction=0.2, seed=0)
    pose_data.check_disjoint(train, held_out)
    for message in pose_data.coverage_warnings(train): print(message)

Frames without usable world joints, with no label, or labelled `transition` / `idle` / `no_hand` are not
used (`data.catalog.RESERVED`); `none` is a class (the no-gesture hand). The label of a frame is
`label.target`; the gesture id a class stands for is `label.gesture` (`tcn_data.class_mapping` rules,
D-004: no action names anywhere).
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from manux.data.capture_plan import label_condition_key
from manux.data.catalog import NONE, RESERVED
from manux.data.recording import read
from manux.features.tcn_gate import LEGACY_GESTURES
from manux.perception.pose_angles import FEATURES, frame_pose_angles

MIN_SEGMENTS = 8          # independent segments a class should have in training (and 2+ held out)
MIN_HELD_OUT = 2
GAP = 0.5                 # seconds between two labelled frames that still belong to one segment


@dataclass(frozen=True)
class Segment:
    """Consecutive labelled frames of one class in one recording (their `(n, 33)` pose-angles rows)."""
    source: str               # recording file name
    sha256: str               # the recording file's content hash (stable id for overlap checks)
    index: int                # running number of the segment inside its recording
    person: str               # hashed alias group, "" when the recording does not say
    condition: str            # capture-plan condition key, "" when unassigned
    label: str
    rows: np.ndarray = field(repr=False)

    @property
    def frames(self) -> int:
        return len(self.rows)

    @property
    def key(self) -> Tuple[str, int]:
        return self.sha256, self.index


@dataclass
class Loaded:
    segments: List[Segment]
    gestures: Dict[str, str]                 # class -> gesture id (only classes that declare one)
    sources: List[Dict[str, object]]         # public per-recording facts for the model card


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _alias(meta: Mapping[str, object]) -> str:
    person = meta.get("person") or meta.get("pc")
    return hashlib.sha256(str(person).encode()).hexdigest()[:12] if person else ""


def load_segments(paths: Iterable[Path], *, split: Optional[str] = None) -> Loaded:
    """Read finished recordings into segments. Raises ValueError for an incomplete or corrupt file, for a
    recording of the wrong `meta.split`, and for two different gesture ids on one class."""
    from manux.data.recorder import public_source   # local: pulls in the recorder only when sources are listed

    segments: List[Segment] = []
    gestures: Dict[str, str] = {}
    sources: List[Dict[str, object]] = []
    seen = set()
    for path in dict.fromkeys(Path(p).resolve() for p in paths):
        digest = file_sha256(path)
        if digest in seen:
            continue                                    # the same content twice is one recording
        seen.add(digest)
        rec = read(path)
        if not rec.complete or rec.bad_lines:
            raise ValueError(f"{path.name}: incomplete or corrupt recording")
        recorded = rec.meta.get("split")
        if split is not None and recorded is not None and recorded != split:
            raise ValueError(f"{path.name}: expected a {split} recording, got {recorded}")
        rows, valid = frame_pose_angles(rec.frames)
        person = _alias(rec.meta)
        kept = 0
        run: List[int] = []
        run_key = None
        last_t = None
        index = 0

        def flush():
            nonlocal run, index
            if run:
                label, condition = run_key
                segments.append(Segment(path.name, digest, index, person, condition, label, rows[run].copy()))
                index += 1
                run = []

        for i, frame in enumerate(rec.frames):
            label = frame.get("label") or {}
            name = label.get("target")
            if not name or name in RESERVED or not valid[i]:
                flush()
                run_key = None
                last_t = None
                continue
            gesture = label.get("gesture")
            if gesture:
                if gestures.setdefault(name, gesture) != gesture:
                    raise ValueError(f"conflicting gesture IDs for class {name!r}")
            condition = label_condition_key(label)
            key = (name, "" if condition == "unassigned" else condition)
            t = float(frame.get("t", i / 30.0))
            if run and (key != run_key or last_t is None or t - last_t > GAP or t < last_t):
                flush()
            run_key = key
            run.append(i)
            last_t = t
            kept += 1
        flush()
        pc, note = public_source(rec.meta)
        camera = {k: v for k, v in (rec.meta.get("camera") or {}).items() if k in ("source", "mode", "mirror")}
        sources.append({"file": path.name, "sha256": digest, "pc": pc, "person": rec.meta.get("person"),
                        "camera": dict(camera, note=note), "frames": len(rec.frames), "used_frames": kept})
    for name in set(s.label for s in segments):
        if name in LEGACY_GESTURES and name not in gestures:
            gestures[name] = LEGACY_GESTURES[name]
    gestures.pop(NONE, None)
    return Loaded(segments, gestures, sources)


def class_names(segments: Sequence[Segment]) -> List[str]:
    """Class names with `none` first, the others sorted."""
    names = sorted({s.label for s in segments} - {NONE})
    return ([NONE] if any(s.label == NONE for s in segments) else []) + names


def segments_per_class(segments: Sequence[Segment]) -> Dict[str, int]:
    count: Dict[str, int] = {}
    for s in segments:
        count[s.label] = count.get(s.label, 0) + 1
    return count


def frames_per_class(segments: Sequence[Segment]) -> Dict[str, int]:
    count: Dict[str, int] = {}
    for s in segments:
        count[s.label] = count.get(s.label, 0) + s.frames
    return count


def split_segments(segments: Sequence[Segment], eval_fraction: float = 0.2, seed: int = 0
                   ) -> Tuple[List[Segment], List[Segment]]:
    """Move whole segments to the held-out side, class by class, until it holds ~`eval_fraction` of that class's
    frames. A class with a single segment stays entirely in training (nothing can be held out for it)."""
    if not 0.0 < eval_fraction < 1.0:
        raise ValueError("eval_fraction must be within (0, 1)")
    rng = random.Random(seed)
    train: List[Segment] = []
    held: List[Segment] = []
    for name in sorted({s.label for s in segments}):
        group = [s for s in segments if s.label == name]
        group.sort(key=lambda s: (s.sha256, s.index))
        rng.shuffle(group)
        if len(group) < 2:
            train.extend(group)
            continue
        goal = eval_fraction * sum(s.frames for s in group)
        taken = 0
        for position, segment in enumerate(group):
            left = len(group) - position
            if taken < goal and left > 1:               # always leave at least one segment in training
                held.append(segment)
                taken += segment.frames
            else:
                train.append(segment)
    return train, held


def check_disjoint(train: Sequence[Segment], held_out: Sequence[Segment]) -> None:
    """Raise ValueError when a segment, or a whole recording file, is on both sides."""
    keys = {s.key for s in train}
    clash = [s for s in held_out if s.key in keys]
    if clash:
        raise ValueError(f"{len(clash)} segment(s) are in both training and evaluation (first: {clash[0].source} #{clash[0].index})")


def check_files_disjoint(train_hashes: Iterable[str], eval_hashes: Iterable[str]) -> None:
    """Separate evaluation recordings must not be (a copy of) a training recording (as `promote_tcn` requires)."""
    clash = set(train_hashes) & set(eval_hashes)
    if clash:
        raise ValueError(f"{len(clash)} evaluation recording(s) are also training recordings")


def coverage_warnings(train: Sequence[Segment], held_out: Sequence[Segment] = (), *, minimum: int = MIN_SEGMENTS,
                      minimum_held_out: int = MIN_HELD_OUT) -> List[str]:
    """Plain-language warnings about classes the numbers cannot be trusted for."""
    out: List[str] = []
    per_train, per_held = segments_per_class(train), segments_per_class(held_out)
    for name in sorted(per_train):
        if per_train[name] < minimum:
            out.append(f"class {name!r}: only {per_train[name]} independent training segment(s) "
                       f"(want at least {minimum}); its accuracy cannot be trusted")
    if held_out:
        for name in sorted(per_train):
            if per_held.get(name, 0) < minimum_held_out:
                out.append(f"class {name!r}: {per_held.get(name, 0)} held-out segment(s) "
                           f"(want at least {minimum_held_out}); its held-out result is a guess")
    people = {s.person for s in list(train) + list(held_out) if s.person}
    if len(people) < 2:
        out.append("one person (or none named) in the data: nothing here says how it behaves for someone else")
    return out


def stack(segments: Sequence[Segment], classes: Sequence[str]) -> Tuple[np.ndarray, np.ndarray]:
    """`(n, 33)` float32 rows and `(n,)` class indices of every segment whose class is in `classes`."""
    lookup = {name: i for i, name in enumerate(classes)}
    xs = [s.rows for s in segments if s.label in lookup]
    ys = [np.full(s.frames, lookup[s.label], dtype=np.int64) for s in segments if s.label in lookup]
    if not xs:
        raise ValueError("no labelled frames with usable world joints")
    return np.concatenate(xs).astype(np.float32), np.concatenate(ys)


# ----------------------------------------------------------------------------- metrics (pure numpy)

def confusion(y: np.ndarray, pred: np.ndarray, n: int) -> np.ndarray:
    cm = np.zeros((n, n), dtype=np.int64)
    np.add.at(cm, (y, pred), 1)
    return cm


def per_class(cm: np.ndarray, classes: Sequence[str]) -> Dict[str, Dict[str, Optional[float]]]:
    out = {}
    for i, name in enumerate(classes):
        support, predicted = int(cm[i].sum()), int(cm[:, i].sum())
        out[name] = {"frames": support,
                     "recall": round(float(cm[i, i] / support), 4) if support else None,
                     "precision": round(float(cm[i, i] / predicted), 4) if predicted else None}
    return out


def coverage_accuracy(probs: np.ndarray, y: np.ndarray, thresholds: Sequence[float] = (0.0, 0.7, 0.9, 0.99)
                      ) -> List[Dict[str, float]]:
    """For each confidence floor: the share of frames the model speaks on and its accuracy on them."""
    top = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    rows = []
    for th in thresholds:
        speaks = conf >= th
        rows.append({"threshold": float(th), "coverage": round(float(speaks.mean()), 4),
                     "accuracy": round(float((top[speaks] == y[speaks]).mean()), 4) if speaks.any() else None})
    return rows


def veto_study(probs: np.ndarray, y: np.ndarray, classes: Sequence[str], gesture_class: str, tau: float = 0.9
               ) -> Dict[str, object]:
    """What a veto for `gesture_class` would do on these frames (frame level, not an event count).

    Veto frame: the model's best class is another class with probability >= tau.
    `wrongly_vetoed`: share of the gesture's own frames that would be vetoed (a missed gesture);
    `correctly_vetoed`: share of the *other* frames that would be vetoed (a prevented false gesture).
    """
    target = classes.index(gesture_class)
    top, conf = probs.argmax(axis=1), probs.max(axis=1)
    veto = (top != target) & (conf >= tau)
    own, other = y == target, y != target
    return {"gesture_class": gesture_class, "tau": tau,
            "own_frames": int(own.sum()), "wrongly_vetoed": round(float(veto[own].mean()), 4) if own.any() else None,
            "other_frames": int(other.sum()),
            "correctly_vetoed": round(float(veto[other].mean()), 4) if other.any() else None}


def evaluate(probs: np.ndarray, y: np.ndarray, classes: Sequence[str], *, veto_classes: Sequence[str] = (),
             tau: float = 0.9) -> Dict[str, object]:
    """One held-out result row for the model card (`held_out_results`). Frame level, on whole held-out
    segments; not a gesture-event score and not a comparison with the TCN."""
    cm = confusion(y, probs.argmax(axis=1), len(classes))
    return {"axis": "segment", "status": "evaluated", "level": "frame",
            "frames": int(len(y)), "accuracy": round(float(np.trace(cm) / max(1, cm.sum())), 4),
            "per_class": per_class(cm, classes), "confusion": cm.tolist(),
            "coverage_accuracy": coverage_accuracy(probs, y),
            "veto": [veto_study(probs, y, list(classes), c, tau) for c in veto_classes if c in classes]}
