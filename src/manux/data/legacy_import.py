"""Local, additive migration of the standalone training studio. No network or model execution."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from manux.core.contracts import RecordingIdentity
from manux.data import capture_plan
from manux.data.recording import read


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pending_model(data_dir):
    root = Path(data_dir).resolve()
    try:
        report = json.loads((root / "imports" / "latest.json").read_text(encoding="utf-8"))
        path = (root / report["candidate"]).resolve()
        if path.is_relative_to(root / "imports") and path.is_file():
            return path
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def stage_import(source, data_dir, profile, *, cancelled=lambda: False):
    """Archive originals byte-for-byte; add complete recordings as train, retaining explicit splits.

    Missing legacy roles are train only. The frame/event lines are copied unchanged.
    Existing plans and existing recordings win; repeating an import cannot duplicate a recording.
    A caller publishes latest.json only after validating/converting the models.
    """
    source, root = Path(source).resolve(), Path(data_dir).resolve()
    identity = RecordingIdentity(profile.strip().lower())
    if root == source or root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("원본 작업 폴더와 사용자 데이터 폴더는 서로 분리되어야 합니다.")
    capture_plan._check_outside_repo(root)
    if not (source / "recordings").is_dir() or not (source / "config").is_dir():
        raise ValueError("recordings와 config가 있는 이전 작업 폴더를 선택하세요.")
    archive = root / "imports" / uuid4().hex
    archive.mkdir(parents=True)
    report = {"schema": "manux-training-import/1", "profile": identity.profile,
              "archive": str(archive.relative_to(root)), "recordings": [], "originals": [], "warnings": [], "plans": []}

    def check():
        if cancelled():
            raise ValueError("가져오기를 중단했습니다. 원본과 기존 모델은 유지됩니다.")

    # Only the named app data, never .env, credentials, screenshots or executable code.
    for folder, patterns in (("models", ("*.onnx", "*.json")),
                             ("config", ("gestures.json", "recording-plans.json", "capture-plans.json", "tcn-ui.ini"))):
        for pattern in patterns:
            for file in sorted((source / folder).rglob(pattern)):
                check()
                if not file.resolve().is_relative_to(source / folder):
                    raise ValueError("원본 폴더 밖을 가리키는 파일은 가져올 수 없습니다.")
                target = archive / file.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
    for file in sorted((source / "recordings").rglob("*.jsonl.gz")):
        check()
        if not file.resolve().is_relative_to(source / "recordings"):
            raise ValueError("원본 폴더 밖의 녹화는 가져올 수 없습니다.")
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        original = archive / "recordings" / (digest + ".jsonl.gz")
        original.parent.mkdir(exist_ok=True)
        shutil.copy2(file, original)
        if hashlib.sha256(original.read_bytes()).hexdigest() != digest:
            raise ValueError("가져오는 동안 원본 녹화가 바뀌었습니다. 녹화를 끝낸 뒤 다시 시도하세요.")
        report["originals"].append({"file": file.name, "sha256": digest})
        rec = read(original)
        if not rec.complete or rec.bad_lines:
            report["warnings"].append(f"{file.name}: 미완료/손상 녹화는 원본만 보관")
            continue
        split = rec.meta.get("split") or "train"
        if split not in ("train", "eval"):
            report["warnings"].append(f"{file.name}: 알 수 없는 용도, 원본만 보관")
            continue
        person = rec.meta.get("person") or rec.meta.get("profile") or identity.profile
        rec_identity = RecordingIdentity(str(person).lower(), rec.meta.get("preset") or "pinch", split)
        target = root / "recordings" / "imported" / (digest + ".jsonl.gz")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".part")
            try:
                with gzip.open(original, "rt", encoding="utf-8") as src, gzip.open(temporary, "wt", encoding="utf-8") as dst:
                    meta = json.loads(next(src))
                    meta.update(person=rec_identity.profile, profile=rec_identity.profile,
                                preset=rec_identity.preset, split=split,
                                imported_from={"sha256": digest, "file": file.name, "role_assigned": not rec.meta.get("split")})
                    dst.write(json.dumps(meta, ensure_ascii=False) + "\n")
                    shutil.copyfileobj(src, dst)
                check()
                os.replace(temporary, target)
                shutil.copystat(original, target)
            finally:
                temporary.unlink(missing_ok=True)
        report["recordings"].append({"file": file.name, "source_sha256": digest,
                                     "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                                     "path": str(target.relative_to(root)), "split": split})
    destination = capture_plan.plans_file(root / "config")
    existing = capture_plan._read(destination)["plans"]
    for name in ("recording-plans.json", "capture-plans.json"):
        old = archive / "config" / name
        if not old.exists():
            continue
        for key, plan in capture_plan._read(old)["plans"].items():
            check()
            if key not in existing:
                capture_plan.save_plan(plan, key, destination)
                existing[key] = plan
            if capture_plan.normalise(existing[key]) == capture_plan.normalise(plan):
                report["plans"].append(key)
    atomic_json(archive / "import.json", report)
    return archive, report
