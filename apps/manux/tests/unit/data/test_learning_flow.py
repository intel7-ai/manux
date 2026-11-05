"""Learning center boundaries with synthetic anonymous data and an offline hub."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from manux.core.contracts import RecordingIdentity
from manux.data.training_center import TrainingCenterService, recording_sets, dataset_summary
from manux.data.worker_job import WorkerJob, worker_command

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import app_worker
import hub
from smoke_worker import recording


class Remote:
    def __init__(self):
        self.rows, self.files = [], {}
    def read_manifest(self): return list(self.rows)
    def push_files(self, uploads, rows, message):
        self.files.update({remote: local.read_bytes() for local, remote in uploads})
        self.rows = rows
        return len(uploads)
    def download_file(self, remote, dest):
        dest.write_bytes(self.files[remote])
        return dest


def test_recursive_discovery_separates_team_train_and_own_eval(tmp_path):
    root = tmp_path / "recordings"
    for person, split in (("synthetic", "train"), ("synthetic", "eval"), ("other", "eval")):
        path = root / f"profile-{person}" / "pinch" / split / "r.jsonl.gz"
        recording(path, split)
        if person == "other":
            import gzip
            with gzip.open(path, "rt") as stream:
                text = stream.read().replace('"person": "synthetic"', '"person": "other"')
            with gzip.open(path, "wt") as stream: stream.write(text)
    train, evaluation = recording_sets(root, RecordingIdentity("synthetic"))
    assert len(train) == len(evaluation) == 1
    assert "profile-synthetic" in evaluation[0].parts
    summary = dataset_summary(root)
    assert len(summary["files"]) == 3
    assert {row["split"] for row in summary["files"]} == {"train", "eval"}


def test_config_never_puts_token_in_preferences_or_job_request(tmp_path, monkeypatch):
    service = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    with pytest.raises(ValueError): service.configure("../escape", "team/data")
    service.configure("SYNTHETIC", "team/data", "hf_synthetic_test_only")
    assert "hf_synthetic" not in service.preferences_path.read_text()
    assert TrainingCenterService(service.config_dir, service.data_dir, service.recordings_dir).profile == "synthetic"
    requests = []
    monkeypatch.setattr(service.hub_job, "start_request", lambda request: requests.append(request))
    service.hub_action("upload")
    assert requests[0]["profile"] == "synthetic" and "hf_synthetic" not in json.dumps(requests)


def test_worker_uploads_only_current_profile_and_downloads_validated_data(tmp_path, monkeypatch):
    # Do not read a developer's .env or use the network in tests.
    monkeypatch.setattr(hub, "load_dotenv", lambda *args, **kwargs: {})
    root = tmp_path / "recordings"
    recording(root / "profile-synthetic/pinch/train/r.jsonl.gz", "train")
    recording(root / "profile-other/pinch/train/r.jsonl.gz", "train")
    remote = Remote()
    request = {"profile": "synthetic", "repo": "team/data", "config_dir": str(tmp_path / "config"),
               "recordings_dir": str(root), "action": "upload", "job_id": "test", "cancel_path": str(tmp_path / "cancel")}
    events = []
    app_worker.run_hub(request, lambda event, **values: events.append((event, values)), client=remote)
    assert len(remote.rows) == 1 and remote.rows[0]["person"] == "synthetic"
    assert remote.rows[0]["path"].startswith("recordings/profile-synthetic/")
    app_worker.run_hub(request, lambda *a, **k: None, client=remote)
    assert len(remote.rows) == 1
    destination = tmp_path / "download"
    assert hub.pull_from_hub(remote, destination)["downloaded"] == 1
    assert hub.pull_from_hub(remote, destination)["downloaded"] == 0
    assert len(dataset_summary(destination)["files"]) == 1


@pytest.mark.parametrize("failure", ["traversal", "hash", "conflict"])
def test_download_rejects_unsafe_corrupt_or_conflicting_files(tmp_path, failure):
    root = tmp_path / "source"
    recording(root / "profile-synthetic/pinch/train/r.jsonl.gz", "train")
    remote = Remote()
    hub.push_to_hub(remote, root)
    row = remote.rows[0]
    destination = tmp_path / "download"
    if failure == "traversal": row["path"] = "recordings/../escape.jsonl.gz"
    if failure == "hash": row["sha256"] = "0" * 64
    if failure == "conflict":
        local = destination / row["path"].removeprefix("recordings/")
        local.parent.mkdir(parents=True)
        local.write_bytes(b"existing user data")
    with pytest.raises(hub.HubError): hub.pull_from_hub(remote, destination)
    if failure == "conflict": assert local.read_bytes() == b"existing user data"
    else: assert not list(destination.rglob("*.jsonl.gz"))


def test_hf_concurrent_commits_merge_latest_manifest_and_pin_parent(tmp_path):
    client = object.__new__(hub.HfDatasetHub)
    client.repo_id, client.create_repo = "team/data", False
    client._hf = SimpleNamespace(CommitOperationAdd=lambda **k: k)
    first = {"path": "recordings/a", "sha256": "a" * 64}
    other = {"path": "recordings/b", "sha256": "b" * 64}
    mine = {"path": "recordings/c", "sha256": "c" * 64}
    state = {"revision": "one", "rows": [first]}
    calls = []
    class Conflict(Exception): response = SimpleNamespace(status_code=412)
    def commit(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            state.update(revision="two", rows=[first, other])
            raise Conflict()
    client.api = SimpleNamespace(repo_info=lambda **k: SimpleNamespace(sha=state["revision"]), create_commit=commit)
    client._manifest_at = lambda revision: list(state["rows"])
    path = tmp_path / "file"
    path.write_bytes(b"synthetic")
    client.push_files([(path, mine["path"])], [first, mine], "test")
    assert [c["parent_commit"] for c in calls] == ["one", "two"]
    merged = [json.loads(line) for line in calls[1]["operations"][-1]["path_or_fileobj"].decode().splitlines()]
    assert merged == [first, other, mine]


def test_frozen_app_uses_bundled_worker_instead_of_relaunching_app(tmp_path):
    exe = tmp_path / "MANUX.exe"
    request = tmp_path / "request.json"
    with pytest.raises(ValueError): worker_command(request, frozen=True, executable=exe)
    worker = tmp_path / "worker/MANUXWorker.exe"
    worker.parent.mkdir()
    worker.touch()
    assert worker_command(request, frozen=True, executable=exe) == [str(worker), "--request", str(request)]


def test_missing_component_is_a_visible_job_error(tmp_path, monkeypatch):
    import manux.data.worker_job as jobs
    monkeypatch.setattr(jobs, "worker_command", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("missing")))
    job = WorkerJob(tmp_path / "jobs")
    assert not job.start_request({"kind": "hub"})
    assert not job.running and job.snapshot()["error"]
