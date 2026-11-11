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
    # the live list: registering motions (any binding registry load) rebuilds gesture_catalog.TASKS after import
    import src.gesture_catalog as gc
    assert tasks_for("thumb_ring") == tuple(t for t in gc.TASKS if t.key == "thumb_ring")
    assert tasks_for("") == gc.TASKS and tasks_for("unknown") == gc.TASKS
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


def _record_postures(folder, postures=(0, 1, 2)):
    """Record a synthetic guided session containing multiple posture conditions for training/evaluation tests."""
    from src.recorder import SessionRecorder
    from tests.test_recording_plan import hand
    rec = SessionRecorder(folder, "guided", {}, now=0.0)
    now = 0.0
    for p in postures:
        for i in range(24):
            now += 1.0 / 30.0
            in_rep = 6 <= i < 16
            lbl = {
                "target": "thumb_index" if in_rep else "none",
                "task": "thumb_index" if in_rep else "none",
                "phase": "do" if in_rep else "free",
                "posture": p,
                "set": p,
                "rep": 0 if in_rep else -1,
                "condition_index": p,
                "condition_key": f"pose-{p}",
            }
            rec.frame(now, hand(), label=lbl, rule={"evaluated": True, "targets": ["thumb_index"] if in_rep else []})
    rec.close()
    return rec


def test_training_mode_option_resolution_and_override_priority():
    """(a) Verify --mode fast|thorough defaults and individual CLI option override priority."""
    from scripts import train_tcn

    # Default mode is fast
    args_default = train_tcn.parse_args([])
    opts_default = train_tcn.resolve_training_options(args_default, conds=[0, 1, 2], aug_cfg={"factor": 2})
    assert opts_default["mode"] == "fast"
    assert opts_default["folds"] is False
    assert opts_default["test_condition"] == 2
    assert opts_default["held"] == [2]
    assert opts_default["window_stride"] == 2
    assert opts_default["epochs"] == 25
    assert opts_default["offline_augment"] is False
    assert opts_default["offline_augment_factor"] == 0

    # When --offline-augment is passed without an integer in fast mode, factor defaults to 1
    args_fast_aug = train_tcn.parse_args(["--mode", "fast", "--offline-augment"])
    opts_fast_aug = train_tcn.resolve_training_options(args_fast_aug, conds=[0, 1, 2], aug_cfg={"factor": 3})
    assert opts_fast_aug["offline_augment"] is True
    assert opts_fast_aug["offline_augment_factor"] == 1
    assert opts_fast_aug["max_augmented_per_recording"] == 1

    # Thorough mode defaults: all condition folds (True), stride 1, user augmentation factor
    args_thorough = train_tcn.parse_args(["--mode", "thorough", "--offline-augment"])
    opts_thorough = train_tcn.resolve_training_options(args_thorough, conds=[0, 1, 2], aug_cfg={"factor": 3})
    assert opts_thorough["mode"] == "thorough"
    assert opts_thorough["folds"] is True
    assert opts_thorough["held"] == [0, 1, 2]
    assert opts_thorough["window_stride"] == 1
    assert opts_thorough["epochs"] == 25
    assert opts_thorough["offline_augment"] is True
    assert opts_thorough["offline_augment_factor"] == 3
    assert opts_thorough["max_augmented_per_recording"] is None

    # Explicit individual options override mode defaults
    args_override = train_tcn.parse_args([
        "--mode", "fast",
        "--folds",
        "--window-stride", "1",
        "--epochs", "10",
        "--test-condition", "1",
        "--offline-augment", "4",
    ])
    opts_override = train_tcn.resolve_training_options(args_override, conds=[0, 1, 2], aug_cfg={"factor": 2})
    assert opts_override["mode"] == "fast"
    assert opts_override["folds"] is True
    assert opts_override["held"] == [0, 1, 2]
    assert opts_override["window_stride"] == 1
    assert opts_override["epochs"] == 10
    assert opts_override["test_condition"] == 1
    assert opts_override["offline_augment"] is True
    assert opts_override["offline_augment_factor"] == 4
    assert opts_override["max_augmented_per_recording"] == 4

    # Hold-out condition selection respects mode and --test-condition overrides (including -1 for 0 folds)
    conds = [0, 1, 2]
    assert train_tcn.resolve_held_out_conds(conds, opts_default) == [2]
    assert train_tcn.resolve_held_out_conds(conds, opts_thorough) == [0, 1, 2]
    opts_no_fold = train_tcn.resolve_training_options(train_tcn.parse_args(["--test-condition", "-1"]), conds=conds)
    assert train_tcn.resolve_held_out_conds(conds, opts_no_fold) == []
    opts_cond1 = train_tcn.resolve_training_options(train_tcn.parse_args(["--test-condition", "1"]), conds=conds)
    assert train_tcn.resolve_held_out_conds(conds, opts_cond1) == [1]


