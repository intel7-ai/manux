"""Exercise the original UI through Qt against the v0.4 services, not a mock dashboard."""
import time
import math
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt, qInstallMessageHandler
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from manux.app import schema, training_specs
from manux.data import capture_plan
from manux.data.training_center import TrainingCenterService
from manux.desktop import build, make_pose_studio
from manux.data.pose_studio import STUDIO_ACTIONS, STUDIO_CATEGORIES
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


def grab_item(item):
    result = item.grabToImage()
    assert result is not None, "visible QML item must support grabbing"
    for _ in range(40):
        QTest.qWait(25)
        if not result.image().isNull():
            return result.image()
    raise AssertionError("QML canvas did not render")


def test_pose_panel_button_collects_streams_stops_and_saves(studio, monkeypatch):
    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    monkeypatch.setattr(bridge.studio, "_run", lambda *args, **kwargs: None)
    center = bridge.window.findChild(QObject, "studioDataManagement")
    center.setProperty("pageIndex", 5)
    QMetaObject.invokeMethod(center, "open")
    QTest.qWait(100)
    bridge.setStudioDiscrepancyOnly(False)
    bridge.setStudioCaptureMode("snapshot")
    points = tuple((.2+.1*(i%5), .25+.1*(i//5), 0.) for i in range(21))
    bridge.apply_view(ViewFrame(None, landmarks=points))
    button = next(b for b in bridge.window.findChildren(QQuickItem, "capturePoseSample") if b.isVisible())
    assert button.property("enabled")
    assert QMetaObject.invokeMethod(button, "click")
    assert len(bridge.pose_studio.samples) == 1
    bridge.setStudioCaptureMode("stream")
    assert QMetaObject.invokeMethod(button, "click")
    assert bridge.studioStreaming
    QTest.qWait(60)
    bridge.apply_view(ViewFrame(None, landmarks=points))
    assert len(bridge.pose_studio.samples) >= 2
    assert QMetaObject.invokeMethod(button, "click")
    assert not bridge.studioStreaming
    count = len(bridge.pose_studio.samples)
    save = next(b for b in bridge.window.findChildren(QQuickItem, "savePoseSamples") if b.isVisible())
    assert QMetaObject.invokeMethod(save, "click")
    assert not bridge.pose_studio.samples
    assert len(bridge.pose_studio.load_all_samples()) == count
    assert bridge.paused and not runtime.engine.state.control_on
    assert not [m for m in messages if "Error" in m or "has no attribute" in m], messages


@pytest.mark.parametrize("action", ["triggerStudioCapture", "trainStudioModel", "toggleSpatialGuard"])
def test_pose_actions_check_worker_busy_not_dataset_attribute(studio, monkeypatch, action):
    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    calls = []
    monkeypatch.setattr(bridge.pose_studio, "toggle_stream_or_snapshot", lambda: calls.append("capture"))
    monkeypatch.setattr(service, "pose_train", lambda model_id: calls.append("train"))
    monkeypatch.setattr(runtime, "request", lambda request: calls.append(request.kind))
    service.studio_job.state = {"running": True}
    getattr(bridge, action)()
    assert calls == []
    service.studio_job.state = {"running": False}
    getattr(bridge, action)()
    assert calls == [{"triggerStudioCapture": "capture", "trainStudioModel": "train", "toggleSpatialGuard": "spatial_guard"}[action]]


def test_main_header_uses_existing_manux_ci_at_both_window_sizes(studio):
    bridge, service, runtime, messages = studio
    for width, height in ((780, 560), (1140, 720)):
        bridge.window.resize(width, height)
        QTest.qWait(80)
        logo = bridge.window.findChild(QQuickItem, "mainBrandLogo")
        assert logo is not None and logo.isVisible()
        assert str(logo.property("source").toString()).endswith("/assets/manux.svg")
        image = grab_item(logo)
        assert not image.isNull()
        assert any(image.pixelColor(x, y).alpha() > 0
                   for x in range(image.width()) for y in range(image.height()))
        toggle = visual_child(bridge.window.contentItem(), "mainControlToggle")
        edge = toggle.mapToScene(QPointF(toggle.width(), toggle.height()))
        assert edge.x() <= width and edge.y() <= height


def test_idle_main_camera_renders_21_points_bones_and_clears_when_hand_leaves(studio):
    import numpy as np
    bridge, service, runtime, messages = studio
    bridge.use_index_pointer = False
    bridge.timer.stop()  # this test supplies the latest camera frames explicitly
    assert bridge.paused and bridge.rec_mode == "off"
    raw = np.zeros((480, 640, 3), dtype=np.uint8)
    landmarks = tuple((.20+.12*(i%5), .25+.10*(i//5), 0.) for i in range(21))
    view = ViewFrame(None, image=raw, landmarks=landmarks,
                     preview_anchor=(.85, .8), preview_moving=True, preview_guides=((12, "pinched"),))
    bridge.last_preview = 0
    bridge.apply_view(view)
    QTest.qWait(120)
    # Camera data stays raw; overlay rendering belongs to the live QML canvas.
    assert bridge.provider.image.pixelColor(320, 240).name() == "#000000"
    assert not raw.any()
    canvases = bridge.window.findChildren(QQuickItem, "liveHandSkeletonCanvas")
    canvas = next((c for c in canvases if c.isVisible() and c.width() > 0), None)
    assert canvas is not None
    image = grab_item(canvas)
    w, h = image.width(), image.height()
    assert not [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "Binding loop"))], messages
    for x, y, _ in landmarks:
        assert image.pixelColor(round(x*w), round(y*h)).alpha() > 0
    # A bone midpoint and anchor ring must be drawn, too.
    midpoint = tuple((landmarks[0][i]+landmarks[1][i])/2 for i in range(2))
    assert image.pixelColor(round(midpoint[0]*w), round(midpoint[1]*h)).alpha() > 0
    assert image.pixelColor(round(.85*w)+10, round(.8*h)).alpha() > 0
    bridge.last_preview = 0
    bridge.apply_view(ViewFrame(None, image=raw))
    QTest.qWait(100)
    cleared = grab_item(canvas)
    for x, y, _ in landmarks:
        assert cleared.pixelColor(round(x*w), round(y*h)).alpha() == 0
    assert cleared.pixelColor(round(.85*w)+10, round(.8*h)).alpha() == 0
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


def test_vertical_input_choice_applies_and_cancel_keeps_saved_choice(studio):
    from PySide6.QtCore import Q_ARG
    from manux.core.contracts import OutputTarget
    from manux.settings.store import load
    bridge, service, runtime, messages = studio
    runtime.pointer_setup_dir = bridge.config_dir
    assert bridge.pointerSetupAvailable
    bridge.command(111)
    QTest.qWait(60)
    dialog = bridge.window.findChild(QObject, "studioSettings")
    tabs = visual_child(bridge.window.contentItem(), "sensitivityTabs")
    tabs.setProperty("currentIndex", 1)
    QTest.qWait(30)
    choice = visual_child(bridge.window.contentItem(), "sensitivity-choice-cursor.vertical_input")
    apply = visual_child(bridge.window.contentItem(), "applySensitivity")
    assert choice.isVisible() and choice.property("count") == 2
    before = bridge.settings.stored()
    choice.setProperty("currentIndex", 0)
    QMetaObject.invokeMethod(choice, "activated", Q_ARG(int, 0))
    assert bridge.settings.stored() == before
    QMetaObject.invokeMethod(apply, "clicked")
    runtime._drain_runtime_requests()
    bridge.poll()
    assert load(schema(), bridge.config_dir).get("cursor.vertical_input") == "hand_position"
    assert runtime.engine.sinks[OutputTarget.PRACTICE].config.vertical_input == "hand_position"
    assert not bridge.pointerSetupAvailable
    assert bridge.paused and not bridge.sensitivityPending
    choice = visual_child(bridge.window.contentItem(), "sensitivity-choice-cursor.vertical_input")
    choice.setProperty("currentIndex", 1)
    QMetaObject.invokeMethod(choice, "activated", Q_ARG(int, 1))
    QMetaObject.invokeMethod(dialog, "close")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(30)
    choice = visual_child(bridge.window.contentItem(), "sensitivity-choice-cursor.vertical_input")
    assert choice.property("currentIndex") == 0
    assert bridge.settings.get("cursor.vertical_input") == "hand_position"
    assert not [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "Binding loop"))], messages


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
            "valid_palm_axes": valid_axes, "palm_point": palm_point,
            "pose_studio": make_pose_studio(tmp_path / "data"),
            "studio_actions": lambda: list(STUDIO_ACTIONS),
            "studio_categories": lambda: list(STUDIO_CATEGORIES)})
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


