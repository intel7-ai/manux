"""Named, immutable model artifacts. Remote operations never activate a model."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

import hub
from manux.data.studio import atomic_json
from manux.ml.predictor import load_model
from manux.perception.local3d import FEATURE_VERSION, FEATURES


def name(value):
    value = str(value).strip()
    if not value or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise ValueError("모델 이름은 1~80자로 입력하세요.")
    return value


def folder(root, identifier):
    if not re.fullmatch(r"[0-9a-f]{64}", str(identifier)):
        raise ValueError("모델을 목록에서 다시 선택하세요.")
    path = Path(root) / identifier
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("모델 보관함 위치가 잘못되었습니다.")
    return path


def checked(path):
    path = Path(path)
    if path.stat().st_size > 256 * 1024 * 1024: raise ValueError("모델 파일이 너무 큽니다.")
    if path.suffix == ".json":
        from manux.ml.spatial_mlp import SpatialMlpModel
        model = SpatialMlpModel.load(path)
        return {"format": "spatial", "classes": list(model.classes), "trained": model.metadata.get("trained_at", ""),
                "sha256": hub.file_sha256(path), "card_sha256": "", "model_file": "model.json"}
    # Reject external tensors before ORT opens anything outside this artifact.
    import onnx
    try:
        graph = onnx.load(path, load_external_data=False)
        def inspect(message):
            if isinstance(message, onnx.TensorProto) and onnx.external_data_helper.uses_external_data(message):
                raise ValueError("외부 파일을 참조하는 모델은 공유할 수 없습니다.")
            for descriptor, value in message.ListFields():
                if descriptor.message_type is not None:
                    for child in value if descriptor.is_repeated else [value]: inspect(child)
        inspect(graph)
        if graph.functions: raise ValueError("외부 함수가 있는 모델은 공유할 수 없습니다.")
        onnx.checker.check_model(graph)
    except Exception:
        raise ValueError("모델 그래프 또는 외부 가중치 참조를 확인하세요.") from None
    model = load_model(path, FEATURE_VERSION, FEATURES, device="cpu")
    if model.card.raw.get("model_sha256") != hub.file_sha256(path):
        raise ValueError("모델과 카드의 해시가 일치하지 않습니다.")
    return {"format": "tcn", "classes": list(model.card.classes), "trained": str(model.card.raw.get("trained", "")),
            "sha256": hub.file_sha256(path), "card_sha256": hub.file_sha256(path.with_suffix(".json")), "model_file": "model.onnx"}


def add(root, path, display_name=None):
    root, path = Path(root), Path(path)
    info = checked(path)
    identifier = sha256((info["sha256"] + info["card_sha256"]).encode()).hexdigest()
    target = folder(root, identifier)
    if target.exists():
        # Never overwrite a previously stored artifact, even if it was corrupted.
        row = local(root, identifier)
        if display_name is not None: rename(root, identifier, display_name)
        return local(root, identifier)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".model-", dir=root) as temporary:
        staged = Path(temporary) / "artifact"
        staged.mkdir()
        shutil.copyfile(path, staged / info["model_file"])
        if info["format"] == "tcn": shutil.copyfile(path.with_suffix(".json"), staged / "model.json")
        if checked(staged / info["model_file"]) != info:
            raise ValueError("보관 중 모델이 변경되었습니다.")
        default_name = ("TCN · " + info["trained"]) if path.stem in ("model", "gesture_tcn") and info["trained"] else path.stem
        atomic_json(staged / "entry.json", {**info, "id": identifier, "name": name(display_name or default_name[:80])})
        try: os.rename(staged, target)
        except FileExistsError: local(root, identifier)
    return local(root, identifier)


def local(root, identifier):
    target = folder(root, identifier)
    row = json.loads((target / "entry.json").read_text(encoding="utf-8"))
    model_file = "model.json" if row.get("format") == "spatial" else "model.onnx"
    if row.get("model_file") != model_file: raise ValueError("모델 형식이 잘못되었습니다.")
    info = checked(target / model_file)
    actual_id = sha256((info["sha256"] + info["card_sha256"]).encode()).hexdigest()
    if actual_id != identifier or any(row.get(k) != v for k, v in info.items()):
        raise ValueError("보관 모델이 변경되었습니다. 원본에서 다시 가져오세요.")
    return {**row, "name": name(row["name"])}


def rename(root, identifier, display_name):
    row = local(root, identifier)
    atomic_json(folder(root, identifier) / "entry.json", {**row, "name": name(display_name)})


def scan(root):
    rows = []
    for path in sorted(Path(root).glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if re.fullmatch(r"[0-9a-f]{64}", path.name):
            try: rows.append(local(root, path.name))
            except (ValueError, OSError, KeyError): continue
    return rows


def remote_path(value):
    value = str(value)
    parts = value.split("/")
    if (not value.startswith("models/") or "\\" in value or ":" in value or
            any(p in ("", ".", "..") for p in parts) or PurePosixPath(value).is_absolute()):
        raise ValueError("원격 모델 경로가 잘못되었습니다.")
    return value


def remote_rows(client):
    rows = []
    for row in client.read_manifest():
        if row.get("type") != "model": continue
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))): continue
        try:
            remote_path(row["path"])
            identifier = row.get("model_id") or row["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", str(identifier)): continue
            rows.append({**row, "id": identifier, "name": name(row.get("name") or Path(row["path"]).stem),
                         "format": row.get("format", "tcn")})
        except (ValueError, KeyError): continue
    return rows


def upload(client, root, identifier, display_name):
    rename(root, identifier, display_name)
    row = local(root, identifier)
    target = folder(root, identifier)
    base = f"models/{identifier}"
    model_path = base + "/" + row["model_file"]
    entry = {**row, "type": "model", "model_id": identifier, "path": model_path,
             "size_bytes": (target / row["model_file"]).stat().st_size}
    uploads = [(target / row["model_file"], model_path)]
    if row["format"] == "tcn":
        entry["card_path"] = base + "/model.json"
        uploads.append((target / "model.json", entry["card_path"]))
    manifest = [r for r in client.read_manifest() if r.get("model_id") != identifier]
    client.push_files(uploads, hub.merge_manifest(manifest, [entry]), "Share named MANUX model")


def download(client, root, identifier):
    matches = [r for r in remote_rows(client) if r["id"] == identifier]
    if len(matches) != 1: raise ValueError("원격 모델 목록을 새로고침하고 다시 선택하세요.")
    row = matches[0]
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as temporary:
        path = Path(temporary) / ("model.json" if row["format"] == "spatial" else "model.onnx")
        client.download_file(remote_path(row["path"]), path)
        if hub.file_sha256(path) != row["sha256"]: raise ValueError("다운로드 모델 해시가 다릅니다.")
        if row["format"] != "spatial":
            client.download_file(remote_path(row["card_path"]), path.with_suffix(".json"))
            if row.get("card_sha256") and hub.file_sha256(path.with_suffix(".json")) != row["card_sha256"]:
                raise ValueError("다운로드 카드 해시가 다릅니다.")
        info = checked(path)
        expected = sha256((info["sha256"] + info["card_sha256"]).encode()).hexdigest()
        if row.get("model_id") and expected != row["model_id"]: raise ValueError("원격 모델 식별자가 다릅니다.")
        return add(root, path, row["name"])
