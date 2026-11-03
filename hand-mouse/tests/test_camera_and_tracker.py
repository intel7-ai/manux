"""
Unit tests for CameraStream source parsing and HandTracker detection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src import (
    CameraConfig,
    CameraStream,
    GestureEngine,
    GestureResult,
    HandTracker,
    MouseController,
    PhoneWebCameraServer,
)
from src.hand_tracker import DEFAULT_MODEL_PATH, ensure_model_exists


def test_package_exports():
    """Verify src package exports all core components."""
    assert CameraConfig is not None
    assert CameraStream is not None
    assert GestureEngine is not None
    assert GestureResult is not None
    assert HandTracker is not None
    assert MouseController is not None
    assert PhoneWebCameraServer is not None


def test_camera_stream_parse_source_numeric():
    """Numeric string and integer should resolve to integer camera index."""
    assert CameraStream.parse_source("0") == 0
    assert CameraStream.parse_source(0) == 0
    assert CameraStream.parse_source("1") == 1
    assert CameraStream.parse_source(2) == 2


def test_camera_stream_parse_source_network():
    """Shorthand IP:PORT and complete URLs should normalize properly."""
    assert (
        CameraStream.parse_source("192.168.0.15:8080")
        == "http://192.168.0.15:8080/video"
    )
    assert (
        CameraStream.parse_source("rtsp://192.168.0.10:554/live")
        == "rtsp://192.168.0.10:554/live"
    )
    assert (
        CameraStream.parse_source("http://192.168.0.15:8080/video")
        == "http://192.168.0.15:8080/video"
    )
    assert (
        CameraStream.parse_source("192.168.0.20:4747/video")
        == "http://192.168.0.20:4747/video"
    )


def test_hand_tracker_model_exists():
    """MediaPipe hand landmarker model file must exist and be valid."""
    model_path = ensure_model_exists(DEFAULT_MODEL_PATH)
    assert model_path.exists()
    assert model_path.stat().st_size > 1_000_000


def test_hand_tracker_synthetic_blank_frame():
    """Detecting on a synthetic blank frame should return [] without crashing."""
    tracker = HandTracker(model_path=DEFAULT_MODEL_PATH, num_hands=1)
    try:
        blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = tracker.detect(blank_frame)
        assert isinstance(detections, list)
        assert len(detections) == 0
    finally:
        tracker.close()


def test_tracker_options_default_to_mediapipe_tracking_and_read_config():
    from src.hand_tracker import TRACKER_DEFAULTS, tracker_options

    opts = tracker_options({})
    assert opts["min_tracking_confidence"] == 0.5 and opts["min_presence_confidence"] == 0.5
    assert opts["min_detection_confidence"] == 0.6 and opts["detect_max_width"] == 640
    opts = tracker_options({"tracker_min_tracking_confidence": 0.35, "tracker_detect_max_width": 0,
                            "tracker_min_presence_confidence": "x", "tracker_min_detection_confidence": 2})
    assert opts["min_tracking_confidence"] == 0.35 and opts["detect_max_width"] == 0
    assert opts["min_presence_confidence"] == TRACKER_DEFAULTS["tracker_min_presence_confidence"]
    assert opts["min_detection_confidence"] == TRACKER_DEFAULTS["tracker_min_detection_confidence"]


def test_actual_mode_is_none_until_a_webcam_is_open():
    from src.camera import CameraConfig, CameraStream

    assert CameraStream(CameraConfig(source=0)).actual_mode() is None
