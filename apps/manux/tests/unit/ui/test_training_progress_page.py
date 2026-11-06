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
