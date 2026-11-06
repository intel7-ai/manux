"""Actual Qt + source-worker interaction with generated anonymous recordings."""
import sys
import time
from pathlib import Path

import pytest
pytest.importorskip("PySide6.QtQml")

from PySide6.QtCore import QObject, QPointF, QMetaObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_bridge import FakeRuntime
from manux.app import schema, training_specs
from manux.core.contracts import RecordingIdentity
from manux.settings.store import Settings
from manux.data.training_center import TrainingCenterService, recording_sets
from manux.ui.bridge import Bridge

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from smoke_worker import recording


def wait_for(condition):
    end = time.monotonic()+10
    while time.monotonic()<end:
        QTest.qWait(30)
        if condition(): return
    pytest.fail("studio worker did not complete")


def visual_child(item, name):
    if item.objectName() == name: return item
    for child in item.childItems():
        result = visual_child(child, name)
        if result is not None: return result


def test_studio_worker_playback_curation_and_small_window_actions(tmp_path):
    path = tmp_path / "recordings/profile-synthetic/pinch/train/synthetic.jsonl.gz"
    recording(path, "train")
    settings = Settings(schema())
    tc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings", specs=training_specs())
    tc.configure("synthetic")
    b = Bridge(FakeRuntime(settings), settings, tmp_path / "config", services={"training_center": tc})
    try:
        b.control_hand = "Right"
        b.finishSplash()
        popup = b.window.findChild(QObject, "trainingCenter")
        QMetaObject.invokeMethod(popup, "open")
        wait_for(lambda: not b.studio.busy and not b.training_busy and b.training_rows)
        assert b.studio.recordings[0]["quality"]["status"] == "pass"
        assert b.paused
        b.studio.selectRecording(b.studio.recordings[0]["key"])
        wait_for(lambda: not b.studio.busy)
        assert b.studio.frameCount == 360
        b.studio.seek(120)
        assert b.studio.frameIndex == 120 and b.studio.frame["t"] == 4
        assert b.studio.frameDelay == pytest.approx(1000/30)
        popup.setProperty("pageIndex", 1)
        QTest.qWait(60)
        button = b.window.findChild(QQuickItem, "studioInclude")
        assert button.property("enabled")
        QMetaObject.invokeMethod(button, "click")
        wait_for(lambda: not b.studio.busy)
        assert not b.studio.selected["included"]
        assert recording_sets(tc.recordings_dir, RecordingIdentity("synthetic"))[0] == []
        b.studio.include(True)
        wait_for(lambda: not b.studio.busy)
        assert recording_sets(tc.recordings_dir, RecordingIdentity("synthetic"))[0] == [path]
        b.setTrainingSelected("thumb_ring", True)
        for width, height in ((780, 560), (1140, 720)):
            b.window.setWidth(width)
            b.window.setHeight(height)
            for page in range(3):
                popup.setProperty("pageIndex", page)
                QTest.qWait(60)
                action = b.window.findChild(QQuickItem, "recordSelection")
                point = action.mapToScene(QPointF(0, 0))
                assert 0 <= point.y() and point.y()+action.height() <= b.window.height()
                assert action.property("enabled")
        before = b.studio.recordings
        b.studio.close()
        b.studio._apply(b.studio.generation, {"recordings": []})
        assert b.studio.recordings == before
    finally: b.close()
