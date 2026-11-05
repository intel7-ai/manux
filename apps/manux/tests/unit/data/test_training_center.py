"""Unit tests for `manux.data.training_center` (dataset summary, v0.4/v0.3 model report, gesture rows, export)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from manux.core.contracts import FrameInfo, HandObservation
from manux.data import capture_plan as plans
from manux.data.catalog import DISPLAY_KEYS, tasks_for
from manux.data.guided import GuidedSession
from manux.data.recorder import SessionRecorder
from manux.data.training_center import (
    TrainingCenterService,
    dataset_summary,
    export_package,
    gesture_rows,
    model_report,
    storage_text,
)


def _hand(t: float) -> HandObservation:
    frame = FrameInfo(0, t, 640, 480, True)
    lm = tuple((0.3 + i * 0.01, 0.5 + (i % 5) * 0.01, 0.0) for i in range(21))
    world = tuple((i * 0.001, (i % 4) * 0.001, (i % 2) * 0.001) for i in range(21))
    return HandObservation(frame, lm, "Right", 0.95, world)


def _record_guided(folder: Path) -> Path:
    plan = plans.preset("basic")
    for row in plan["conditions"]:
        row["reps"] = 2
        row["prep"] = 0.5
        row["hold"] = 0.6
        row["rest"] = 0.5
        row["free_seconds"] = 10
    plan["conditions"] = plan["conditions"][:1]
    tasks = tasks_for(["thumb_ring", "none"])
    session = GuidedSession(tasks, plan=plan)
    rec = SessionRecorder(folder, "guided", {"guided": session.describe(), "capture_plan": plan}, now=0.0)
    t = 0.0
    while not session.done:
        session.advance(t)
        session.update(t, available=True)
        rec.frame(t, _hand(t), label=session.label(t))
        t = round(t + 0.05, 4)
    return rec.close()


def test_dataset_summary_and_gesture_rows_with_v04_held_out_results(tmp_path: Path) -> None:
    rec_dir = tmp_path / "recordings"
    _record_guided(rec_dir)
    summary = dataset_summary(rec_dir)
    assert len(summary["files"]) == 1
    assert summary["per_gesture"]["thumb_ring"]["reps"] == 2
    assert summary["per_gesture"]["none"]["minutes"] > 0.0
    text, warn = storage_text(summary)
    assert "MB" in text and not warn

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model_path = model_dir / "gesture_tcn.onnx"
    model_path.write_bytes(b"fake-onnx")
    card = {
        "classes": ["none", "thumb_ring"],
        "gestures": {"thumb_ring": "pinch.thumb_ring"},
        "kind": "personal",
        "window": 15,
        "features": 133,
        "feature_version": "local3d-v1",
        "threshold": 0.7,
        "frames_in_a_row": 3,
        "trained": "2026-11-05T16:00:00Z",
        "data": [{"file": summary["files"][0]["file"], "pc": "pc-1234", "frames": 100}],
        "held_out_results": [
            {
                "axis": "condition",
                "group": "pose-1",
                "status": "evaluated",
                "per_gesture": {"thumb_ring": {"attempts": 10, "tcn_hits": 9, "rule_hits": 6}},
                "neutral_seconds": 60.0,
                "tcn_misfires_per_minute": 0.2,
                "rule_misfires_per_minute": 1.5,
            }
        ],
    }
    model_path.with_suffix(".json").write_text(json.dumps(card), encoding="utf-8")

    report = model_report(model_path)
    assert report is not None
    assert report["per_gesture"]["thumb_ring"]["rate"] == 0.9
    assert report["per_gesture"]["thumb_ring"]["rules_rate"] == 0.6

    rows = gesture_rows(summary, report)
    assert [r["key"] for r in rows] == list(DISPLAY_KEYS)
    by_key = {r["key"]: r for r in rows}
    assert by_key["thumb_ring"]["score_text"] == "인식 90% (규칙 60%)"
    assert by_key["thumb_ring"]["can_train"] is True
    assert by_key["none"]["can_train"] is False
    assert "오작동 0.2회/분" in by_key["none"]["score_text"]


def test_export_package_creates_zip_and_readme(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model_path = model_dir / "gesture_tcn.onnx"
    model_path.write_bytes(b"fake-onnx")
    card = {
        "classes": ["none", "thumb_ring"],
        "window": 15,
        "features": 133,
        "feature_version": "local3d-v1",
        "threshold": 0.7,
        "frames_in_a_row": 3,
        "trained": "2026-11-05T16:00:00Z",
        "data": [],
        "held_out_results": [],
    }
    model_path.with_suffix(".json").write_text(json.dumps(card), encoding="utf-8")
    out = export_package(model_path, tmp_path / "exports")
    zip_file = out.with_suffix(".zip")
    assert zip_file.is_file()
    with zipfile.ZipFile(zip_file) as z:
        names = set(z.namelist())
        assert {"gesture_tcn.onnx", "gesture_tcn.json", "README.md"} <= names


def test_training_center_service_plan_round_trip(tmp_path: Path) -> None:
    svc = TrainingCenterService(tmp_path / "config", tmp_path / "data", tmp_path / "recordings")
    plan = svc.load_plan("thumb_ring")
    plan["conditions"][0]["name"] = "커스텀 정면"
    svc.save_plan(plan, "thumb_ring")
    reloaded = svc.load_plan("thumb_ring")
    assert reloaded["conditions"][0]["name"] == "커스텀 정면"
    assert svc.has_task("thumb_ring")
    assert not svc.has_task("nonexistent_gesture")
