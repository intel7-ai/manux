"""Replay separate eval attempts through the direct recognizer before publishing a personal model."""
from __future__ import annotations

import argparse
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path

from manux.core.contracts import FrameContext, OutputTarget, Stage
from manux.data.recording import Recording, read, replay
from manux.features.tcn import TcnFeature
from manux.ml.card import ModelError, card_path
from manux.ml.predictor import load_model
from manux.ml.store import REPORT_SCHEMA, active_token, install, load_active
from manux.perception.local3d import FEATURES, FEATURE_VERSION
from manux.settings.paths import resolve
from tcn_data import LEGACY_RULES, load
from manux.data.rule_replay import rule_action
from manux.data.studio import capture_hashes
from manux.data.catalog import BY_KEY


def measure(segments, mapping, model=None, mode="pinch", known_labels=None):
    """Count actual tap/begin events, including neutral rearming and per-class thresholds."""
    by_gesture = {gesture: name for name, gesture in mapping.items()}
    rows = {name: {"attempts": 0, "tcn_hits": 0} for name in mapping}
    known_labels = set(known_labels or mapping)
    neutral_seconds, misfires, wrong = 0., 0, 0
    for segment in segments:
        events = {}
        if model is not None:
            feature = TcnFeature(model.predictor, model.card)
            rec = Recording(Path("evaluation"), {"frame_aspect": segment.aspect, "camera": {"mirror": False}},
                            frames=segment.frames)
            for i, item in enumerate(replay(rec)):
                task = BY_KEY.get((segment.frames[i].get("label") or {}).get("task"))
                frame_mode = "motion" if task is not None and task.direction else mode
                ctx = FrameContext(item.frame, Stage.TUTORIAL, OutputTarget.PRACTICE, frame_mode)
                for intent in feature.step(item.hand, ctx):
                    if intent.phase in ("tap", "begin"):
                        events[i] = by_gesture.get(intent.gesture)
        else:
            last = None
            for i, frame in enumerate(segment.frames):
                recorded = (frame.get("rule") or {}).get("gesture")
                gesture = (recorded if isinstance(recorded, str) and "." in recorded
                           else LEGACY_RULES.get(rule_action(frame), rule_action(frame)))
                name = by_gesture.get(gesture)
                if name and name != last:
                    events[i] = name
                last = name
        labels = segment.labels()
        for label, indices_ in groupby(range(len(labels)), key=labels.__getitem__):
            indices = list(indices_)
            if label == "none":
                misfires += sum(i in events for i in indices)
                neutral_seconds += sum(max(0., float(segment.frames[j].get("t", j / 30))
                                          - float(segment.frames[i].get("t", i / 30)))
                                       for i, j in zip(indices, indices[1:]))
            elif label in rows:
                rows[label]["attempts"] += 1
                rows[label]["tcn_hits"] += any(events.get(i) == label for i in indices)
            if label in known_labels:
                wrong += sum(i in events and events[i] != label for i in indices)
    return {"per_gesture": rows, "neutral_seconds": neutral_seconds, "wrong_events": wrong,
            "tcn_misfires_per_minute": misfires * 60 / neutral_seconds if neutral_seconds else None}


def validate_and_install(candidate_path, recordings, store, *, mode="pinch", cache_dir=None, loader=load_model,
                         cancelled=None, expected_active=...):
    token = active_token(store)
    if expected_active is not ... and token != expected_active:
        raise ValueError("학습 중 사용 모델이 바뀌었습니다. 다시 평가해 주세요.")
    current = load_active(store, FEATURE_VERSION, FEATURES) if token is not None else None
    before_model = sha256(Path(candidate_path).read_bytes()).hexdigest()
    before_card = sha256(card_path(Path(candidate_path)).read_bytes()).hexdigest()
    candidate = loader(Path(candidate_path), FEATURE_VERSION, FEATURES)
    if current is not None:
        for name, gesture in current.card.gestures.items():
            if candidate.card.gestures.get(name) != gesture:
                raise ValueError("existing gesture classes cannot be removed or remapped during installation")
    paths = list(dict.fromkeys(Path(p).resolve() for p in recordings))
    hashes, captures = [], set()
    for path in paths:
        rec = read(path)
        if rec.meta.get("split") != "eval":
            raise ValueError("installation requires explicitly marked split=eval recordings")
        if rec.meta.get("lineage"):
            raise ValueError("evaluation requires original independent captures")
        captures.update(capture_hashes(path, rec))
        hashes.append(sha256(path.read_bytes()).hexdigest())
    for model in (candidate, current):
        if model is None:
            continue
        training = [row.get("sha256") for row in model.card.raw["data"] if isinstance(row, dict)]
        if not training or any(not isinstance(h, str) or len(h) != 64 or set(h) - set("0123456789abcdef") for h in training):
            raise ValueError("model must identify every training recording by SHA256")
        training_captures = set(training)
        for row in model.card.raw["data"]:
            training_captures.update(row.get("capture_sha256", [row["sha256"]]))
        if training_captures.intersection(captures):
            raise ValueError("evaluation recordings overlap model training")
    segments = load(paths, split="eval", cache_dir=cache_dir)
    if current is None:
        for segment in segments:
            for frame in segment.frames:
                if (frame.get("label") or {}).get("target") not in {"none", *candidate.card.gestures}:
                    continue
                rule = frame.get("rule")
                if not isinstance(rule, dict) or not ({"action", "gesture"} & rule.keys()):
                    raise ValueError("initial installation requires a recorded rule baseline on eval frames")
    metrics = measure(segments, candidate.card.gestures, candidate, mode)
    baseline = measure(segments, current.card.gestures if current else candidate.card.gestures, current, mode,
                       known_labels=candidate.card.gestures)
    report = {"schema": REPORT_SCHEMA, "model_sha256": before_model, "card_sha256": before_card,
              "evaluation_sha256": hashes, "mode": mode, "candidate": metrics, "baseline": baseline}
    if [sha256(path.read_bytes()).hexdigest() for path in paths] != hashes:
        raise ValueError("evaluation recordings changed during validation")
    installed = install(Path(store), candidate, report, token, cancelled=cancelled)
    return installed, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("recordings", type=Path, nargs="+")
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--mode", choices=("pointer", "pinch", "motion"), default="pinch")
    parser.add_argument("--cache-dir", type=Path, default=resolve().data_dir / "features")
    args = parser.parse_args(argv)
    from manux.data.repository import repository_root
    repo = repository_root(Path(__file__))
    if args.store.resolve().is_relative_to(repo) or args.cache_dir.resolve().is_relative_to(repo):
        parser.error("write the model store and feature cache outside the repository")
    try:
        installed, report = validate_and_install(args.candidate, args.recordings, args.store,
                                                mode=args.mode, cache_dir=args.cache_dir)
        print(json.dumps({"installed": str(installed), "validation": report}, indent=2))
        return 0
    except (ModelError, ValueError, OSError, ImportError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
