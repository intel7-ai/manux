"""Studio operations in MANUXWorker, isolated from the live engine and all Windows input."""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re

from manux.data import studio
from manux.data.recording import read, replay
from manux.core.contracts import RecordingIdentity


def history(store):
    from manux.ml.card import load_card, ModelError
    from manux.ml.store import active_token, validate_report
    from manux.perception.local3d import FEATURES, FEATURE_VERSION
    store = Path(store)
    token = active_token(store)
    try: active = json.loads(token).get("version") if token else None
    except (ValueError, AttributeError): active = None
    rows = []
    for path in sorted((store / "versions").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not re.fullmatch(r"[0-9a-f]{32}", path.name) or not path.resolve().is_relative_to(store.resolve()):
            continue
        try:
            card = load_card(path / "model.json", FEATURE_VERSION, FEATURES)
            validate_report(json.loads((path / "validation.json").read_text(encoding="utf-8")), card,
                            studio.digest(path / "model.onnx"), studio.digest(path / "model.json"))
            rows.append({"version": path.name, "trained": str(card.raw.get("trained", "")),
                         "artifact_id": hashlib.sha256((studio.digest(path / "model.onnx") + studio.digest(path / "model.json")).encode()).hexdigest(),
                         "classes": list(card.gestures), "active": path.name == active, "valid": True})
        except (OSError, ValueError, TypeError, ModelError):
            rows.append({"version": path.name, "trained": "손상된 버전", "classes": [], "active": path.name == active, "valid": False})
    return rows


def simulate(request, root, key, cancelled):
    from manux.app import build_engine, schema
    from manux.settings.store import Settings
    from manux.ml.store import active_token, load_active
    from manux.ml.predictor import load_model
    from manux.features import tcn_gate
    from manux.app import _FEATURES
    from manux.perception.local3d import FEATURES, FEATURE_VERSION
    from manux.core.contracts import OutputTarget

    result = studio.playback(root, key)
    rec = read(studio.recording_path(root, key))
    settings = Settings(schema(), request.get("settings", {}))
    model = load_active(Path(request["store"]), FEATURE_VERSION, FEATURES) if active_token(Path(request["store"])) else None
    filters = ()
    if model is None and request.get("simulation_model"):
        if request.get("simulation_mode") == "direct":
            model = load_model(Path(request["simulation_model"]), FEATURE_VERSION, FEATURES)
        else:
            eligible = {gid for module, _ in _FEATURES for gid in module.DEFAULT_BINDINGS
                        if module.GESTURE_PHASES[gid] & {"tap", "begin"}}
            gate = tcn_gate.create_gate(Path(request["simulation_model"]), settings.section("tcn_gate"), eligible=eligible)
            if gate.gate is None: raise ValueError("시뮬레이션의 TCN 확인기 모델을 읽을 수 없습니다.")
            filters = (gate.gate,)
    mode = request.get("preset", "pinch")
    if mode == "trigger": mode = "pointer"
    bindings = request.get("bindings")
    policies = None
    if model is not None:
        from manux.settings.tcn_policy import GesturePolicies
        policies = GesturePolicies(Path(request["policy_path"])).modes(model.card.raw) if request.get("policy_path") else None
        if not request.get("global_tcn_enabled", True): policies = {g:"rules" for g in model.card.gestures.values()}
    engines = {"rules": build_engine(settings, mode, binding_overrides=bindings),
               "model": build_engine(settings, mode, filters, direct_model=model, binding_overrides=bindings, policies=policies)}
    if model is not None:
        engines["preview"] = build_engine(settings, mode, direct_model=model, binding_overrides=bindings,
                                          policies={g:"apply" for g in model.card.gestures.values()})
    events, counts, errors = [], {name:0 for name in engines}, 0
    try:
        for index, item in enumerate(replay(rec)):
            if index % 100 == 0 and cancelled():
                raise ValueError("시뮬레이션을 취소했습니다.")
            for name, engine in engines.items():
                snap = engine.step(item.frame, item.hand)
                if snap.output is OutputTarget.WINDOWS:
                    raise RuntimeError("simulation output must never be Windows")
                frame = result["frames"][index]
                frame[name] = [{"action": a, "phase": p, "result": why} for a, p, why in snap.results if p != "hold"]
                frame[name + "_intents"] = [g + " · " + p for g, p in snap.intents if p != "hold"]
                frame[name + "_mode"] = snap.mode
                errors += len(snap.errors)
                for action, phase, why in snap.results:
                    if phase in ("tap", "begin"):
                        counts[name] += 1
                        events.append({"t": item.frame.t, "text": f"{name} · {action} · {phase} · {why}"})
    finally:
        for engine in engines.values(): engine.shutdown()
    result["events"] = sorted([*result["events"], *events], key=lambda e: e["t"])
    result["simulation"] = {"model": model.path.parent.name if model else "TCN 확인기" if filters else "없음 · 규칙 기준만 비교",
                            "counts": counts, "errors": errors,
                            "note": "현재 정책과 TCN을 모두 적용한 연습 출력을 구분합니다. 엔진·중재기·바인딩·중립 복귀·연속 확정을 재생하며 Windows 입력은 보내지 않습니다."}
    return result


def run(request, send, cancelled):
    root = Path(request["recordings_dir"])
    action, key = request["action"], request.get("key", "")
    if cancelled(): raise ValueError("작업을 취소했습니다.")
    if key and request.get("expected_sha256") and studio.digest(studio.recording_path(root, key)) != request["expected_sha256"]:
        raise ValueError("녹화가 변경되었습니다. 새로고침 후 다시 선택하세요.")
    send("progress", stage="녹화 검사 중" if action == "scan" else "처리 중", progress=.05)
    ready_store = None
    candidate = None
    if action == "scan":
        result = {"recordings": studio.library(root, cancelled,
            lambda i, n: send("progress", stage=f"녹화 검사 {i}/{n}", progress=i / max(1, n))),
                  "versions": history(request["store"])}
        if request.get("data_dir"):
            from model_library import add, scan
            library = Path(request["data_dir"]) / "models/library"
            sources = [Path(request["store"]) / "versions" / row["version"] / "model.onnx"
                       for row in result["versions"] if row["valid"]]
            sources += [Path(request.get("model_path", "")), Path(request["data_dir"]) / "models/spatial_mlp/spatial_pose_mlp.json"]
            for path in sources:
                if path.is_file():
                    try: add(library, path)
                    except (ValueError, OSError): pass
            result["models"] = scan(library)
    elif action in ("load", "simulate"):
        result = studio.playback(root, key) if action == "load" else simulate(request, root, key, cancelled)
    elif action == "select":
        studio.select_recording(root, key, request["included"])
        result = {"recordings": studio.library(root, cancelled)}
    elif action == "trash":
        note = studio.trash_recording(root, key)
        result = {"recordings": studio.library(root, cancelled), "note": note}
    elif action in ("clean", "augment"):
        created = studio.derive(root, key, action)
        result = {"recordings": studio.library(root, cancelled), "created": created,
                  "note": "파생본을 만들었습니다. 검수 후 ‘학습에 포함’을 직접 선택하세요. 원본은 보존됩니다."}
    elif action == "restore":
        from manux.data.training_center import recording_sets
        from promote_tcn import validate_and_install
        version_id = request.get("version", "")
        known = {r["version"] for r in history(request["store"]) if r["valid"]}
        if version_id not in known:
            raise ValueError("검증 가능한 저장 버전을 선택하세요.")
        _, evaluation = recording_sets(root, RecordingIdentity(request["profile"], request.get("preset", "pinch")))
        if not evaluation:
            raise ValueError("복원을 검증할 현재 별명의 별도 eval 녹화를 먼저 준비하세요.")
        mode = request.get("preset", "pinch")
        if mode == "trigger": mode = "pointer"
        validate_and_install(Path(request["store"]) / "versions" / version_id / "model.onnx", evaluation,
                             Path(request["store"]), mode=mode, cache_dir=Path(request["cache_dir"]), cancelled=cancelled)
        ready_store = request["store"]
        result = {"versions": history(request["store"]), "note": "현재 평가를 통과해 저장 버전을 새 버전으로 설치했습니다."}
    elif action in ("pose_upload", "pose_download"):
        import hub, pose_hub
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", request.get("profile", "")) and action == "pose_upload":
            raise ValueError("내 별명을 먼저 저장하세요.")
        try:
            client, repo, provider = hub.configured_client(request)
            source = Path(request["data_dir"]) / "pose_snapshots"
            if action == "pose_upload":
                client.check_upload_access()
                count = pose_hub.push(client, source, request["profile"], request.get("classes", ()), cancelled)
            else:
                count = pose_hub.pull(client, source, request.get("classes", ()), cancelled)
        except Exception:
            raise ValueError("포즈 공유 실패 · .env·권한·별명·데이터 형식·네트워크를 확인하세요.") from None
        result = {"note": f"{provider} · 선택 포즈 데이터 {count}개 {'업로드' if action == 'pose_upload' else '다운로드'} · train/eval 구분 유지"}
    elif action.startswith("model_"):
        import model_library as models
        library = Path(request["data_dir"]) / "models/library"
        identifier = request.get("identifier", "")
        if action in ("model_remote", "model_upload", "model_download"):
            import hub
            try:
                client, repo, provider = hub.configured_client(request)
                if action == "model_upload":
                    client.check_upload_access()
                    models.upload(client, library, identifier, request.get("name", ""))
                elif action == "model_download":
                    models.download(client, library, identifier)
                remote = models.remote_rows(client)
            except Exception:
                raise ValueError("모델 공유 실패 · .env의 저장소·읽기/쓰기 권한·네트워크와 파일 해시를 확인하세요.") from None
            result = {"models": models.scan(library), "remote_models": remote,
                      "note": f"{provider} · {repo} · 원격 모델 {len(remote)}개 · 다운로드는 사용 모델을 바꾸지 않습니다."}
        elif action == "model_import":
            models.add(library, Path(request["source"]))
            result = {"models": models.scan(library), "note": "모델 파일을 검증해 보관함에 복사했습니다. 원본과 active는 유지했습니다."}
        elif action == "model_rename":
            models.rename(library, identifier, request.get("name", ""))
            result = {"models": models.scan(library), "note": "모델 표시 이름을 저장했습니다."}
        elif action in ("model_preview", "model_apply"):
            row = models.local(library, identifier)
            path = models.folder(library, identifier) / row["model_file"]
            if row["format"] == "spatial":
                from manux.data.studio import atomic_json
                from manux.ml.spatial_mlp import SpatialMlpModel
                destination = Path(request["data_dir"]) / "models/spatial_mlp/spatial_pose_mlp.json"
                atomic_json(destination, SpatialMlpModel.load(path).to_dict())
                result = {"models": models.scan(library), "spatial_selected": True, "note": "포즈 미리보기 모델 선택 · TCN 제어 모델과 보조 차단 설정은 유지했습니다."}
            elif action == "model_preview":
                candidate = str(path)
                result = {"note": "TCN 모델 시험 선택 · 제어를 다시 켜면 강제 shadow로 비교합니다. 실제 입력에는 적용하지 않습니다."}
            else:
                from manux.data.training_center import recording_sets
                from manux.ml.store import active_token
                from promote_tcn import validate_and_install
                _, evaluation = recording_sets(root, RecordingIdentity(request["profile"], request.get("preset", "pinch")))
                if not evaluation: raise ValueError("현재 별명으로 별도 평가 녹화를 먼저 준비하세요.")
                mode = request.get("preset", "pinch"); mode = "pointer" if mode == "trigger" else mode
                validate_and_install(path, evaluation, Path(request["store"]), mode=mode,
                                     cache_dir=Path(request["cache_dir"]), expected_active=active_token(Path(request["store"])), cancelled=cancelled)
                ready_store = request["store"]
                result = {"versions": history(request["store"]), "note": "전체 독립 평가를 통과해 선택한 모델을 새 버전으로 설치했습니다. 동작별 시험/적용을 선택하세요."}
        else:
            raise ValueError("알 수 없는 모델 작업입니다.")
    elif action == "diagnostics":
        packages = []
        for name in ("numpy", "PySide6", "mediapipe", "onnxruntime", "torch", "huggingface-hub"):
            try: found = version(name)
            except PackageNotFoundError: found = "미설치"
            packages.append({"name": name, "value": found})
        result = {"diagnostics": packages, "versions": history(request["store"]),
                  "note": "로컬 구성 확인 완료. GPU/NPU 속도·카메라·실제 입력은 별도 실측 대상입니다."}
    else:
        raise ValueError("알 수 없는 스튜디오 작업입니다.")
    if cancelled() and ready_store is None: raise ValueError("작업을 취소했습니다.")
    if "versions" in result and request.get("data_dir"):
        from model_library import scan
        known = result.get("models") or scan(Path(request["data_dir"]) / "models/library")
        names = {row["id"]:row["name"] for row in known}
        for row in result["versions"]: row["name"] = names.get(row.get("artifact_id"), row["trained"])
    studio.atomic_json(Path(request["job_dir"]) / "result.json", result)
    send("done", stage=result.get("note", "완료"), action=action, job_id=request["job_id"],
         **({"store": ready_store} if ready_store else {}), **({"candidate": candidate} if candidate else {}))
