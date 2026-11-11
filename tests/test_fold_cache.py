"""Finished evaluation folds are reused only when recordings, options and code are the same."""
from src.fold_cache import FoldCache, code_hash, recordings_key


def parts(**kw):
    base = {"recordings": [["a.gz", 10, 1]], "code": "x", "seed": 0}
    base.update(kw)
    return base


def test_a_saved_fold_comes_back_for_the_same_key(tmp_path):
    cache = FoldCache(tmp_path, parts())
    assert cache.load(2) is None
    cache.save(2, {"summary": [(2, {"a": 1}, {"b": 2}, 1.5)], "events": []})
    again = FoldCache(tmp_path, parts())
    assert again.load(2)["summary"] == [(2, {"a": 1}, {"b": 2}, 1.5)]
    assert again.load(3) is None


def test_another_recording_option_or_code_never_reuses_a_fold(tmp_path):
    FoldCache(tmp_path, parts()).save(2, {"summary": []})
    for other in (parts(seed=1), parts(code="y"), parts(recordings=[["a.gz", 11, 1]])):
        assert FoldCache(tmp_path, other).load(2) is None


def test_disabled_cache_neither_loads_nor_saves(tmp_path):
    off = FoldCache(tmp_path, parts(), enabled=False)
    off.save(2, {"summary": []})
    assert off.load(2) is None and not any(tmp_path.iterdir())
    FoldCache(tmp_path, parts()).save(2, {"summary": []})
    assert off.load(2) is None


def test_a_broken_file_is_ignored_and_old_keys_are_pruned(tmp_path):
    cache = FoldCache(tmp_path, parts())
    cache.save(2, {"summary": []})
    cache._path(2).write_bytes(b"not a pickle")
    assert cache.load(2) is None
    FoldCache(tmp_path, parts(seed=9)).save(1, {"summary": []})
    cache.prune_others()
    assert [p.name for p in tmp_path.iterdir()] == [cache.fingerprint]


def test_recordings_key_changes_with_the_file(tmp_path):
    f = tmp_path / "r.jsonl.gz"
    f.write_bytes(b"1")
    before = recordings_key([f])
    f.write_bytes(b"12")
    assert recordings_key([f]) != before
    assert len(code_hash()) == 16


def _job(tmp_path, monkeypatch):
    import src.fold_cache as fc
    from src.training_center import TrainingJob
    monkeypatch.setattr(fc, "CACHE_ROOT", tmp_path)
    return TrainingJob(), fc


def test_pause_request_is_a_file_the_training_checks(tmp_path, monkeypatch):
    _, fc = _job(tmp_path, monkeypatch)
    assert not fc.pause_requested()
    fc.request_pause()
    assert fc.pause_requested() and (tmp_path / "PAUSE").exists()
    fc.clear_pause()
    assert not fc.pause_requested()


def test_unfinished_training_is_found_resumed_with_the_same_options_and_can_be_discarded(tmp_path, monkeypatch):
    job, fc = _job(tmp_path, monkeypatch)
    assert job.interrupted() is None                       # nothing started: nothing to go on with
    job._write_job_marker({"epochs": 12, "gestures": None, "mode": "fast", "offline_augment": False,
                           "device": "cpu", "folds": 18, "started": 1.0})
    FoldCache(tmp_path / "abc", {}).save(0, {"summary": []})   # a finished fold of some key
    (tmp_path / "abc").mkdir(exist_ok=True)
    found = job.interrupted()
    assert found["total"] == 18 and found["paused"] is False
    seen = {}
    job.start = lambda **kw: seen.update(kw) or True
    assert job.resume() is True
    assert seen == {"epochs": 12, "gestures": None, "mode": "fast", "offline_augment": False, "device": "cpu"}
    job.discard_interrupted()
    assert job.interrupted() is None and not any(p.is_dir() for p in tmp_path.iterdir())


def test_a_running_training_is_never_offered_for_resume(tmp_path, monkeypatch):
    job, _ = _job(tmp_path, monkeypatch)
    job._write_job_marker({"epochs": 5, "folds": 3})
    job.state = {"running": True}
    assert job.interrupted() is None and job.resume() is False
