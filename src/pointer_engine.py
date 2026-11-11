"""
Pointer engine: one hand's landmarks in, one pointer intent out (2026-11-04).

This is the portable core of the Qt index-pointer mode. It holds no camera, MediaPipe,
Qt, OpenCV or OS-input code: the caller feeds 21 normalized hand landmarks (as MediaPipe
gives them in Python or JavaScript) plus plain settings, and applies the returned intent
with whatever mouse backend the platform has. See docs/engine-contract.md for the data
contract, written so the engine can be ported (e.g. to TypeScript for a Tauri shell) and
checked against the same cases.

Per frame (`step`):
- tracking only (click "off" or the tracking test): always move, never click
- move latch: while the anchor point is moving, move and detect no click
- otherwise the chosen click style: "pinch" (thumb + middle/ring via the gesture engine)
  or "trigger" (thumb pressed on the index side, option A)
- a click needs the thumb to leave the finger first after a latch or a hand loss
- an isolated index-tip jump holds the cursor for a frame (index anchor only)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

try:
    from src.pointer_workspace import (
        IndexTipGuard, MoveLatch, PointerPose, ThumbTrigger, calibrated_pointer,
        is_pointing_pose, thumb_contacts, thumb_distances,
    )
except ImportError:  # pragma: no cover - running from src/
    from pointer_workspace import (
        IndexTipGuard, MoveLatch, PointerPose, ThumbTrigger, calibrated_pointer,
        is_pointing_pose, thumb_contacts, thumb_distances,
    )

INDEX_TIP = 8
CLICKS = ("left_click", "right_click")
POINTER_GESTURES = {
    "thumb_middle_pinch": {"enabled": True, "action": "left_click", "cooldown": .35},
    "thumb_ring_pinch": {"enabled": True, "action": "right_click", "cooldown": .45},
}


@dataclass
class PointerSettings:
    click_style: str = "pinch"          # "pinch" | "trigger" | "off"
    anchor: str = "index"               # "index" (fingertip) | "palm" (palm centre)
    tracking_only: bool = False         # forced by the tracking test (no clicks at all)
    axes: Optional[Tuple[float, float, float, float]] = None  # direction calibration
    config: Dict[str, Any] = field(default_factory=dict)      # thresholds (gestures.json)

    @property
    def clicks_off(self) -> bool:
        return self.tracking_only or self.click_style == "off"


@dataclass
class PointerFrame:
    """What the platform should do this frame. Coordinates are normalized camera space."""
    preview_point: Tuple[float, float]              # the anchor point (for drawing)
    contacts: Tuple[int, ...] = ()                  # fingertips touching the thumb (diagnostics)
    action: Optional[str] = None                    # None while inactive (paused / calibrating)
    cursor_point: Optional[Tuple[float, float]] = None  # calibrated anchor; set when moving
    pointer_point: Optional[Tuple[float, float]] = None  # calibrated anchor (always, for the mouse adapter)
    pointer_source: str = "index"
    warp: Optional[Tuple[int, int]] = None          # put the screen cursor here first (trigger rewind)
    latched: bool = False
    speed: float = 0.0
    click_armed: bool = False
    trigger_ratio: float = math.nan


class PointerEngine:
    def __init__(self, gesture_engine) -> None:
        # gesture_engine: src.gesture_engine.GestureEngine (pinch detection + finger curls)
        self.gestures = gesture_engine
        self.guard = IndexTipGuard()
        self.pose = PointerPose()
        self.trigger = ThumbTrigger()
        self.latch = MoveLatch()
        self.click_armed = False

    # ------------------------------------------------------------------ resets
    def reset(self) -> None:
        """Everything back to rest (pointer mode off, paused, calibrating)."""
        self._reset_clicks()
        self.click_armed = False
        self.latch.reset()
        self.gestures.reset()

    def frame_missing(self) -> None:
        """The camera gave no frame: drop click state, keep the move latch and arming."""
        self.guard.reset()
        self.pose.reset()
        self.trigger.reset()
        self.gestures.reset()

    def _reset_clicks(self) -> None:
        self.guard.reset()
        self.pose.reset()
        self.trigger.reset()

    def record_cursor(self, t: float, screen_pos: Tuple[int, int]) -> None:
        """Feed back where the cursor ended up (the trigger rewinds to an earlier one)."""
        self.trigger.record(t, screen_pos)

    # ------------------------------------------------------------------ per frame
    def step(self, t: float, landmarks: Optional[Sequence], aspect: float,
             settings: PointerSettings, *, active: bool, detecting: bool = True
             ) -> Optional[PointerFrame]:
        """One camera frame. `landmarks` = the selected hand's 21 points, or None."""
        if landmarks is None or not active:
            self._reset_clicks()
            self.click_armed = False
        if not active:
            self.latch.reset()
        elif landmarks is None:
            self.latch.expire(t)  # a short hand loss keeps the latch
        if not active or not detecting:
            self.gestures.reset()
        elif landmarks is None:
            self.gestures.process(None, timestamp=t)
        if landmarks is None:
            return None

        points = landmarks
        tip = points[INDEX_TIP]
        anchor_palm = settings.anchor == "palm"
        self.gestures.config.update(settings.config)
        preview = (self.gestures.palm_anchor_point(points) if anchor_palm else (tip.x, tip.y))
        contacts = thumb_contacts(thumb_distances(points, aspect))
        frame = PointerFrame(preview_point=preview, contacts=contacts,
                             pointer_source="palm" if anchor_palm else "index")
        if not active:
            return frame

        trigger_mode = settings.click_style == "trigger" and not settings.clicks_off
        curls = self.gestures.finger_curl_ratios(points)
        pointing = is_pointing_pose(points, curls, aspect)
        idle_move = "move_cursor" if pointing else "pointer_wait"
        self.latch.configure(settings.config)
        pressing = trigger_mode and self.trigger.state in ("press", "drag")
        latched = self.latch.update(t, preview, aspect)

        if settings.clicks_off:
            # Follow the hand, whatever its shape, and never detect a click
            self.gestures.reset()
            self.pose.reset()
            self.trigger.reset()
            self.click_armed = True
            action = "move_cursor"
        elif latched and not pressing:
            self.gestures.reset()
            self.pose.reset()
            self.trigger.reset()
            self.click_armed = False
            action = "move_cursor"
        elif trigger_mode:
            self.gestures.reset()
            self.pose.reset()
            self.trigger.configure(settings.config)
            action = self.trigger.update(t, points, aspect, pointing=pointing)
            if not self.click_armed:
                # A thumb already at the index side when the latch ends is a rest, not a press
                if self.trigger.state == "idle":
                    self.click_armed = True
                else:
                    self.trigger.reset()
                    action = idle_move
        else:
            action = self._pinch(t, points, pointing, idle_move)

        if action == "move_cursor" and not anchor_palm:
            if not self.guard.accept(points, aspect):
                action = "pointer_wait"
        else:
            self.guard.reset()

        pointer = calibrated_pointer(preview, settings.axes, aspect)
        moving = action in ("move_cursor", "drag_hold")
        frame.action = action
        frame.pointer_point = pointer
        frame.cursor_point = pointer if moving else None
        frame.warp = self.trigger.warp if trigger_mode else None
        frame.latched = latched
        frame.speed = self.latch.speed
        frame.click_armed = self.click_armed
        frame.trigger_ratio = self.trigger.ratio
        return frame

    def _pinch(self, t, points, pointing, idle_move) -> str:
        cfg = self.gestures.config
        cfg.update(gestures=POINTER_GESTURES, record_fling_enabled=False,
                   grab_scroll_enabled=False, long_press_drag=False)
        gesture = self.gestures.process(points, timestamp=t)
        if not self.click_armed:
            # In a pointing pose the curled middle/ring tips sit next to the thumb, so a
            # "pinch" exists before any press. Only one that starts after the thumb left counts.
            if gesture.active_gesture not in POINTER_GESTURES and gesture.action not in CLICKS:
                self.click_armed = True
            self.pose.reset()
            return idle_move
        released = False
        if self.pose.pending:
            finger_tip = 12 if self.pose.pending == "left_click" else 16
            thumb = points[4]
            release = float(cfg.get("click_threshold", .052)) + float(cfg.get("click_release_margin", .02))
            released = all(math.hypot(thumb.x - p.x, thumb.y - p.y) > release
                           for p in (points[finger_tip], points[finger_tip - 1]))
        return self.pose.update(t, pointing=pointing, click=gesture.action, released=released,
                                pinching=gesture.active_gesture in POINTER_GESTURES)
