"""Audit QML handler targets and real local navigation at supported window sizes."""

import ast
import re
from pathlib import Path
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QMetaObject
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem
from test_studio_layout import studio, visual_child


def test_all_qml_bridge_and_library_handler_targets_are_defined():
    ui = Path(__file__).resolve().parents[3] / "src/manux/ui"
    methods = {}
    for file, owner in [("bridge.py", "Bridge"), ("studio.py", "StudioBridge"), ("ai_customization.py", "AiCustomizationBridge")]:
        tree = ast.parse((ui / file).read_text(encoding="utf-8"))
        cls = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == owner
        )
        methods[owner] = {
            node.name for node in cls.body if isinstance(node, ast.FunctionDef)
        }
    handlers = 0
    for path in (ui / "qml").rglob("*.qml"):
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not re.search(
                r"on(?:Clicked|Triggered|Moved|Activated|Accepted|Toggled|TextEdited|EditingFinished|PressedChanged)\s*:",
                line,
            ):
                continue
            handlers += 1
            for method in re.findall(
                r"(?:controller|dashboard)\.([A-Za-z_]\w*)\s*\(", line
            ):
                owner = "AiCustomizationBridge" if path.name in ("AiCustomizationDialog.qml", "AiDrawOverlay.qml") else "Bridge"
                assert method in methods[owner], (path, line_no, method)
            for method in re.findall(
                r"(?:library|learningStudio)\.([A-Za-z_]\w*)\s*\(", line
            ):
                assert method in methods["StudioBridge"], (path, line_no, method)
    assert handlers > 0


def test_every_learning_tab_opens_with_real_services_and_no_qml_errors(
    studio, monkeypatch
):
    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    monkeypatch.setattr(bridge.studio, "_run", lambda *args, **kwargs: None)
    center = bridge.window.findChild(QObject, "studioDataManagement")
    QMetaObject.invokeMethod(center, "open")
    QTest.qWait(100)
    for width, height in [(780, 560), (1140, 720)]:
        bridge.window.resize(width, height)
        for index in range(2):
            button = visual_child(bridge.window.contentItem(), f"studioTab_{index}")
            assert button is not None
            assert button.property("enabled")
            assert QMetaObject.invokeMethod(button, "click")
            QTest.qWait(30)
            assert center.property("pageIndex") == index
    QMetaObject.invokeMethod(center, "close")
    assert not bridge._pose_studio_visible
    assert not [
        message
        for message in messages
        if any(
            word in message
            for word in (
                "Error:",
                "ReferenceError",
                "TypeError",
                "has no attribute",
                "Binding loop",
            )
        )
    ], messages


def test_pose_save_failure_is_visible_and_stops_training_and_upload(
    studio, monkeypatch
):
    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    calls = []

    def fail_save():
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(bridge.pose_studio, "save_dataset", fail_save)
    monkeypatch.setattr(service, "pose_train", lambda model_id: calls.append("train"))
    monkeypatch.setattr(
        bridge.studio, "_run", lambda *args, **kwargs: calls.append("upload")
    )
    assert not bridge.saveStudioDataset()
    assert "표본 저장 실패" in bridge.studioNote
    bridge.trainStudioModel()
    bridge.studio.poseDatasetAction("upload")
    assert calls == []


