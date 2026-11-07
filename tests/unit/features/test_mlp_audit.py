from pathlib import Path
import sys
import numpy as np
import pytest
from manux.core.contracts import FrameContext, FrameInfo, HandObservation, Intent
from manux.data.pose_studio import ACTION_IDS, normalize_system_action
from manux.desktop import make_pose_studio
from manux.features.spatial_pose import SpatialPoseFeature, SpatialPoseGuard
from manux.ml.spatial_mlp import SpatialMlpModel
from test_spatial_pose import _dummy_hand


def constant_model(label, version="spatial-mlp-v1"):
    dim = 33 if version == "pose-angles-v1" else 96
    bias = np.zeros(len(ACTION_IDS))
    bias[ACTION_IDS.index(label)] = 12
    return SpatialMlpModel(
        np.zeros((2, dim)),
        np.zeros(2),
        np.zeros((len(ACTION_IDS), 2)),
        bias,
        classes=ACTION_IDS,
        feature_version=version,
    )


@pytest.mark.parametrize("label", ACTION_IDS)
def test_every_mlp_class_is_predicted_captured_saved_and_reloaded(tmp_path, label):
    studio = make_pose_studio(tmp_path)
    constant_model(label).save(studio.models_dir / "spatial_pose_mlp.json")
    points = _dummy_hand()
    studio.observe_frame(points, None, label)
    assert studio.live_ai_action == label
    assert studio.capture_snapshot(ground_truth=label)["success"]
    path = studio.save_dataset()
    assert path.is_file() and not studio.samples
    assert studio.load_all_samples()[0]["groundTruthAction"] == label
    studio.split = "eval"
    studio.capture_snapshot(ground_truth=label)
    studio.save_dataset()
    assert len(studio.load_all_samples()) == 1


def test_33_dimension_model_works_in_preview_and_guard_and_skips_missing_world(
    tmp_path,
):
    model = constant_model("thumb_middle", "pose-angles-v1")
    studio = make_pose_studio(tmp_path)
    model.save(studio.models_dir / "spatial_pose_mlp.json")
    points = tuple(_dummy_hand())
    studio.observe_frame(points, points, "thumb_middle")
    assert studio.live_ai_action == "thumb_middle"
    frame = FrameInfo(1, 1.0, 640, 480, False)
    hand = HandObservation(frame, points, "Right", 0.99, points)
    feature = SpatialPoseFeature(model)
    ctx = FrameContext(frame, "main", "practice", "pointer")
    feature.step(hand, ctx)
    assert feature.evidence.action == "thumb_middle"
    feature.step(HandObservation(frame, points, "Right", 0.99), ctx)
    assert feature.evidence is None
    studio.observe_frame(points, None, "none")
    assert studio.live_ai_confidence == 0.0


@pytest.mark.parametrize(
    "label,gesture",
    [("thumb_middle", "pointer.left_click"), ("thumb_ring", "pointer.right_click")],
)
def test_pointer_alias_matches_same_mlp_class(label, gesture):
    feature = SpatialPoseFeature(constant_model(label))
    guard = SpatialPoseGuard(feature)
    frame = FrameInfo(1, 1.0, 640, 480, False)
    hand = HandObservation(frame, tuple(_dummy_hand()), "Right", 0.99)
    ctx = FrameContext(frame, "main", "practice", "pointer")
    guard.observe(hand, ctx)
    intent = Intent(1.0, "pointer", gesture, "tap")
    assert guard.filter((intent,), ctx) == (intent,)


def test_v_control_is_reported_as_v_in_pose_comparison():
    from types import SimpleNamespace

    assert (
        normalize_system_action(
            SimpleNamespace(intents=(("control.v_sign", "tap"),), results=())
        )
        == "v_sign"
    )
    assert normalize_system_action(None, ("control.toggle",)) == "v_sign"


