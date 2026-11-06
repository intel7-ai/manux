"""Optional Pose stays dormant by default; failed tracking cannot stop recording."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from manux.app import schema
from manux.desktop import build
from manux.settings.store import Settings
from manux.tracking.pose import PoseTracker, MODEL_SHA256


def test_disabled_pose_never_opens_a_model_and_failure_is_recording_local(tmp_path):
    runtime = build(Settings(schema()), SimpleNamespace(close=lambda: None), windows=False)
    assert runtime._pose_step(None, 1) is None and runtime._pose_tracker is None
    runtime.settings.set("tracking.pose_enabled", True)
    runtime._rec = SimpleNamespace()
    runtime.pose_model = tmp_path / "missing.task"
    assert runtime._pose_step(object(), 1) is None
    assert runtime._pose_failed and "계속" in runtime.pose_status
    runtime.settings.set("tracking.pose_enabled", False)
    assert runtime._pose_step(None, 2) is None and not runtime._pose_failed
    runtime._rec = None
    runtime.stop()


def test_pose_rejects_an_unexpected_model_before_loading_vision(tmp_path):
    path = tmp_path / "pose.task"
    path.write_bytes(b"synthetic invalid model")
    with pytest.raises(ValueError, match="checksum"): PoseTracker(path)


def test_bundled_pose_can_process_a_synthetic_blank_frame():
    pytest.importorskip("mediapipe")
    np = pytest.importorskip("numpy")
    import hashlib
    path = Path(__file__).resolve().parents[3] / "models" / "pose_landmarker_lite.task"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256
    tracker = PoseTracker(path)
    try:
        assert tracker.detect(np.zeros((480, 640, 3), dtype=np.uint8), 1.) is None
        assert tracker.detect(np.zeros((480, 640, 3), dtype=np.uint8), 1.1) is None
    finally: tracker.close()
