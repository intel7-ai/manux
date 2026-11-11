"""Online data collection inside the runtime and the "what was that really?" question (fake camera, no OS input)."""
import os
import time

import numpy as np
import pytest

from src.gesture_engine import GestureEngine
from src.mouse_controller import MouseController
from src.online.collector import OnlineCollector
from src.online.samples import SUPERVISED_SOURCES, USER_CORRECTION, SampleStore
from src.qt_runtime import QtRuntime, UiState
from tests.test_online_data import det, hand_world, store  # noqa: F401
from tests.test_qt_runtime import FakeCamera, FakeTracker


def runtime(tmp_path, enabled=True):
    rt = QtRuntime(camera=FakeCamera(), tracker=FakeTracker(), engine=GestureEngine(),
                   mouse=MouseController(dry_run=True, screen_size=(1920, 1080)), perf_log=None,
                   dry_run=True, log_dir=tmp_path, online_root=tmp_path / "online")
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off")
    rt.engine.config["online_learning_enabled"] = enabled
    return rt


def steps(rt, n):
    for _ in range(n):
        rt._step()
        time.sleep(1 / 200)


def with_world(rt):
    """The fake hand has no world joints; collection needs them."""
    real = rt.tracker.detect

    def detect(frame, roi=None):
        out = real(frame, roi)
        for hand in out:
            hand.world = [tuple(p) for p in hand_world()]
        return out

    rt.tracker.detect = detect


def test_collection_is_off_by_default(tmp_path):
    rt = runtime(tmp_path, enabled=False)
    rt.engine.config.pop("online_learning_enabled")
    with_world(rt)
    steps(rt, 5)
    assert rt.online is None and not (tmp_path / "online").exists() and rt.take_correction() is None


def test_f_saves_the_last_moments_unlabeled_and_nothing_becomes_a_target_by_itself(tmp_path):
    rt = runtime(tmp_path)
    with_world(rt)
    steps(rt, 20)
    rt.post("failure_mark")
    steps(rt, 2)
    rt.online.close()
    sample = rt.take_correction()
    assert sample is not None and sample.extra["reason"] == "manual" and sample.frames >= 15
    assert sample.label == "unknown" and sample.pc
    assert rt.online.store.samples(sources=SUPERVISED_SOURCES) == []


def test_no_correction_without_a_hand_and_a_guided_recording_is_not_collected(tmp_path):
    rt = runtime(tmp_path)
    rt.tracker.present = False
    steps(rt, 5)
    rt.post("failure_mark")
    steps(rt, 2)
    rt.online.close()
    assert rt.take_correction() is None


