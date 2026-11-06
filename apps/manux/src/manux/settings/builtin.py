"""Settings owned by the shared layers (app, camera, tracking). Features declare their own.

Defaults are the v0.3 values (hand-mouse/src/camera.py CameraConfig, hand_tracker.TRACKER_DEFAULTS,
gesture_engine hand_lost_reset, qt_runtime low_light_threshold).
"""
from __future__ import annotations

from manux.settings.schema import Setting

APP = (
    Setting("app.training_device", "auto", kind="choice", choices=("auto","cpu","cuda","xpu")),
    Setting("app.inference_device", "cpu", kind="choice", choices=("auto","cpu","cuda","openvino-cpu","openvino-gpu","openvino-npu")),
    Setting("app.hand_lost_reset", 0.3, minimum=0.0, maximum=5.0,
            doc="seconds without a hand before features reset and the output releases everything"),
    Setting("app.pc_alias", "", kind="str", doc="nickname written into recordings instead of the PC name"),
)

CAMERA = (
    Setting("camera.source", "0", kind="str", doc="camera index ('0', '1', ...) or a stream URL"),
    Setting("camera.width", 1280, kind="int", minimum=160, maximum=7680),
    Setting("camera.height", 720, kind="int", minimum=120, maximum=4320),
    Setting("camera.fps", 60, kind="int", minimum=1, maximum=240),
    Setting("camera.mirror", True, kind="bool"),
    Setting("camera.rotation", "0", kind="choice", choices=("0", "90", "180", "270")),
    Setting("camera.low_light_threshold", 70.0, minimum=0.0, maximum=255.0,
            doc="mean grey level below which the screen warns that the room is dark"),
    Setting("camera.note", "", kind="str", doc="free text written into recordings, e.g. 'phone camera'"),
)

TRACKING = (
    Setting("tracking.pose_enabled", False, kind="bool", doc="record optional shoulder/elbow/wrist landmarks; extra processing only during capture"),
    Setting("tracking.pose_every_n_frames", 5, kind="int", minimum=1, maximum=30),
    Setting("tracking.num_hands", 1, kind="int", minimum=1, maximum=2,
            doc="maximum detected hands; one avoids searching continuously for a second hand"),
    Setting("tracking.min_detection_confidence", 0.6, minimum=0.05, maximum=0.95),
    Setting("tracking.min_presence_confidence", 0.5, minimum=0.05, maximum=0.95),
    Setting("tracking.min_tracking_confidence", 0.5, minimum=0.05, maximum=0.95),
    Setting("tracking.detect_max_width", 640, kind="int", minimum=0, maximum=4096,
            doc="shrink frames wider than this before detection; 0 = full width"),
    Setting("tracking.hand_crop", False, kind="bool", doc="look at a full-resolution square around the last hand"),
    Setting("tracking.control_hand", "", kind="choice", choices=("", "Left", "Right"),
            doc="the physical hand that controls; empty = not chosen yet"),
    Setting("tracking.min_hand_score", 0.75, minimum=0.0, maximum=1.0,
            doc="handedness score a detection needs to count as the control hand"),
)

ALL = APP + CAMERA + TRACKING