def test_window_stride_halves_training_windows_while_keeping_eval_windows_intact(tmp_path):
    """(b) Verify --window-stride halves training windows per epoch while keeping evaluation window count intact."""
    import numpy as np
    from scripts import train_tcn

    rec = _record_postures(tmp_path, postures=(0, 1, 2)).path
    sessions = train_tcn.load([str(rec)])
    s0 = sessions[0]
    classes = ["none", "thumb_index"]
    orig = s0["y"].copy()
    s0["y"][orig == 0] = 0
    s0["y"][orig > 0] = 1
    s0["classes"] = classes

    all_x, all_y = train_tcn.pick(sessions, [0, 1, 2])
    eval_x, eval_y = train_tcn.pick(sessions, [2])
    total_train_windows = len(all_x)
    total_eval_windows = len(eval_x)
    assert total_train_windows >= 20
    assert total_eval_windows >= 10

    # Epoch 0 and Epoch 1 with stride=2 cover alternating halves of the training windows
    x_ep0, y_ep0 = train_tcn.slice_with_stride(all_x, all_y, stride=2, offset=0)
    x_ep1, y_ep1 = train_tcn.slice_with_stride(all_x, all_y, stride=2, offset=1)
    assert len(x_ep0) == (total_train_windows + 1) // 2
    assert len(x_ep1) == total_train_windows // 2
    assert len(x_ep0) + len(x_ep1) == total_train_windows
    assert np.array_equal(x_ep0[0], all_x[0])
    assert np.array_equal(x_ep1[0], all_x[1])

    # Evaluation (predict / evaluate) still runs on the full unmodified session windows
    model = train_tcn.train(
        all_x, all_y,
        epochs=2, seed=7, classes=classes, window_stride=2,
    )
    probs = train_tcn.predict(model, s0["x"])
    ev = train_tcn.evaluate(s0, [2], probs, 0.7, 3)
    assert probs.shape == (len(s0["x"]), 2)
    assert ev["reps"]["thumb_index"][1] == 1


def test_training_job_progress_stays_bounded_for_0_1_and_3_folds():
    """(c) Verify TrainingJob progress stays in [0, 1] and reaches 1.0 on done for folds = 0, 1, 3."""
    for folds in (0, 1, 3):
        job = tc.TrainingJob()
        job.state = {"running": True, "fold": 0, "folds": 0, "epoch": 0, "epochs": 25, "progress": 0.0}
        job._apply({"event": "loaded", "folds": folds, "windows": 500})
        assert 0.0 <= job.snapshot()["progress"] <= 1.0

        prev = job.snapshot()["progress"]
        for fold in range(1, folds + 1):
            job._apply({"event": "fold_start", "fold": fold, "folds": folds, "posture": fold - 1})
            p = job.snapshot()["progress"]
            assert 0.0 <= p < 1.0
            for ep in (1, 12, 25):
                job._apply({
                    "event": "epoch",
                    "stage": f"fold{fold - 1}",
                    "epoch": ep,
                    "epochs": 25,
                    "loss": 0.25,
                })
                p = job.snapshot()["progress"]
                assert 0.0 <= p < 1.0
                assert p >= prev
                prev = p

        job._apply({"event": "final_start"})
        p = job.snapshot()["progress"]
        assert 0.0 <= p < 1.0
        for ep in (1, 13, 25):
            job._apply({
                "event": "epoch",
                "stage": "final",
                "epoch": ep,
                "epochs": 25,
                "loss": 0.15,
            })
            p = job.snapshot()["progress"]
            assert 0.0 <= p < 1.0
            assert p >= prev
            prev = p

        job._apply({"event": "done", "per_gesture": {}})
        snap = job.snapshot()
        assert snap["progress"] == 1.0
        assert "끝" in snap["stage"]


