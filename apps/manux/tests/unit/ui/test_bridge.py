"""The QML bridge with an offscreen Qt and a fake runtime. Skipped when PySide6 (the `ui` extra) is missing."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtQml")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from manux.app import schema  # noqa: E402
from manux.core.contracts import Stage  # noqa: E402
from manux.engine.commands import CommandQueue, RequestMode, SetBaseMode, SetControl, SetStage  # noqa: E402
from manux.settings.store import Settings  # noqa: E402
from manux.ui.view import SeenHand, ViewFrame  # noqa: E402


class FakeEngine:
    def __init__(self):
        self.commands = CommandQueue()


class FakeRuntime:
    def __init__(self, settings):
        self.engine = FakeEngine()
        self.settings = settings
        self.control_hand = ""
        self.use_crop = False
        self.want_image = True
        self.gain = None
        self.overrides = {}
        self.view = (0, ViewFrame(None))

    def set_pointer_gain(self, gain):
        self.gain = gain

    def set_override(self, fid, key, value):
        self.overrides[(fid, key)] = value

    def set_recording(self, mode, focus="", plan=None, **identity):
        self.rec_call = (mode, focus, plan)
        self.rec_identity = identity

    def advance_recording(self):
        self.advanced = getattr(self, "advanced", 0) + 1

    def set_recording_paused(self, paused):
        self.rec_paused = paused

    def latest(self):
        return self.view


@pytest.fixture()
def bridge(tmp_path):
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    b = Bridge(runtime, settings, tmp_path)
    yield b, runtime
    b.close()


def drained(runtime):
    return runtime.engine.commands.drain()


def test_starts_on_the_splash_with_pinch_as_base_mode(bridge):
    b, rt = bridge
    cmds = drained(rt)
    assert SetBaseMode("pinch") in cmds and SetStage(Stage.SPLASH) in cmds
    assert b.stage == "splash" and rt.gain == 1.0
    b.finishSplash()
    assert b.stage == "welcome" and SetStage(Stage.WELCOME) in drained(rt)   # no hand chosen yet


def test_space_turns_control_on_and_settings_changes_pause_it(bridge, tmp_path):
    b, rt = bridge
    b.control_hand = "Right"
    b.finishSplash()
    drained(rt)
    b.command(32)
    assert SetControl(True) in drained(rt) and b.controlLabel in ("일시정지", "제어 시작")
    b.setClickStyle("trigger")
    assert SetControl(False) in drained(rt)
    assert b.clickStyle == "trigger" and b.settings.get("pointer.click_style") == "trigger"
    assert (tmp_path / "settings.json").exists()                   # saved to the user folder


def test_hand_choice_saves_the_hand_and_starts_the_tutorial(bridge):
    b, rt = bridge
    b.beginHandSetup()
    t0 = __import__("time").perf_counter()
    b.choice.since = t0 - 10                                      # pretend the hand was held long enough
    for _ in range(6):
        b.choice.last_at = __import__("time").perf_counter()
        b.choice.side = "Left"
        b.choice.frames += 1
    b._choose(ViewFrame(None, hands=(SeenHand("Left", 0.95),)))
    assert b.chosenHand == "왼손" and rt.control_hand == "Left"
    assert b.stage == "tutorial" and b.tutorialCount > 0
    assert SetStage(Stage.TUTORIAL) in drained(rt)


def test_motion_toggle_and_card_switches(bridge):
    b, rt = bridge
    b.control_hand = "Right"
    b.finishSplash()
    drained(rt)
    b.toggleMotion()
    assert RequestMode("motion") in drained(rt)
    b.setCardEnabled("drag", False)
    assert b.settings.get("pinch.long_press_drag") is False
    assert "drag" not in [c[0] for c in b.gestureCards]


def test_training_center_and_recording_plan_in_bridge(tmp_path):
    from manux.data.training_center import TrainingCenterService
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    tc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    tc.configure("tester01")
    b = Bridge(runtime, settings, tmp_path / "config", services={"training_center": tc})
    try:
        b.control_hand = "Right"
        b.finishSplash()
        assert b.stage == "main"
        assert b.focusRecord("thumb_ring") is True
        assert b.recordingPlan.title.startswith("엄지+약지")
        assert b.recordingPlan.start() is True, b.recordingPlan.message
        assert b.recMode == "guided" and b.recFocus == "thumb_ring"
        assert runtime.rec_call[0] == "guided" and runtime.rec_call[1] == "thumb_ring"
        assert runtime.rec_identity == {"profile": "tester01", "split": "train"}
        b.toggleRecordingPause()
        assert b.recPaused is True and runtime.rec_paused is True
        b.setRecMode("off")
        assert b.recMode == "off"
    finally:
        b.close()


def test_training_center_profile_eval_and_confirmed_upload(tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    from manux.data.training_center import TrainingCenterService
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    tc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    b = Bridge(runtime, settings, tmp_path / "config", services={"training_center": tc})
    calls = []
    monkeypatch.setattr(tc, "hub_action", lambda action, classes=(): calls.append((action,list(classes))))
    try:
        b.control_hand = "Right"
        b.finishSplash()
        assert not b.focusRecord("thumb_ring")
        b.saveTrainingProfile("synthetic")
        b.setRecordingSplit("eval")
        b.setRecMode("guided")
        assert runtime.rec_identity["split"] == "eval"
        b.runHub("upload")
        assert calls == []  # active recording prevents uploading incomplete work
        b.setRecMode("off")
        popup = b.window.findChild(QObject, "trainingCenter")
        confirmation = b.window.findChild(QObject, "uploadConfirm")
        assert popup is not None and confirmation is not None
        assert QMetaObject.invokeMethod(popup, "open")
        QTest.qWait(100)
        assert popup.property("visible")
        for _ in range(50):
            QTest.qWait(50)
            if not b.studio.busy:
                break
        assert QMetaObject.invokeMethod(confirmation, "open")
        QTest.qWait(100)
        assert confirmation.property("visible") and calls == []
        assert QMetaObject.invokeMethod(confirmation, "accept")
        QTest.qWait(100)
        assert calls == [("upload", [])]
    finally:
        b.close()


def test_checked_subset_persists_across_filters_and_records_exact_tasks(tmp_path, monkeypatch):
    from manux.data.training_center import TrainingCenterService
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    runtime = FakeRuntime(settings)
    tc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings",
        specs={"pinch.thumb_ring": {"kind": "tap", "modes": ["pinch"], "point": "none"}})
    tc.configure("synthetic")
    b = Bridge(runtime, settings, tmp_path / "config", services={"training_center": tc})
    try:
        b.control_hand = "Right"
        b.finishSplash()
        b.training_rows = tc.summary_and_rows()[2]
        assert not b.recordTrainingSelection()
        b.setTrainingSelected("thumb_ring", True)
        b.setTrainingSelected("none", True)
        b.setTrainingSelected("invalid", True)
        b.setTrainingFilter("창·모드")
        assert b.selectedTrainingKeys == ["thumb_ring", "none"]
        assert b.selectionCanTrain
        calls = []
        monkeypatch.setattr(tc.job, "start", lambda **kwargs: calls.append(kwargs) or True)
        b.trainTrainingSelection()
        assert calls == [{"gestures": ["thumb_ring"]}]
        assert b.recordTrainingSelection()
        # Changing selection after opening the planner must not change the queued subset.
        b.setTrainingSelected("thumb_index", True)
        assert b.recordingPlan.start(), b.recordingPlan.message
        assert runtime.rec_call[1] == ("thumb_ring", "none")
        assert runtime.rec_identity == {"profile": "synthetic", "split": "train"}
        assert not b.recordTrainingSelection()
    finally:
        b.close()


def test_capture_only_or_empty_selection_does_not_start_training(tmp_path, monkeypatch):
    from manux.data.training_center import TrainingCenterService
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    tc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    b = Bridge(FakeRuntime(settings), settings, tmp_path / "config", services={"training_center": tc})
    try:
        b.training_rows = tc.summary_and_rows()[2]
        calls = []
        monkeypatch.setattr(tc.job, "start", lambda **kwargs: calls.append(kwargs))
        b.trainTrainingSelection()
        b.setTrainingSelected("swipe_left", True)
        assert not b.selectionCanTrain
        b.trainTrainingSelection()
        assert not calls
    finally:
        b.close()


def test_qml_checkbox_drives_selection_and_record_button(tmp_path):
    from PySide6.QtCore import QObject, QPointF, QMetaObject, Qt
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from manux.data.training_center import TrainingCenterService
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    tc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    tc.configure("synthetic")
    b = Bridge(FakeRuntime(settings), settings, tmp_path / "config", services={"training_center": tc})
    try:
        b.control_hand = "Right"
        b.finishSplash()
        popup = b.window.findChild(QObject, "trainingCenter")
        QMetaObject.invokeMethod(popup, "open")
        # The bridge publishes background summaries on its 400 ms UI timer.
        for _ in range(40):
            QTest.qWait(50)
            if not b.trainingBusy and b.trainingRows:
                b.trainingChanged.emit()
                QTest.qWait(50)
                break
        record = b.window.findChild(QQuickItem, "recordSelection")
        assert not record.property("enabled")
        def visual_child(item, name):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                found = visual_child(child, name)
                if found is not None:
                    return found
            return None

        task_list = b.window.findChild(QQuickItem, "trainingTasks")
        checkbox = visual_child(task_list, "taskCheck_thumb_middle")
        assert checkbox is not None
        position = checkbox.mapToScene(QPointF(checkbox.width() / 2, checkbox.height() / 2)).toPoint()
        QTest.mouseClick(b.window, Qt.MouseButton.LeftButton, pos=position)
        assert b.selectedTrainingKeys == ["thumb_middle"]
        assert record.property("enabled")
        assert b.window.findChild(QObject, "hubCredential") is None
        assert not b.window.findChild(QQuickItem, "uploadRecordings").property("enabled")
        assert QMetaObject.invokeMethod(record, "click")
        QTest.qWait(100)
        assert b.recordingPlan.focus == ("thumb_middle",)
        assert not popup.property("visible")
        for width, height in ((780, 560), (1140, 720)):
            b.window.setWidth(width)
            b.window.setHeight(height)
            QTest.qWait(100)
            form = b.window.findChild(QQuickItem, "recordingForm")
            assert form.width() > 0
            assert not b.window.grabWindow().isNull()
    finally:
        b.close()


def test_alias_keyboard_survives_polling_and_starts_selected_recording(tmp_path):
    """First-run path: type, wait through polling, save, and click record in the real QML."""
    from PySide6.QtCore import QObject, QPointF, QMetaObject, Qt
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from manux.data.training_center import TrainingCenterService
    from manux.ui.bridge import Bridge
    settings = Settings(schema())
    tc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    b = Bridge(FakeRuntime(settings), settings, tmp_path / "config", services={"training_center": tc})
    try:
        b.control_hand = "Right"
        b.finishSplash()
        popup = b.window.findChild(QObject, "trainingCenter")
        QMetaObject.invokeMethod(popup, "open")
        for _ in range(40):
            QTest.qWait(50)
            if not b.trainingBusy and b.trainingRows:
                break
        b.setTrainingSelected("thumb_ring", True)
        record = b.window.findChild(QQuickItem, "recordSelection")

        def click(item):
            position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mouseClick(b.window, Qt.MouseButton.LeftButton, pos=position)
            QTest.qWait(50)

        click(record)
        message = b.window.findChild(QQuickItem, "recordSelectionMessage")
        assert popup.property("visible")
        assert message.property("visible") and "별명" in message.property("text")
        alias = b.window.findChild(QQuickItem, "trainingAlias")
        click(alias)
        # Include every main-window letter shortcut; slow typing crosses the 400 ms timer.
        for char in "qomnfcp01":
            QTest.keyClick(b.window, Qt.Key(ord(char.upper())))
            QTest.qWait(450)
        assert alias.property("text") == "qomnfcp01"
        assert not b.closed
        assert tc.profile == ""  # edits are drafts until saved
        click(b.window.findChild(QQuickItem, "saveTrainingAlias"))
        assert tc.profile == "qomnfcp01"
        for _ in range(40):
            QTest.qWait(50)
            if record.property("enabled"):
                break
        assert record.property("enabled")
        click(record)
        assert not popup.property("visible")
        assert b.recordingPlan.focus == ("thumb_ring",)
        assert b.recordingPlan.start(), b.recordingPlan.message
        assert b.runtime.rec_identity == {"profile": "qomnfcp01", "split": "train"}
    finally:
        b.close()


def test_read_connection_does_not_enable_upload_without_verified_write_access(bridge):
    b, _ = bridge
    b.tc.hub_job.state = {"result": {"repo": "synthetic/data", "upload_access": False}}
    b.tc.hub_job.snapshot = lambda: b.tc.hub_job.state
    assert b.hubConnected and not b.hubCanUpload
    b.tc.hub_job.state["result"]["upload_access"] = True
    assert b.hubCanUpload


def test_runtime_frame_latency_is_shown_without_relabeling_inference_time(bridge):
    b, _ = bridge
    b.apply_view(ViewFrame(None, fps=30.0, detect_ms=7.0, frame_age_ms=22.0))
    assert "추론 7.0 ms" in b.state[-1]
    assert "앱 내 프레임 지연 22.0 ms" in b.state[-1]
