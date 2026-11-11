from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

from src.action_bindings import (
    ActionBindingRegistry,
    CompositeGestureDetector,
    format_action_badge,
)
from src.gesture_catalog import BY_KEY, CLASSES, MOTION_TARGETS, is_motion_task
from src.motion_mode import (
    MotionTrajectoryModel,
    extract_motion_window_features,
    train_motion_templates_from_recordings,
)
from src.training_center import dataset_summary, gesture_rows


@dataclass
class _Pt:
    x: float
    y: float
    z: float = 0.0


def _make_hand(cx: float, cy: float = 0.5, pinch_index: bool = False):
    pts = [_Pt(cx, cy, 0.0) for _ in range(21)]
    pts[0] = _Pt(cx, cy + 0.08, 0.0)
    pts[5] = _Pt(cx - 0.03, cy, 0.0)
    pts[9] = _Pt(cx, cy - 0.02, 0.0)
    pts[17] = _Pt(cx + 0.03, cy, 0.0)
    pts[4] = _Pt(cx - 0.02, cy - 0.03, 0.0)
    if pinch_index:
        pts[8] = _Pt(cx - 0.02, cy - 0.03, 0.0)
    else:
        pts[8] = _Pt(cx - 0.03, cy - 0.09, 0.0)
    return pts


def test_action_binding_registry_and_app_overrides(tmp_path: Path):
    cfg = tmp_path / "gesture_bindings.json"
    reg = ActionBindingRegistry(path=cfg)

    # Default shortcut badge is visible
    b_left = reg.get_binding("swipe_left")
    assert "Alt + ←" in b_left["badge"]

    # Customize global binding to YouTube key
    saved = reg.set_binding("swipe_left", "youtube_key", "j", enabled=True)
    assert "유튜브" in saved["detail"] or "YT -10초" in saved["badge"]

    # Add foreground app-specific override for Premiere / Chrome
    reg.add_app_override("swipe_left", "premiere", "hotkey", "ctrl+z")
    eff_default = reg.resolve_effective_action("swipe_left", {"process": "explorer.exe", "title_full": "내 PC"})
    assert eff_default["action_type"] == "youtube_key"
    assert eff_default["action_value"] == "j"
    assert eff_default["matched_app"] == ""

    eff_premiere = reg.resolve_effective_action(
        "swipe_left", {"process": "Adobe Premiere Pro.exe", "title_full": "Project 1"}
    )
    assert eff_premiere["action_type"] == "hotkey"
    assert eff_premiere["action_value"] == "ctrl+z"
    assert eff_premiere["matched_app"] == "premiere"

    # Dry-run execution check
    ok, badge = reg.execute_resolved_action(eff_premiere, dry_run=True)
    assert ok is True
    assert "Ctrl + Z" in badge


def test_register_custom_temporal_motion_syncs_catalog(tmp_path: Path):
    cfg = tmp_path / "gesture_bindings.json"
    reg = ActionBindingRegistry(path=cfg)

    m_def = reg.register_motion_gesture(
        key="motion_swipe_up_newtab",
        name="모션 · 손 펴고 위로 쓸기 (새 탭)",
        posture="open_hand",
        direction="up",
        action_type="hotkey",
        action_value="ctrl+t",
        min_distance=0.14,
        min_speed=0.25,
    )
    k = m_def["key"]
    assert k in BY_KEY
    assert BY_KEY[k].track == "motion"
    assert is_motion_task(k) is True
    assert k in CLASSES
    assert MOTION_TARGETS.get(k) == k

    # Clean up custom motion
    assert reg.delete_motion_gesture(k) is True
    assert k not in BY_KEY


