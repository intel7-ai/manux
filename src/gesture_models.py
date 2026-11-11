"""Independent gesture models, atomic publication and runtime composition. No Qt or torch."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np

SCHEMA = "manux-gesture-models/1"
LABEL = re.compile(r"^[a-z][a-z0-9_]*$")


def read_bank(folder):
    path = Path(folder) / "manifest.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA or not isinstance(doc.get("models"), dict):
        raise ValueError("선택 모델 목록 형식이 맞지 않습니다.")
    for label, entry in doc["models"].items():
        if not LABEL.fullmatch(label) or label == "none" or not isinstance(entry, dict):
            raise ValueError("선택 모델의 동작 이름이 잘못됐습니다.")
        for key, suffix in (("model", ".onnx"), ("card", ".json")):
            name = entry.get(key, "")
            if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
                raise ValueError("선택 모델 파일은 모델 폴더 안에 있어야 합니다.")
            if not name.endswith(suffix):
                raise ValueError("선택 모델의 파일 형식이 잘못됐습니다.")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("선택 모델의 해시가 잘못됐습니다.")
    return doc["models"]


@contextmanager
def training_lock(folder):
    """The OS releases this lock even when the training process is terminated."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "training.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("다른 선택 학습이 진행 중입니다. 끝난 뒤 다시 시작하세요.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def publish_bank(folder, entries):
    """Called under training_lock after all new models have passed validation."""
    folder = Path(folder)
    merged = {**read_bank(folder), **entries}
    fd, pending = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": SCHEMA, "models": merged}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, folder / "manifest.json")
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def remove_from_bank(folder, labels):
    """Remove specified gesture labels from the specialist manifest atomically under training_lock."""
    folder = Path(folder)
    if not (folder / "manifest.json").exists():
        return False
    current = read_bank(folder)
    target_set = {labels} if isinstance(labels, str) else set(labels)
    updated = {k: v for k, v in current.items() if k not in target_set}
    if len(updated) == len(current):
        return False
    fd, pending = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": SCHEMA, "models": updated}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, folder / "manifest.json")
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return True


def load_single(path, feature_version):
    import onnxruntime as ort

    path = Path(path)
    info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    if info.get("feature_version") != feature_version:
        raise ValueError(f"model features {info.get('feature_version')} != app features {feature_version}")
    classes = info.get("classes", [])
    if (len(classes) < 2 or len(set(classes)) != len(classes) or "none" not in classes
            or not all(isinstance(name, str) and LABEL.fullmatch(name) for name in classes)):
        raise ValueError("모델의 클래스 목록이 잘못됐습니다.")
    window, features = info.get("window"), info.get("features")
    if not isinstance(window, int) or window < 1 or features != 133:
        raise ValueError("모델 입력 크기가 잘못됐습니다.")
    sess_options = ort.SessionOptions()
    sess_options.intra_op_num_threads = 2
    sess_options.inter_op_num_threads = 1
    sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    try:
        sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        sess_options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    except Exception:
        pass
    session = ort.InferenceSession(str(path), sess_options=sess_options, providers=["CPUExecutionProvider"])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or len(inputs[0].shape) != 3 or inputs[0].shape[1:] != [window, features]
            or len(outputs) != 1 or len(outputs[0].shape) != 2 or outputs[0].shape[1] != len(classes)):
        raise ValueError("ONNX 입력·출력 크기와 모델 정보가 다릅니다.")

    def predict(x):
        logits = np.asarray(session.run(None, {inputs[0].name: x.astype(np.float32)})[0][0])
        if logits.shape != (len(classes),) or not np.isfinite(logits).all():
            raise ValueError("모델이 유효한 확률을 반환하지 않았습니다.")
        scores = np.exp(logits - logits.max())
        return scores / scores.sum()

    predict(np.zeros((1, window, features), dtype=np.float32))
    return predict, info


def compose_predictor(model_path, base, info):
    folder = Path(model_path).parent / "gestures"
    entries = read_bank(folder)
    if not entries:
        return base, info
    predictors = {}
    thresholds, frames = {}, {}
    classes = list(info["classes"])
    for label, entry in entries.items():
        model = folder / entry["model"]
        if model.with_suffix(".json").name != entry["card"]:
            raise ValueError("선택 모델과 정보 파일 이름이 다릅니다.")
        if hashlib.sha256(model.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"{label} 모델 파일의 해시가 다릅니다.")
        predictor, card = load_single(model, info["feature_version"])
        if card["classes"] != ["none", label] or card["window"] != info["window"]:
            raise ValueError(f"{label} 선택 모델의 클래스 또는 관찰 길이가 다릅니다.")
        predictors[label] = predictor
        thresholds[label] = float(card.get("threshold", 0.7))
        frames[label] = int(card.get("frames_in_a_row", 3))
        if label not in classes:
            classes.append(label)

    def predict(x):
        scores = np.zeros(len(classes), dtype=np.float64)
        scores[:len(info["classes"])] = base(x)
        for label, predictor in predictors.items():
            scores[classes.index(label)] = predictor(x)[1]
        scores[classes.index("none")] = 1 - max(
            (scores[i] for i, name in enumerate(classes) if name != "none"), default=0)
        return scores

    return predict, {**info, "classes": classes, "independent_gestures": sorted(entries),
                     "gesture_thresholds": thresholds, "gesture_frames": frames}
