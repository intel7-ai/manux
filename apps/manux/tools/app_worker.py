"""External training/HF worker; also built as MANUXWorker.exe. Never opens the desktop app."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import re
import sys

from manux.core.contracts import RecordingIdentity


class HubActionError(ValueError):
    """A fixed, credential-free message suitable for the desktop UI."""


def upload_access(client):
    """Return verified permission and a safe explanation, without remote exception text."""
    if not hasattr(client, "check_upload_access"):
        return None, "업로드 권한 확인 기능을 사용할 수 없습니다."
    try:
        client.check_upload_access()
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status in (401, 403):
            return False, "업로드 권한 없음 · HF 로그인 또는 쓰기 권한이 있는 토큰을 확인하세요."
        return None, "업로드 권한 확인 실패 · 네트워크와 인증 설정을 확인하세요."
    return True, "업로드 권한 확인"


def emit(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=True), file=sys.__stdout__, flush=True)


def check_cancel(request):
    if Path(request["cancel_path"]).exists():
        raise ValueError("작업을 취소했습니다. 사용 중 모델은 유지됩니다.")


def run_training(request, send=emit):
    import train_tcn
    from compose_tcn import compose
    from training_device import select, record
    execution = select(request.get("device", os.environ.get("MANUX_TRAIN_DEVICE", "auto")))
    send("progress", stage="학습 장치 · " + execution.actual, execution=record(execution))
    from promote_tcn import validate_and_install
    from tcn_data import load, class_mapping, class_names
    from manux.ml.store import active_token, load_active
    from manux.perception.local3d import FEATURES, FEATURE_VERSION

    from manux.data.training_center import recording_sets
    identity = RecordingIdentity(request["profile"], request.get("preset", "pinch"))
    found_train, found_eval = recording_sets(Path(request["recordings_dir"]), identity)
    train_paths = [Path(p) for p in request.get("train", found_train)]
    eval_paths = [Path(p) for p in request.get("eval", found_eval)]
    if not train_paths or not eval_paths:
        raise ValueError("학습용과 별도 평가용 녹화를 먼저 준비하세요.")
    check_cancel(request)
    cache = Path(request["cache_dir"])
    segments = load(train_paths, split="train", cache_dir=cache)
    all_classes = class_names(segments)
    from manux.features.tcn_gate import LEGACY_GESTURES
    mode = request.get("mode", "pinch")
    specs = {g: d for g, d in request["specs"].items() if mode in d["modes"] or "motion" in d["modes"]}
    mapping = class_mapping(segments, [c for c in all_classes if c == "none" or LEGACY_GESTURES.get(c) in specs or any(
        (f.get("label") or {}).get("target") == c and (f.get("label") or {}).get("gesture") in specs
        for s in segments for f in s.frames)])
    classes = ["none", *sorted(c for c, g in mapping.items() if g in specs)]
    if len(classes) < 2:
        raise ValueError("학습할 동작의 유효한 녹화가 없습니다.")
    focused = request.get("focus_labels", [request["focus_label"]] if request.get("focus_label") else [])
    if set(focused) - set(classes):
        raise ValueError("선택한 동작의 학습 녹화가 없습니다.")
    selected = sorted(set(focused or classes[1:]))
    if any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name == "none" for name in selected):
        raise ValueError("학습할 동작 이름이 잘못되었습니다.")
    store = Path(request["store"])
    token = active_token(store)
    current = None
    if token is not None:
        current = load_active(store, FEATURE_VERSION, FEATURES)
    elif request.get("previous_candidate"):
        from manux.ml.predictor import load_model
        current = load_model(Path(request["previous_candidate"]), FEATURE_VERSION, FEATURES)
    retained = set(current.card.gestures) if current else set()
    output_classes = {"none", *retained, *selected}
    evaluation = load(eval_paths, split="eval", cache_dir=cache)
    targets = {name for s in evaluation for name in s.labels()}
    if not output_classes <= targets:
        missing = ", ".join(sorted(output_classes - targets))
        raise ValueError(f"평가 녹화가 부족한 동작: {missing}. 별도 시도로 녹화하세요.")
    folder = Path(request["job_dir"])
    candidate = folder / "candidate.onnx"
    send("progress", stage="선택 동작 학습 준비 · 다른 분류기 유지", phase="loading", progress=.1, steps=len(selected),
         train_files=len(train_paths), eval_files=len(eval_paths), conditions=len({s.condition for s in segments}),
         frames=sum(len(s.frames) for s in segments))
    # The training CLI prints its legacy report; only explicit progress events leave this worker.
    class EventOutput:
        buffer = ""
        def write(self, text):
            self.buffer += text
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("event") == "epoch":
                    check_cancel(request)
                    send("progress", stage=f"선택 동작 학습 중 · {label}", phase="training",
                         progress=.1 + .6 * (step - 1 + row["epoch"] / row["epochs"]) / len(selected),
                         step=step, steps=len(selected), epoch=row["epoch"], epochs=row["epochs"], loss=row["loss"])
                elif isinstance(row, dict) and row.get("event") == "device":
                    send("progress", stage="학습 장치 · "+row["actual"], execution={k:row[k] for k in ("requested","actual","runtime","name","reason")})
            return len(text)
        def flush(self): pass
    replacements = []
    for step, label in enumerate(selected, 1):
        check_cancel(request)
        trained = folder / f"component-{label}.onnx"
        declarations = folder / f"decisions-{label}.json"
        declarations.write_text(json.dumps({label: specs[mapping[label]]}), encoding="utf-8")
        with redirect_stdout(EventOutput()):
            train_tcn.main([*[str(p) for p in train_paths], "--output", str(trained), "--classes", "none", label,
                            "--decisions", str(declarations), "--cache-dir", str(cache), "--negative-other-classes",
                            "--epochs", str(request.get("epochs", 20)), "--device", request.get("device", os.environ.get("MANUX_TRAIN_DEVICE","auto")), "--events", "--skip-folds"])
        replacements.append(trained)
    check_cancel(request)
    if active_token(store) != token:
        raise ValueError("학습 중 사용 모델이 바뀌었습니다. 다시 평가해 주세요.")
    send("progress", stage="선택 분류기 결합 중 · 기존 가중치 유지", phase="saving", progress=.75)
    compose(current.path if current else None, replacements, candidate)
    check_cancel(request)
    send("progress", stage="모든 동작의 별도 평가 녹화로 검증 중", phase="evaluating", progress=.8)
    installed, report = validate_and_install(candidate, eval_paths, store, mode=request.get("mode", "pinch"),
                                             cache_dir=cache, cancelled=lambda: Path(request["cancel_path"]).exists(), expected_active=token)
    send("done", stage="검증 통과 · 선택한 동작의 새 모델은 시험 모드", store=str(store),
         classes=len(output_classes) - 1, trained_labels=selected, metrics=report["candidate"], job_id=request["job_id"])


def run_hub(request, send=emit, client=None):
    import hub
    action = request["action"]
    # Only uploads belong to a person. Reading team data needs no recording alias.
    identity = RecordingIdentity(request["profile"], request.get("preset", "pinch")) if action == "upload" else None
    preset = request.get("preset", "pinch")
    if preset not in ("pinch", "pointer", "trigger"):
        raise ValueError("녹화 방식을 확인하세요.")
    if not getattr(sys, "frozen", False): hub.load_dotenv()
    hub.load_dotenv([Path(p) for p in request.get("env_files", ())], override=True)
    if client is None:
        client, repo, _provider = hub.configured_client(request)
    else:
        repo = os.environ.get(hub.ENV_REPO) or getattr(client, "repo_id", request.get("repo", hub.DEFAULT_REPO))
    root = Path(request["recordings_dir"])
    action = request["action"]
    check_cancel(request)
    send("progress", stage="팀 저장소 연결 중", progress=.1)
    classes = request.get("classes", ())
    if not isinstance(classes, (list, tuple)) or any(not re.fullmatch(r"[a-z][a-z0-9_]*", c) for c in classes):
        raise ValueError("공유할 동작을 다시 선택하세요.")
    can_upload = None
    if action == "upload":
        if hasattr(client, "check_upload_access"):
            can_upload, permission_note = upload_access(client)
            if can_upload is not True:
                raise HubActionError(permission_note)
        own = root / f"profile-{identity.profile.lower()}"
        if not own.resolve().is_relative_to(root.resolve()):
            raise ValueError("녹화 폴더 위치가 잘못되었습니다.")
        valid, errors = hub.scan_local_recordings(own)
        if errors or any(r.entry["person"] != identity.profile.lower() for r in valid):
            raise ValueError("내 별명의 완료된 녹화만 올릴 수 있습니다. 녹화 정보를 확인하세요.")
        if not valid:
            raise ValueError("내 별명으로 완료된 녹화가 없습니다.")
        result = hub.push_to_hub(client, own, classes=classes)
        message = f"업로드 완료 · 새 녹화 {result['new_recordings']}개"
    elif action == "download":
        result = hub.pull_from_hub(client, root, presets=[preset], classes=classes)
        message = f"팀 데이터 받기 완료 · {result['downloaded']}개"
    elif action == "status":
        result = hub.summarize_entries(client.read_manifest())
        can_upload, permission_note = upload_access(client)
        message = f"읽기 연결 성공 · 팀 녹화 {result['files']}개 · {permission_note}"
    else:
        raise ValueError("알 수 없는 허브 작업입니다.")
    send("done", stage=message, summary=result, repo=repo, read_access=True,
         upload_access=can_upload, job_id=request["job_id"])


def run_pose_training(request, send=emit):
    import numpy as np
    from manux.data.pose_studio import ACTION_IDS, PoseSnapshotStudio
    from manux.ml.spatial_mlp import train_spatial_mlp
    from manux.perception.spatial_features import extract_spatial_features
    from manux.data.studio import atomic_json
    from model_library import add
    studio = PoseSnapshotStudio(Path(request["data_dir"]))
    xs, ys, weights = [], [], []
    send("progress", stage="포즈 표본 준비 중", progress=.1, phase="loading")
    for sample in studio.load_all_samples():
        check_cancel(request)
        label = sample.get("groundTruthAction")
        if label not in ACTION_IDS: continue
        features = extract_spatial_features(sample.get("rawLandmarks"))
        if features is None: continue
        xs.append(features); ys.append(ACTION_IDS.index(label))
        weights.append(1.8 if sample.get("isFailureSample") else 1.)
    if len(xs) < 2: raise ValueError("유효한 포즈 표본을 2개 이상 모으세요.")
    send("progress", stage="포즈 MLP 학습 중 · 제어 모델은 유지", progress=.4, phase="training")
    from manux.data.pose_studio import pose_training_partition
    tx, ty, tw, validation = pose_training_partition(np.stack(xs), np.asarray(ys), np.asarray(weights))
    model, metrics = train_spatial_mlp(tx, ty, tw, validation_data=validation, classes=ACTION_IDS)
    check_cancel(request)
    from manux.data.pose_studio import summarize_pose_fit
    metrics = summarize_pose_fit(model, metrics, np.stack(xs), np.asarray(ys))
    # Serialize atomically. This is a studio candidate, never the TCN active pointer.
    path = studio.models_dir / "spatial_pose_mlp.json"
    atomic_json(path, model.to_dict())
    add(Path(request["data_dir"]) / "models/library", path, "포즈 MLP · " + metrics.trained_at)
    send("done", stage="포즈 학습 완료 · 프레임 참고 점수이며 제어에는 자동 적용하지 않습니다",
         job_id=request["job_id"], spatial_candidate=str(path))


def run_import(request, send=emit):
    from import_training import restore
    send("progress", stage="이전 녹화·설정·선택 모델을 로컬로 가져오는 중", phase="loading", progress=.1)
    report = restore(Path(request["source"]), Path(request["data_dir"]), request.get("profile", ""),
                     cancelled=lambda: Path(request["cancel_path"]).exists())
    send("done", stage=f"이전 작업 복구 · 녹화 {len(report['recordings'])}개 · 모델은 별도 평가 전 시험 상태",
         candidate=str(Path(request["data_dir"]) / report["candidate"]) if report["candidate"] else "",
         profile=report["profile"], job_id=request["job_id"])


def run_validate_import(request, send=emit):
    from promote_tcn import validate_and_install
    from manux.data.training_center import recording_sets
    from manux.ml.store import active_token
    identity = RecordingIdentity(request["profile"], request.get("preset", "pinch"))
    _, paths = recording_sets(Path(request["recordings_dir"]), identity)
    if not paths:
        raise ValueError("가져온 녹화는 학습용입니다. 별도 평가용(eval) 시도를 새로 녹화한 뒤 검증하세요.")
    send("progress", stage="가져온 모델을 별도 평가 녹화로 검증 중", phase="evaluating", progress=.2)
    store = Path(request["store"])
    _, report = validate_and_install(Path(request["candidate"]), paths, store, mode=request.get("mode", "pinch"),
                                    cache_dir=Path(request["cache_dir"]), expected_active=active_token(store),
                                    cancelled=lambda: Path(request["cancel_path"]).exists())
    send("done", stage="가져온 모델 검증 통과 · 동작별 시험/적용 선택 가능", store=str(store),
         metrics=report["candidate"], job_id=request["job_id"])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path)
    parser.add_argument("--self-check", type=Path)
    args = parser.parse_args(argv)
    if args.self_check is not None:
        import torch, onnxruntime, huggingface_hub
        args.self_check.write_text(json.dumps({"worker": True, "torch": torch.__version__,
                                               "onnxruntime": onnxruntime.__version__, "hub": huggingface_hub.__version__}), encoding="utf-8")
        return 0
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if request.get("schema") != "manux-worker-job/1":
            raise ValueError("지원하지 않는 작업 요청입니다.")
        if request["kind"] == "train":
            run_training(request)
        elif request["kind"] == "pose_train":
            run_pose_training(request)
        elif request["kind"] == "import":
            run_import(request)
        elif request["kind"] == "validate_import":
            run_validate_import(request)
        elif request["kind"] == "hub":
            try:
                run_hub(request)
            except HubActionError:
                raise
            except Exception:
                # Never echo HTTP headers, auth tokens or remote exception bodies.
                raise ValueError("허브 작업 실패 · 저장소 주소, 읽기/쓰기 권한, 토큰과 네트워크를 확인하세요.") from None
        elif request["kind"] == "studio":
            from studio_worker import run
            run(request, emit, lambda: Path(request["cancel_path"]).exists())
        else:
            raise ValueError("알 수 없는 작업입니다.")
        return 0
    except Exception as error:
        text = str(error) if isinstance(error, ValueError) else "작업 구성요소 또는 파일을 확인하세요."
        emit("error", message=text)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
