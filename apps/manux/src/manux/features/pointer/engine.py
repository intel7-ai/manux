"""Pointer engine core (ported from v0.3 `hand-mouse/src/pointer_engine.py`, 2026-11-04 version).

Same inputs, same per-frame output (`PointerFrame`) and same rules as v0.3
(`hand-mouse/docs/engine-contract.md`). The only structural change: the pinch click uses
`pinch.PointerPinch` instead of a v0.3 `GestureEngine` instance (see pinch.py for why and what was
kept). `PointerFeature` (__init__.py) turns these frames into contract `Intent`s.

Per frame (`step`):
- tracking only (click "off" or the tracking test): always move, never click
- move latch: while the anchor point is moving, move and detect no click
- otherwise the chosen click style: "pinch" (thumb + middle / ring) or "trigger" (thumb pressed
  on the index side)
- a click needs the thumb to leave the finger first after a latch or a hand loss
- an isolated index-tip jump holds the cursor for a frame (index anchor only)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from manux.features.pointer.pinch import LEFT, RIGHT, PointerPinch, finger_curl_ratios, palm_anchor_point
from manux.features.pointer.settings import PointerSettings
from manux.features.pointer.workspace import (
    IndexTipGuard, MoveLatch, PointerPose, ThumbTrigger, calibrated_pointer,
    is_pointing_pose, thumb_contacts, thumb_distances,
)

INDEX_TIP = 8
CLICKS = (LEFT, RIGHT)


@dataclass
class PointerFrame:
    """What should happen this frame. Coordinates are normalized camera space."""
    preview_point: Tuple[float, float]
    contacts: Tuple[int, ...] = ()
    action: Optional[str] = None          # move_cursor | pointer_wait | pointer_release | left_click | right_click | drag_hold
    cursor_point: Optional[Tuple[float, float]] = None
    pointer_point: Optional[Tuple[float, float]] = None
    pointer_source: str = "index"
    warp: Optional[Tuple] = None          # put the cursor here first (trigger rewind)
    latched: bool = False
    speed: float = 0.0
    click_armed: bool = False
    trigger_ratio: float = math.nan


class PointerCore:
    def __init__(self) -> None:
        self.pinch = PointerPinch()
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
        self.pinch.reset()

    def frame_missing(self) -> None:
        """The camera gave no frame: drop click state, keep the move latch and arming."""
        self.guard.reset()
        self.pose.reset()
        self.trigger.reset()
        self.pinch.reset()

    def _reset_clicks(self) -> None:
        self.guard.reset()
        self.pose.reset()
        self.trigger.reset()

    def record_cursor(self, t: float, pos: Tuple) -> None:
        """Where the cursor ended up this frame (the trigger rewinds to an earlier one)."""
        self.trigger.record(t, pos)

    @property
    def pressing(self) -> bool:
        """A click or drag is in progress (pinch waiting for release, trigger pressed)."""
        return bool(self.pose.pending) or self.trigger.state in ("press", "drag")

    # ------------------------------------------------------------------ per frame
    def step(self, t: float, landmarks: Optional[Sequence], aspect: float,
             settings: PointerSettings, *, active: bool = True, detecting: bool = True
             ) -> Optional[PointerFrame]:
        if landmarks is None or not active:
            self._reset_clicks()
            self.click_armed = False
        if not active:
            self.latch.reset()
        elif landmarks is None:
            self.latch.expire(t)  # a short hand loss keeps the latch
        if not active or not detecting:
            self.pinch.reset()
        elif landmarks is None:
            self.pinch.process(None, t)
        if landmarks is None:
            return None

        points = landmarks
        tip = points[INDEX_TIP]
        anchor_palm = settings.anchor == "palm"
        self.pinch.config.update(settings.config)
        preview = (palm_anchor_point(points, self.pinch.config) if anchor_palm else (tip.x, tip.y))
        contacts = thumb_contacts(thumb_distances(points, aspect))
        frame = PointerFrame(preview_point=preview, contacts=contacts,
                             pointer_source="palm" if anchor_palm else "index")
        if not active:
            return frame

        trigger_mode = settings.click_style == "trigger" and not settings.clicks_off
        curls = finger_curl_ratios(points)
        pointing = is_pointing_pose(points, curls, aspect)
        idle_move = "move_cursor" if pointing else "pointer_wait"
        self.latch.configure(settings.config)
        pressing = trigger_mode and self.trigger.state in ("press", "drag")
        latched = self.latch.update(t, preview, aspect)

        if settings.clicks_off:
            self.pinch.reset()
            self.pose.reset()
            self.trigger.reset()
            self.click_armed = True
            action = "move_cursor"
        elif latched and not pressing:
            self.pinch.reset()
            self.pose.reset()
            self.trigger.reset()
            self.click_armed = False
            action = "move_cursor"
        elif trigger_mode:
            self.pinch.reset()
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
        cfg = self.pinch.config
        gesture = self.pinch.process(points, t)
        if not self.click_armed:
            # In a pointing pose the curled middle/ring tips sit next to the thumb, so a
            # "pinch" exists before any press. Only one that starts after the thumb left counts.
            if not gesture.pinching and gesture.click not in CLICKS:
                self.click_armed = True
            self.pose.reset()
            return idle_move
        released = False
        if self.pose.pending:
            finger_tip = 12 if self.pose.pending == LEFT else 16
            thumb = points[4]
            release = float(cfg.get("click_threshold", .052)) + float(cfg.get("click_release_margin", .02))
            released = all(math.hypot(thumb.x - p.x, thumb.y - p.y) > release
                           for p in (points[finger_tip], points[finger_tip - 1]))
        return self.pose.update(t, pointing=pointing, click=gesture.click, released=released,
                                pinching=gesture.pinching)
