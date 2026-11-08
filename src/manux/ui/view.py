"""ViewFrame: what the desktop runtime hands the UI after each frame (docs/contracts.md section 12).

The engine's `Snapshot` plus the parts only the composition root has: the preview image, the hands
found (for the hand-choice screen), the control hand's landmarks (for drawing and the finger HUD),
camera metrics and timing. Immutable; the UI keeps only the newest one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

from manux.core.contracts import Landmark, PracticeEvent
from manux.engine.snapshot import Snapshot


@dataclass(frozen=True)
class SeenHand:
    side: str                    # physical "Left" / "Right", or "" when the label was unsure
    score: float


@dataclass(frozen=True)
class ViewFrame:
    snapshot: Optional[Snapshot]               # None before the first engine frame
    image: Any = None                          # BGR numpy frame for the preview, None while the window is hidden
    hands: Tuple[SeenHand, ...] = ()           # every hand tracking found
    landmarks: Optional[Tuple[Landmark, ...]] = None   # the control hand, image coordinates
    world: Optional[Tuple[Landmark, ...]] = None       # the control hand, 3D world coordinates in metres
    mirrored: bool = True
    aspect: float = 0.5625                     # frame height / width
    camera_mode: str = ""                      # e.g. "1280×720 · 30fps"
    brightness: int = -1                       # mean grey 0..255, -1 unknown
    low_light: bool = False
    fps: float = 0.0
    detect_ms: float = 0.0
    camera_ok: bool = True
    message: str = ""                          # one-line status from the runtime (camera errors ...)
    actions: Tuple[str, ...] = field(default=())   # action ids routed this frame (cards, tutorial, HUD)
    model_status: str = ""                     # composed by the runtime, no UI model decisions
    tracking: Optional[Mapping[str, Any]] = None   # tracking test window state (data.tracking_test.view), None = closed
    recent: Tuple[str, ...] = ()               # tracking test result lines, newest first
    rec: Optional[Mapping[str, Any]] = None    # dataset recording view (guided / free), None = off
    frame_age_ms: Optional[float] = None      # app capture timestamp -> runtime publish, not sensor/display latency
    pose_status: str = ""                    # optional recording-only Pose tracking status
    spatial_guard_enabled: bool = False      # actual runtime state, never a UI prediction
    preview_guides: Tuple[Tuple[int, str], ...] = ()  # fingertip index + near/pinched/action; display only
    preview_anchor: Optional[Tuple[float, float]] = None
    preview_moving: bool = False              # rule preview, not evidence of OS output
    control_hand: str = ""                    # physical selected hand, supplied by the runtime
    preview_gesture: str = ""                 # engine gesture id; the UI never classifies landmarks
    practice_cursor: Optional[Tuple[float, float]] = None  # virtual sink cursor, normalized screen
    practice_precision: bool = False          # accepted movement gain below one
    practice_events: Tuple[PracticeEvent, ...] = ()  # bounded events; UI consumes each sequence once
    practice_epoch: int = 0                    # persistent reset boundary; latest-only reads are safe
    rule_preview: Optional[Snapshot] = None    # practice-only rules during registration or recording