def test_per_gesture_and_card_reading_work_with_0_or_1_folds_and_record_metadata(tmp_path):
    """(d) & (e) Verify 0/1 fold per_gesture & card reading work cleanly and gesture_tcn.json records training_mode metadata."""
    from scripts import train_tcn

    rec = _record_postures(tmp_path, postures=(0, 1, 2)).path
    classes = ["none", "thumb_index"]

    # (d-1) 0 folds (held_out_results is empty)
    pg_zero = train_tcn.per_gesture([], {"none": 30, "thumb_index": 20}, {"none": 0.98, "thumb_index": 0.95}, classes)
    assert "thumb_index" in pg_zero
    assert pg_zero["thumb_index"]["rate"] is None
    assert pg_zero["thumb_index"]["total"] == 0
    assert pg_zero["none"]["misfires_per_min"] is None

    out_zero = tmp_path / "models_zero"
    rc_zero = train_tcn.main([
        "--recordings", str(tmp_path / "*-guided.jsonl.gz"),
        "--output-dir", str(out_zero),
        "--mode", "fast",
        "--test-condition", "-1",
        "--epochs", "1",
    ])
    assert rc_zero == 0
    card_zero = json.loads((out_zero / "gesture_tcn.json").read_text(encoding="utf-8"))
    assert card_zero["training_mode"] == "fast"
    assert card_zero["folds"] == []
    assert card_zero["window_stride"] == 2
    assert card_zero["offline_augment_factor"] == 0
    assert card_zero["epochs"] == 1

    rep_zero = tc.model_report(out_zero / "gesture_tcn.onnx")
    rows_zero = {r["key"]: r for r in tc.gesture_rows(tc.dataset_summary(tmp_path), rep_zero)}
    assert rows_zero["thumb_index"]["is_trained"] is True
    assert "일부 자세 평가" in rows_zero["thumb_index"]["model_text"]

    # (d-2) & (e) 1 fold in fast mode on multi-condition data -> marks partial evaluation ("일부 자세 평가")
    out_fast = tmp_path / "models_fast"
    rc_fast = train_tcn.main([
        "--recordings", str(tmp_path / "*-guided.jsonl.gz"),
        "--output-dir", str(out_fast),
        "--mode", "fast",
        "--epochs", "1",
    ])
    assert rc_fast == 0
    card_fast = json.loads((out_fast / "gesture_tcn.json").read_text(encoding="utf-8"))
    assert card_fast["training_mode"] == "fast"
    assert card_fast["folds"] == [2]
    assert card_fast["window_stride"] == 2
    assert card_fast["offline_augment_factor"] == 0
    assert card_fast["epochs"] == 1
    assert len(card_fast["held_out_results"]) == 1

    rep_fast = tc.model_report(out_fast / "gesture_tcn.onnx")
    rows_fast = {r["key"]: r for r in tc.gesture_rows(tc.dataset_summary(tmp_path), rep_fast)}
    assert rows_fast["thumb_index"]["is_trained"] is True
    assert "일부 자세 평가" in rows_fast["thumb_index"]["model_text"]
    assert "일부 자세 평가" in rows_fast["thumb_index"]["score_text"]

    # Also verify training_mode persistence in training_selection.json
    assert tc.get_training_mode(tmp_path) == "fast"
    assert tc.set_training_mode("thorough", tmp_path) == "thorough"
    assert tc.get_training_mode(tmp_path) == "thorough"