def test_a_failing_store_switches_collection_off_and_the_loop_goes_on(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    with_world(rt)
    monkeypatch.setattr("src.qt_runtime.SampleStore", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    steps(rt, 3)
    assert rt._online_failed and rt.online is None and rt.frames == 3


def test_the_camera_step_barely_pays_for_collection(tmp_path, store):  # noqa: F811
    collector = OnlineCollector(store)
    hand = det(hand_world(pinch=1.0))
    times = []
    for i in range(600):
        t0 = time.perf_counter()
        collector.frame(i / 30, hand, "scroll_grab" if i % 60 < 30 else None)
        times.append(time.perf_counter() - t0)
    collector.close()
    assert np.median(times) * 1000 < 2.0          # the frame budget is 33 ms
    assert collector.failed == 0


# ---------------------------------------------------------------- the question
@pytest.fixture
def dashboard(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from src.qt_dashboard import QtDashboard
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    board = QtDashboard(dry_run=True)
    assert board.engine.rootObjects(), "dashboard.qml did not load"
    yield board
    app.processEvents()


def unlabeled(store_, guess="thumb_index"):
    from tests.test_online_data import add
    return add(store_, suggested_label=guess, extra={"reason": "manual"})


def test_an_answer_labels_the_sample_as_a_correction(dashboard, store):  # noqa: F811
    sample = unlabeled(store)
    dashboard.show_correction(sample, store)
    assert dashboard.correctionVisible and dashboard.correctionGuess.startswith("엄지+검지")
    assert dashboard.correctionChoices[0]["label"] == "none"
    dashboard.answerCorrection("thumb_ring")
    assert not dashboard.correctionVisible
    saved = store.get(sample.sample_id)
    assert saved.label == "thumb_ring" and saved.label_source == USER_CORRECTION and saved.approved


def test_no_answer_deletes_the_sample_and_f_does_nothing_while_asking(dashboard, store):  # noqa: F811
    sample = unlabeled(store)
    dashboard.show_correction(sample, store)
    dashboard.failure_mark_requested = False
    dashboard.markFailure()
    assert not dashboard.failure_mark_requested
    dashboard.dismissCorrection()
    assert store.get(sample.sample_id) is None and not dashboard.correctionVisible
    dashboard.markFailure()
    assert dashboard.failure_mark_requested


# ---------------------------------------------------------------- adding a gesture inside the runtime
def ready_runtime(tmp_path):
    from src.online.pose_memory import POSE_FILE, PoseMemory
    from src.online.registration import RegistrationConfig
    root = tmp_path / "online"
    memory = PoseMemory()
    far = np.zeros((6, 63), np.float32)
    far[:, 0] = 9.0
    memory.add_known("none", far)
    memory.save(root / POSE_FILE)
    rt = runtime(tmp_path)
    rt.registration_config = RegistrationConfig(demos=3, prep_seconds=.02, do_seconds=.25, rest_seconds=.02)
    with_world(rt)
    return rt


def run_until(rt, states, limit=400):
    for _ in range(limit):
        rt._step()
        time.sleep(1 / 120)
        if (rt._registration_view or {}).get("state") in states:
            return True
    return False


def test_the_whole_registration_runs_with_the_mouse_paused_and_needs_approval(tmp_path, monkeypatch):
    import src.online.registration as reg
    monkeypatch.setattr(reg, "MIN_FRAMES", 3)
    monkeypatch.setattr(reg, "SKIP_DO_FRAMES", 0)
    rt = ready_runtime(tmp_path)
    rt.paused = False
    rt.post("register_start", "주먹")
    rt._step()
    assert rt.paused and rt.registration.active and rt._registration_view["state"] in ("prep", "do", "rest")
    rt.post("key", ord(" "))        # Space must not start the mouse during demonstrations
    rt._step()
    assert rt.paused and rt.registration.active
    assert run_until(rt, ("review", "failed"))
    assert rt._registration_view["state"] == "review", rt._registration_view
    assert "주먹" not in rt.online_memory.classes
    rt.post("register_approve")
    rt._step()
    assert rt._registration_view["state"] == "done" and rt.registration is None
    assert "주먹" in rt.online_memory.classes
    saved = SampleStore(tmp_path / "online").samples(sources=SUPERVISED_SOURCES)
    assert len(saved) == 3 and saved[0].label == "주먹"
    from src.online.pose_memory import PoseMemory
    assert "주먹" in PoseMemory.load(tmp_path / "online" / "pose_memory.json").classes
    rt.online.close()


def test_esc_cancels_the_registration_and_keeps_nothing(tmp_path):
    rt = ready_runtime(tmp_path)
    rt.post("register_start", "주먹")
    rt._step()
    rt.post("key", 27)
    rt._step()
    assert rt.registration is None and rt._registration_view is None and "주먹" not in rt.online_memory.classes


def test_a_bad_name_or_a_missing_encoder_says_so_and_starts_nothing(tmp_path):
    rt = ready_runtime(tmp_path)
    rt.post("register_start", "none")
    rt._step()
    assert rt.registration is None and rt._registration_view["state"] == "failed" and rt._registration_view["message"]
    plain = runtime(tmp_path / "other")
    plain.post("register_start", "주먹")
    plain._step()
    assert plain.registration is None and plain._registration_view["state"] == "failed"


def test_a_memory_for_other_features_is_refused(tmp_path):
    rt = ready_runtime(tmp_path)
    from src.online.pose_memory import PoseMemory
    path = tmp_path / "online" / "pose_memory.json"
    memory = PoseMemory.load(path)
    memory.feature_version = "other"
    memory.save(path)
    rt.post("register_start", "주먹")
    rt._step()
    assert rt.registration is None and rt._registration_view["state"] == "failed"


def test_the_dashboard_queues_commands_and_words_the_report(dashboard):
    dashboard.startRegistration("주먹")
    dashboard.approveRegistration()
    dashboard.cancelRegistration()
    assert dashboard.online_commands == [("register_start", "주먹"), ("register_approve",), ("register_cancel",)]
    dashboard.set_registration({"state": "review", "report": {"reliable": True, "conflicts": [], "confused_with": "", "rest_fires": False}})
    assert dashboard.registrationAdvice == "기존 동작과 잘 구분돼요."
    dashboard.set_registration({"state": "review", "report": {"reliable": False, "conflicts": [{"label": "thumb_ring"}], "rest_fires": True}})
    assert "엄지+약지" in dashboard.registrationAdvice and "thumb_ring" not in dashboard.registrationAdvice
    dashboard.set_registration(None)
    assert dashboard.registration == {"state": ""}


def test_a_recognised_gesture_reaches_the_screen_and_fades_after_the_hand_leaves(tmp_path, dashboard):
    from src.online.pose_memory import pose_features
    from src.online.registration import USER_SOURCE
    rt = ready_runtime(tmp_path)
    frames = rt.tracker.detect(np.zeros((720, 1280, 3), np.uint8))
    hand = frames[0]
    pose = pose_features([{"t": 0.0, "lm": [(p.x, p.y, p.z) for p in hand.landmarks], "world": hand.world}], .5625)
    assert rt._online_load()
    rt.online_memory.add_class("집기", np.repeat(pose, 6, axis=0), demo_ids=np.arange(6) % 3, source=USER_SOURCE)
    steps(rt, 6)
    assert rt._snap.online_gesture == "집기"
    dashboard.set_live_gesture(rt._snap.online_gesture)
    assert dashboard.liveGesture == "집기"
    rt.tracker.present = False
    rt._step()
    time.sleep(1.1)
    rt._step()
    assert rt._snap.online_gesture == ""
    assert rt.paused
    rt.online.close()


def test_in_the_virtual_hand_a_registered_gesture_wins_over_the_built_in_shapes(tmp_path):
    """The built-in shapes are few on purpose; the rest is the person's own gestures. A registered one that looks like a thumb +
    index pinch is that gesture, not a click."""
    import dataclasses
    from src.online.pose_memory import pose_features
    from src.online.registration import USER_SOURCE
    rt = ready_runtime(tmp_path)
    rt.ui = dataclasses.replace(rt.ui, use_index_pointer=False, virtual_hand=True, virtual_box=(0.0, 0.0, 1.0, 1.0))
    rt.paused = False
    hand = rt.tracker.hand
    tip = hand[8]
    hand[4] = type(tip)(tip.x, tip.y, tip.z)                  # the registered gesture is a thumb + index pinch shape
    hand[12] = type(tip)(hand[9].x, hand[9].y, hand[9].z)
    frames = rt.tracker.detect(np.zeros((720, 1280, 3), np.uint8))
    pose = pose_features([{"t": 0.0, "lm": [(p.x, p.y, p.z) for p in frames[0].landmarks], "world": frames[0].world}], .5625)
    assert rt._online_load()
    rt.online_memory.add_class("집기", np.repeat(pose, 6, axis=0), demo_ids=np.arange(6) % 3, source=USER_SOURCE)
    steps(rt, 6)
    assert rt._snap.online_gesture == "집기"
    assert "left_click" not in rt.mouse.event_log and not rt.mouse.is_dragging
    rt.online.close()