def test_real_worker_training_writes_selected_slot_preserves_default_and_eval(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
    from app_worker import run_pose_training

    studio = make_pose_studio(tmp_path)
    default = studio.models_dir / "spatial_pose_mlp.json"
    constant_model("none").save(default)
    original = default.read_bytes()
    result = studio.create_model("audit slot")
    selected = result["id"]
    for label in ("thumb_middle", "thumb_ring"):
        for _ in range(3):
            studio.capture_snapshot(
                ground_truth=label,
                landmarks=_dummy_hand(12 if label == "thumb_middle" else 16),
            )
    studio.save_dataset()
    studio.split = "eval"
    studio.capture_snapshot(ground_truth="v_sign", landmarks=_dummy_hand())
    studio.save_dataset()
    events = []
    run_pose_training(
        {
            "data_dir": str(tmp_path),
            "model_id": selected,
            "job_id": "synthetic",
            "cancel_path": str(tmp_path / "cancel"),
        },
        send=lambda *args, **kw: events.append((args, kw)),
    )
    assert default.read_bytes() == original
    model = SpatialMlpModel.load(studio.models_dir / selected)
    assert model.metadata["total_samples"] == 6
    assert events[-1][1]["spatial_candidate"] == str(studio.models_dir / selected)


def test_guard_toggle_preserves_selected_model_and_cannot_restore_stale_startup_file(
    tmp_path,
):
    from types import SimpleNamespace
    from manux.app import schema
    from manux.core.contracts import RuntimeRequest
    from manux.desktop import build
    from manux.settings.store import Settings

    startup = constant_model("none")
    selected = constant_model("thumb_middle")
    runtime = build(
        Settings(schema()),
        SimpleNamespace(close=lambda: None),
        windows=False,
        spatial_pose_model=startup,
    )
    runtime.spatial_model_path = startup.save(tmp_path / "startup.json")
    try:
        runtime.set_spatial_model(selected)
        runtime._drain_runtime_requests()
        for state in ("on", "off", "on"):
            runtime.request(RuntimeRequest("spatial_guard", state))
            runtime._drain_runtime_requests()
            assert runtime.spatial_feature.model is selected
        runtime.set_spatial_model(None)
        runtime._drain_runtime_requests()
        runtime.request(RuntimeRequest("spatial_guard", "on"))
        runtime._drain_runtime_requests()
        assert runtime.spatial_feature.model is None
    finally:
        runtime.stop()


@pytest.mark.parametrize("busy_reason", ["enrolling", "recording"])
def test_registration_has_practice_only_rule_comparison_while_control_is_blocked(
    busy_reason,
    monkeypatch,
):
    from types import SimpleNamespace
    from manux.app import schema
    from manux.core.contracts import OutputTarget, Stage
    from manux.desktop import build
    from manux.engine.commands import SetBusy, SetStage
    from manux.settings.store import Settings

    class Source:
        fps = 30.0
        seq = 0

        def read(self, *args):
            frame = FrameInfo(self.seq, 1 + self.seq * 0.05, 640, 480, True)
            self.seq += 1
            hand = HandObservation(frame, tuple(_dummy_hand(12)), "Right", 0.99)
            return frame, None, hand, (), 0.0

        def mode_text(self):
            return "synthetic"

        def close(self):
            pass

    runtime = build(Settings(schema()), Source(), windows=False)
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    runtime.engine.commands.put(SetBusy(busy_reason, True))
    views = []
    recorded = []
    runtime._record_frame = lambda now, hand, snap, pose: recorded.append(snap)
    try:
        for _ in range(30):
            runtime.step()
            views.append(runtime.latest()[1])
        assert all(view.snapshot.output is OutputTarget.NONE for view in views)
        assert all(
            not view.snapshot.control_on and not view.snapshot.results for view in views
        )
        assert all(view.rule_preview.output is OutputTarget.PRACTICE for view in views)
        assert recorded == [view.rule_preview for view in views]
        assert OutputTarget.WINDOWS not in runtime._pose_preview.sinks
        assert any(
            normalize_system_action(view.rule_preview) == "thumb_middle"
            for view in views
        )
        missing = []
        with monkeypatch.context() as patch:
            patch.setattr(runtime.source, "read", lambda *args: None)
            patch.setattr(runtime._pose_preview, "frame_missing", missing.append)
            runtime.step()
        assert len(missing) == 1
        runtime.engine.commands.put(SetBusy(busy_reason, False))
        runtime.step()
        assert (
            runtime.latest()[1].rule_preview is None and runtime._pose_preview is None
        )
    finally:
        runtime.stop()


def test_motion_entry_event_is_visible_in_pose_comparison():
    from types import SimpleNamespace

    snapshot = SimpleNamespace(
        intents=(), results=(), events=("mode:pinch->motion:intent",)
    )
    assert normalize_system_action(snapshot) == "thumbs_up"

@pytest.mark.parametrize(
    ("guard_enabled", "v_total", "starts"),
    [(False, 5, True), (True, 0, True), (True, 5, False), (True, None, False)],
)
def test_v_respects_the_active_pose_guard_and_v_training(guard_enabled, v_total, starts):
    from manux.app import schema
    from manux.core.contracts import RuntimeRequest, Stage
    from manux.desktop import build
    from manux.engine.commands import SetStage
    from manux.settings.store import Settings
    from test_control import v_hand

    class Source:
        fps = 30.0
        seq = 0

        def read(self, *args):
            frame = FrameInfo(self.seq, 1 + self.seq / self.fps, 1280, 720, True)
            self.seq += 1
            hand = HandObservation(frame, tuple(v_hand()), "Right", .99)
            return frame, None, hand, (), 0.0

        def mode_text(self):
            return "synthetic"

        def close(self):
            pass

    model = constant_model("thumb_middle")
    if v_total is not None:
        model.metadata["per_class"] = {"v_sign": {"total": v_total}}
    runtime = build(Settings(schema()), Source(), windows=False, spatial_pose_model=model)
    runtime.engine.commands.put(SetStage(Stage.MAIN))
    runtime.request(RuntimeRequest("spatial_guard", "on" if guard_enabled else "off"))
    try:
        for _ in range(30):
            runtime.step()
        assert runtime.spatial_feature.model is model
        assert runtime.latest()[1].snapshot.control_on is starts
    finally:
        runtime.stop()
