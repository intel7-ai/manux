"""Real v0.4 QML interaction, offscreen; no camera and no Windows input."""
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtQml")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from manux import desktop
from manux.app import schema, training_specs
from manux.data.training_center import TrainingCenterService
from manux.settings.store import Settings
from manux.ui.bridge import Bridge


def test_progress_navigation_eta_and_close_keep_training_alive(tmp_path):
    messages = []
    previous = qInstallMessageHandler(lambda kind, ctx, message: messages.append(message))
    application = QApplication.instance() or QApplication([])
    # The offscreen platform does not enumerate Windows system fonts.
    if os.name == "nt":
        for font in ("malgun.ttf", "malgunbd.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font))
    settings = Settings(schema())
    runtime = desktop.build(settings, SimpleNamespace(close=lambda: None), windows=False)
    service = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings", specs=training_specs())
    bridge = None
    try:
        bridge = Bridge(runtime, settings, tmp_path / "config", services={"training_center": service})
        bridge._set_stage("main")
        service.job.state = dict(running=True, job_id="synthetic", started=time.time() - 8,
            stage="선택 동작 학습 중 · 좌클릭", phase="training", progress=.45, epoch=4, epochs=20, step=1, steps=2,
            epoch_seconds=[2., 2.], last_epoch_at=time.time(), task_names=["좌클릭"],
            losses=[1., .8, .6, .5], loss=.5, events=["선택 동작 학습 중"])
        bridge.trainingStarted.emit()
        QTest.qWait(100)
        page = bridge.window.findChild(QObject, "trainingProgressPage")
        center = bridge.window.findChild(QObject, "trainingCenter")
        assert page is not None and page.property("visible")
        assert bridge.window.property("learningDialogOpen") and bridge.paused
        assert bridge.window.findChild(QObject, "trainingPercent").property("text") == "45%"
        assert "약 " in bridge.window.findChild(QObject, "trainingEta").property("value")
        for width, height in ((1140, 720), (780, 560)):
            bridge.window.resize(width, height)
            QTest.qWait(60)
            assert page.property("width") <= width and page.property("height") <= height
            if os.getenv("MANUX_TEST_SCREENSHOTS"):
                destination = Path(os.environ["MANUX_TEST_SCREENSHOTS"])
                destination.mkdir(parents=True, exist_ok=True)
                assert bridge.window.grabWindow().save(str(destination / f"training-progress-{width}.png"))
        back = bridge.window.findChild(QObject, "trainingProgressBack")
        position = back.mapToScene(QPointF(back.width() / 2, back.height() / 2)).toPoint()
        QTest.mouseClick(bridge.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position)
        QTest.qWait(100)
        assert center.property("visible") and not page.property("visible")
        progress_button = bridge.window.findChild(QObject, "openTrainingProgress")
        assert QMetaObject.invokeMethod(progress_button, "clicked")
        QTest.qWait(100)
        assert page.property("visible") and not center.property("visible")
        QTest.keyClick(bridge.window, Qt.Key.Key_Escape)
        QTest.qWait(100)
        assert not page.property("visible")
        assert service.job.running and bridge.training_timer.isActive()
        assert not [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "Binding loop"))]
    finally:
        service.job.state = {"running": False}
        if bridge is not None:
            # Wait for the learning center's background summary read before disposal.
            deadline = time.monotonic() + 2
            while bridge.training_busy and time.monotonic() < deadline:
                QTest.qWait(10)
            bridge.close()
        runtime.stop()
        qInstallMessageHandler(previous)


def test_studio_restores_per_gesture_actions_saved_plans_and_visible_progress(tmp_path, monkeypatch):
    from PySide6.QtQuick import QQuickItem
    from manux.data import capture_plan
    application = QApplication.instance() or QApplication([])
    settings = Settings(schema())
    runtime = desktop.build(settings, SimpleNamespace(close=lambda: None), windows=False)
    service = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings", specs=training_specs())
    service.configure("synthetic")
    service.save_plan(capture_plan.preset("quick_click"), "thumb_middle")
    service.save_plan(capture_plan.preset("positions"), "thumb_ring")
    original_summary = service.summary_and_rows
    def summary():
        data, report, rows, storage = original_summary()
        for row in rows:
            if row["key"] == "thumb_middle": row["reps"] = 30
        return data, report, rows, storage
    monkeypatch.setattr(service, "summary_and_rows", summary)
    requests = []
    def start(**kwargs):
        requests.append(kwargs)
        service.job.state = {"running": True, "task_names": ["좌클릭"]}
        return True
    monkeypatch.setattr(service.job, "start", start)
    bridge = Bridge(runtime, settings, tmp_path / "config", services={"training_center": service})
    def visual_child(item, name):
        if item.objectName() == name: return item
        for child in item.childItems():
            found = visual_child(child, name)
            if found is not None: return found
        return None
    try:
        bridge._set_stage("main")
        center = bridge.window.findChild(QObject, "trainingCenter")
        QMetaObject.invokeMethod(center, "open")
        for _ in range(40):
            QTest.qWait(50)
            if not bridge.trainingBusy:
                bridge.trainingChanged.emit(); QTest.qWait(50); break
        assert len(bridge.trainingRows) == 18
        progress = bridge.window.findChild(QQuickItem, "openTrainingProgress")
        assert progress.isVisible() and progress.mapToScene(QPointF(0, 0)).y() < 140
        listing = bridge.window.findChild(QQuickItem, "trainingTasks")
        focus = visual_child(bridge.window.contentItem(), "focusRecord_thumb_middle")
        assert focus is not None and focus.property("text") == "집중 녹화 설정"
        assert QMetaObject.invokeMethod(focus, "clicked")
        QTest.qWait(80)
        assert bridge.recordingPlan.focus == "thumb_middle"
        assert len(bridge.recordingPlan.plan["conditions"]) == 27 and bridge.recordingPlan.clickGesture
        bridge.recordingPlan.open("thumb_ring")
        assert len(bridge.recordingPlan.plan["conditions"]) == 9
        bridge.recordingPlan.open("none")
        assert not bridge.recordingPlan.clickGesture
        # Close the recording popup and use the restored single-action training button.
        QTest.keyClick(bridge.window, Qt.Key.Key_Escape)
        QMetaObject.invokeMethod(center, "open")
        for _ in range(40):
            QTest.qWait(50)
            if not bridge.trainingBusy:
                bridge.trainingChanged.emit(); QTest.qWait(50); break
        train = visual_child(bridge.window.contentItem(), "trainGesture_thumb_middle")
        assert train is not None and train.property("enabled"), (bridge.training_busy, bridge.recActive, service.job.running, service.hub_job.running, bridge.trainingRows[0])
        assert QMetaObject.invokeMethod(train, "clicked")
        QTest.qWait(60)
        assert requests == [{"gestures": ["thumb_middle"]}]
        assert bridge.window.findChild(QObject, "trainingProgressPage").property("visible")
    finally:
        service.job.state = {"running": False}
        deadline = time.monotonic() + 2
        while bridge.training_busy and time.monotonic() < deadline: QTest.qWait(10)
        bridge.close(); runtime.stop()
