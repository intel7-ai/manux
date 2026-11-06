"""Composition: pose assist is off by default, attaches only when asked for, and a bad model never stops the app."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "features"))
from test_pose import CLASSES, GESTURES, FakeModel, make_card  # noqa: E402
from test_tcn import Predictor as TcnPredictor, card as tcn_card  # noqa: E402

from manux import app  # noqa: E402
from manux.app import _load_pose_assist, build_engine, schema  # noqa: E402
from manux.features import pose  # noqa: E402
from manux.features.pose import PoseAssist, PoseFeature, PoseGuard, create_pose_assist  # noqa: E402
from manux.ml.predictor import LoadedModel  # noqa: E402
from manux.settings.store import Settings  # noqa: E402

INFO = {"classes": CLASSES, "gestures": GESTURES, "role": "pose_evidence", "kind": "personal", "window": 1,
        "features": 33, "feature_version": "pose-angles-v1", "threshold": 0.9, "frames_in_a_row": 3,
        "trained": "x", "data": [], "held_out_results": []}


class Session:
    """What onnxruntime would give for a window-1 model of 33 values and 4 classes."""

    def __init__(self, window=1, classes=4):
        self.window, self.classes = window, classes

    def get_inputs(self):
        return [SimpleNamespace(name="features", shape=["batch", self.window, 33])]

    def get_outputs(self):
        return [SimpleNamespace(name="logits", shape=["batch", self.classes])]

    def run(self, names, feeds):
        return [np.array([[0.0, 6.0, 0.0, 0.0][:self.classes]])]


def model_files(tmp_path, **info):
    onnx = tmp_path / "pose.onnx"
    onnx.write_bytes(b"x")
    onnx.with_suffix(".json").write_text(json.dumps({**INFO, **info}), encoding="utf-8")
    return onnx


def assist():
    feature = PoseFeature(FakeModel(), make_card())
    return PoseAssist(feature, PoseGuard(feature), feature.card)


# ---------------------------------------------------------------- off by default

def test_the_default_engine_has_no_pose_parts():
    engine = build_engine(Settings(schema()), "pinch")
    try:
        assert [f.id for f in engine.features] == ["pointer", "pinch", "motion"] and engine.filters == []
    finally:
        engine.shutdown()


def test_settings_are_declared_with_safe_defaults_and_ranges():
    s = schema()
    for key, default in pose.SETTINGS.items():
        assert s.get(key).default == default
    settings = Settings(s)
    assert settings.get("pose_assist.enabled") is False and settings.get("pose_assist.model") == ""
    assert not settings.set("pose_assist.tau_veto", 0.2)        # below the 0.5 floor
    assert not settings.set("pose_assist.k_frames", 0)
    assert settings.set("pose_assist.k_frames", 5) and settings.get("pose_assist.k_frames") == 5


def test_not_asked_for_means_no_load_at_all(capsys):
    settings = Settings(schema())
    assert _load_pose_assist(settings, None) is None
    settings.set("pose_assist.model", "somewhere.onnx")          # a path alone does not turn it on
    assert _load_pose_assist(settings, None) is None
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------- attaching

def test_an_assist_is_attached_last_without_changing_the_rest():
    base = build_engine(Settings(schema()), "pinch")
    engine = build_engine(Settings(schema()), "pinch", pose_assist=assist())
    try:
        assert [f.id for f in engine.features] == [f.id for f in base.features] + ["pose_assist"]
        assert [f.id for f in engine.filters] == ["pose_guard"]
        assert engine.router.bindings == base.router.bindings
    finally:
        base.shutdown()
        engine.shutdown()


def test_the_guard_goes_after_a_confirmation_filter():
    gate = SimpleNamespace(id="tcn_gate", reset=lambda reason: None)
    engine = build_engine(Settings(schema()), "pinch", (gate,), pose_assist=assist())
    try:
        assert [f.id for f in engine.filters] == ["tcn_gate", "pose_guard"]
    finally:
        engine.shutdown()


def test_direct_mode_takes_the_guard_but_still_refuses_a_confirmation_filter():
    tcn = tcn_card()
    model = LoadedModel(Path("synthetic.onnx"), tcn, TcnPredictor(tcn))
    engine = build_engine(Settings(schema()), "pinch", direct_model=model, pose_assist=assist())
    try:
        assert {"tcn", "pose_assist"} <= {f.id for f in engine.features}
        assert [f.id for f in engine.filters] == ["pose_guard"]
    finally:
        engine.shutdown()
    with pytest.raises(ValueError, match="cannot be used together"):
        build_engine(Settings(schema()), "pinch", (SimpleNamespace(id="tcn_gate"),), direct_model=model)


# ---------------------------------------------------------------- loading

def test_create_pose_assist_builds_the_pair_from_a_model_and_its_card(tmp_path):
    load = create_pose_assist(model_files(tmp_path), {"pose_assist.tau_veto": 0.8, "pose_assist.k_frames": 4},
                              session_factory=lambda p: Session())
    assert load.reason == "ok" and load.text == "" and load.assist is not None
    a = load.assist
    assert a.guard.tau_veto == 0.8 and a.guard.k == 4
    assert a.feature.threshold == pytest.approx(0.8)             # min(tau_veto 0.8, tau_idle 0.9)
    assert a.guard.veto_gestures == ("pinch.thumb_ring",) and a.card.kind == "personal"


@pytest.mark.parametrize("info,reason", [
    ({"feature_version": "local3d-v1"}, "feature_version"),
    ({"role": "something_else"}, "invalid"),
    ({"gestures": {}}, "no_gestures"),
])
def test_a_wrong_model_gives_no_assist_and_a_reason_never_an_exception(tmp_path, info, reason):
    load = create_pose_assist(model_files(tmp_path, **info), session_factory=lambda p: Session())
    assert load.assist is None and load.reason == reason and load.text


def test_a_card_that_is_not_one_frame_long_is_refused(tmp_path):
    load = create_pose_assist(model_files(tmp_path, window=15), session_factory=lambda p: Session(window=15))
    assert load.assist is None and load.reason == "invalid"


def test_a_missing_or_unreadable_model_is_a_reason(tmp_path):
    assert create_pose_assist(tmp_path / "missing.onnx").reason == "missing"
    # without onnxruntime (CI) the reason says so; with it, the placeholder file is unreadable
    assert create_pose_assist(model_files(tmp_path)).reason in ("runtime_missing", "unreadable")


def test_asked_for_by_the_command_line_or_by_enabling_it(tmp_path, monkeypatch, capsys):
    path = model_files(tmp_path)
    real = pose.create_pose_assist
    monkeypatch.setattr(app.pose, "create_pose_assist",
                        lambda p, s=None, f=None: real(p, s, lambda _: Session()))
    settings = Settings(schema())
    assert isinstance(_load_pose_assist(settings, path), PoseAssist)          # --pose-model
    settings.set("pose_assist.enabled", True)
    assert _load_pose_assist(settings, None) is None                          # enabled, but no model path yet
    assert "pose_assist.model is empty" in capsys.readouterr().err
    settings.set("pose_assist.model", str(path))
    assert isinstance(_load_pose_assist(settings, None), PoseAssist)          # enabled with a model


def test_a_bad_model_never_stops_the_app_and_says_why(tmp_path, capsys):
    settings = Settings(schema())
    assert _load_pose_assist(settings, tmp_path / "nope.onnx") is None
    assert "모델 없음" in capsys.readouterr().err