def test_composite_gesture_detector_swipe_hold_and_double_pinch(tmp_path: Path):
    cfg = tmp_path / "gesture_bindings.json"
    reg = ActionBindingRegistry(path=cfg)
    reg.register_composite_gesture(
        key="comp_index_pinch_swipe_left",
        name="검지 핀치 + 왼쪽 스와이프",
        base_posture="thumb_index",
        trigger_type="swipe_left",
        action_type="hotkey",
        action_value="ctrl+shift+tab",
        min_distance=0.08,
        min_speed=0.20,
    )
    reg.register_composite_gesture(
        key="comp_index_pinch_hold",
        name="검지 핀치 길게 유지",
        base_posture="thumb_index",
        trigger_type="hold",
        action_type="hotkey",
        action_value="win+tab",
        hold_seconds=0.55,
    )
    det = CompositeGestureDetector(reg)

    # 1. Pinch + Swipe Left -> comp_index_pinch_swipe_left
    hit = None
    t = 10.0
    for i in range(10):
        cx = 0.60 - i * 0.015  # moves left by -0.135 in ~0.18s
        t += 0.02
        lm = _make_hand(cx, 0.5, pinch_index=True)
        res = det.step(t, lm, "thumb_index_pinch", {"index"}, 1.0)
        if res is not None:
            hit = res
            break
    assert hit is not None
    assert hit["key"] == "comp_index_pinch_swipe_left"

    # 2. Pinch + Hold still -> comp_index_pinch_hold
    det.reset()
    t = 20.0
    hold_hit = None
    for _ in range(35):
        t += 0.02
        lm = _make_hand(0.5, 0.5, pinch_index=True)
        res = det.step(t, lm, "thumb_index_pinch", {"index"}, 1.0)
        if res is not None:
            hold_hit = res
            break
    assert hold_hit is not None
    assert hold_hit["key"] == "comp_index_pinch_hold"

    # 3. Register a Double-Pinch composite gesture and trigger it
    reg.register_composite_gesture(
        key="comp_index_double",
        name="검지 더블 핀치",
        base_posture="thumb_index",
        trigger_type="double_pinch",
        action_type="youtube_key",
        action_value="k",
    )
    det.reset()
    t = 30.0
    # First pinch
    for _ in range(4):
        t += 0.02
        det.step(t, _make_hand(0.5, 0.5, pinch_index=True), "thumb_index_pinch", {"index"}, 1.0)
    # Release
    for _ in range(4):
        t += 0.02
        det.step(t, _make_hand(0.5, 0.5, pinch_index=False), "none", set(), 1.0)
    # Second pinch within window
    t += 0.04
    dbl_hit = det.step(t, _make_hand(0.5, 0.5, pinch_index=True), "thumb_index_pinch", {"index"}, 1.0)
    assert dbl_hit is not None
    assert dbl_hit["key"] == "comp_index_double"


def test_dedicated_motion_trajectory_training_and_scoring(tmp_path: Path):
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir(parents=True, exist_ok=True)
    rec_file = rec_dir / "session_motion.jsonl.gz"

    rows = [
        {"type": "meta", "schema": 2, "mode": "guided", "target_gestures": ["swipe_right"]},
    ]
    t = 0.0
    # Record 3 repetitions of swipe_right ("do" phase moves right, "rest" phase returns left)
    for rep in range(1, 4):
        for i in range(18):
            t += 0.03
            cx = 0.30 + (i / 17.0) * 0.28
            lm = [[cx, 0.5, 0.0] for _ in range(21)]
            rows.append(
                {
                    "type": "frame",
                    "t": round(t, 4),
                    "lm": lm,
                    "label": {"phase": "do", "task": "swipe_right", "target": "swipe_right", "set": 1, "rep": rep},
                }
            )
        for i in range(18):
            t += 0.03
            cx = 0.58 - (i / 17.0) * 0.28
            lm = [[cx, 0.5, 0.0] for _ in range(21)]
            rows.append(
                {
                    "type": "frame",
                    "t": round(t, 4),
                    "lm": lm,
                    "label": {"phase": "rest", "task": "swipe_right", "target": "none", "set": 1, "rep": rep},
                }
            )
    rows.append({"type": "end", "frames": len(rows) - 1})
    with gzip.open(rec_file, "wt", encoding="utf-8") as gz:
        gz.write("\n".join(json.dumps(r) for r in rows) + "\n")

    out_model_path = tmp_path / "motion_templates.json"
    res = train_motion_templates_from_recordings(
        rec_dir=rec_dir,
        target_keys=["swipe_right"],
        output_path=out_model_path,
    )
    assert res["ok"] is True
    assert res["trained_count"] == 1
    tmpl = res["templates"]["swipe_right"]
    assert tmpl["positive_reps"] == 3
    assert tmpl["trajectory_recognition_rate"] >= 0.95
    assert tmpl["return_suppression_rate"] >= 0.95

    model = MotionTrajectoryModel.load(out_model_path)
    fwd_samples = [(i * 0.03, 0.30 + i * 0.018, 0.50) for i in range(15)]
    rev_samples = [(i * 0.03, 0.60 - i * 0.018, 0.50) for i in range(15)]

    ok_fwd, score_fwd, _ = model.score_window("swipe_right", fwd_samples)
    ok_rev, score_rev, _ = model.score_window("swipe_right", rev_samples)
    assert ok_fwd is True and score_fwd >= 0.58
    assert ok_rev is False and score_rev < 0.35


