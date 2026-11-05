from copy import deepcopy
from hashlib import sha256
import json
import sys
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from manux.ml.card import ModelError
from manux.ml.predictor import load_model
from manux.ml import store

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import promote_tcn


class Session:
    def get_inputs(self):
        return [SimpleNamespace(name="x", shape=[1, 1, 133])]
    def get_outputs(self):
        return [SimpleNamespace(name="logits", shape=[1, 2])]
    def run(self, names, feeds):
        return [np.array([[3., 0.]])]


def candidate(folder, data=b"synthetic model"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "model.onnx"
    path.write_bytes(data)
    raw = dict(classes=["none", "rock"], window=1, features=133, feature_version="local3d-v1",
               threshold=.7, frames_in_a_row=2, trained="synthetic", kind="personal",
               data=[{"sha256": "a" * 64}], held_out_results=[], gestures={"rock": "custom.rock"},
               decisions={"rock": {"kind": "tap", "modes": ["pinch"], "point": "none"}},
               model_sha256=sha256(data).hexdigest())
    path.with_suffix(".json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_model(path, "local3d-v1", 133, session_factory=lambda _: Session())
    return loaded


def evidence(model):
    metric = {"per_gesture": {"rock": {"attempts": 3, "tcn_hits": 2}}, "neutral_seconds": 10.,
              "tcn_misfires_per_minute": 0., "wrong_events": 0}
    return dict(schema=store.REPORT_SCHEMA, model_sha256=sha256(model.path.read_bytes()).hexdigest(),
                card_sha256=sha256(model.path.with_suffix(".json").read_bytes()).hexdigest(),
                evaluation_sha256=["b" * 64], candidate=deepcopy(metric), baseline=deepcopy(metric))


def test_install_is_an_immutable_version_with_a_checked_atomic_pointer(tmp_path):
    model = candidate(tmp_path / "input")
    folder = tmp_path / "store"
    path = store.install(folder, model, evidence(model), None)
    first = store.active_token(folder)
    assert path.read_bytes() == model.path.read_bytes()
    loaded = store.load_active(folder, "local3d-v1", 133, session_factory=lambda _: Session())
    assert loaded.card.gestures == model.card.gestures
    changed = candidate(tmp_path / "input2", b"synthetic second model")
    second_path = store.install(folder, changed, evidence(changed), first)
    assert second_path != path and path.exists()
    assert store.active_token(folder) != first
    assert not (folder / ".install.lock").exists()


@pytest.mark.parametrize("failure", ["recall", "misfires", "wrong", "unmeasured", "overlap", "changed", "omitted"])
def test_failed_validation_preserves_the_active_model(tmp_path, failure):
    model = candidate(tmp_path / "input")
    folder = tmp_path / "store"
    path = store.install(folder, model, evidence(model), None)
    before = store.active_token(folder)
    report = evidence(model)
    if failure == "recall": report["candidate"]["per_gesture"]["rock"]["tcn_hits"] = 1
    if failure == "misfires": report["candidate"]["tcn_misfires_per_minute"] = 1.
    if failure == "wrong": report["candidate"]["wrong_events"] = 1
    if failure == "unmeasured": report["candidate"]["neutral_seconds"] = 0.
    if failure == "overlap": report["evaluation_sha256"] = ["a" * 64]
    if failure == "changed": report["model_sha256"] = "c" * 64
    if failure == "omitted": report["baseline"]["per_gesture"] = {}
    with pytest.raises(ValueError):
        store.install(folder, model, report, before)
    assert store.active_token(folder) == before and path.read_bytes() == model.path.read_bytes()
    assert not (folder / ".install.lock").exists()


def test_failed_pointer_commit_and_stale_baseline_preserve_active(tmp_path, monkeypatch):
    model = candidate(tmp_path / "input")
    folder = tmp_path / "store"
    store.install(folder, model, evidence(model), None)
    before = store.active_token(folder)
    with pytest.raises(ValueError, match="changed during evaluation"):
        store.install(folder, model, evidence(model), None)
    def fail_replace(*args):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(store.os, "replace", fail_replace)
    with pytest.raises(OSError):
        store.install(folder, model, evidence(model), before)
    assert store.active_token(folder) == before
    assert not (folder / ".install.lock").exists()


def test_active_model_tampering_and_path_traversal_are_rejected(tmp_path):
    model = candidate(tmp_path / "input")
    folder = tmp_path / "store"
    path = store.install(folder, model, evidence(model), None)
    path.write_bytes(b"wrong model")
    with pytest.raises(ModelError, match="checksum"):
        store.load_active(folder, "local3d-v1", 133, session_factory=lambda _: Session())
    (folder / "active.json").write_text(json.dumps({"schema": store.SCHEMA, "version": "../input"}), encoding="utf-8")
    with pytest.raises(ModelError):
        store.load_active(folder, "local3d-v1", 133, session_factory=lambda _: Session())


class GeometricPredictor:
    classes = ("none", "rock")
    def __call__(self, window):
        return np.array([.99, .01]) if window[-1, 126] > .4 else np.array([.01, .99])


def eval_recording(path, split="eval"):
    lines = [{"type": "meta", "schema": "hand-mouse-recording/1", "mode": "guided",
              "split": split, "person": "synthetic", "camera": {"mode": [640, 480, 30], "mirror": False}}]
    lm = [[.4 + i * .005, .6 - i * .005, 0.] for i in range(21)]
    for i, label in enumerate(["none"] * 10 + ["rock"] * 10 + ["none"] * 10):
        world = [[j % 5 * .01, j // 5 * .01, j % 2 * .001] for j in range(21)]
        world[0], world[9], world[5], world[17] = [0., 0., 0.], [0., .1, 0.], [-.04, .08, 0.], [.04, .08, 0.]
        world[8] = [-.04, .08, 0.]
        world[4] = world[8] if label == "rock" else [.15, .08, 0.]
        lines.append({"type": "frame", "t": i / 30, "lm": lm, "world": world, "hand": ["Right", .95],
                      "label": {"target": label, "gesture": "custom.rock" if label == "rock" else None},
                      "rule": {"action": "none"}})
    lines.append({"type": "end", "frames": 30})
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n", encoding="utf-8")
    return path


def test_evaluation_replays_the_direct_runtime_before_first_install(tmp_path):
    model = replace(candidate(tmp_path / "input"), predictor=GeometricPredictor())
    recording = eval_recording(tmp_path / "eval.jsonl")
    path, report = promote_tcn.validate_and_install(model.path, [recording], tmp_path / "store",
                                                   loader=lambda *args: model)
    assert path.is_file()
    assert report["candidate"]["per_gesture"]["rock"] == {"attempts": 1, "tcn_hits": 1}
    assert report["candidate"]["tcn_misfires_per_minute"] == 0
    assert report["baseline"]["per_gesture"]["rock"]["tcn_hits"] == 0


def test_training_split_and_training_hash_overlap_cannot_install(tmp_path):
    model = replace(candidate(tmp_path / "input"), predictor=GeometricPredictor())
    recording = eval_recording(tmp_path / "train.jsonl", "train")
    folder = tmp_path / "store"
    with pytest.raises(ValueError, match="split=eval"):
        promote_tcn.validate_and_install(model.path, [recording], folder, loader=lambda *args: model)
    assert store.active_token(folder) is None
    recording = eval_recording(tmp_path / "eval.jsonl")
    raw = json.loads(model.path.with_suffix(".json").read_text())
    raw["data"] = [{"sha256": sha256(recording.read_bytes()).hexdigest()}]
    model.path.with_suffix(".json").write_text(json.dumps(raw), encoding="utf-8")
    changed = replace(load_model(model.path, "local3d-v1", 133, session_factory=lambda _: Session()),
                      predictor=GeometricPredictor())
    with pytest.raises(ValueError, match="overlap"):
        promote_tcn.validate_and_install(model.path, [recording], folder, loader=lambda *args: changed)
    assert store.active_token(folder) is None


def test_runtime_reload_releases_output_and_keeps_the_live_model_on_bad_update(tmp_path, monkeypatch):
    from manux import desktop
    from manux.app import schema
    from manux.settings.store import Settings
    from manux.core.contracts import ActionCommand, FrameInfo, OutputTarget, Stage
    from manux.engine.commands import SetStage
    current = candidate(tmp_path / "first")
    live = []
    loads = []
    folder = tmp_path / "store"
    store.install(folder, current, evidence(current), None)
    def load(*args):
        loads.append(True)
        if live:
            assert not live[0].engine.sink().holding()  # release happens before model IO
        if store.active_token(folder) == b"broken":
            raise ModelError("invalid")
        return current
    monkeypatch.setattr(desktop, "load_active", load)
    runtime = desktop.build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False,
                            tcn_store=folder, tcn_mode="direct")
    live.append(runtime)
    runtime.engine.commands.put(SetStage(Stage.TUTORIAL))
    runtime.engine.step(FrameInfo(0, 1., 640, 480, False), None)
    sink = runtime.engine.sinks[OutputTarget.PRACTICE]
    sink.apply(ActionCommand(1., "mouse.drag", "begin", "pinch.drag", point=(.4, .4)))
    assert sink.holding()
    current = candidate(tmp_path / "second", b"second synthetic model")
    store.install(folder, current, evidence(current), store.active_token(folder))
    assert runtime.reload_model(force=True)
    assert not sink.holding()
    assert runtime.engine.features[-1].predictor is current.predictor
    snap = runtime.engine.step(FrameInfo(1, 1.03, 640, 480, False), None)
    assert "model:reloaded" in snap.events
    original_feature = runtime.engine.features[-1]
    (folder / "active.json").write_bytes(b"broken")
    assert not runtime.reload_model(force=True)
    assert runtime.engine.features[-1] is original_feature
    assert "기존 모델 유지" in runtime.model_status
    attempts = len(loads)
    assert not runtime.reload_model(force=True) and len(loads) == attempts
    runtime.stop()
    assert not runtime.reload_model(force=True)


def test_personal_file_cannot_enable_windows_input_without_verified_store(tmp_path, monkeypatch):
    from manux import desktop
    from manux.app import schema
    from manux.settings.store import Settings
    model = candidate(tmp_path / "input")
    monkeypatch.setattr(desktop, "load_model", lambda *args: model)
    with pytest.raises(ValueError, match="verified"):
        desktop.build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=True,
                      tcn_model=model.path, tcn_mode="direct")


def test_real_onnx_model_validation_install_and_desktop_reload(tmp_path):
    pytest.importorskip("onnxruntime")
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper
    from manux import desktop
    from manux.app import schema
    from manux.settings.store import Settings

    def graph_candidate(folder, margin):
        model = candidate(folder)
        x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 1, 133])
        y = helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 2])
        constants = [helper.make_tensor("start", TensorProto.INT64, [1], [126]),
                     helper.make_tensor("end", TensorProto.INT64, [1], [127]),
                     helper.make_tensor("axis", TensorProto.INT64, [1], [2]),
                     helper.make_tensor("reduce_axis", TensorProto.INT64, [1], [1]),
                     helper.make_tensor("margin", TensorProto.FLOAT, [1], [margin]),
                     helper.make_tensor("scale", TensorProto.FLOAT, [1], [10.])]
        nodes = [helper.make_node("Slice", ["x", "start", "end", "axis"], ["distance"]),
                 helper.make_node("ReduceMean", ["distance", "reduce_axis"], ["d"], keepdims=0),
                 helper.make_node("Sub", ["d", "margin"], ["delta"]),
                 helper.make_node("Mul", ["delta", "scale"], ["none"]),
                 helper.make_node("Neg", ["none"], ["rock"]),
                 helper.make_node("Concat", ["none", "rock"], ["logits"], axis=1)]
        graph = helper.make_model(helper.make_graph(nodes, "synthetic", [x], [y], initializer=constants),
                                  opset_imports=[helper.make_opsetid("", 18)])
        graph.ir_version = min(graph.ir_version, 10)
        onnx.save(graph, str(model.path))
        raw = dict(model.card.raw)
        raw["model_sha256"] = sha256(model.path.read_bytes()).hexdigest()
        model.path.with_suffix(".json").write_text(json.dumps(raw), encoding="utf-8")
        return model.path

    recording = eval_recording(tmp_path / "eval.jsonl")
    model = graph_candidate(tmp_path / "first", .4)
    folder = tmp_path / "store"
    _, report = promote_tcn.validate_and_install(model, [recording], folder)
    assert report["candidate"]["per_gesture"]["rock"]["tcn_hits"] == 1
    runtime = desktop.build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False,
                            tcn_store=folder, tcn_mode="direct")
    first = runtime.engine.features[-1]
    second = graph_candidate(tmp_path / "second", .5)
    promote_tcn.validate_and_install(second, [recording], folder)
    assert runtime.reload_model(force=True)
    assert runtime.engine.features[-1] is not first
    runtime.stop()
