"""Freeze untouched classifiers and export one self-contained, flat ONNX ensemble.

Each component retains its own softmax. Routed class scores plus the minimum
component-neutral score are normalized by the runtime's final softmax(log(scores)).
Composition can change competition between classes: full independent evaluation
is still required before installation. No unselected weights are optimized.
"""
from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import re


def component_id(digest, classes):
    # Identical tensors can be exported for different labels; include semantics.
    return sha256((digest + json.dumps(classes, separators=(",", ":"))).encode()).hexdigest()


def _parts(path):
    import onnx
    from onnx.compose import add_prefix
    path = Path(path)
    card = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    digest = sha256(path.read_bytes()).hexdigest()
    if card.get("model_sha256") != digest:
        raise ValueError("분류기 파일이 검증 후 바뀌었습니다.")
    model = onnx.load(path, load_external_data=False)
    if model.functions or any(onnx.external_data_helper.uses_external_data(t) for t in model.graph.initializer):
        raise ValueError("외부 가중치/함수가 없는 자기완결 ONNX가 필요합니다.")
    if [(x.domain, x.version) for x in model.opset_import] != [("", 17)]:
        raise ValueError("선택 학습 결합에는 opset 17 모델이 필요합니다.")
    composition = card.get("composition")
    if composition is not None:
        if not isinstance(composition, dict) or composition.get("version") != 1:
            raise ValueError("지원하지 않는 분류기 구성입니다.")
        components = composition["components"]
        routes = composition["routes"]
    else:
        identifier = component_id(digest, card["classes"])
        prefix = f"c_{identifier}_"
        model = add_prefix(model, prefix)
        old_input = model.graph.input[0].name
        for index, node in enumerate(model.graph.node):
            node.name = prefix + str(index)
            for i, value in enumerate(node.input):
                if value == old_input:
                    node.input[i] = "features"
        components = [{"id": identifier, "classes": card["classes"], "output": model.graph.output[0].name}]
        routes = {name: identifier for name in card["gestures"]}
    parts = {}
    for component in components:
        identifier = component["id"]
        if not re.fullmatch("[0-9a-f]{64}", identifier):
            raise ValueError("잘못된 분류기 식별자입니다.")
        prefix = f"c_{identifier}_"
        nodes = [copy.deepcopy(n) for n in model.graph.node if n.name.startswith(prefix)]
        initializers = [copy.deepcopy(t) for t in model.graph.initializer if t.name.startswith(prefix)]
        if not nodes or component["output"] not in {v for n in nodes for v in n.output}:
            raise ValueError("분류기 구성 요소를 찾지 못했습니다.")
        parts[identifier] = (copy.deepcopy(component), nodes, initializers)
    if any(identifier not in parts for identifier in routes.values()):
        raise ValueError("분류기 경로가 잘못되었습니다.")
    return card, routes, parts, model.ir_version


def compose(previous, replacements, output):
    """Only labels in replacement cards change owner; obsolete graphs are removed."""
    import numpy as np
    import onnx
    from onnx import helper as h, numpy_helper as nh, TensorProto
    paths = ([Path(previous)] if previous is not None else []) + list(map(Path, replacements))
    if not paths:
        raise ValueError("결합할 모델이 없습니다.")
    routes, parts, gestures, decisions, defaults, sources, identities = {}, {}, {}, {}, {}, {}, {}
    reference = None
    ir = 0
    for path in paths:
        card, incoming, blocks, version = _parts(path)
        dimensions = tuple(card[key] for key in ("feature_version", "window", "features"))
        if reference is not None and dimensions != reference:
            raise ValueError("기존 모델과 선택 모델의 특징/시간 창이 다릅니다.")
        reference = dimensions
        for name, gid in card["gestures"].items():
            if name in gestures and gestures[name] != gid:
                raise ValueError("기존 클래스의 동작 ID를 바꿀 수 없습니다.")
            gestures[name] = gid
            decisions[name] = {"threshold": card["threshold"], "frames_in_a_row": card["frames_in_a_row"],
                               **card["decisions"][name]}
            defaults[name] = card.get("class_default_modes", {}).get(name, "apply")
            identities[name] = card.get("class_model_ids", {}).get(name, card["model_sha256"])
        routes.update(incoming)
        parts.update(blocks)
        ir = max(ir, version)
        for row in card["data"]:
            sources[row["sha256"]] = row
    used = sorted(set(routes.values()))
    classes = ["none", *sorted(routes)]
    nodes, tensors, components = [], [], []
    for identifier in used:
        component, subnodes, subtensors = parts[identifier]
        nodes.extend(subnodes); tensors.extend(subtensors); components.append(component)
        nodes.append(h.make_node("Softmax", [component["output"]], ["prob_" + identifier], axis=1))
        tensors.append(nh.from_array(np.array([component["classes"].index("none")], np.int64), "neutral_idx_" + identifier))
        nodes.append(h.make_node("Gather", ["prob_" + identifier, "neutral_idx_" + identifier], ["neutral_" + identifier], axis=1))
    # Dynamic pairwise Minimum fails in the local Intel NPU compiler. Reduction
    # preserves the same minimum across component-neutral probabilities.
    nodes.append(h.make_node("Concat", ["neutral_" + identifier for identifier in used], ["neutral_scores"], axis=1))
    nodes.append(h.make_node("ReduceMin", ["neutral_scores"], ["score_none"], axes=[1], keepdims=1))
    scores = ["score_none"]
    for name in classes[1:]:
        identifier = routes[name]
        component = parts[identifier][0]
        tensors.append(nh.from_array(np.array([component["classes"].index(name)], np.int64), "idx_" + name))
        nodes.append(h.make_node("Gather", ["prob_" + identifier, "idx_" + name], ["score_" + name], axis=1))
        scores.append("score_" + name)
    nodes.append(h.make_node("Concat", scores, ["scores"], axis=1))
    tensors.append(nh.from_array(np.array(1e-8, np.float32), "epsilon"))
    nodes.append(h.make_node("Max", ["scores", "epsilon"], ["safe_scores"]))
    nodes.append(h.make_node("Log", ["safe_scores"], ["logits"]))
    graph = h.make_graph(nodes, "selected_gesture_components",
        [h.make_tensor_value_info("features", TensorProto.FLOAT, [1, reference[1], reference[2]])],
        [h.make_tensor_value_info("logits", TensorProto.FLOAT, [1, len(classes)])], initializer=tensors)
    model = h.make_model(graph, opset_imports=[h.make_opsetid("", 17)], ir_version=ir)
    onnx.checker.check_model(model)
    output = Path(output)
    onnx.save(model, output)
    # The last card supplies current training time/metrics; ownership and provenance
    # include all preserved classifiers, including their prior training hashes.
    result = {**card, "classes": classes, "gestures": gestures, "decisions": decisions,
              "data": list(sources.values()), "class_model_ids": identities, "class_default_modes": defaults,
              "composition": {"version": 1, "components": components, "routes": routes},
              "training_pipeline": "continuous-v2", "model_sha256": sha256(output.read_bytes()).hexdigest()}
    output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