def test_training_center_rows_separate_static_and_motion_tracks(tmp_path: Path):
    summary = dataset_summary(tmp_path)
    rows = gesture_rows(summary, None)
    by_key = {r["key"]: r for r in rows}

    assert by_key["thumb_middle"]["track"] == "static"
    assert "정지 동작·핀치 학습" in by_key["thumb_middle"]["training_format_badge"]
    assert by_key["thumb_middle"]["shortcut_badge"] != ""

    assert by_key["swipe_left"]["track"] == "motion"
    assert "시계열 모션 학습" in by_key["swipe_left"]["training_format_badge"]
    assert by_key["swipe_left"]["shortcut_badge"] != ""


def test_qt_dashboard_train_motion_templates_slot_handles_zero_and_existing_recordings(tmp_path: Path, monkeypatch):
    from src import qt_dashboard, training_center

    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir(parents=True)
    monkeypatch.setattr(training_center, "REC_DIR", rec_dir)

    class _DummySignal:
        def emit(self):
            pass

    class _DummyJob:
        running = False

    class _DummyDash:
        training_busy = ""
        training_job = _DummyJob()
        training_note = ""
        trainingChanged = _DummySignal()
        trained_keys = []
        refreshed = False

        def _set_busy_task(self, title, step="", progress=-1.0):
            self.training_busy = title

        def trainSelectedGesture(self, key):
            self.trained_keys.append(key)

        def refreshTraining(self):
            self.refreshed = True

    dash = _DummyDash()
    # 1) When 0 recordings exist for swipe_tab_left, does not raise AttributeError and gives clear message
    # (the slot runs the template step on a worker thread; its UI-thread half is _finish_motion_templates)
    from src.motion_mode import train_motion_templates_from_recordings as real_train
    res0 = qt_dashboard.QtDashboard._finish_motion_templates(
        dash, "swipe_tab_left", real_train(rec_dir=rec_dir, target_keys=["swipe_tab_left"],
                                           output_path=tmp_path / "templates.json"), "")
    assert res0 is False
    assert "오류" not in dash.training_note
    assert "현재 0회" in dash.training_note
    assert dash.trained_keys == []

    # 2) When recordings exist for swipe_tab_left, trains trajectory template and calls trainSelectedGesture
    trained = {
        "ok": True,
        "trained_count": 1,
        "templates": {
            "swipe_tab_left": {
                "name": "모션 · 탭 왼쪽(이전 탭) 쓸기",
                "trajectory_recognition_rate": 1.0,
                "return_suppression_rate": 1.0,
            }
        },
    }
    res1 = qt_dashboard.QtDashboard._finish_motion_templates(dash, "swipe_tab_left", trained, "")
    assert res1 is True
    assert dash.trained_keys == ["swipe_tab_left"]
    assert "시계열 모션 궤적 템플릿 학습 완료" in dash.training_note



def test_motion_template_training_runs_off_the_ui_thread(monkeypatch):
    """Reading every recording took ~15 s on the UI thread (window froze); now a worker does it."""
    import threading
    from src import qt_dashboard

    seen = {}

    def slow_train(rec_dir=None, target_keys=None):
        seen["thread"] = threading.current_thread()
        return {"trained_count": 0, "templates": {}}

    monkeypatch.setattr("src.motion_mode.train_motion_templates_from_recordings", slow_train)

    class _Sig:
        def __init__(self):
            self.args = None
            self.done = threading.Event()

        def emit(self, *a):
            self.args = a
            self.done.set()

    class _Timer:
        def start(self):
            pass

    class _Dash:
        training_busy = ""
        training_job = type("J", (), {"running": False})()
        motionTemplatesDone = _Sig()
        training_timer = _Timer()

        def _set_busy_task(self, title, step="", progress=-1.0):
            self.training_busy = title

    d = _Dash()
    assert qt_dashboard.QtDashboard.trainMotionTemplatesSlot(d, "swipe_tab_left") is True
    assert d.training_busy                                  # busy banner while the worker reads files
    assert d.motionTemplatesDone.done.wait(5)
    assert seen["thread"] is not threading.main_thread()
    assert d.motionTemplatesDone.args[0] == "swipe_tab_left" and d.motionTemplatesDone.args[2] == ""