def test_gpu_capability_detection_and_device_selection(tmp_path, monkeypatch):
    """Verify GPU capability detection, device selection persistence, and safe CPU fallback."""
    from scripts import train_tcn

    cap = tc.detect_gpu_training_capability(force_refresh=True)
    assert isinstance(cap["cuda_available"], bool)
    assert cap["recommended_device"] in ("gpu", "cpu")
    assert isinstance(cap["status_label"], str) and cap["status_label"]

    # Default preference is 'auto'
    assert tc.get_training_device(tmp_path) == "auto"
    assert tc.set_training_device("cpu", tmp_path) == "cpu"
    assert tc.get_training_device(tmp_path) == "cpu"
    assert tc.resolve_effective_training_device(rec_dir=tmp_path, cuda_available=True) == "cpu"

    assert tc.set_training_device("gpu", tmp_path) == "gpu"
    assert tc.get_training_device(tmp_path) == "gpu"
    # When CUDA is available, 'gpu' resolves to 'gpu'; when unavailable, falls back to 'cpu'
    assert tc.resolve_effective_training_device(rec_dir=tmp_path, cuda_available=True) == "gpu"
    assert tc.resolve_effective_training_device(rec_dir=tmp_path, cuda_available=False) == "cpu"

    # CLI --device resolution in train_tcn
    args_cpu = train_tcn.parse_args(["--device", "cpu"])
    opts_cpu = train_tcn.resolve_training_options(args_cpu)
    assert opts_cpu["device"] == "cpu"
    assert opts_cpu["effective_device"] == "cpu"
    assert opts_cpu["device_name"] == "CPU"

    # Simulate a PC without CUDA and verify --device gpu safely falls back to cpu
    monkeypatch.setattr(train_tcn.torch.cuda, "is_available", lambda: False)
    args_gpu_no_cuda = train_tcn.parse_args(["--device", "gpu"])
    opts_fallback = train_tcn.resolve_training_options(args_gpu_no_cuda)
    assert opts_fallback["device"] == "gpu"
    assert opts_fallback["effective_device"] == "cpu"
    meta = train_tcn.card_training_metadata(opts_fallback, [2])
    assert meta["training_device"] == "cpu"


def test_evaluate_training_improvement_and_gesture_rows_deltas(tmp_path):
    """Verify post-training Before-vs-After improvement evaluation and per-gesture row delta badges."""
    before_report = {
        "classes": ["none", "thumb_index", "thumb_ring"],
        "per_gesture": {
            "none": {"free_minutes": 2.0, "misfires": 4, "rules_misfires": 6, "misfires_per_min": 2.0, "rules_misfires_per_min": 3.0},
            "thumb_index": {"recognised": 14, "total": 20, "rate": 0.70, "rules_rate": 0.65, "train_accuracy": 0.90},
            "thumb_ring": {"recognised": 18, "total": 20, "rate": 0.90, "rules_rate": 0.80, "train_accuracy": 0.96},
        },
        "spatial_mlp": {"test_accuracy": 88.5},
        "specialists": {},
    }
    after_report = {
        "classes": ["none", "thumb_index", "thumb_ring", "thumb_middle"],
        "per_gesture": {
            "none": {"free_minutes": 2.0, "misfires": 1, "rules_misfires": 6, "misfires_per_min": 0.5, "rules_misfires_per_min": 3.0},
            "thumb_index": {"recognised": 18, "total": 20, "rate": 0.90, "rules_rate": 0.65, "train_accuracy": 0.97},
            "thumb_ring": {"recognised": 17, "total": 20, "rate": 0.85, "rules_rate": 0.80, "train_accuracy": 0.94},
            "thumb_middle": {"recognised": 16, "total": 20, "rate": 0.80, "rules_rate": 0.60, "train_accuracy": 0.95},
        },
        "spatial_mlp": {"test_accuracy": 94.0},
        "specialists": {},
    }
    dummy_model = tmp_path / "gesture_tcn.onnx"
    imp = tc.evaluate_training_improvement(
        after_report=after_report,
        before_report=before_report,
        backup_id="backup:backup-test",
        model=dummy_model,
        backup_dir=tmp_path / "backups",
    )
    assert imp["available"] is True
    assert imp["has_before_model"] is True
    assert imp["can_rollback"] is True
    assert imp["backup_id"] == "backup:backup-test"
    assert imp["spatial_accuracy_delta"] == 5.5
    assert "직전 모델 대비" in imp["headline"]

    by_label = {item["label"]: item for item in imp["items"]}
    # thumb_index improved from 70% to 90% (+20%p)
    assert by_label["thumb_index"]["status"] == "improved"
    assert by_label["thumb_index"]["delta_prev_pp"] == 20.0
    assert "+20%p" in by_label["thumb_index"]["badge"]
    # thumb_ring regressed from 90% to 85% (-5%p)
    assert by_label["thumb_ring"]["status"] == "regressed"
    assert by_label["thumb_ring"]["delta_prev_pp"] == -5.0
    # thumb_middle is newly trained compared to before_report
    assert by_label["thumb_middle"]["status"] in ("improved", "new")
    # none misfires dropped from 2.0/min to 0.5/min (-1.5/min)
    assert by_label["none"]["status"] == "improved"
    assert by_label["none"]["delta_prev"] == -1.5

    # Persisted JSON can be reloaded
    loaded = tc.load_last_training_improvement(model=dummy_model)
    assert loaded["available"] is True
    assert loaded["headline"] == imp["headline"]

    # Gesture rows receive delta_badge, delta_status, and delta_detail
    summary = tc.dataset_summary(tmp_path)
    rows = {r["key"]: r for r in tc.gesture_rows(summary, after_report, imp)}
    assert rows["thumb_index"]["delta_status"] == "improved"
    assert "+20%p" in rows["thumb_index"]["delta_badge"]
    assert "70% → 90%" in rows["thumb_index"]["delta_detail"]


