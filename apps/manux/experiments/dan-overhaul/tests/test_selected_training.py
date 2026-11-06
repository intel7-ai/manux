import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src import gesture_models as bank
from src import training_center as tc
from src.gesture_catalog import CLASSES


def entry(folder, label):
    model = folder / f"{label}-test.onnx"
    model.write_bytes(label.encode())
    card = {"classes": ["none", label], "window": 15, "features": 133,
            "feature_version": "local3d-v1", "trained": "synthetic", "per_gesture": {},
            "per_task": {}, "data": []}
    model.with_suffix(".json").write_text(json.dumps(card), encoding="utf-8")
    return {"model": model.name, "card": model.with_suffix(".json").name,
            "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}


def test_binary_training_uses_other_gestures_as_negatives():
    from scripts.train_selected import binary_sessions, selected_labels

    original = np.array([-1, CLASSES.index("none"), CLASSES.index("thumb_ring"), CLASSES.index("thumb_index")])
    result, = binary_sessions([{"y": original}], "thumb_ring")
    assert result["y"].tolist() == [-1, 0, 1, 0]
    assert original[2] == CLASSES.index("thumb_ring")
    assert selected_labels(["thumb_middle", "thumb_middle_drag"]) == ["thumb_middle"]
    with pytest.raises(ValueError):
        selected_labels(["none"])
    with pytest.raises(ValueError):
        selected_labels(["missing"])


def test_selected_publication_preserves_unselected_models(tmp_path):
    ring, middle = entry(tmp_path, "thumb_ring"), entry(tmp_path, "thumb_middle")
    with bank.training_lock(tmp_path):
        bank.publish_bank(tmp_path, {"thumb_ring": ring})
    previous = (tmp_path / ring["model"]).read_bytes()
    with bank.training_lock(tmp_path):
        bank.publish_bank(tmp_path, {"thumb_middle": middle})
    assert bank.read_bank(tmp_path) == {"thumb_ring": ring, "thumb_middle": middle}
    assert (tmp_path / ring["model"]).read_bytes() == previous


def test_failure_before_publication_preserves_manifest_and_releases_lock(tmp_path):
    ring = entry(tmp_path, "thumb_ring")
    with bank.training_lock(tmp_path):
        bank.publish_bank(tmp_path, {"thumb_ring": ring})
    before = (tmp_path / "manifest.json").read_bytes()
    with pytest.raises(RuntimeError), bank.training_lock(tmp_path):
        entry(tmp_path, "thumb_middle")
        raise RuntimeError("export failed")
    assert (tmp_path / "manifest.json").read_bytes() == before
    with bank.training_lock(tmp_path):
        pass


def test_process_termination_releases_training_lock(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = ("from src.gesture_models import training_lock; import sys,time; "
            "lock=training_lock(sys.argv[1]); lock.__enter__(); print('locked',flush=True); time.sleep(30)")
    proc = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], cwd=root,
                            stdout=subprocess.PIPE, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        assert proc.stdout.readline().strip() == "locked"
        with pytest.raises(ValueError, match="다른 선택 학습"):
            with bank.training_lock(tmp_path):
                pass
    finally:
        tc.terminate_training_process(proc)
        proc.wait(timeout=10)
    with bank.training_lock(tmp_path):
        pass


def test_specialist_rejection_never_falls_back_to_base(tmp_path, monkeypatch):
    folder = tmp_path / "gestures"
    folder.mkdir()
    selected = entry(folder, "thumb_middle")
    bank.publish_bank(folder, {"thumb_middle": selected})
    def load(*_):
        return lambda x: np.array([0.99, 0.01]), {"classes": ["none", "thumb_middle"], "window": 15}
    monkeypatch.setattr(bank, "load_single", load)
    base_info = {"classes": ["none", "thumb_middle", "thumb_ring"],
                 "window": 15, "feature_version": "local3d-v1"}
    predict, info = bank.compose_predictor(tmp_path / "gesture_tcn.onnx",
                                          lambda x: np.array([0.01, 0.98, 0.8]), base_info)
    scores = predict(None)
    assert scores[info["classes"].index("thumb_middle")] == 0.01
    assert scores[info["classes"].index("thumb_ring")] == 0.8
    (folder / selected["model"]).write_bytes(b"broken")
    with pytest.raises(ValueError, match="해시"):
        bank.compose_predictor(tmp_path / "gesture_tcn.onnx", lambda x: None, base_info)


def test_training_job_passes_the_selected_gesture_to_the_trainer(monkeypatch):
    seen = []
    monkeypatch.setattr(tc.subprocess, "Popen", lambda args, **kw: seen.append(args) or SimpleNamespace())
    monkeypatch.setattr(tc.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    job = tc.TrainingJob()
    assert job.start(gestures=["thumb_middle"])
    assert seen[0][-2:] == ["--gestures", "thumb_middle"]
    assert not job.start(gestures=["thumb_ring"])


def test_ui_row_starts_only_its_selected_gesture():
    from src.qt_dashboard import QtDashboard
    seen = []
    ui = SimpleNamespace(
        rec_mode="off",
        training_job=SimpleNamespace(running=False, start=lambda **kw: seen.append(kw) or True),
        _pause_for_change=lambda: None,
        training_timer=SimpleNamespace(start=lambda: None),
        trainingChanged=SimpleNamespace(emit=lambda: None),
    )
    QtDashboard.trainSelectedGesture(ui, "thumb_middle")
    assert seen == [{"gestures": ["thumb_middle"]}]
    assert ui._training_was_running
    QtDashboard.trainSelectedGesture(ui, "none")
    assert len(seen) == 1


def test_export_contains_the_same_specialist_manifest(tmp_path, monkeypatch):
    model = tmp_path / "gesture_tcn.onnx"
    model.write_bytes(b"base")
    model.with_suffix(".json").write_text(json.dumps({
        "classes": ["none", "thumb_ring"], "trained": "synthetic", "window": 15,
        "features": 133, "feature_version": "local3d-v1", "threshold": 0.7,
        "frames_in_a_row": 3, "data": []}), encoding="utf-8")
    folder = tmp_path / "gestures"
    folder.mkdir()
    specialist = entry(folder, "thumb_middle")
    bank.publish_bank(folder, {"thumb_middle": specialist})
    monkeypatch.setattr(tc, "MODEL", model)
    out = tc.export_package(tmp_path / "exports", with_dataset=False)
    assert bank.read_bank(out / "gestures") == bank.read_bank(folder)
    assert (out / "gestures" / specialist["model"]).read_bytes() == b"thumb_middle"
    assert (out / "gesture_models.py").is_file()
    compile((out / "example_use.py").read_text(encoding="utf-8"), "example_use.py", "exec")
    rows = tc.gesture_rows({"per_gesture": {}, "newest": 0}, tc.model_report(model))
    middle = next(row for row in rows if row["key"] == "thumb_middle")
    assert "선택 동작 모델" in middle["model_text"]
    assert next(row for row in rows if row["key"] == "none")["can_train"] is False


def test_hot_reload_failure_keeps_previous_gate_and_success_releases_inputs(tmp_path, monkeypatch):
    import src.qt_runtime as module
    from tests.test_qt_runtime import runtime

    rt = runtime(tmp_path)
    previous = object()
    rt.gate = previous
    ui = SimpleNamespace(model_version=1, tcn_gate=True)
    def failed(_):
        raise ValueError("new card is invalid")
    monkeypatch.setattr(module, "load_predictor", failed)
    assert rt._gate_for(ui) is previous
    released = []
    monkeypatch.setattr(rt.mouse, "release_all", lambda: released.append(True))
    monkeypatch.setattr(module, "load_predictor", lambda _: (
        lambda x: np.array([1., 0., 0.]),
        {"classes": ["none", "thumb_index", "thumb_ring"], "window": 15}))
    ui.model_version += 1
    assert rt._gate_for(ui) is not previous
    assert released == [True]
