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


def _record_full(folder, close=True):
    from types import SimpleNamespace
    from src.recorder import SessionRecorder
    rec = SessionRecorder(folder, "guided", {}, now=0.0)
    pts = [SimpleNamespace(x=0.3 + 0.02 * (i % 5), y=0.4 + 0.03 * (i // 5), z=0.001 * i) for i in range(21)]
    world = [(0.02 * (i % 5), 0.03 * (i // 5) + 0.01 * i, 0.002 * i) for i in range(21)]
    hand = SimpleNamespace(landmarks=pts, world=world, handedness="Right", score=0.98)
    for i in range(20):
        in_rep = 5 <= i < 12
        lbl = (
            {"target": "thumb_index", "task": "thumb_index", "phase": "do", "posture": 0, "rep": 0}
            if in_rep
            else {"target": "none", "task": "none", "phase": "rest", "posture": 0, "rep": -1}
        )
        rec.frame(i * (1.0 / 30.0), hand=hand, label=lbl, rule={"targets": {"thumb_index": in_rep}})
    if close:
        rec.close()
    return rec


def test_discard_recording_removes_part_file_without_saving(tmp_path):
    rec = _record_full(tmp_path, close=False)
    assert rec.part_path.exists()
    rec.discard()
    assert not rec.part_path.exists()
    assert not rec.path.exists()
    assert list(tmp_path.glob("*.jsonl.gz")) == []


def test_recording_selection_and_deletion_filter_dataset_summary(tmp_path):
    r1 = _record_full(tmp_path).path
    import time as _t
    _t.sleep(0.01)
    r2 = _record_full(tmp_path).path
    assert r1 != r2

    # Initially both are selected
    s_all = tc.dataset_summary(tmp_path)
    assert s_all["per_gesture"]["thumb_index"]["reps"] == 2
    assert s_all["excluded_count"] == 0

    # Exclude r1 from training
    tc.set_recording_selected(r1.name, False, tmp_path)
    assert not tc.is_recording_selected(r1.name, tmp_path)
    assert tc.is_recording_selected(r2.name, tmp_path)
    filtered = tc.filter_selected_recording_paths([r1, r2], rec_dir=tmp_path)
    assert filtered == [str(r2)]

    s_sel = tc.dataset_summary(tmp_path)
    assert s_sel["per_gesture"]["thumb_index"]["reps"] == 1
    assert s_sel["excluded_count"] == 1

    # Delete r1 completely
    assert tc.delete_recording_file(r1.name, tmp_path)
    assert not r1.exists()
    s_after = tc.dataset_summary(tmp_path)
    assert len(s_after["files"]) == 1


def test_reset_gesture_training_disables_main_and_removes_specialist(tmp_path):
    import hashlib
    from src import gesture_models as bank
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model = model_dir / "gesture_tcn.onnx"
    model.write_bytes(b"dummy-onnx")
    card = {
        "classes": ["none", "thumb_index", "thumb_ring"],
        "window": 15,
        "features": 133,
        "feature_version": "local3d-v1",
        "trained": "2026-11-06 01:00:00",
        "per_gesture": {
            "thumb_index": {"rate": 0.9, "rules_rate": 0.5, "train_accuracy": 0.95},
            "thumb_ring": {"rate": 0.85, "rules_rate": 0.6, "train_accuracy": 0.92},
        },
    }
    model.with_suffix(".json").write_text(json.dumps(card), encoding="utf-8")

    # Also add a specialist for thumb_index
    spec_dir = model_dir / "gestures"
    spec_dir.mkdir()
    spec_onnx = spec_dir / "thumb_index-123.onnx"
    spec_onnx.write_bytes(b"spec-onnx")
    spec_onnx.with_suffix(".json").write_text(json.dumps({"classes": ["none", "thumb_index"]}), encoding="utf-8")
    spec_sha = hashlib.sha256(spec_onnx.read_bytes()).hexdigest()
    bank.publish_bank(spec_dir, {"thumb_index": {"model": spec_onnx.name, "card": spec_onnx.with_suffix(".json").name, "sha256": spec_sha}})

    backup_dir = model_dir / "backups"
    info = tc.reset_gesture_training("thumb_index", model=model, backup_dir=backup_dir)
    assert info["removed_specialist"] is True
    assert info["disabled_in_main"] is True

    # A backup was automatically created before reset
    backups = tc.list_model_backups(backup_dir=backup_dir, export_dir=tmp_path / "exports")
    assert len(backups) == 1

    # Report and gesture_rows reflect that thumb_index is reset while thumb_ring remains trained
    rep = tc.model_report(model)
    rows = {r["key"]: r for r in tc.gesture_rows(summary(), rep)}
    assert rows["thumb_index"]["is_disabled"] is True
    assert rows["thumb_index"]["is_trained"] is False
    assert "초기화" in rows["thumb_index"]["model_text"]
    assert rows["thumb_ring"]["is_trained"] is True

    # Restoring the backup brings back both the specialist and the enabled main model
    tc.restore_model_backup(backups[0]["id"], model=model, backup_dir=backup_dir, export_dir=tmp_path / "exports")
    rep_restored = tc.model_report(model)
    assert not rep_restored.get("disabled_gestures")
    assert (spec_dir / "manifest.json").is_file()


def test_simulator_and_diagnostics_run_cleanly(tmp_path):
    rec = _record_full(tmp_path).path
    sim = tc.simulate_model_on_recording(rec.name, rec_dir=tmp_path)
    assert sim["file"] == rec.name
    assert sim["frames"] > 0
    if sim["has_model"]:
        assert len(sim["timeline"]) > 0
        assert len(sim["reps"]) == 1

    diag = tc.run_app_diagnostics(rec_dir=tmp_path)
    assert diag["overall"] in ("ok", "warn", "fail")
    assert len(diag["checks"]) >= 5


def test_simulator_evaluation_calibration_and_augmentation_controls(tmp_path):
    rec = _record_full(tmp_path).path

    # 1. Evaluate raw recording quality in simulator
    ev = tc.evaluate_recording_in_simulator(rec.name, rec_dir=tmp_path, force_recheck=True)
    assert ev["file"] == rec.name
    assert ev["cleaned_exists"] is False
    assert ev["total_frames"] == 20
    assert "품질" in ev["headline"]

    # 2. Calibrate recording data (interpolation & outlier smoothing) and verify cleaned file
    cal = tc.calibrate_recording_data(
        rec.name,
        max_interp_gap=3,
        max_joint_jump=0.38,
        min_do_frames=4,
        rec_dir=tmp_path,
    )
    assert (tmp_path / "cleaned" / rec.name).is_file()
    assert cal["after_valid"] >= cal["before_valid"]

    ev_after = tc.evaluate_recording_in_simulator(rec.name, rec_dir=tmp_path)
    assert ev_after["cleaned_exists"] is True
    assert "보정본 적용 중" in ev_after["headline"]

    # 3. Reset calibrated recording back to raw
    rst = tc.reset_calibrated_recording(rec.name, rec_dir=tmp_path)
    assert rst["removed"] is True
    assert not (tmp_path / "cleaned" / rec.name).exists()

    # 4. Configure and generate offline augmentation (enabled, strength, factor)
    aug_res = tc.apply_recording_augmentation(
        rec.name,
        enabled=True,
        strength="strong",
        factor=3,
        rec_dir=tmp_path,
    )
    assert aug_res["settings"]["enabled"] is True
    assert aug_res["settings"]["strength"] == "strong"
    assert aug_res["settings"]["factor"] == 3
    assert len(aug_res["generated_files"]) == 3
    for gf in aug_res["generated_files"]:
        assert (tmp_path / "augmented" / gf).is_file()

    # 5. Disable augmentation and verify generated copies are cleaned up
    aug_off = tc.apply_recording_augmentation(
        rec.name,
        enabled=False,
        strength="light",
        factor=1,
        rec_dir=tmp_path,
    )
    assert aug_off["settings"]["enabled"] is False
    assert aug_off["generated_files"] == []
    assert list((tmp_path / "augmented").glob("*.jsonl.gz")) == []


def test_simulator_handles_list_rule_targets_and_builds_playback_frames(tmp_path):
    """Real recordings from CaptureRules.observe() store `rule['targets']` as a list of gesture keys."""
    from types import SimpleNamespace
    from src.recorder import SessionRecorder

    rec = SessionRecorder(tmp_path, "guided", {}, now=0.0)
    pts = [SimpleNamespace(x=0.3 + 0.02 * (i % 5), y=0.4 + 0.03 * (i // 5), z=0.001 * i) for i in range(21)]
    world = [(0.02 * (i % 5), 0.03 * (i // 5) + 0.01 * i, 0.002 * i) for i in range(21)]
    hand = SimpleNamespace(landmarks=pts, world=world, handedness="Right", score=0.98)
    for i in range(20):
        in_do = 5 <= i < 12
        label = {"target": "thumb_index" if in_do else "none", "phase": "do" if in_do else "rest", "rep": 1}
        rule = {
            "evaluated": True,
            "action": "left_click" if i == 8 else "idle",
            "gesture": "index_pinch" if i == 8 else None,
            "targets": ["thumb_index"] if i == 8 else [],
        }
        rec.frame(1000.0 + i * 0.033, hand=hand, label=label, rule=rule)
    rec.close()

    sim = tc.simulate_model_on_recording(rec.path.name, rec_dir=tmp_path)
    assert sim["file"] == rec.path.name
    assert len(sim["playback_frames"]) == 20
    f8 = sim["playback_frames"][8]
    assert f8["hand_present"] is True
    assert len(f8["lm"]) == 21
    assert f8["palm"] is not None
    assert "thumb_index" in f8["rule_targets"]
    assert f8["event_kind"] == "pinch"
    assert f8["pinch_joints"] == [4, 8]
    if sim["has_model"] and sim["reps"]:
        assert sim["reps"][0]["rule_ok"] is True
        assert sim["reps"][0]["start_frame"] == 5
        assert sim["reps"][0]["end_frame"] == 11


def test_simulator_extracts_arm_shoulder_pose_target_intent_and_screen_zones(tmp_path):
    """Verify pose (shoulder/elbow/wrist) skeleton extraction, target gesture summary, and 3x3 screen region tracking."""
    from types import SimpleNamespace
    from src.recorder import SessionRecorder

    rec = SessionRecorder(tmp_path, "guided", {"focus": "thumb_index"}, now=0.0)
    # Place hand in the top-left camera zone (x ~ 0.2, y ~ 0.2)
    pts = [SimpleNamespace(x=0.18 + 0.01 * (i % 5), y=0.20 + 0.01 * (i // 5), z=0.001 * i) for i in range(21)]
    world = [(0.02 * (i % 5), 0.03 * (i // 5) + 0.01 * i, 0.002 * i) for i in range(21)]
    hand = SimpleNamespace(landmarks=pts, world=world, handedness="Right", score=0.98)

    # MediaPipe Pose joints 11..16 (L/R shoulder, L/R elbow, L/R wrist) stored as 6 rows [x, y, z, visibility]
    pose_pts = [
        [0.62, 0.68, 0.0, 0.99],  # 11: Left shoulder
        [0.34, 0.66, 0.0, 0.99],  # 12: Right shoulder
        [0.68, 0.52, 0.0, 0.98],  # 13: Left elbow
        [0.25, 0.45, 0.0, 0.98],  # 14: Right elbow
        [0.70, 0.38, 0.0, 0.97],  # 15: Left wrist
        [0.19, 0.21, 0.0, 0.99],  # 16: Right wrist (close to hand wrist)
    ]

    for i in range(16):
        in_do = 4 <= i < 12
        lbl = {
            "target": "thumb_index" if in_do else "none",
            "task": "thumb_index",
            "phase": "do" if in_do else "rest",
            "posture": 0,
            "condition": "왼쪽 위 · 자연스러운 손 · 보통 거리",
            "condition_key": "왼쪽 위|자연스러운 손|보통 거리",
            "position": "왼쪽 위",
            "set": 0,
            "rep": 0,
        }
        rec.frame(
            100.0 + i * 0.04,
            hand=hand,
            pose=pose_pts,
            label=lbl,
            rule={"targets": ["thumb_index"] if in_do else []},
            cursor=(320, 180),
        )
    rec.close()

    ev = tc.evaluate_recording_in_simulator(rec.path.name, rec_dir=tmp_path, force_recheck=True)
    assert tc.NAMES["thumb_index"] in ev["target_gestures_text"]
    assert ev["pose_coverage_pct"] == 100
    assert "왼쪽 위" in ev["screen_zones_text"]

    sim = tc.simulate_model_on_recording(rec.path.name, rec_dir=tmp_path)
    assert tc.NAMES["thumb_index"] in sim["target_gestures_text"]
    assert sim["pose_coverage_pct"] == 100
    f6 = sim["playback_frames"][6]
    assert f6["pose_present"] is True
    assert f6["active_arm"] is not None
    assert f6["active_arm"]["source"] == "pose"
    assert f6["active_arm"]["elbow_deg"] is not None
    assert f6["screen_zone"] == "왼쪽 위"
    assert f6["target_position"] == "왼쪽 위"
    assert f6["cursor"] == [320, 180]