def test_training_substage_events_and_dashboard_work_progress():
    """Verify TrainingJob substage progress updates and QtDashboard work progress properties."""
    from src.qt_dashboard import QtDashboard

    job = tc.TrainingJob()
    job.state = {"running": True, "fold": 0, "folds": 1, "epoch": 0, "epochs": 25, "progress": 0.02, "stage": "준비 중…"}
    job._apply({"event": "substage", "stage": "녹화 파일 읽는 중 (1/3)…", "progress": 0.08})
    snap = job.snapshot()
    assert snap["stage"] == "녹화 파일 읽는 중 (1/3)…"
    assert abs(snap["progress"] - 0.08) < 1e-6

    ui = QtDashboard()
    assert ui.appWorking is False
    ui._set_busy_task("모델 시뮬레이션 실행 중", "[1/3단계] 프레임 추론 중…", 0.35)
    assert ui.appWorking is True
    assert ui.workTitle == "모델 시뮬레이션 실행 중"
    assert "[1/3단계]" in ui.workDetail
    assert ui.workIndeterminate is False
    assert ui.workPercentText == "35%"
    assert "초 경과" in ui.workElapsedText

    ui._set_busy_task("")
    assert ui.appWorking is False
    assert ui.workPercentText == ""


def test_diagnose_label_performance_and_adaptive_boost_summary(tmp_path):
    """Verify root-cause drop diagnosis and adaptive posture boost reporting."""
    dummy_model = tmp_path / "gesture_tcn.onnx"
    dummy_model.write_bytes(b"dummy")

    before_report = {
        "classes": ["none", "thumb_middle"],
        "folds": [2],
        "training_mode": "fast",
        "data": [{"file": "rec1.jsonl.gz"}],
        "per_gesture": {
            "thumb_middle": {
                "rate": 0.80,
                "rules_rate": 0.90,
                "recognised": 32,
                "total": 40,
                "train_accuracy": 1.0,
            },
        },
    }
    after_report = {
        "classes": ["none", "thumb_middle"],
        "folds": [0, 1, 2],
        "training_mode": "thorough",
        "data": [{"file": "rec1.jsonl.gz"}, {"file": "rec2-new.jsonl.gz"}],
        "condition_groups": [
            {"name": "정면 · 중앙"},
            {"name": "손등 보임 · 좌측"},
            {"name": "오른쪽 기울기 · 우측"},
        ],
        "held_out_results": [
            {"posture": 0, "tcn": {"reps": {"thumb_middle": [40, 42]}, "misfires": {}, "confused": 0}, "rules": {"reps": {"thumb_middle": [38, 42]}, "misfires": {}}},
            {"posture": 1, "tcn": {"reps": {"thumb_middle": [20, 41]}, "misfires": {}, "confused": 2}, "rules": {"reps": {"thumb_middle": [37, 41]}, "misfires": {}}},
            {"posture": 2, "tcn": {"reps": {"thumb_middle": [26, 41]}, "misfires": {}, "confused": 0}, "rules": {"reps": {"thumb_middle": [38, 41]}, "misfires": {}}},
        ],
        "final_results": [
            {"posture": 0, "tcn": {"reps": {"thumb_middle": [42, 42]}, "misfires": {}, "confused": 0}},
            {"posture": 1, "tcn": {"reps": {"thumb_middle": [41, 41]}, "misfires": {}, "confused": 0}},
            {"posture": 2, "tcn": {"reps": {"thumb_middle": [41, 41]}, "misfires": {}, "confused": 0}},
        ],
        "adaptive_boost": {
            "triggered": True,
            "rounds": 2,
            "target_postures": [1, 2],
            "heterogeneous_postures": [1, 2],
            "posture_details": [
                {"posture": 1, "name": "손등 보임 · 좌측", "zero_shot_rate": 0.488, "after_final_rate": 1.0, "boosted": True},
                {"posture": 2, "name": "오른쪽 기울기 · 우측", "zero_shot_rate": 0.634, "after_final_rate": 1.0, "boosted": True},
            ],
        },
        "per_gesture": {
            "thumb_middle": {
                "rate": 0.694,
                "zero_shot_rate": 0.694,
                "final_rate": 1.0,
                "rules_rate": 0.911,
                "recognised": 86,
                "total": 124,
                "train_accuracy": 1.0,
            },
        },
    }

    imp = tc.evaluate_training_improvement(
        before_report=before_report,
        after_report=after_report,
        model=dummy_model,
    )
    by_label = {item["label"]: item for item in imp["items"]}
    tm = by_label["thumb_middle"]
    assert tm["status"] == "regressed"
    assert "적응형 자세 재학습(2회) 완료" in tm["boost_summary"]
    assert "이질적인 자세·각도 차이" in tm["diagnosis_summary"]
    assert "손등 보임 · 좌측" in tm["diagnosis_summary"]
    assert any("평가 범위 확대 영향" in r for r in tm["diagnosis_reasons"])
    assert any("신규 녹화 데이터 영향" in r for r in tm["diagnosis_reasons"])
    assert any("타 동작 혼동" in r for r in tm["diagnosis_reasons"])


