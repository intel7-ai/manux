"""Restore standalone studio data and convert its selected model registry without retraining."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

from manux.data.legacy_import import atomic_json, pending_model, stage_import


def _to_opset17(graph):
    """Convert the legacy torch export, including Pad-18 without its optional axes input."""
    import onnx
    try:
        return onnx.version_converter.convert_version(graph, 17)
    except RuntimeError:
        # ONNX has no Pad 18->17 adapter. The 18 change adds a fourth `axes` input;
        # the old three-input form retains Pad-13 semantics. Prove that this is the
        # only changed operator schema, reject subgraphs/functions, then check the
        # whole resulting graph. convert_models also compares actual ORT outputs.
        if graph.functions or len(graph.opset_import) != 1 or graph.opset_import[0].domain:
            raise
        for node in graph.graph.node:
            if node.domain or any(a.type in (onnx.AttributeProto.GRAPH, onnx.AttributeProto.GRAPHS) for a in node.attribute):
                raise ValueError("이전 모델의 하위 그래프/사용자 연산은 변환할 수 없습니다.")
            old = onnx.defs.get_schema(node.op_type, 18)
            new = onnx.defs.get_schema(node.op_type, 17)
            if old.since_version != new.since_version and not (
                    node.op_type == "Pad" and len(node.input) <= 3):
                raise ValueError("이전 모델 연산의 의미를 보존하는 변환이 필요합니다: " + node.op_type)
        result = onnx.ModelProto()
        result.CopyFrom(graph)
        result.opset_import[0].version = 17
        onnx.checker.check_model(result)
        return result


def convert_models(archive, report, previous=None):
    import numpy as np
    import onnx
    from compose_tcn import compose
    from manux.app import training_specs
    from manux.data.catalog import class_gestures
    from manux.ml.predictor import load_model
    from manux.perception.local3d import FEATURES, FEATURE_VERSION

    folder = archive / "models"
    base = folder / "gesture_tcn.onnx"
    paths = [base] if base.is_file() else []
    overrides = {}
    bank = folder / "gestures" / "manifest.json"
    if bank.is_file():
        for label, entry in json.loads(bank.read_text(encoding="utf-8")).get("models", {}).items():
            path = (folder / "gestures" / entry["model"]).resolve()
            if not path.is_relative_to((folder / "gestures").resolve()) or path.with_suffix(".json").name != entry["card"]:
                raise ValueError("이전 모델 은행의 파일 경로를 확인하세요.")
            if sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("이전 모델 은행의 파일 해시가 다릅니다.")
            overrides[label] = "gestures/" + entry["model"]
    selected = folder / "gesture_tcn.selected.json"
    if selected.is_file():
        doc = json.loads(selected.read_text(encoding="utf-8"))
        if doc.get("version") != 1 or not isinstance(doc.get("overrides"), dict):
            raise ValueError("이전 선택 모델 목록의 형식을 확인하세요.")
        overrides.update(doc["overrides"])
    for label, relative in overrides.items():
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError("선택 모델이 원본 모델 폴더 밖을 가리킵니다.")
        card = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if card.get("classes") != ["none", label]:
            raise ValueError("선택 모델 클래스와 목록이 다릅니다.")
        if path not in paths:
            paths.append(path)
    if not paths:
        return None
    converted = archive / "converted"
    converted.mkdir()
    mapping, specs = class_gestures(), training_specs()
    source_rows = {row["file"]: row for row in report["recordings"]}
    originals = {row["file"]: row for row in report["originals"]}
    replacements = []
    for index, path in enumerate(paths):
        loaded = load_model(path, FEATURE_VERSION, FEATURES)
        card = dict(loaded.card.raw)
        gestures = {label: mapping[label] for label in card["classes"]
                    if label != "none" and label in mapping and mapping[label] in specs}
        observed = sorted(set(card["classes"]) - {"none"} - set(gestures))
        if observed:
            report["warnings"].append("실행 계약이 없는 클래스는 예측만 보존: " + ", ".join(observed))
        if not gestures:
            raise ValueError("실행 선언이 있는 클래스가 없는 모델입니다. 원본을 보관했습니다.")
        sources = []
        for row in card.get("data", []):
            source = source_rows.get(row.get("file"))
            if source is None:
                # Archived originals still provide provenance even if not complete enough to train again.
                original = originals.get(row.get("file"))
                if original is None:
                    raise ValueError("이전 모델의 학습 원본 일부를 찾지 못했습니다. 원본 녹화가 있는 작업 폴더를 선택하세요.")
                sources.append(original)
            else:
                for digest in dict.fromkeys((source["source_sha256"], source["sha256"])):
                    sources.append({"file": row["file"], "sha256": digest})
        target = converted / f"component-{index}.onnx"
        shutil.copy2(path, target)
        graph = onnx.load(target, load_external_data=False)
        if [(o.domain, o.version) for o in graph.opset_import] == [("", 18)]:
            # The standalone torch exporter emitted opset 18. Convert the graph,
            # never merely relabel the opset, and check the original probabilities below.
            graph = _to_opset17(graph)
            onnx.checker.check_model(graph)
            onnx.save(graph, target)
        disabled = set(card.get("disabled_gestures", ())) - set(card.get("independent_gestures", ()))
        card.update(kind="personal", gestures=gestures,
                    decisions={label: {**specs[g],
                        "threshold": card.get("gesture_thresholds", {}).get(label, card["threshold"]),
                        "frames_in_a_row": card.get("gesture_frames", {}).get(label, card["frames_in_a_row"])}
                        for label, g in gestures.items()}, data=sources,
                    class_default_modes={label: "rules" if label in disabled else "shadow" for label in gestures},
                    legacy_model_mtime=path.stat().st_mtime,
                    model_sha256=sha256(target.read_bytes()).hexdigest(),
                    legacy_metrics={"held_out_results": card.get("held_out_results", []), "per_gesture": card.get("per_gesture", {}),
                                    "per_task": card.get("per_task", {})},
                    held_out_results=[], per_gesture={}, per_task={}, import_pending=True)
        atomic_json(target.with_suffix(".json"), card)
        checked = load_model(target, FEATURE_VERSION, FEATURES)
        rng = np.random.default_rng(21)
        for window in (np.zeros((card["window"], FEATURES), np.float32),
                       rng.normal(size=(card["window"], FEATURES)).astype(np.float32)):
            if not np.allclose(loaded.predictor(window), checked.predictor(window), rtol=1e-5, atol=1e-6):
                raise ValueError("변환한 모델의 예측이 원본과 다릅니다. 기존 모델을 유지합니다.")
        replacements.append(target)
    candidate = archive / "candidate.onnx"
    if previous is None and len(replacements) == 1:
        # Keep non-executable class logits (e.g. thumbs_up) and probability competition intact.
        shutil.copy2(replacements[0], candidate)
        shutil.copy2(replacements[0].with_suffix(".json"), candidate.with_suffix(".json"))
    else:
        compose(previous, replacements, candidate)
    load_model(candidate, FEATURE_VERSION, FEATURES)
    return candidate


def restore(source, data_dir, profile="", *, cancelled=lambda: False):
    from manux.app import schema
    from manux.settings.migrate_v03 import convert
    from manux.settings.store import Settings, load, save

    source, root = Path(source).resolve(), Path(data_dir).resolve()
    config = source / "config" / "gestures.json"
    old = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    profile = profile or old.get("pc_alias") or "local"
    previous = pending_model(root)
    store = root / "models/personal"
    if (store / "active.json").is_file():
        from manux.ml.store import load_active
        from manux.perception.local3d import FEATURE_VERSION, FEATURES
        previous = load_active(store, FEATURE_VERSION, FEATURES).path
    archive, report = stage_import(source, root, profile, cancelled=cancelled)
    candidate = convert_models(archive, report, previous) or previous
    from model_library import add
    spatial = archive / "models/spatial_mlp.json"
    if spatial.is_file():
        add(root / "models/library", spatial, "가져온 Spatial MLP")
        report["spatial_candidate"] = str(spatial.relative_to(root))
    if candidate:
        add(root / "models/library", candidate, "가져온 TCN")
    if cancelled():
        raise ValueError("가져오기를 중단했습니다. 기존 모델은 유지됩니다.")
    # Existing profiles include intentionally unstored current defaults. Importing
    # missing legacy keys would silently replace them (e.g. old fist gain 35x).
    # Migrate settings only for a genuinely new profile; data/model imports are additive.
    settings = load(schema(), root / "config")
    merged = settings if (root / "config/settings.json").is_file() else Settings(schema(), convert(old))
    save(merged, root / "config")
    preferences = root / "config" / "training-center.json"
    if not preferences.is_file():
        atomic_json(preferences, {"profile": report["profile"]})
    report["candidate"] = str(candidate.relative_to(root)) if candidate else ""
    atomic_json(archive / "import.json", report)
    atomic_json(root / "imports" / "latest.json", report)
    return report


def main(argv=None):
    from manux.settings.paths import resolve
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--data-dir", type=Path, default=resolve().data_dir)
    parser.add_argument("--profile", default="")
    args = parser.parse_args(argv)
    report = restore(args.source, args.data_dir, args.profile)
    print(json.dumps({"recordings": len(report["recordings"]), "plans": report["plans"],
                      "candidate": bool(report["candidate"]), "warnings": report["warnings"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
