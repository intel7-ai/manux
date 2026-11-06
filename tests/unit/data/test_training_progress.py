"""Worker lifecycle and measured ETA, without a real subprocess or clock waits."""
import io
import json
from types import SimpleNamespace

import pytest

from manux.data.training_progress import view
from manux.data.worker_job import WorkerJob


def test_eta_needs_measurements_and_includes_remaining_selected_classifiers():
    state = dict(running=True, started=100, progress=.3, phase="training", epoch=3, epochs=10,
                 step=1, steps=2, last_epoch_at=108, task_names=["좌클릭"])
    assert view(state, 109)["etaSeconds"] == -1
    measured = view({**state, "epoch_seconds": [2., 2.]}, 109)
    assert measured["etaSeconds"] == 33 and measured["tasks"] == "좌클릭"
    assert measured["elapsed"] == "9초" and measured["percent"] == 30
    evaluating = view({**state, "phase": "evaluating", "progress": 1.}, 109)
    assert evaluating["percent"] == 99 and evaluating["etaSeconds"] == -1
    assert evaluating["remaining"] == "검증·저장 중"


@pytest.mark.parametrize("code,done,expected", [(0, True, "success"), (1, True, "error"), (0, False, "error")])
def test_only_done_and_successful_exit_can_report_100(tmp_path, code, done, expected):
    callbacks = []
    job = WorkerJob(tmp_path, on_done=callbacks.append)
    job.state = dict(job_id="current", running=True, started=100.)
    job._cancel = tmp_path / "cancel"
    events = [{"event": "progress", "progress": 1., "phase": "evaluating"}]
    if done:
        events.append({"event": "done", "stage": "saved"})
    proc = SimpleNamespace(stdout=io.StringIO("\n".join(map(json.dumps, events))), wait=lambda: code)
    job._read(proc, {"job_id": "current"})
    result = view(job.snapshot())
    assert result["status"] == expected
    assert (result["percent"] == 100) == (expected == "success")
    assert bool(callbacks) == (expected == "success")


def test_stale_job_never_changes_new_job_or_calls_activation(tmp_path):
    callbacks = []
    job = WorkerJob(tmp_path, on_done=callbacks.append)
    job.state = dict(job_id="new", running=True, progress=.2)
    proc = SimpleNamespace(stdout=io.StringIO('{"event":"done"}\n'), wait=lambda: 0)
    job._read(proc, {"job_id": "old"})
    assert job.snapshot() == dict(job_id="new", running=True, progress=.2)
    assert callbacks == []


def test_cancellation_is_cooperative_and_never_says_complete(tmp_path):
    job = WorkerJob(tmp_path)
    job._cancel = tmp_path / "cancel"
    job.state = dict(job_id="current", running=True, kind="train", progress=.4)
    job.proc = SimpleNamespace(stdout=io.StringIO(""), wait=lambda: 1)
    job.stop()
    assert job._cancel.exists() and view(job.snapshot())["status"] == "stopping"
    job._read(job.proc, {"job_id": "current"})
    assert view(job.snapshot())["status"] == "cancelled"
    assert view(job.snapshot())["percent"] == 40


def test_completed_progress_is_restored_with_metrics_but_never_restarts_job(tmp_path):
    job = WorkerJob(tmp_path)
    job.state = dict(job_id="finished", running=True, started=100., losses=[.8, .3], loss=.3,
                     train_files=4, eval_files=2, conditions=9, frames=300, task_names=["좌클릭"])
    job._cancel = tmp_path / "cancel"
    proc = SimpleNamespace(stdout=io.StringIO('{"event":"done","stage":"saved"}\n'), wait=lambda: 0)
    job._read(proc, {"job_id": "finished"})
    restored = WorkerJob(tmp_path)
    state = view(restored.snapshot())
    assert not restored.running and state["restored"] and state["percent"] == 100
    assert state["bestLoss"] == "0.3000" and "학습 녹화 4개" in state["data"]


@pytest.mark.parametrize("kind", ["import", "validate_import"])
def test_import_and_validation_cancel_at_safe_boundaries(tmp_path, kind):
    job = WorkerJob(tmp_path)
    job._cancel = tmp_path / "cancel"
    job.state = dict(running=True, kind=kind)
    job.proc = object()  # No terminate/taskkill path is allowed.
    job.stop()
    assert job._cancel.exists() and job.state["cancelled"]
