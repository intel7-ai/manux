"""Exercise the original UI through Qt against the v0.4 services, not a mock dashboard."""
import time
import math
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QMetaObject, Qt, qInstallMessageHandler
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from manux.app import schema, training_specs
from manux.data import capture_plan
from manux.data.training_center import TrainingCenterService
from manux.desktop import build
from manux.perception.palm_axes import PalmAxesCalibration, valid_axes, palm_point
from manux.settings.store import Settings
from manux.ui.bridge import Bridge
from manux.ui.view import ViewFrame


def visual_child(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_child(child, name)
        if found is not None:
            return found


def test_idle_main_camera_paints_motion_ring_and_clears_it_when_hand_leaves(studio):
    import numpy as np
    bridge, service, runtime, messages = studio
    bridge.use_index_pointer = False
    assert bridge.paused and bridge.rec_mode == "off"
    raw = np.zeros((480, 640, 3), dtype=np.uint8)
    landmarks = tuple((.2+.01*(i%5), .2+.02*(i//5), 0.) for i in range(21))
    view = ViewFrame(None, image=raw, landmarks=landmarks,
                     preview_anchor=(.5, .5), preview_moving=True, preview_guides=((12, "pinched"),))
    bridge.last_preview = 0
    bridge.apply_view(view)
    assert bridge.provider.image.pixelColor(320, 240).name() == "#76e600"
    assert bridge.provider.image.pixelColor(330, 240).name() != "#000000"
    assert not raw.any()  # overlays never change camera/recording source data
    bridge.last_preview = 0
    bridge.apply_view(ViewFrame(None, image=raw))
    assert bridge.provider.image.pixelColor(320, 240).name() == "#000000"
    assert bridge.paused and bridge.rec_mode == "off"


def test_sensitivity_dialog_applies_only_edited_values_and_cancel_discards_draft(studio):
    from manux.settings.store import load
    bridge, service, runtime, messages = studio
    bridge.command(111)
    QTest.qWait(60)
    dialog = bridge.window.findChild(QObject, "studioSettings")
    assert dialog.property("visible")
    field = visual_child(bridge.window.contentItem(), "sensitivity-value-pinch.click_threshold")
    apply = visual_child(bridge.window.contentItem(), "applySensitivity")
    assert field is not None and apply is not None
    before = bridge.settings.stored()
    field.setProperty("text", "0.08")
    QMetaObject.invokeMethod(field, "textEdited")
    assert bridge.settings.stored() == before  # typing is a draft
    QMetaObject.invokeMethod(apply, "clicked")
    assert bridge.sensitivityPending
    runtime._drain_runtime_requests()
    bridge.poll()
    assert not bridge.sensitivityPending
    assert "완료" in bridge.sensitivityStatus
    assert load(schema(), bridge.config_dir).get("pinch.click_threshold") == .08
    assert bridge.paused and bridge.rec_mode == "off"
    assert runtime.settings_for("pinch")["click_threshold"] == .08
    assert not apply.property("enabled")
    field = visual_child(bridge.window.contentItem(), "sensitivity-value-pinch.click_threshold")
    field.setProperty("text", "0.09")
    QMetaObject.invokeMethod(field, "textEdited")
    QMetaObject.invokeMethod(dialog, "close")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(30)
    field = visual_child(bridge.window.contentItem(), "sensitivity-value-pinch.click_threshold")
    assert float(field.property("text")) == .08
    assert bridge.window.objectName() == "raspJunStudio"
    assert not [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "Binding loop"))]


@pytest.fixture
def studio(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = Settings(schema())
    runtime = build(settings, SimpleNamespace(close=lambda: None), windows=False)
    service = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings", specs=training_specs())
    service.configure("synthetic")
    messages = []
    old = qInstallMessageHandler(lambda kind, context, text: messages.append(text))
    bridge = None
    try:
        bridge = Bridge(runtime, settings, tmp_path / "config", layout="studio", services={
            "training_center": service, "palm_calibration": PalmAxesCalibration,
            "valid_palm_axes": valid_axes, "palm_point": palm_point})
        bridge._set_stage("main")
        yield bridge, service, runtime, messages
    finally:
        if bridge:
            service.job.state = {"running": False}
            for _ in range(100):
                if not bridge.training_busy:
                    break
                QTest.qWait(20)
            bridge.close()
        runtime.stop()
        qInstallMessageHandler(old)


def test_original_studio_flows_use_current_training_service(studio, monkeypatch):
    bridge, service, runtime, messages = studio
    service.save_plan(capture_plan.preset("quick_click"), "thumb_middle")
    service.save_plan(capture_plan.preset("positions"), "thumb_ring")
    summary = service.summary_and_rows
    def with_recordings():
        data, report, rows, storage = summary()
        for row in rows:
            if row["key"] == "thumb_middle":
                row["reps"] = 30
        return data, report, rows, storage
    monkeypatch.setattr(service, "summary_and_rows", with_recordings)
    assert bridge.window.objectName() == "raspJunStudio"
    center = bridge.window.findChild(QObject, "trainingCenter")
    QMetaObject.invokeMethod(center, "open")
    for _ in range(60):
        QTest.qWait(30)
        if not bridge.training_busy:
            bridge.trainingChanged.emit()
            QTest.qWait(100)
            break
    assert len(bridge.studioTrainingRows) == 18
    assert not bridge.window.findChild(QObject, "settingsNav")
    assert not bridge.window.findChild(QObject, "homeNav")
    bridge.command(32)
    assert bridge.paused  # global hotkey cannot turn on real input inside the learning dialog
    focus = visual_child(bridge.window.contentItem(), "focus-record-thumb_middle")
    assert focus is not None
    assert QMetaObject.invokeMethod(focus, "clicked")
    QTest.qWait(60)
    assert bridge.recordingPlan.focus == "thumb_middle"
    assert len(bridge.recordingPlan.plan["conditions"]) == 27
    assert bridge.window.findChild(QObject, "recordingPlanDialog").property("visible")
    bridge.recordingPlan.open("thumb_ring")
    assert len(bridge.recordingPlan.plan["conditions"]) == 9
    QTest.keyClick(bridge.window, Qt.Key.Key_Escape)
    QTest.qWait(60)
    assert not bridge.window.findChild(QObject, "recordingPlanDialog").property("visible")
    bridge.selectTrainingTask("thumb_middle", True)
    calls = []
    def start(**kw):
        calls.append(kw)
        service.job.state = {"running": True, "started": time.time() - 5, "phase": "training", "progress": .25,
                             "epochs": 20, "epoch": 5, "losses": [.7, .4], "epoch_seconds": [.5, .6],
                             "task_names": ["좌클릭"]}
        return True
    monkeypatch.setattr(service.job, "start", start)
    bridge.startTraining()
    QTest.qWait(80)
    assert calls == [{"gestures": ["thumb_middle"]}]
    page = bridge.window.findChild(QObject, "trainingProgressPage")
    assert page.property("visible")
    assert bridge.trainingDetails["percent"] == 25
    assert bridge.trainingDetails["etaSeconds"] > 0
    QTest.keyClick(bridge.window, Qt.Key.Key_Escape)
    QTest.qWait(60)
    assert not page.property("visible") and service.job.running
    assert bridge.training_timer.isActive()
    assert not [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "Binding loop"))]


def test_palm_calibration_preserves_previous_axes_on_failure_and_cancel(studio, monkeypatch):
    bridge, service, runtime, messages = studio
    bridge._palm_axes = (1., -.2, 0., 1.)
    bridge.beginPalmCalibration()
    bridge.command(32)
    assert bridge.paused and bridge.palmCalibrating
    bridge.command(27)
    assert not bridge.palmCalibrating and bridge._palm_axes == (1., -.2, 0., 1.)
    bridge.beginPalmCalibration()
    finished_at = bridge._palm_cal._step_t0 + 10
    monkeypatch.setattr("manux.ui.bridge.time.perf_counter", lambda: finished_at)
    bridge.apply_view(ViewFrame(None))
    assert not bridge.palmCalibrating and bridge._palm_axes == (1., -.2, 0., 1.)
    assert "실패" in bridge.palmAxesHint


def test_tcn_global_toggle_is_a_runtime_request_and_keeps_policies(studio):
    bridge, service, runtime, messages = studio
    bridge.setTcnGate(False)
    runtime._drain_runtime_requests()
    assert runtime.tcn_enabled is False and not bridge.tcnGate
    bridge.setTcnGate(True)
    runtime._drain_runtime_requests()
    assert runtime.tcn_enabled is True and bridge.preferences.value("tcnEnabled", type=bool)


def test_palm_calibration_saves_success_only_for_the_selected_hand(studio, monkeypatch):
    bridge, service, runtime, messages = studio
    bridge.control_hand = "Right"
    bridge.beginPalmCalibration()
    start = bridge._palm_cal._step_t0
    for i in range(170):
        now = start + i / 30
        monkeypatch.setattr("manux.ui.bridge.time.perf_counter", lambda: now)
        step = .1 * math.sin(i / 6)
        landmarks = tuple((.5 + step * .3, .5 + step, 0.) for _ in range(21))
        bridge._update_palm_view(ViewFrame(None, landmarks=landmarks, aspect=1.))
    assert bridge.palmCalibrated and not bridge.palmCalibrating
    saved = bridge._palm_axes
    assert runtime.settings_for("pinch")["palm_axes"] == saved
    bridge.control_hand = "Left"
    bridge._load_palm_axes()
    assert not bridge.palmCalibrated
    bridge.control_hand = "Right"
    bridge._load_palm_axes()
    assert bridge._palm_axes == saved