def test_small_window_keeps_control_and_tutorial_footer_inside_viewport(studio):
    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    bridge.window.resize(780, 560)
    QTest.qWait(80)
    toggle = visual_child(bridge.window.contentItem(), "mainControlToggle")
    assert toggle is not None and toggle.isVisible()
    edge = toggle.mapToScene(QPointF(toggle.width(), toggle.height()))
    assert edge.x() <= bridge.window.width() and edge.y() <= bridge.window.height()
    bridge.control_hand = "Right"
    bridge.beginTutorial()
    QTest.qWait(80)
    page = bridge.window.findChild(QQuickItem, "onboardingPage")
    advance = visual_child(bridge.window.contentItem(), "tutorialAdvance")
    assert page is not None and page.isVisible() and advance is not None
    bottom = advance.mapToScene(QPointF(advance.width(), advance.height()))
    assert bottom.x() <= bridge.window.width() and bottom.y() <= bridge.window.height()
    assert not advance.property("enabled")  # camera confirmation has not succeeded
    assert not [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "Binding loop"))], messages


def test_small_learning_center_can_scroll_to_all_six_tabs_and_player_controls(studio):
    bridge, service, runtime, messages = studio
    bridge.timer.stop()
    bridge.window.resize(780, 560)
    center = bridge.window.findChild(QObject, "studioDataManagement")
    center.setProperty("pageIndex", 1)
    QMetaObject.invokeMethod(center, "open")
    QTest.qWait(100)
    scroll = bridge.window.findChild(QQuickItem, "learningNavigationScroll")
    flick = scroll.property("contentItem")
    max_y = max(0., flick.property("contentHeight") - flick.height())
    flick.setProperty("contentY", max_y)
    QTest.qWait(60)
    tab = visual_child(bridge.window.contentItem(), "studioTab_5")
    assert tab is not None
    top = tab.mapToScene(QPointF(0, 0)).y()
    bottom = tab.mapToScene(QPointF(0, tab.height())).y()
    viewport_top = scroll.mapToScene(QPointF(0, 0)).y()
    assert viewport_top <= top and bottom <= viewport_top + scroll.height()
    detail = bridge.window.findChild(QQuickItem, "recordingDetailScroll")
    flick = detail.property("contentItem")
    assert flick.property("contentHeight") > flick.height()
    play = visual_child(bridge.window.contentItem(), "studioPlay")
    # Scroll the actual content, then verify the playback button fits the viewport.
    viewport_top = detail.mapToScene(QPointF(0, 0)).y()
    play_top = play.mapToScene(QPointF(0, 0)).y()
    target_y = flick.property("contentY") + play_top - viewport_top
    flick.setProperty("contentY", max(0., min(target_y, flick.property("contentHeight")-flick.height())))
    QTest.qWait(60)
    play_top = play.mapToScene(QPointF(0, 0)).y()
    play_bottom = play.mapToScene(QPointF(0, play.height())).y()
    assert viewport_top <= play_top and play_bottom <= viewport_top + detail.height()
    assert not [m for m in messages if any(s in m for s in ("ReferenceError", "TypeError", "Error:", "Cannot assign", "Binding loop"))], messages
