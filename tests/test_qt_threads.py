"""A worker thread must not start or stop the dashboard's QTimer (Qt prints "Timers cannot be started / stopped from
another thread" and does nothing), and the file that says which recordings are excluded must not lose a change."""
import os
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def dashboard():
    from src.qt_dashboard import QtDashboard

    app = QApplication.instance() or QApplication([])
    messages = []
    qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))
    ui = QtDashboard()
    messages.clear()
    yield app, ui, messages
    qInstallMessageHandler(None)


def from_a_thread(fn):
    t = threading.Thread(target=fn)
    t.start()
    t.join()


def test_a_worker_thread_can_start_and_stop_the_timer_through_the_dashboard(dashboard):
    app, ui, messages = dashboard
    ui.training_timer.start()
    from_a_thread(lambda: (ui._timer_start(), ui._timer_stop()))
    app.processEvents()
    assert not [m for m in messages if "Timers cannot be" in m]
    assert not ui.training_timer.isActive()                 # the stop was carried out on the screen thread
    from_a_thread(ui._timer_start)
    app.processEvents()
    assert ui.training_timer.isActive()
    ui.training_timer.stop()


def test_a_refresh_asked_from_a_worker_thread_runs_on_the_screen_thread(dashboard, monkeypatch):
    app, ui, messages = dashboard
    import src.training_center as tc

    monkeypatch.setattr(tc, "dataset_summary", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("test")))
    ui.training_note = ""
    from_a_thread(ui.refreshTraining)
    deadline = time.time() + 5
    while time.time() < deadline and "읽지 못했어요" not in ui.training_note:
        app.processEvents()
        time.sleep(0.02)
    assert "읽지 못했어요" in ui.training_note              # it really ran (the failing summary was read once)
    app.processEvents()
    assert not [m for m in messages if "Timers cannot be" in m]
    ui.training_timer.stop()


def test_two_threads_marking_recordings_do_not_lose_each_others_exclusions(tmp_path):
    import src.training_center as tc

    names = [f"rec-{i}.jsonl.gz" for i in range(40)]

    def mark(chunk):
        for n in chunk:
            tc.set_recording_selected(n, False, tmp_path)

    threads = [threading.Thread(target=mark, args=(names[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(tc.load_training_selection(tmp_path)["excluded"]) == set(names)


def test_a_saved_recording_shows_checking_first_and_the_result_later_and_then_reads_the_lists_again(dashboard, monkeypatch):
    app, ui, messages = dashboard
    refreshed = []
    monkeypatch.setattr(ui, "refreshTraining", lambda: refreshed.append(1))
    base = {"mode": "off", "file": "a.jsonl.gz", "sub": "녹화 저장"}
    ui._review_saved_recording({**base, "quality_status": "pending", "quality_message": "품질 검사 중…"})
    assert ui.last_rec_review["status"] == "pending" and not refreshed      # nothing to read again yet
    ui._review_saved_recording({**base, "quality_status": "pending", "quality_message": "품질 검사 중…"})
    ui._review_saved_recording({**base, "quality_status": "fail", "quality_score": 0.1, "quality_message": "품질 미달로 학습 제외"})
    assert ui.last_rec_review["status"] == "fail" and refreshed == [1]      # the result replaced it, the lists are re-read once
    ui._review_saved_recording({**base, "quality_status": "fail", "quality_score": 0.1, "quality_message": "품질 미달로 학습 제외"})
    assert refreshed == [1]
