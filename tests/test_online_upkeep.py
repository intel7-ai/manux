"""Spec 9.3 / 10 / 11 / 13 upkeep: duplicate and low-quality samples, automatic rollback, learning priority, emergency stop."""
import sys
from types import SimpleNamespace

import pytest

from src.online.samples import UNLABELED_UNKNOWN, USER_CONFIRMED
from tests.test_online_data import add, as_frame, hand_world, store  # noqa: F401


def test_prune_drops_duplicates_and_unchecked_samples_without_a_hand_but_keeps_what_a_person_labelled(store):  # noqa: F811
    first = add(store, label="pinch", label_source=USER_CONFIRMED, quality_score=.9)
    twin = add(store, label="pinch", label_source=USER_CONFIRMED, quality_score=.9)       # the same joints saved again
    other = add(store, n=12, label="pinch", label_source=USER_CONFIRMED, quality_score=.9)
    poor_mine = add(store, n=14, label="pinch", label_source=USER_CONFIRMED, quality_score=.1)
    poor_unchecked = add(store, n=16, label_source=UNLABELED_UNKNOWN, quality_score=.1)
    assert store.prune() == {"low_quality": 1, "duplicates": 1}
    kept = {s.sample_id for s in store.samples()}
    assert first.sample_id in kept and other.sample_id in kept and poor_mine.sample_id in kept
    assert twin.sample_id not in kept and poor_unchecked.sample_id not in kept


class FakeManager:
    def __init__(self, prev=True):
        self.prev = prev
        self.rolled = False

    def has(self, name):
        return name == "production" or (name == "previous" and self.prev)

    def load(self, name="production"):
        classes = ["none", "a"] if name == "previous" else ["none", "a", "b"]
        return {"info": {"classes": classes}, "state": name}

    def rollback(self):
        self.rolled = True
        return True


def reports(monkeypatch, before, after):
    import src.online.update_job as job
    monkeypatch.setattr(job, "_model", lambda info, n, state: SimpleNamespace(eval=lambda: state))
    monkeypatch.setattr(job, "evaluate_samples", lambda model, data, idx, window, device: before if model == "previous" else after)
    data = SimpleNamespace(samples=[SimpleNamespace(label="a"), SimpleNamespace(label="none")], classes=["none", "a", "b"])
    return job, data


def report(recall, false_fires):
    return {"reps": {"a": [recall, 10]}, "false_fires": false_fires, "none_minutes": 5.0, "macro_f1": recall / 10}


def test_a_production_model_that_fell_behind_the_previous_one_is_rolled_back(monkeypatch):
    job, data = reports(monkeypatch, report(9, 2), report(4, 30))
    manager = FakeManager()
    assert job.auto_rollback(manager, data, [True, True], 15) and manager.rolled
    assert data.classes == ["none", "a", "b"]                     # put back for the training that would follow


def test_an_equal_production_model_stays_and_without_a_previous_one_nothing_happens(monkeypatch):
    job, data = reports(monkeypatch, report(9, 2), report(9, 2))
    manager = FakeManager()
    assert job.auto_rollback(manager, data, [True, True], 15) == [] and not manager.rolled
    assert job.auto_rollback(FakeManager(prev=False), data, [True, True], 15) == []


class FakeJob:
    def __init__(self):
        self.running = True
        self.calls = []
        self.stopped = False

    def set_low_priority(self, low):
        self.calls.append(low)
        return True

    def stop(self):
        self.stopped = True


def test_learning_is_slowed_while_the_camera_lags_and_let_go_after(monkeypatch):
    from src.qt_dashboard import QtDashboard
    board = SimpleNamespace(online_job=FakeJob(), LAG_FPS=20.0, RECOVER_FPS=26.0)
    throttle = QtDashboard.throttle_learning
    for fps in (29, 18, 17, 22, 27):
        throttle(board, fps)
    assert board.online_job.calls == [True, False]                # down once at 18, up once at 27 (22 is in between)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process priority")
def test_the_learning_process_priority_can_really_be_lowered(tmp_path):
    from src.online.app_job import OnlineJob
    job = OnlineJob(tmp_path)
    assert job.start(command=[sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        assert job.set_low_priority(True) and job.set_low_priority(False)
    finally:
        job.stop()


def test_the_emergency_stop_ends_learning():
    from src.qt_dashboard import QtDashboard
    board = SimpleNamespace(online_job=FakeJob(), learn_message="", learnChanged=SimpleNamespace(emit=lambda: None))
    QtDashboard.emergency_stop(board)
    assert board.online_job.stopped and "긴급 정지" in board.learn_message
