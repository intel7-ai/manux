"""Unit tests for post-recording data quality check, cleaning, offline augmentation, and pipeline hook."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.capture_dataset import discover_augmented_paths, load_sessions, resolve_cleaned_path, usable
from src.data_quality import QualityReport, check_recording, clean_recording
from src.gesture_engine import DEFAULT_CONFIG, Point3D
from src.hand_tracker import HandDetection
from src.offline_augment import augment_frames_sequence, augment_recording_file, generate_augmented_records
from src.recorder import SessionRecorder, read_session
from src.tcn_features import FEATURES, frame_features, windows
from src.training_center import TrainingJob, run_post_recording_pipeline


def _make_hand(cx: float = 0.5, cy: float = 0.5, scale: float = 0.12) -> HandDetection:
    """Create a realistic 21-joint hand layout with valid normalized image and world coordinates."""
    lm = []
    world = []
    for i in range(21):
        row = i // 5
        col = i % 5
        dx = (col - 2) * scale * 0.35
        dy = -row * scale * 0.45 if i > 0 else 0.0
        dz = -0.01 * row
        if i == 9:  # middle_mcp
            dx, dy, dz = 0.0, -scale, 0.0
        elif i == 5:  # index_mcp
            dx, dy, dz = -scale * 0.45, -scale * 0.9, 0.0
        elif i == 17:  # pinky_mcp
            dx, dy, dz = scale * 0.45, -scale * 0.85, 0.0
        lm.append(Point3D(cx + dx, cy + dy, dz))
        world.append((dx, dy, dz))
    return HandDetection(landmarks=lm, handedness="Right", score=0.95, world=world)


def _write_guided_recording(
    folder: Path,
    *,
    do_frames: int = 10,
    rest_frames: int = 8,
    missing_in_do: int = 0,
    single_gap_in_do: bool = False,
    jump_in_do: bool = False,
    tiny_hand_in_do: bool = False,
    excessive_prep_frames: int = 2,
) -> Path:
    rec = SessionRecorder(folder, "guided", {"pc_alias": "test-pc", "frame_aspect": 0.5625}, now=0.0)
    t = 0.0
    dt = 1.0 / 30.0
    cond = {"id": "p0", "name": "기본 자세"}

    for _ in range(excessive_prep_frames):
        t += dt
        rec.frame(
            t,
            _make_hand(),
            label={"label": "transition", "target": "transition", "phase": "prep", "task": "thumb_ring", "posture": 0, "condition": cond, "set": 0, "rep": 1},
        )

    for i in range(do_frames):
        t += dt
        hand = _make_hand(cx=0.5 + 0.002 * i, cy=0.5)
        if i < missing_in_do:
            hand = None
        elif single_gap_in_do and i == do_frames // 2:
            hand = None
            rec.frame(
                t,
                hand,
                label={"label": "idle", "target": "idle", "phase": "paused", "task": "thumb_ring", "posture": 0, "condition": cond, "set": 0, "rep": 1},
            )
            continue
        elif jump_in_do and i == do_frames // 2:
            hand = _make_hand(cx=1.95, cy=-1.5)  # sudden spike
        elif tiny_hand_in_do and i == do_frames // 2:
            hand = _make_hand(cx=0.5, cy=0.5, scale=0.002)  # abnormally tiny hand
        rec.frame(
            t,
            hand,
            label={"label": "thumb_ring", "target": "thumb_ring", "phase": "do", "task": "thumb_ring", "posture": 0, "condition": cond, "set": 0, "rep": 1},
        )

    for i in range(rest_frames):
        t += dt
        rec.frame(
            t,
            _make_hand(cx=0.5, cy=0.5 + 0.001 * i),
            label={"label": "none", "target": "none", "phase": "rest", "task": "thumb_ring", "posture": 0, "condition": cond, "set": 0, "rep": 1},
        )

    return rec.close()


def test_config_defaults_present_in_engine_and_json():
    expected = {
        "auto_quality_check": True,
        "auto_clean": True,
        "auto_offline_augment": False,
        "auto_retrain_after_recording": False,
        "min_quality_score": 0.7,
        "offline_augment_factor": 4,
        "offline_augment_rotation_deg": 40,
        "offline_augment_scale": 0.15,
    }
    for k, v in expected.items():
        assert k in DEFAULT_CONFIG
        assert DEFAULT_CONFIG[k] == v

    cfg_path = Path(__file__).resolve().parents[1] / "config" / "gestures.json"
    cfg_json = json.loads(cfg_path.read_text(encoding="utf-8"))
    for k, v in expected.items():
        assert k in cfg_json
        assert cfg_json[k] == v


def test_quality_check_passes_clean_guided_recording(tmp_path):
    path = _write_guided_recording(tmp_path, do_frames=12, rest_frames=10)
    report = check_recording(path)
    assert isinstance(report, QualityReport)
    assert report.status == "pass"
    assert report.score >= 0.85
    assert report.metrics["missing_hand_ratio"] == 0.0
    assert report.metrics["short_do_count"] == 0


def test_quality_check_detects_missing_hand_and_fails(tmp_path):
    path = _write_guided_recording(tmp_path, do_frames=12, rest_frames=4, missing_in_do=12)
    report = check_recording(path)
    assert report.status == "fail"
    assert report.score < 0.7
    kinds = {iss.kind for iss in report.issues}
    assert "missing_hand" in kinds or "short_do" in kinds


def test_quality_check_detects_short_do_jump_abnormal_size_and_transition(tmp_path):
    p_short = _write_guided_recording(tmp_path / "short", do_frames=2, rest_frames=10)
    r_short = check_recording(p_short)
    assert any(iss.kind == "short_do" for iss in r_short.issues)

    p_jump = _write_guided_recording(tmp_path / "jump", do_frames=12, rest_frames=10, jump_in_do=True)
    r_jump = check_recording(p_jump)
    assert any(iss.kind == "joint_jump" for iss in r_jump.issues)

    p_tiny = _write_guided_recording(tmp_path / "tiny", do_frames=12, rest_frames=10, tiny_hand_in_do=True)
    r_tiny = check_recording(p_tiny)
    assert any(iss.kind == "abnormal_size" for iss in r_tiny.issues)

    p_trans = _write_guided_recording(tmp_path / "trans", do_frames=2, rest_frames=1, excessive_prep_frames=30)
    r_trans = check_recording(p_trans)
    assert any(iss.kind == "excessive_transition" for iss in r_trans.issues)


def test_clean_recording_preserves_original_and_fixes_defects(tmp_path):
    path = _write_guided_recording(
        tmp_path,
        do_frames=12,
        rest_frames=10,
        single_gap_in_do=True,
        jump_in_do=False,
    )
    orig_bytes = path.read_bytes()
    report = check_recording(path)
    cleaned_path = clean_recording(path, report)

    assert cleaned_path != path
    assert cleaned_path.parent.name == "cleaned"
    assert path.read_bytes() == orig_bytes

    meta, cleaned_frames, _ = read_session(cleaned_path)
    assert meta["cleaned"] is True
    assert all(usable(f) for f in cleaned_frames)
    assert sum(1 for f in cleaned_frames if (f.get("label") or {}).get("phase") == "do") == 12
    assert not any((f.get("label") or {}).get("phase") == "paused" for f in cleaned_frames)

    # Also verify a spike outlier is smoothed and a 2-frame short 'do' is masked to transition/idle
    path_spike = _write_guided_recording(tmp_path / "spike", do_frames=10, rest_frames=8, jump_in_do=True)
    cleaned_spike = clean_recording(path_spike)
    r_after = check_recording(cleaned_spike)
    assert r_after.metrics["outlier_count"] == 0

    path_short = _write_guided_recording(tmp_path / "short_do", do_frames=2, rest_frames=10)
    cleaned_short = clean_recording(path_short)
    _, short_frames, _ = read_session(cleaned_short)
    assert not any((f.get("label") or {}).get("phase") == "do" for f in short_frames)


def test_offline_augmentation_creates_valid_sessions_and_preserves_labels(tmp_path):
    path = _write_guided_recording(tmp_path, do_frames=16, rest_frames=12)
    cleaned = clean_recording(path)
    aug_paths = augment_recording_file(
        cleaned,
        factor=3,
        rotation_deg=40.0,
        scale_range=0.15,
        seed=123,
    )
    assert len(aug_paths) == 3
    assert all(p.parent.name == "augmented" for p in aug_paths)
    assert all(p.exists() for p in aug_paths)

    discovered = discover_augmented_paths([path])
    assert len(discovered) == 3

    sessions = load_sessions([cleaned, *aug_paths])
    assert len(sessions) == 4
    for s in sessions:
        assert s["features"].shape[1] == FEATURES
        assert (s["y"] >= 0).sum() > 0
        w = windows(frame_features(s["frames"], 0.5625), 15)
        assert w.shape == (len(s["frames"]), 15, FEATURES)

    # In-memory augmentation records also work with load_sessions
    mem_records = generate_augmented_records([cleaned], factor=2, seed=7)
    mem_sessions = load_sessions([cleaned], extra_records=mem_records)
    assert len(mem_sessions) == 3


def test_post_recording_pipeline_orders_steps_and_respects_flags(tmp_path, monkeypatch):
    good_path = _write_guided_recording(tmp_path / "good", do_frames=14, rest_frames=10)
    job = TrainingJob()
    started = []
    monkeypatch.setattr(job, "start", lambda **kw: (started.append(kw), True)[1])

    res = run_post_recording_pipeline(
        good_path,
        {
            "auto_quality_check": True,
            "auto_clean": True,
            "auto_offline_augment": True,
            "auto_retrain_after_recording": True,
            "min_quality_score": 0.7,
            "offline_augment_factor": 3,
            "offline_augment_rotation_deg": 40,
            "offline_augment_scale": 0.15,
        },
        training_job=job,
    )
    assert res["status"] == "pass"
    assert res["cleaned_path"] is not None and Path(res["cleaned_path"]).exists()
    assert len(res["augmented_paths"]) == 3
    assert res["retrain_started"] is True
    assert len(started) == 1
    assert resolve_cleaned_path(good_path) == Path(res["cleaned_path"])

    # Failing recording stops at step 2 without cleaning, augmenting, or retraining
    bad_path = _write_guided_recording(tmp_path / "bad", do_frames=10, rest_frames=2, missing_in_do=10)
    started.clear()
    res_bad = run_post_recording_pipeline(
        bad_path,
        {
            "auto_quality_check": True,
            "auto_clean": True,
            "auto_offline_augment": True,
            "auto_retrain_after_recording": True,
            "min_quality_score": 0.7,
        },
        training_job=job,
    )
    assert res_bad["status"] == "fail"
    assert res_bad["cleaned_path"] is None
    assert res_bad["augmented_paths"] == []
    assert res_bad["retrain_started"] is False
    assert started == []
    assert "quality_warning" in job.snapshot()


def test_free_mode_recording_quality_and_cleaning(tmp_path):
    rec = SessionRecorder(tmp_path, "free", {"pc_alias": "home-pc"}, now=0.0)
    for i in range(20):
        rec.frame((i + 1) / 30.0, _make_hand(cx=0.45 + 0.003 * i), label={"target": "none", "phase": "use"})
    path = rec.close()

    report = check_recording(path)
    assert report.mode == "free"
    assert report.status == "pass"
    cleaned = clean_recording(path, report)
    assert cleaned.exists()
    aug = augment_frames_sequence({"mode": "free"}, read_session(cleaned)[1], rotation_deg=35.0, scale_range=0.12)[1]
    assert len(aug) >= 15