def test_simulator_target_recording_switch_updates_frames_and_result(monkeypatch):
    """Verify switching simulator recordings never leaves simulationResult or playback frames stuck on the old file."""
    import time
    from src.qt_dashboard import QtDashboard

    fake_eval_calls = []
    fake_sim_calls = []

    def fake_eval(filename, force_recheck=False, rec_dir=None):
        fake_eval_calls.append(filename)
        return {
            "file": filename,
            "headline": f"{filename} 평가 완료",
            "active_status": "pass",
            "cleaned_exists": False,
            "issues": [],
            "target_gestures_text": f"목표:{filename}",
        }

    def fake_sim(filename, use_cleaned=True, model_id="", rec_dir=None, model=None):
        fake_sim_calls.append((filename, bool(use_cleaned)))
        return {
            "file": filename,
            "using_cleaned": bool(use_cleaned),
            "summary": f"{filename} 시뮬레이션 완료",
            "target_gestures_text": f"목표:{filename}",
            "gestures": [{"key": "thumb_index", "name": "검지 핀치", "reps": 3, "trained": True}],
            "timeline": [{"bin": 0, "prob": 0.9, "target": "thumb_index", "confirmed": True}],
            "reps": [],
            "playback_frames": [
                {"frame": 0, "target_name": f"동작:{filename}", "palm": [0.5, 0.5], "event_kind": "pinch"},
                {"frame": 1, "target_name": f"동작:{filename}", "palm": [0.6, 0.6], "event_kind": "none"},
            ],
            "quality": fake_eval(filename),
        }

    monkeypatch.setattr(tc, "evaluate_recording_in_simulator", fake_eval)
    monkeypatch.setattr(tc, "simulate_model_on_recording", fake_sim)
    monkeypatch.setattr(tc, "get_augmentation_settings", lambda *a, **kw: {"enabled": True, "strength": "normal", "factor": 2})

    ui = QtDashboard()
    ui.training_files = [{"file": "rec_a.jsonl.gz"}, {"file": "rec_b.jsonl.gz"}]

    # Run initial simulation on rec_a
    ui.runSimulation("rec_a.jsonl.gz", True)
    deadline = time.time() + 3.0
    while ui.training_busy and time.time() < deadline:
        time.sleep(0.01)
    assert ui.simulationResult["file"] == "rec_a.jsonl.gz"
    assert ui.getSimPlaybackFrame(0)["target_name"] == "동작:rec_a.jsonl.gz"

    # Now switch to rec_b even if evaluateSimRecording + runSimulation are called back-to-back
    ui.evaluateSimRecording("rec_b.jsonl.gz")
    ui.runSimulation("rec_b.jsonl.gz", False)
    deadline = time.time() + 3.0
    while (ui.training_busy or ui._pending_sim_request) and time.time() < deadline:
        time.sleep(0.01)

    assert ui.selectedSimFile == "rec_b.jsonl.gz"
    assert ui.simulationResult["file"] == "rec_b.jsonl.gz"
    assert ui.simulationResult["using_cleaned"] is False
    assert ui.sim_eval["file"] == "rec_b.jsonl.gz"
    assert ui.getSimPlaybackFrame(0)["target_name"] == "동작:rec_b.jsonl.gz"


