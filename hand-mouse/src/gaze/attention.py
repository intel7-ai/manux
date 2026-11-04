"""AttentionGate (structure C): are the user's head and face turned toward the screen?

Only the head angles are used, so no eye model and no calibration are needed. Default state is
DISABLED: a disabled gate always allows commands, so adding it changes nothing until a person
turns it on. It is not connected to the hand pointer in this work (see docs/gaze-experiment.md
for the proposed connection).

Hysteresis: entering "attending" needs both angles inside the enter limits for
`min_enter_frames` consecutive frames; leaving needs a frame outside the exit limits (or no
face) for `min_exit_frames` consecutive frames. Exit limits are wider than enter limits, so a
head resting near the boundary does not flicker. The limit values are untuned placeholders.
"""

from __future__ import annotations

import math
from typing import Optional

from .features import HeadPose


class AttentionGate:
    def __init__(self, enabled: bool = False, enter_yaw_deg: float = 20.0, exit_yaw_deg: float = 28.0,
                 enter_pitch_deg: float = 15.0, exit_pitch_deg: float = 24.0,
                 min_enter_frames: int = 3, min_exit_frames: int = 2) -> None:
        self.enabled = bool(enabled)
        self.enter_yaw = abs(float(enter_yaw_deg))
        self.exit_yaw = max(abs(float(exit_yaw_deg)), self.enter_yaw)
        self.enter_pitch = abs(float(enter_pitch_deg))
        self.exit_pitch = max(abs(float(exit_pitch_deg)), self.enter_pitch)
        self.min_enter_frames = max(1, int(min_enter_frames))
        self.min_exit_frames = max(1, int(min_exit_frames))
        self.attending = False
        self._inside = 0
        self._outside = 0

    def reset(self) -> None:
        self.attending = False
        self._inside = 0
        self._outside = 0

    @staticmethod
    def _finite(pose: Optional[HeadPose]) -> bool:
        return pose is not None and math.isfinite(pose.yaw_deg) and math.isfinite(pose.pitch_deg)

    def update(self, pose: Optional[HeadPose]) -> bool:
        """Feed one frame (None = no face). Returns True when hand commands may run."""
        if not self.enabled:
            return True
        if not self._finite(pose):
            self._inside = 0
            self._outside += 1
            if self._outside >= self.min_exit_frames:
                self.attending = False
            return self.attending
        yaw, pitch = abs(pose.yaw_deg), abs(pose.pitch_deg)  # type: ignore[union-attr]
        if self.attending:
            out = yaw > self.exit_yaw or pitch > self.exit_pitch
            self._outside = self._outside + 1 if out else 0
            if self._outside >= self.min_exit_frames:
                self.attending = False
                self._outside = 0
                self._inside = 0
        else:
            inside = yaw <= self.enter_yaw and pitch <= self.enter_pitch
            self._inside = self._inside + 1 if inside else 0
            if self._inside >= self.min_enter_frames:
                self.attending = True
                self._inside = 0
                self._outside = 0
        return self.attending
