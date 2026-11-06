"""
On/off sign (2026-11-04): the V sign (index and middle finger up, ring and little finger folded), held still, switches the
mouse control on or off - the same as the Space key / the dashboard button, for an app that runs in the tray.

Pure logic like close_sign.py: feed (time, 21 landmarks | None, blocked); returns a "toggle" event after the hold. The
caller flips the control. It keeps watching while the control is OFF (that is how the sign turns it on again).

Why this pose: it touches no thumb pinch (no click can leak out first), differs from the rock sign (close window), the
thumbs-up (motion mode), the sideways open hand (show desktop) and the fist; a hand opening from a fist passes through it
for a moment only, which the hold time and the "still" rule absorb.

Honest limits: thresholds are starting values from the landmark geometry; how reliably a person forms the sign is not measured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from src.motion_mode import hand_features

DEFAULTS: Dict[str, Any] = {
    "toggle_sign_enabled": 1.0,      # 0 = off
    "toggle_sign_hold": 0.6,         # s the sign is held still (1.0 until 2026-11-04 night: with the quiet time it took ~2 s)
    "toggle_sign_still": 0.05,       # palm centre may move this much (frame widths) during the hold (was 0.03)
    "toggle_sign_grace": 0.2,        # s the sign may drop out of the hold (a noisy frame) without starting over
    "toggle_sign_up_ratio": 1.4,     # index and middle finger at least this extended (curl ratio)
    "toggle_sign_fold_ratio": 1.15,  # ring and little finger at most this
    "toggle_sign_gap": 0.3,          # and the extended ones this much above the folded ones
    "toggle_sign_quiet": 0.5,        # s after the last pinch / scroll / drag / click before a hold may start (control on; was 1.0)
}


@dataclass
class ToggleEvent:
    kind: str                        # "toggle"
    info: Dict[str, Any] = field(default_factory=dict)


class ToggleSign:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = dict(DEFAULTS)
        if config:
            self.config.update({k: v for k, v in config.items() if k in DEFAULTS})
        self.reset()

    def reset(self) -> None:
        self._since: Optional[float] = None
        self._anchor = (0.0, 0.0)
        self._lost_since: Optional[float] = None
        self._need_release = False
        self.last_pose: Optional[bool] = None

    def cfg(self, key: str) -> float:
        return float(self.config[key])

    def pose(self, f: Dict[str, Any]) -> bool:
        i, m, r, p = f["curl"]
        up, fold = self.cfg("toggle_sign_up_ratio"), self.cfg("toggle_sign_fold_ratio")
        return (i >= up and m >= up and r <= fold and p <= fold
                and min(i, m) - max(r, p) >= self.cfg("toggle_sign_gap"))

    def progress(self, now: float) -> float:
        if self._since is None or self._need_release:
            return 0.0
        return max(0.0, min(1.0, (now - self._since) / self.cfg("toggle_sign_hold")))

    def holding(self, now: float, settle: float = 0.25) -> bool:
        """True once the sign has been held for `settle` s (the cursor and the pinches are suspended meanwhile)."""
        return self._since is not None and not self._need_release and now - self._since >= settle

    def update(self, now: float, landmarks: Optional[Sequence[Any]], blocked: bool = False) -> Optional[ToggleEvent]:
        if self.cfg("toggle_sign_enabled") < 1.0:
            return None
        feats = hand_features(landmarks) if landmarks is not None else None
        ok = feats is not None and not blocked and self.pose(feats)
        self.last_pose = ok
        if not ok:
            if self._since is not None and feats is not None and not blocked:
                if self._lost_since is None:
                    self._lost_since = now
                if now - self._lost_since <= self.cfg("toggle_sign_grace"):
                    return None
            self._since = None
            self._lost_since = None
            self._need_release = False
            return None
        self._lost_since = None
        if self._need_release:
            return None                  # it switched something a moment ago: let go of the sign before it counts again
        palm = feats["palm"]
        if self._since is None or math.hypot(palm[0] - self._anchor[0], palm[1] - self._anchor[1]) > self.cfg("toggle_sign_still"):
            self._since, self._anchor = now, palm
            return None
        if now - self._since >= self.cfg("toggle_sign_hold"):
            self._since = None
            self._need_release = True
            return ToggleEvent("toggle")
        return None
