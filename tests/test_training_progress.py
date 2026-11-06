from src.training_center import TrainingJob
from src.training_progress import duration, progress_details


def job_at(monkeypatch, start=1000, epochs=10, folds=2):
    clock = [float(start)]
    monkeypatch.setattr("src.training_center.time.time", lambda: clock[0])
    job = TrainingJob()
    job.state = {"running": True, "started": start, "epochs": epochs, "epoch": 0, "folds": 0, "fold": 0,
                 "progress": 0., "tasks": ["thumb_ring"], "phase": "loading"}
    job._apply({"event": "loaded", "windows": 500, "recordings": 2, "postures": [0, 1], "folds": folds})
    return job, clock


def test_eta_waits_for_observed_speed_then_decreases(monkeypatch):
    job, clock = job_at(monkeypatch)
    assert progress_details(job.snapshot(), clock[0])["etaSeconds"] == -1
    job._apply({"event": "fold_start", "fold": 1, "posture": 0})
    clock[0] += 2
    job._apply({"event": "epoch", "epoch": 1, "loss": 1.2})
    assert progress_details(job.snapshot(), clock[0])["remaining"] == "계산 중"
    clock[0] += 2
    job._apply({"event": "epoch", "epoch": 2, "loss": .8})
    view = progress_details(job.snapshot(), clock[0])
    assert view["etaSeconds"] == 56  # 28 remaining epochs at 2 seconds each
    assert view["remaining"] == "약 0분 56초" and view["finish"] != "계산 중"
    assert view["percent"] == 6 and view["elapsed"] == "0분 04초"
    assert "엄지+약지" in view["tasks"] and "500" in view["data"]
    assert progress_details(job.snapshot(), clock[0] + 1)["etaSeconds"] == 55


def test_skipped_evaluations_and_final_save_have_monotonic_progress(monkeypatch):
    job, clock = job_at(monkeypatch, epochs=2, folds=2)
    values = []
    for event in [
        {"event": "fold_skip", "fold": 1},
        {"event": "fold_start", "fold": 2, "posture": 1},
        {"event": "epoch", "epoch": 1, "loss": .9},
        {"event": "epoch", "epoch": 2, "loss": .5},
        {"event": "evaluation_start", "posture": 1},
        {"event": "evaluation_done", "fold": 2},
        {"event": "final_start"},
        {"event": "epoch", "epoch": 1, "loss": .7},
        {"event": "epoch", "epoch": 2, "loss": .3},
        {"event": "evaluation_start"},
        {"event": "export_start"},
    ]:
        clock[0] += 2
        job._apply(event)
        values.append(job.snapshot()["progress"])
    assert values == sorted(values) and max(values) < 1
    before = progress_details(job.snapshot(), clock[0])
    assert before["percent"] == 98 and before["remaining"] == "마무리 중"
    assert before["losses"] == [.7, .3]  # the new stage never inherits an old curve
    job._apply({"event": "done", "tasks": ["thumb_ring"]})
    job._finish(0)
    view = progress_details(job.snapshot(), clock[0] + 30)
    assert view["status"] == "success" and view["percent"] == 100
    assert view["remaining"] == "완료" and view["etaSeconds"] == -1
    assert view["elapsed"] == before["elapsed"]  # no running stopwatch after completion


def test_error_cancel_and_unexpected_exit_are_not_success(monkeypatch):
    for kind in ("error", "cancelled", "unexpected"):
        job, clock = job_at(monkeypatch)
        job._apply({"event": "fold_start", "fold": 1})
        clock[0] += 2
        job._apply({"event": "epoch", "epoch": 1, "loss": .5})
        if kind == "error":
            job._apply({"event": "error", "text": "사용 가능한 녹화가 없습니다"})
        if kind == "cancelled":
            job.state["cancelled"] = True
            assert progress_details(job.snapshot())["status"] == "stopping"
        job._finish(0 if kind == "unexpected" else 1)
        view = progress_details(job.snapshot(), clock[0] + 10)
        assert view["status"] == ("cancelled" if kind == "cancelled" else "error")
        assert view["percent"] < 100 and view["etaSeconds"] == -1 and view["finish"] == "—"


def test_new_stage_drops_old_loss_and_snapshots_do_not_mutate_live_state(monkeypatch):
    job, clock = job_at(monkeypatch)
    job._apply({"event": "fold_start", "fold": 1})
    clock[0] += 1
    job._apply({"event": "epoch", "epoch": 1, "loss": .5})
    snapshot = job.snapshot()
    snapshot["losses"].append(999)
    assert job.snapshot()["losses"] == [.5]
    job._apply({"event": "final_start"})
    view = progress_details(job.snapshot())
    assert view["loss"] == "—" and view["losses"] == []


def test_idle_state_and_long_durations():
    view = progress_details({"running": False}, now=100)
    assert view["status"] == "idle" and view["percent"] == 0
    assert view["remaining"] == view["finish"] == "—" and view["epoch"] == "—"
    assert duration(3661) == "1시간 1분"


def test_progress_history_is_bounded(monkeypatch):
    job, _ = job_at(monkeypatch)
    for i in range(130):
        job._apply({"event": "note", "text": f"안내 {i}"})
    history = progress_details(job.snapshot())["events"]
    assert len(history) == 100 and history[-1].endswith("안내 129")


def test_closing_progress_keeps_updates_until_training_finishes():
    from types import SimpleNamespace
    from src.qt_dashboard import QtDashboard
    active = []
    stub = SimpleNamespace(_training_center_open=False, _training_progress_open=True,
                           training_job=SimpleNamespace(running=True), training_busy="",
                           training_timer=SimpleNamespace(start=lambda: active.append(True),
                                                          stop=lambda: active.append(False)))
    stub._sync_training_timer = lambda: QtDashboard._sync_training_timer(stub)
    QtDashboard.closeTrainingProgress(stub)
    assert active[-1] is True and stub.training_job.running
    stub.training_job.running = False
    QtDashboard._sync_training_timer(stub)
    assert active[-1] is False
