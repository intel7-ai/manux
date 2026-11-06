"""Base TCN plus independently replaceable gesture models; publication is one atomic pointer update.

Selected models return independent confirmation probabilities, not one joint softmax.
This module is also copied into exported packages and has no app/Qt/torch dependencies.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import numpy as np

try:
    from .tcn_features import FEATURE_VERSION, FEATURES
except ImportError:  # standalone export
    from tcn_features import FEATURE_VERSION, FEATURES


def registry_path(model):
    model = Path(model)
    return model.with_name(model.stem + ".selected.json")


def available(model):
    return Path(model).is_file() or registry_path(model).is_file() or (Path(model).parent / "gestures" / "manifest.json").is_file()


def _registry(model):
    path = registry_path(model)
    if not path.exists():
        return {"version": 1, "overrides": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("overrides"), dict):
        raise ValueError("선택 학습 모델 목록을 읽을 수 없습니다.")
    return data


def _inside(folder, relative):
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()) or Path(relative).is_absolute():
        raise ValueError("선택 학습 모델 경로가 올바르지 않습니다.")
    return path


def _card(path):
    info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    classes = info["classes"]
    if info.get("feature_version") != FEATURE_VERSION:
        raise ValueError(f"model features {info.get('feature_version')} != app features {FEATURE_VERSION}")
    if (info.get("features", FEATURES) != FEATURES
            or info.get("window", 0) <= 0 or "none" not in classes or len(set(classes)) != len(classes)):
        raise ValueError("모델의 입력 특징 또는 클래스가 앱과 맞지 않습니다.")
    if not path.is_file():
        raise FileNotFoundError(path)
    return info


def components(model):
    """Resolve a consistent registry snapshot; each model is read only once."""
    model = Path(model).resolve()
    cards = {}
    if model.exists() or model.with_suffix(".json").exists():
        cards[model] = _card(model)
    overrides = {}
    # Import the remote branch's model bank; the selected registry remains
    # authoritative for gestures explicitly replaced through the current UI.
    try:
        from .gesture_models import read_bank
    except ImportError:
        from gesture_models import read_bank
    for label, entry in read_bank(model.parent / "gestures").items():
        path = _inside(model.parent, "gestures/" + entry["model"])
        if path.with_suffix(".json").name != entry["card"]:
            raise ValueError("선택 모델과 정보 파일 이름이 다릅니다.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"{label} 모델 파일의 해시가 다릅니다.")
        card = _card(path)
        if card["classes"] != ["none", label]:
            raise ValueError(f"{label} 선택 모델의 클래스가 다릅니다.")
        cards[path], overrides[label] = card, path
    for label, relative in _registry(model)["overrides"].items():
        path = _inside(model.parent, relative)
        if path not in cards:
            cards[path] = _card(path)
        if label == "none" or label not in cards[path]["classes"]:
            raise ValueError(f"선택 학습 모델에 {label} 동작이 없습니다.")
        overrides[label] = path
    if len({card["window"] for card in cards.values()}) > 1:
        raise ValueError("선택 학습 모델과 기존 모델의 입력 길이가 다릅니다.")
    return cards, overrides


def _combined_info(model, cards, overrides):
    model = Path(model).resolve()
    base = cards.get(model, next(iter(cards.values())))
    info = copy.deepcopy(base)
    classes = list(base["classes"]) if model in cards else ["none"]
    classes += [label for label in overrides if label not in classes]
    info["classes"] = classes
    info["selected_classes"] = list(overrides)
    info["specialists"] = {label: copy.deepcopy(cards[path]) for label, path in overrides.items()}
    info["class_decisions"] = {}
    info["class_mtime"] = {}
    info["class_model_ids"] = {}
    info["class_quality"] = {}
    info["task_quality"] = {}
    info["class_pipeline_versions"] = {}
    for field in ("per_gesture", "per_task", "train_accuracy"):
        info.setdefault(field, {})
    for label in classes:
        path = overrides.get(label, model if model in cards else next(iter(cards)))
        card = cards[path]
        info["class_decisions"][label] = {
            "threshold": card.get("threshold", .7), "frames_in_a_row": card.get("frames_in_a_row", 3)}
        info["class_mtime"][label] = path.stat().st_mtime
        info["class_model_ids"][label] = f"{path.relative_to(model.parent).as_posix()}:{path.stat().st_mtime_ns}:{path.stat().st_size}"
        info["class_quality"][label] = copy.deepcopy(card.get("interaction_validation", {}).get(label, {}))
        info["class_pipeline_versions"][label] = card.get("pipeline_version", "legacy")
        for task, target in card.get("task_targets", {}).items():
            if target == label:
                info["task_quality"][task] = copy.deepcopy(card.get("interaction_validation_tasks", {}).get(task, {}))
        if label in overrides:
            for field in ("per_gesture", "train_accuracy"):
                info[field].pop(label, None)
                if label in card.get(field, {}):
                    info[field][label] = copy.deepcopy(card[field][label])
            # Remove scores for aliases of this label before inserting the newly evaluated tasks.
            for task, target in card.get("task_targets", {}).items():
                if target == label:
                    info["per_task"].pop(task, None)
                    if task in card.get("per_task", {}):
                        info["per_task"][task] = copy.deepcopy(card["per_task"][task])
    if overrides:
        # A collection of independent confirmers has no joint neutral accuracy/misfire score.
        info["per_gesture"].pop("none", None)
        for task, target in next(reversed(cards.values())).get("task_targets", {}).items():
            if target == "none":
                info["per_task"].pop(task, None)
    data = {}
    for card in cards.values():
        for entry in card.get("data", []):
            data[entry["file"]] = entry
    info["data"] = list(data.values())
    info["mtime"] = max(path.stat().st_mtime for path in cards)
    info["trained"] = max(card.get("trained", "") for card in cards.values())
    return info


def model_info(model):
    cards, overrides = components(model)
    return _combined_info(model, cards, overrides) if cards else None


def load_predictor(model):
    import onnxruntime as ort

    cards, overrides = components(model)
    if not cards:
        raise FileNotFoundError("학습한 모델이 없습니다.")
    info = _combined_info(model, cards, overrides)
    model = Path(model).resolve()
    routes = {label: overrides.get(label, model if model in cards else next(iter(cards)))
              for label in info["classes"] if label != "none"}
    active = set(routes.values())
    if model in cards:
        active.add(model)
    sessions = {}
    for path in active:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        sample = session.run(None, {"x": np.zeros((1, info["window"], FEATURES), np.float32)})[0]
        if sample.shape != (1, len(cards[path]["classes"])) or not np.isfinite(sample).all():
            raise ValueError("모델 출력과 클래스 목록이 일치하지 않습니다.")
        sessions[path] = session

    def predict(x):
        values = {}
        for path, session in sessions.items():
            logits = session.run(None, {"x": x.astype(np.float32)})[0][0]
            exp = np.exp(logits - logits.max())
            values[path] = exp / exp.sum()
        result = np.zeros(len(info["classes"]), np.float32)
        for i, label in enumerate(info["classes"]):
            if label in routes:
                path = routes[label]
                result[i] = values[path][cards[path]["classes"].index(label)]
        result[info["classes"].index("none")] = (
            values[model][cards[model]["classes"].index("none")] if model in values else 1. - result.max())
        return result

    return predict, info


def publish_selected(model, trained, labels):
    """Validate all files before changing routes. Interrupted/failed training leaves the old model active."""
    model, trained = Path(model).resolve(), Path(trained).resolve()
    labels = list(dict.fromkeys(labels))
    card = _card(trained)
    if not labels or set(labels) != set(card["classes"]) - {"none"}:
        raise ValueError("선택한 동작과 학습된 모델 클래스가 다릅니다.")
    cards, _ = components(model)
    if any(c["window"] != card["window"] for c in cards.values()):
        raise ValueError("기존 모델과 입력 길이가 다릅니다.")
    load_predictor(trained)  # ONNX must actually load and run before activation
    relative = trained.relative_to(model.parent).as_posix()
    data = _registry(model)
    data["overrides"].update({label: relative for label in labels})
    dest = registry_path(model)
    temp = dest.with_name(dest.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def copy_bundle(model, folder):
    """Export the exact active routes, not just the older base ONNX."""
    model, folder = Path(model).resolve(), Path(folder)
    cards, overrides = components(model)
    manifest = model.parent / "gestures" / "manifest.json"
    if manifest.exists():
        (folder / "gestures").mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest, folder / "gestures" / "manifest.json")
    for path in cards:
        dest = folder / path.relative_to(model.parent)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        shutil.copy2(path.with_suffix(".json"), dest.with_suffix(".json"))
    if overrides:
        data = {"version": 1, "overrides": {label: path.relative_to(model.parent).as_posix()
                                            for label, path in overrides.items()}}
        registry_path(folder / model.name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
