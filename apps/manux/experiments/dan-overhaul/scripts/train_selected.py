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
            from src.training_center import (
                backup_current_model,
                filter_selected_recording_paths,
                get_augmentation_settings,
            )
            paths = sorted(Path(path) for path in glob.glob(args.recordings))
            complete = {p.resolve() for parent in {p.parent for p in paths}
                        for p in finished_recordings(parent)[0]}
            paths = [path for path in paths if path.resolve() in complete]
            paths = [Path(p) for p in filter_selected_recording_paths(paths)]
            if not paths:
                raise ValueError("선택된 완료 녹화가 없습니다.")
            use_cleaned = not getattr(args, "no_cleaned", False)
            from src.capture_dataset import discover_augmented_paths, resolve_cleaned_path
            resolved_primary = [resolve_cleaned_path(p, use_cleaned=use_cleaned) for p in paths]
            aug_cfg = get_augmentation_settings(paths[0].parent)
            use_aug = bool(getattr(args, "offline_augment", False))
            aug_paths = discover_augmented_paths(paths) if use_aug else []
            extra_records = None
            if use_aug and not aug_paths:
                from src.offline_augment import generate_augmented_records
                extra_records = generate_augmented_records(
                    resolved_primary,
                    factor=int(aug_cfg.get("factor", 2)),
                    rotation_deg=float(aug_cfg.get("rotation_deg", 35.0)),
                    scale_range=float(aug_cfg.get("scale_range", 0.15)),
                    jitter_std=float(aug_cfg.get("jitter_std", 0.003)),
                    time_stretch_range=tuple(aug_cfg.get("time_stretch_range", (0.85, 1.15))),
                    frame_drop_prob=float(aug_cfg.get("frame_drop_prob", 0.05)),
                )
            all_load_paths = list(resolved_primary) + list(aug_paths)
            sessions = load_sessions(all_load_paths, training.WINDOW, extra_records=extra_records)
            eval_sessions = sessions[: len(paths)]
            all_y = np.concatenate([session["y"] for session in sessions])
            if not (all_y == CLASSES.index("none")).any():
                raise ValueError("중립·쉬기 구간의 유효 녹화가 필요합니다.")
            missing = [label for label in labels if not (all_y == CLASSES.index(label)).any()]
            if missing:
                raise ValueError("유효한 녹화가 없는 동작: " + ", ".join(missing))
            sources = []
            for path, session in zip(paths, eval_sessions):
                pc, camera = public_source(session["meta"])
                sources.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "pc": pc, "camera": {"note": camera}, "frames": len(session["frames"])})
            staged = {}
            for index, label in enumerate(labels, 1):
                training.emit("gesture_start", target=label, index=index, count=len(labels))
                selected = binary_sessions(sessions, label)
                eval_selected = selected[: len(paths)]
                classes = ["none", label]
                conds = sorted({int(c) for s in eval_selected for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
                xa, ya = training.pick(selected, conds)
                xa_eval, ya_eval = training.pick(eval_selected, conds)
                held = conds if args.folds else ([] if args.test_condition < 0 else [args.test_condition])
                training.emit("loaded", windows=len(ya), folds=len(held), per_class=dict(Counter(ya_eval.tolist())))
                results = []
                for fold, test in enumerate(held, 1):
                    x, y = training.pick(selected, [c for c in conds if c != test])
                    if not len(y) or len(set(y)) != 2:
                        training.emit("note", text=f"{label}: 조건 {test} 제외 평가는 학습 자료 부족으로 보류합니다.")
                        continue
                    training.emit("fold_start", posture=test, fold=fold, folds=len(held))
                    model = training.train(x, y, args.epochs, classes=classes)
                    for session in eval_selected:
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
                pred = training.predict(model, xa_eval).argmax(1)
                accuracy = {name: round(float((pred[ya_eval == i] == i).mean()), 3) for i, name in enumerate(classes)}
                per_class = {name: int((ya_eval == i).sum()) for i, name in enumerate(classes)}
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
                    model_cpu = model.cpu().eval()
                    torch.onnx.export(model_cpu, torch.zeros(1, training.WINDOW, FEATURES), str(path),
                                      input_names=["x"], output_names=["logits"],
                                      dynamic_axes={"x": {0: "batch"}, "logits": {0: "batch"}},
                                      opset_version=18, dynamo=False)
                    path.with_suffix(".json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
                    load_single(path, FEATURE_VERSION)
                    staged[label] = {"model": path.name, "card": path.with_suffix(".json").name,
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                training.emit("gesture_done", target=label)
            if not args.no_export:
                try:
                    backup_current_model(
                        model=args.output_dir / "gesture_tcn.onnx",
                        backup_dir=args.output_dir / "backups",
                        reason="before-selected-train",
                    )
                except Exception:
                    pass
                publish_bank(folder, staged)
                main_card = args.output_dir / "gesture_tcn.json"
                if main_card.is_file():
                    try:
                        mc = json.loads(main_card.read_text(encoding="utf-8"))
                        disabled = [x for x in (mc.get("disabled_gestures") or []) if x not in labels]
                        if disabled != (mc.get("disabled_gestures") or []):
                            mc["disabled_gestures"] = disabled
                            main_card.write_text(json.dumps(mc, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass
            training.emit("done", targets=labels, published=not args.no_export)
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        training.emit("error", text=str(error))
        if not training.PROGRESS:
            print(str(error), flush=True)
        return 1
