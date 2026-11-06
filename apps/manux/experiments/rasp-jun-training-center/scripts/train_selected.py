"""Train only requested binary gesture models and publish the completed bundle atomically."""
from __future__ import annotations

import glob
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import numpy as np
import torch

from src.capture_dataset import load_sessions
from src.gesture_catalog import BY_KEY, CLASSES
from src.gesture_models import load_single, publish_bank, training_lock
from src.recorder import finished_recordings, public_source
from src.tcn_features import FEATURES, FEATURE_VERSION


def selected_labels(keys):
    labels = []
    for key in keys:
        if key not in BY_KEY:
            raise ValueError(f"알 수 없는 동작: {key}")
        label = BY_KEY[key].label
        if label == "none":
            raise ValueError("이동·중립은 선택한 동작의 오답 자료로 함께 사용됩니다.")
        if label not in labels:
            labels.append(label)
    if not labels:
        raise ValueError("학습할 동작을 하나 이상 선택하세요.")
    return labels


def binary_sessions(sessions, label):
    target = CLASSES.index(label)
    return [{**session, "y": np.where(session["y"] < 0, -1, (session["y"] == target).astype(np.int64)),
             "classes": ["none", label]} for session in sessions]


def run(args, training):
    try:
        labels = selected_labels(args.gestures)
        if args.epochs < 1 or not 0 < args.thr <= 1 or args.k < 1:
            raise ValueError("학습 반복과 확률·연속 프레임 기준을 확인하세요.")
        folder = args.output_dir / "gestures"
        with training_lock(folder):
            paths = sorted(Path(path) for path in glob.glob(args.recordings))
            complete = {p.resolve() for parent in {p.parent for p in paths}
                        for p in finished_recordings(parent)[0]}
            paths = [path for path in paths if path.resolve() in complete]
            if not paths:
                raise ValueError("완료된 녹화가 없습니다.")
            sessions = load_sessions(paths, training.WINDOW)
            all_y = np.concatenate([session["y"] for session in sessions])
            if not (all_y == CLASSES.index("none")).any():
                raise ValueError("중립·쉬기 구간의 유효 녹화가 필요합니다.")
            missing = [label for label in labels if not (all_y == CLASSES.index(label)).any()]
            if missing:
                raise ValueError("유효한 녹화가 없는 동작: " + ", ".join(missing))
            sources = []
            for path, session in zip(paths, sessions):
                pc, camera = public_source(session["meta"])
                sources.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "pc": pc, "camera": {"note": camera}, "frames": len(session["frames"])})
            staged = {}
            for index, label in enumerate(labels, 1):
                training.emit("gesture_start", target=label, index=index, count=len(labels))
                selected = binary_sessions(sessions, label)
                classes = ["none", label]
                conds = sorted({int(c) for s in selected for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
                xa, ya = training.pick(selected, conds)
                held = conds if args.folds else ([] if args.test_condition < 0 else [args.test_condition])
                training.emit("loaded", windows=len(ya), folds=len(held), per_class=dict(Counter(ya.tolist())))
                results = []
                for fold, test in enumerate(held, 1):
                    x, y = training.pick(selected, [c for c in conds if c != test])
                    if not len(y) or len(set(y)) != 2:
                        training.emit("note", text=f"{label}: 조건 {test} 제외 평가는 학습 자료 부족으로 보류합니다.")
                        continue
                    training.emit("fold_start", posture=test, fold=fold, folds=len(held))
                    model = training.train(x, y, args.epochs, classes=classes)
                    for session in selected:
                        prob = training.predict(model, session["x"])
                        negative = (session["y"] == 0) & (session["cond"] == test)
                        results.append({"test_posture": test, "rules": training.evaluate(session, [test]),
                                        "tcn": training.evaluate(session, [test], prob, args.thr, args.k),
                                        "negative_frames": int(negative.sum()),
                                        "negative_frames_above_threshold": int((negative & (prob[:, 1] >= args.thr)).sum()),
                                        "free_minutes": training.free_minutes(session, [test])})
                training.emit("final_start")
                losses = []
                model = training.train(xa, ya, args.epochs, losses=losses, classes=classes)
                pred = training.predict(model, xa).argmax(1)
                accuracy = {name: round(float((pred[ya == i] == i).mean()), 3) for i, name in enumerate(classes)}
                per_class = {name: int((ya == i).sum()) for i, name in enumerate(classes)}
                card = {
                    "classes": classes, "target": label, "model_kind": "gesture_binary",
                    "window": training.WINDOW, "features": FEATURES, "feature_version": FEATURE_VERSION,
                    "threshold": args.thr, "frames_in_a_row": args.k,
                    "trained": time.strftime("%Y-%m-%d %H:%M:%S"), "data": sources,
                    "condition_groups": sessions[0]["condition_groups"],
                    "negative_classes": [name for i, name in enumerate(CLASSES)
                                         if name != label and (all_y == i).any()],
                    "held_out_results": results, "loss_curve": losses,
                    "per_gesture": training.per_gesture(results, per_class, accuracy, classes),
                    "per_task": training.per_gesture(results, per_class, accuracy, classes, by_task=True),
                }
                if not args.no_export:
                    path = folder / f"{label}-{uuid4().hex}.onnx"
                    torch.onnx.export(model.eval(), torch.zeros(1, training.WINDOW, FEATURES), str(path),
                                      input_names=["x"], output_names=["logits"],
                                      dynamic_axes={"x": {0: "batch"}, "logits": {0: "batch"}},
                                      opset_version=18, dynamo=False)
                    path.with_suffix(".json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
                    load_single(path, FEATURE_VERSION)
                    staged[label] = {"model": path.name, "card": path.with_suffix(".json").name,
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                training.emit("gesture_done", target=label)
            if not args.no_export:
                publish_bank(folder, staged)
            training.emit("done", targets=labels, published=not args.no_export)
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        training.emit("error", text=str(error))
        if not training.PROGRESS:
            print(str(error), flush=True)
        return 1
