"""Training center: advice per gesture, training progress parsing, focused recording, export package."""
import json
import zipfile

from src import training_center as tc
from src.guided_recording import TASKS, GuidedSession, tasks_for


def summary(reps=45, postures=3, minutes=9.5, newest=0.0):
    per = {k: {"reps": reps, "postures": {str(i): reps // postures for i in range(postures)}, "minutes": 0.0}
           for k in ("thumb_index", "thumb_ring")}
    per["none"] = {"reps": 0, "postures": {}, "minutes": minutes}
    return {"files": [], "per_gesture": per, "newest": newest}


def report(rate=0.9, misfires=0.2, mtime=100.0):
    g = {"rate": rate, "rules_rate": 0.5, "recognised": 27, "total": 30, "train_accuracy": 0.95}
    return {"mtime": mtime, "per_gesture": {
        "thumb_index": dict(g), "thumb_ring": dict(g),
        "none": {"misfires_per_min": misfires, "rules_misfires_per_min": 3.0, "train_accuracy": 0.99}}}


def rows_by_key(s, r):
    return {row["key"]: row for row in tc.gesture_rows(s, r)}


def test_enough_data_and_good_scores_say_ok():
    rows = rows_by_key(summary(), report())
    assert all(rows[k]["level"] == "ok" for k in ("none", "thumb_index", "thumb_ring"))
    assert rows["thumb_middle"]["level"] == "need"  # old model has no left-click examples
    assert rows["thumb_ring"]["score_text"].startswith("인식 90%")


def test_low_recognition_asks_for_focused_recording():
    rows = rows_by_key(summary(), report(rate=0.6))
    assert rows["thumb_ring"]["level"] == "need"
    assert "집중 녹화" in rows["thumb_ring"]["advice"]


def test_little_data_and_one_posture_are_flagged():
    rows = rows_by_key(summary(reps=10, postures=1, minutes=2), None)
    assert rows["thumb_index"]["level"] == "need"
    assert "부족" in rows["thumb_index"]["advice"] and "자세" in rows["thumb_index"]["advice"]
    assert "부족" in rows["none"]["advice"]
    assert rows["thumb_index"]["score_text"] == "아직 평가 없음"


def test_many_misfires_ask_for_more_half_fist_moving():
    rows = rows_by_key(summary(), report(misfires=2.0))
    assert rows["none"]["level"] == "need"


def test_recordings_newer_than_the_model_ask_to_retrain():
    rows = rows_by_key(summary(newest=200.0), report(mtime=100.0))
    assert all(rows[k]["level"] == "retrain" for k in ("none", "thumb_index", "thumb_ring"))


def test_training_progress_lines_move_the_bar():
    job = tc.TrainingJob()
    job.state = {"running": True, "fold": 0, "folds": 0, "epoch": 0, "epochs": 10, "progress": 0.0}
    job._apply({"event": "loaded", "folds": 3, "windows": 1000})
    job._apply({"event": "fold_start", "fold": 2, "folds": 3, "posture": 1})
    job._apply({"event": "epoch", "epoch": 5, "epochs": 10, "loss": 0.4})
    s = job.snapshot()
    assert 0.3 < s["progress"] < 0.5 and s["losses"] == [0.4] and "2/3" in s["stage"]
    job._apply({"event": "done", "per_gesture": {}})
    assert job.snapshot()["progress"] == 1.0


def test_focused_recording_has_only_that_gesture():
    assert tasks_for("thumb_ring") == tuple(t for t in TASKS if t.key == "thumb_ring")
    assert tasks_for("") == TASKS and tasks_for("unknown") == TASKS
    g = GuidedSession(tasks=tasks_for("thumb_ring"))
    assert {t.key for _, t in g.sets} == {"thumb_ring"} and len(g.sets) == 3   # one set per posture


def test_export_package_has_model_code_example_and_zip(tmp_path, monkeypatch):
    model = tmp_path / "gesture_tcn.onnx"
    model.write_bytes(b"onnx")
    model.with_suffix(".json").write_text(json.dumps({
        "trained": "2026-11-05", "classes": ["none", "thumb_index", "thumb_ring"], "window": 15, "features": 133,
        "feature_version": "local3d-v1", "threshold": 0.7, "frames_in_a_row": 3, "data": [],
        "per_gesture": report()["per_gesture"]}), encoding="utf-8")
    monkeypatch.setattr(tc, "MODEL", model)
    out = tc.export_package(tmp_path / "exports", with_dataset=False)
    names = {p.name for p in out.iterdir()}
    assert {"gesture_tcn.onnx", "gesture_tcn.json", "tcn_features.py", "example_use.py", "README.md"} <= names
    compile((out / "example_use.py").read_text(encoding="utf-8"), "example_use.py", "exec")
    with zipfile.ZipFile(str(out) + ".zip") as z:
        assert "example_use.py" in z.namelist()


# ---------------------------------------------------------------- recordings shared through git (2026-11-05)
def _record(folder, close=True):
    from src.recorder import SessionRecorder
    rec = SessionRecorder(folder, "guided", {}, now=0.0)
    rec.frame(0.1)
    if close:
        rec.close()
    return rec


def test_recording_name_has_the_pc_and_is_hidden_until_finished(tmp_path):
    from src.recorder import finished_recordings, machine_info
    rec = _record(tmp_path, close=False)
    assert list(tmp_path.glob("*.jsonl.gz")) == []          # still recording: only the .part file
    rec.close()
    (p,) = tmp_path.glob("*.jsonl.gz")
    pc = "".join(c for c in machine_info().get("pc", "") if c.isalnum() or c in "_-") or "pc"
    assert p.name.endswith(f"-{pc}-guided.jsonl.gz")
    assert finished_recordings(tmp_path) == ([p], [])


def test_half_downloaded_or_unclosed_recordings_wait_for_the_next_training(tmp_path):
    import gzip
    from src.recorder import finished_recordings
    good = _record(tmp_path).path
    cut = tmp_path / "20261105-000000-OTHERPC-guided.jsonl.gz"
    cut.write_bytes(good.read_bytes()[:-20])                # git pull stopped mid-file
    open_ = tmp_path / "20261105-000001-OTHERPC-guided.jsonl.gz"
    with gzip.open(open_, "wt", encoding="utf-8") as f:     # app closed without the end line
        f.write('{"type":"meta"}\n{"type":"frame","t":0}\n')
    usable, skipped = finished_recordings(tmp_path)
    assert usable == [good] and {p for p, _ in skipped} == {cut, open_}
    s = tc.dataset_summary(tmp_path)
    assert sum("error" in f for f in s["files"]) == 2


def test_storage_advice_past_500_mb():
    text, warn = tc.storage_text({"total_bytes": 120 * 1024 * 1024})
    assert not warn and "120 MB" in text
    text, warn = tc.storage_text({"total_bytes": 501 * 1024 * 1024})
    assert warn and "전용 저장소" in text


def test_recordings_never_store_the_real_pc_or_user_name(tmp_path):
    import getpass
    import socket
    from src.recorder import SessionRecorder, public_source, read_session
    rec = SessionRecorder(tmp_path, "free", {"pc_alias": "home-pc"}, now=0.0)
    rec.close()
    assert rec.path.name.endswith("-home-pc-free.jsonl.gz")
    meta, _, _ = read_session(rec.path)
    assert meta["pc"] == "home-pc" and "user" not in meta and "pc_alias" not in meta
    rec = SessionRecorder(tmp_path / "b", "free", {}, now=0.0)      # no nickname: a code, not the name
    rec.close()
    raw = rec.path.read_bytes()
    import gzip
    text = gzip.decompress(raw).decode("utf-8")
    for secret in {socket.gethostname(), getpass.getuser()} - {""}:
        assert secret not in text and secret not in rec.path.name
    # recordings made before the change: the name is replaced when shown or exported
    old = {"pc": "DESKTOP-ABC123", "user": "someone", "camera": {"note": "webcam, DESKTOP-ABC123"}}
    pc, note = public_source(old)
    assert pc.startswith("pc-") and "DESKTOP" not in note and pc in note
