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


def emit(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=True), file=sys.__stdout__, flush=True)


def check_cancel(request):
    if Path(request["cancel_path"]).exists():
        raise ValueError("작업을 취소했습니다. 사용 중 모델은 유지됩니다.")


def run_training(request, send=emit):
    import train_tcn
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
    specs = {g: d for g, d in request["specs"].items() if mode in d["modes"]}
    mapping = class_mapping(segments, [c for c in all_classes if c == "none" or LEGACY_GESTURES.get(c) in specs or any(
        (f.get("label") or {}).get("target") == c and (f.get("label") or {}).get("gesture") in specs
        for s in segments for f in s.frames)])
    classes = ["none", *sorted(c for c, g in mapping.items() if g in specs)]
    if len(classes) < 2:
        raise ValueError("학습할 동작의 유효한 녹화가 없습니다.")
    focused = request.get("focus_labels", [request["focus_label"]] if request.get("focus_label") else [])
    if set(focused) - set(classes):
        raise ValueError("선택한 동작의 학습 녹화가 없습니다.")
    store = Path(request["store"])
    token = active_token(store)
    if token is not None:
        current = load_active(store, FEATURE_VERSION, FEATURES)
        if not set(current.card.gestures) <= set(classes):
            raise ValueError("기존 동작의 학습 데이터가 부족합니다. 팀 데이터를 받거나 다시 녹화하세요.")
    evaluation = load(eval_paths, split="eval", cache_dir=cache)
    targets = {name for s in evaluation for name in s.labels()}
    if not set(classes) <= targets:
        missing = ", ".join(sorted(set(classes) - targets))
        raise ValueError(f"평가 녹화가 부족한 동작: {missing}. 별도 시도로 녹화하세요.")
    folder = Path(request["job_dir"])
    candidate = folder / "candidate.onnx"
    declarations = folder / "decisions.json"
    declarations.write_text(json.dumps({c: specs[mapping[c]] for c in classes if c != "none"}), encoding="utf-8")
    send("progress", stage="통합 모델 학습 중 · 기존 동작 함께 유지", progress=.1)
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
                    send("progress", stage="통합 모델 학습 중", progress=.1 + .6 * row["epoch"] / row["epochs"],
                         epoch=row["epoch"], epochs=row["epochs"], loss=row["loss"])
            return len(text)
        def flush(self): pass
    with redirect_stdout(EventOutput()):
        train_tcn.main([*[str(p) for p in train_paths], "--output", str(candidate), "--classes", *classes,
                        "--decisions", str(declarations), "--cache-dir", str(cache),
                        "--epochs", str(request.get("epochs", 20)), "--events", "--skip-folds"])
    check_cancel(request)
    if active_token(store) != token:
        raise ValueError("학습 중 사용 모델이 바뀌었습니다. 다시 평가해 주세요.")
    send("progress", stage="별도 평가 녹화로 검증 중", progress=.8)
    installed, report = validate_and_install(candidate, eval_paths, store, mode=request.get("mode", "pinch"),
                                             cache_dir=cache, cancelled=lambda: Path(request["cancel_path"]).exists())
    send("done", stage="검증 통과 · 모델 적용 요청 완료", store=str(store),
         classes=len(classes) - 1, metrics=report["candidate"], job_id=request["job_id"])


def run_hub(request, send=emit, client=None):
    import hub
    identity = RecordingIdentity(request["profile"], request.get("preset", "pinch"))
    if not getattr(sys, "frozen", False):
        hub.load_dotenv()
    hub.load_dotenv([Path(path) for path in request.get("env_files", ())], override=True)
    repo = os.environ.get(hub.ENV_REPO) or request.get("repo") or hub.DEFAULT_REPO
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("HF 저장소는 소유자/저장소 형식으로 입력하세요.")
    client = client or hub.HfDatasetHub(repo, create_repo=False)
    root = Path(request["recordings_dir"])
    action = request["action"]
    check_cancel(request)
    send("progress", stage="팀 저장소 연결 중", progress=.1)
    if action == "upload":
        own = root / f"profile-{identity.profile.lower()}"
        if not own.resolve().is_relative_to(root.resolve()):
            raise ValueError("녹화 폴더 위치가 잘못되었습니다.")
        valid, errors = hub.scan_local_recordings(own)
        if errors or any(r.entry["person"] != identity.profile.lower() for r in valid):
            raise ValueError("내 별명의 완료된 녹화만 올릴 수 있습니다. 녹화 정보를 확인하세요.")
        if not valid:
            raise ValueError("내 별명으로 완료된 녹화가 없습니다.")
        result = hub.push_to_hub(client, own)
        message = f"업로드 완료 · 새 녹화 {result['new_recordings']}개"
    elif action == "download":
        result = hub.pull_from_hub(client, root, presets=[identity.preset])
        message = f"팀 데이터 받기 완료 · {result['downloaded']}개"
    elif action == "status":
        result = hub.summarize_entries(client.read_manifest())
        if hasattr(client, "check_upload_access"):
            client.check_upload_access()
        message = f"읽기·업로드 권한 확인 · 팀 녹화 {result['files']}개"
    else:
        raise ValueError("알 수 없는 허브 작업입니다.")
    send("done", stage=message, summary=result, repo=repo, job_id=request["job_id"])


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
        elif request["kind"] == "hub":
            try:
                run_hub(request)
            except Exception:
                # Never echo HTTP headers, auth tokens or remote exception bodies.
                raise ValueError("허브 작업 실패 · 저장소 주소, 읽기/쓰기 권한, 토큰과 네트워크를 확인하세요.") from None
        else:
            raise ValueError("알 수 없는 작업입니다.")
        return 0
    except Exception as error:
        text = str(error) if isinstance(error, ValueError) else "작업 구성요소 또는 파일을 확인하세요."
        emit("error", message=text)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
