"""Actual Qt + source-worker interaction with generated anonymous recordings."""
import sys
import time
from pathlib import Path

import pytest
pytest.importorskip("PySide6.QtQml")

from PySide6.QtCore import QObject, QPointF, QMetaObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication
from manux.ui.studio import StudioBridge
from types import SimpleNamespace
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


@pytest.fixture
def playback_clock():
    app = QGuiApplication.instance() or QGuiApplication([])
    owner = QObject()
    owner.tc = SimpleNamespace(studio_job=None)
    b = StudioBridge(owner)
    now = [100.]
    b._clock = lambda: now[0]
    b.active = True
    frames = [{"t": t, "lm": [[.5, .5, 0.]] * 21 if i in (1, 3) else []}
              for i, t in enumerate((10., 10.033, 10.080, 10.110, 12.))]
    b._apply(0, {"frames": frames, "events": [], "recording": {}})
    yield b, now
    b.close()


def test_playback_uses_elapsed_time_instead_of_advancing_once_per_tick(playback_clock):
    b, now = playback_clock
    b.togglePlayback()
    now[0] += .105  # a delayed UI tick must catch up to the right recorded frame
    b._advance()
    assert b.frameIndex == 2
    now[0] += .2
    b._advance()
    assert b.frameIndex == 3  # preserve a real long gap, do not invent motion
    now[0] += 2
    b._advance()
    assert b.frameIndex == 4 and not b.playing


def test_playback_speed_changes_preserve_time_and_seek_pauses(playback_clock):
    b, now = playback_clock
    b.togglePlayback()
    now[0] += .04
    b.setPlaybackSpeed(2.)
    now[0] += .03
    b._advance()
    assert b.frameIndex == 2
    b.seekTime(10.11)
    assert b.frameIndex == 3 and not b.playing
    b.togglePlayback()
    b.hide()
    assert not b.playing
    b.togglePlayback()
    assert not b.playing  # hidden dialogs cannot start playback


def test_hand_seek_does_not_fill_empty_frames_and_wraps(playback_clock):
    b, now = playback_clock
    assert b.handFrameCount == 2 and b.frame["lm"] == []
    b.nextHand()
    assert b.frameIndex == 1
    b.nextHand()
    assert b.frameIndex == 3
    b.nextHand()
    assert b.frameIndex == 1
    b.seek(0)
    assert b.frame["lm"] == []


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
                if page in (1, 2):
                    canvases = [c for c in b.window.findChildren(QQuickItem, "studioSkeleton") if c.isVisible()]
                    assert len(canvases) == 1
                    canvas = canvases[0]
                    assert canvas.property("drawHeight") / canvas.property("drawWidth") == pytest.approx(b.studio.selected["frame_aspect"])
                    assert canvas.property("drawWidth") <= canvas.width()
                    assert canvas.property("drawHeight") <= canvas.height()
        before = b.studio.recordings
        b.studio.close()
        b.studio._apply(b.studio.generation, {"recordings": []})
        assert b.studio.recordings == before
    finally: b.close()
