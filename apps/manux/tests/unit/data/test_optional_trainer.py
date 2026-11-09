"""기본 설치본은 제어·녹화를 유지하고 worker 작업을 시작하지 않는다."""
from pathlib import Path

import pytest

from manux.data import worker_job as jobs
from manux.data.training_center import TrainingCenterService


def frozen(monkeypatch, tmp_path, *, trainer=False):
    exe = tmp_path / "app" / "MANUX.exe"
    monkeypatch.setattr(jobs.sys, "frozen", True, raising=False)
    monkeypatch.setattr(jobs.sys, "executable", str(exe))
    if trainer:
        worker = exe.parent / "worker" / "MANUXWorker.exe"
        worker.parent.mkdir(parents=True)
        worker.touch()
    return exe


def test_default_install_blocks_worker_before_creating_request(tmp_path, monkeypatch):
    exe = frozen(monkeypatch, tmp_path)
    assert not jobs.worker_available()
    with pytest.raises(ValueError, match="추가 구성요소 필요"):
        jobs.worker_command(tmp_path / "request.json")
    job = jobs.WorkerJob(tmp_path / "jobs")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("worker must not start"))
    for kind in ("train", "hub"):
        assert not job.start_request({"kind": kind})
        assert not job.running
        assert job.snapshot()["stage"] == "추가 구성요소 필요"
    assert not (tmp_path / "jobs").exists()


def test_trainer_install_and_source_use_their_own_worker(tmp_path, monkeypatch):
    exe = frozen(monkeypatch, tmp_path, trainer=True)
    assert jobs.worker_available()
    assert jobs.worker_command(tmp_path / "r.json")[0] == str(exe.parent / "worker" / "MANUXWorker.exe")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "app_worker.py").touch()
    assert jobs.worker_available(frozen=False, tools_dir=tools)
    assert not jobs.worker_available(frozen=False, tools_dir=tmp_path / "missing")


def test_service_keeps_recording_rows_but_disables_advanced_training(tmp_path, monkeypatch):
    frozen(monkeypatch, tmp_path)
    service = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    summary, report, rows, storage = service.summary_and_rows()
    assert rows
    assert all(not r["trainer_available"] and not r["can_train"] for r in rows)
    assert all("추가 구성요소 필요" in r["advice"] for r in rows)
    assert all(service.has_task(r["key"]) for r in rows)
    assert not service.hub_action("status")
    assert "추가 구성요소 필요" in service.hub_job.snapshot()["error"]


def test_trainer_service_retains_supported_training(tmp_path, monkeypatch):
    frozen(monkeypatch, tmp_path, trainer=True)
    specs = {"pinch.thumb_ring": {"kind": "tap", "modes": ["pinch"], "point": "none"}}
    service = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings", specs=specs)
    _, _, rows, _ = service.summary_and_rows()
    assert all(r["trainer_available"] for r in rows)
    assert any(r["can_train"] for r in rows)