def test_target_centric_guided_recording_judgment_vs_transition_and_no_specialist_dilution(tmp_path):
    """Verify V-sign probability is not diluted by competing specialists and guided playback/timeline only tracks target judgment vs transition."""
    import numpy as np
    from types import SimpleNamespace
    from src.capture_rules import CaptureRules
    from src.recorder import SessionRecorder
    from src.spatial_mlp import blend_tcn_and_spatial_probs
    from tests.test_toggle_sign import vsign

    # 1. Cross-specialist firing must never dilute v_sign below threshold via row_sum division
    cls = ["none", "thumb_ring", "thumb_pinky", "v_sign"]
    tcn_p = np.array([[0.0, 0.99, 0.99, 1.0]], dtype=np.float64)
    sp_p = np.array([[0.0, 0.99, 0.99, 1.0]], dtype=np.float64)
    blended = blend_tcn_and_spatial_probs(tcn_p, cls, sp_p, cls)
    assert float(blended[0, cls.index("v_sign")]) >= 0.95

    # 2. During guided `v_sign` capture, interfering pinch actions from folded fingers are never emitted
    rules = CaptureRules({})
    hand_v = SimpleNamespace(landmarks=vsign(), world=[(p.x, p.y, p.z) for p in vsign()], handedness="Right", score=0.99)
    interfering_result = SimpleNamespace(action="right_click", active_gesture="thumb_ring_pinch")
    obs_do = rules.observe(1.0, hand_v, interfering_result, {"task": "v_sign", "target": "v_sign", "phase": "do", "set": 0, "rep": 1})
    assert obs_do == ["v_sign"]
    obs_rest = rules.observe(1.5, hand_v, interfering_result, {"task": "v_sign", "target": "none", "phase": "rest", "set": 0, "rep": 1})
    assert obs_rest == []

    # 3. Simulator playback & timeline distinguish 판정 구간 (judgment) vs 전환 과정 (transition) without interfering signals
    rec = SessionRecorder(tmp_path, "guided", {"focus": "v_sign"}, now=0.0)
    for i in range(24):
        in_do = 6 <= i < 18
        lbl = {
            "target": "v_sign" if in_do else "none",
            "task": "v_sign",
            "phase": "do" if in_do else "rest",
            "rep": 1 if in_do else -1,
        }
        # Even if raw recording contained an interfering rule target, simulator strips non-target interference
        raw_rule = {"evaluated": True, "action": "right_click", "gesture": "thumb_ring_pinch", "targets": ["thumb_ring", "v_sign"] if in_do else ["thumb_ring"]}
        rec.frame(100.0 + i * 0.04, hand=hand_v, label=lbl, rule=raw_rule)
    rec.close()

    sim = tc.simulate_model_on_recording(rec.path.name, rec_dir=tmp_path)
    for pf in sim["playback_frames"]:
        assert pf["hand_present"] is True
        assert len(pf["lm"]) == 21
        assert "thumb_ring" not in pf["rule_targets"]
        assert "손가락 핀치 근접" not in pf["event_text"]
        if pf["phase"] == "do":
            assert pf["zone"] == "judgment"
            assert pf["event_kind"] == "sign"
            assert "판정 구간" in pf["event_text"]
            assert "thumb_ring" in pf["interfering_targets"]
        else:
            assert pf["zone"] == "transition"
            assert pf["event_kind"] == "transition"
            assert "전환 과정" in pf["event_text"]
    assert any(b.get("zone") == "judgment" for b in sim["timeline"])
    assert any(b.get("zone") == "transition" for b in sim["timeline"])