def test_model_slot_switch_preserves_unsaved_pose_samples(studio):
    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    points = tuple((0.2 + 0.1 * (i % 5), 0.25 + 0.1 * (i // 5), 0.0) for i in range(21))
    bridge.pose_studio.capture_snapshot(ground_truth="thumb_middle", landmarks=points)
    sample_id = bridge.pose_studio.samples[0]["sampleId"]
    bridge.createStudioModel("new synthetic slot")
    assert not bridge.pose_studio.samples
    from manux.data.pose_studio import PoseSnapshotStudio

    original = PoseSnapshotStudio(service.data_dir)
    original.active_model_id = "spatial_pose_mlp.json"
    assert sample_id in {sample["sampleId"] for sample in original.load_all_samples()}


def test_library_pose_preview_selects_model_after_preserving_current_samples(
    studio, monkeypatch
):
    import numpy as np
    from manux.ml.spatial_mlp import SpatialMlpModel

    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    model = SpatialMlpModel(
        np.zeros((2, 96)),
        np.zeros(2),
        np.zeros((2, 2)),
        np.zeros(2),
        classes=("none", "thumb_ring"),
    )
    model.save(bridge.pose_studio.models_dir / "spatial_pose_mlp.json")
    bridge.createStudioModel("temporary selected slot")
    selected = bridge.pose_studio.active_model_id
    points = tuple((0.2 + 0.1 * (i % 5), 0.25 + 0.1 * (i // 5), 0.0) for i in range(21))
    bridge.pose_studio.capture_snapshot(ground_truth="thumb_middle", landmarks=points)
    sample_id = bridge.pose_studio.samples[0]["sampleId"]
    calls = []
    monkeypatch.setattr(
        bridge.studio, "_run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    bridge.studio.modelAction("preview", "synthetic-model", "")
    assert calls and not bridge.pose_studio.samples
    bridge.studio._apply(bridge.studio.generation, {"spatial_selected": True})
    assert bridge.pose_studio.active_model_id == "spatial_pose_mlp.json"
    assert bridge.pose_studio.active_model is not None
    from manux.data.pose_studio import PoseSnapshotStudio

    previous = PoseSnapshotStudio(service.data_dir)
    previous.active_model_id = selected
    assert sample_id in {sample["sampleId"] for sample in previous.load_all_samples()}


@pytest.mark.parametrize(
    "dialog_name", ["studioDataManagement", "advancedSettings", "studioSettings"]
)
def test_v_cannot_enable_control_inside_capture_or_settings_dialogs(
    studio, dialog_name
):
    from manux.core.contracts import FrameInfo, HandObservation, OutputTarget

    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    dialog = bridge.window.findChild(QObject, dialog_name)
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(60)
    points = [(0.5, 0.8, 0.0)] * 21
    points[4] = (0.52, 0.64, 0.0)
    for mcp, tip, x in [(5, 8, 0.44), (9, 12, 0.48), (13, 16, 0.52), (17, 20, 0.56)]:
        points[mcp] = (x, 0.6, 0.0)
        points[tip] = (x, 0.4 if mcp in (5, 9) else 0.68, 0.0)
    for index in range(25):
        frame = FrameInfo(index, 1 + index * 0.05, 1280, 720, True)
        snap = runtime.engine.step(
            frame, HandObservation(frame, tuple(points), "Right", 0.99)
        )
        assert "enrolling" in snap.busy
        assert not snap.control_on and snap.output is OutputTarget.NONE
    QMetaObject.invokeMethod(dialog, "close")
    QTest.qWait(60)
    for index in range(25):
        frame = FrameInfo(100 + index, 3 + index * 0.05, 1280, 720, True)
        snap = runtime.engine.step(
            frame, HandObservation(frame, tuple(points), "Right", 0.99)
        )
    assert not snap.busy and snap.control_on


def test_studio_tracker_and_branded_tray_notice(studio):
    from manux.platform.tray import QtTray

    bridge, _, _, messages = studio
    tracker = bridge.window.findChild(QObject, "fingerTracker")
    assert tracker is not None and not tracker.isVisible()
    bridge.setFingerOverlay(True)
    assert tracker.isVisible()
    bridge.setFingerOverlay(False)
    assert not tracker.isVisible()
    assert bridge.application.applicationDisplayName() == "MANUX"
    assert not bridge.application.windowIcon().isNull()
    assert not bridge.window.icon().isNull()

    tray = QtTray(bridge)
    try:
        assert not tray.tray.icon().isNull()
        tray._hidden()
        assert tray.notice.text().startswith("MANUX")
        assert tray.notice.isVisible() and tray.notice_timer.interval() == 2500
        tray.notice_timer.timeout.emit()
        assert not tray.notice.isVisible()
        assert not [message for message in messages if "ReferenceError" in message]
    finally:
        tray.hide()


def test_control_badge_follows_engine_transitions_and_expires_without_frames(studio):
    from manux.core.contracts import FrameInfo
    from manux.engine.commands import SetControl
    from manux.ui.view import ViewFrame

    bridge, _, runtime, _ = studio
    bridge.timer.stop()
    for index, (enabled, label) in enumerate(((True, "제어 켜짐"), (False, "제어 꺼짐"))):
        runtime.engine.commands.put(SetControl(enabled))
        snap = runtime.engine.step(FrameInfo(index, index + 1., 640, 480, True), None)
        bridge.apply_view(ViewFrame(snap))
        assert bridge.motionBadgeVisible and bridge.motionBadgeText == label
    bridge._control_notice_until = 0.
    bridge._seq = runtime.latest()[0]
    bridge.poll()
    assert not bridge.motionBadgeVisible
