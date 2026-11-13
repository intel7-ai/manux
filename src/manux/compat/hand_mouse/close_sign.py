"""
Close-window sign (2026-11-04): the rock sign (index and little finger up, middle and ring folded), held still, asks
to close the window in front; the same sign once more within a few seconds sends Alt+F4.

Pure logic like motion_mode.py: feed (time, 21 landmarks | None, blocked); no camera, Qt or OS input here. The
caller shows the question and sends the key (MouseController.send_motion_action("close_window"), which refuses the
desktop, the taskbar and this app's own window).

Why this pose: the thumb touches no finger, so no click can leak out before the hold (a thumb+middle+ring pinch
would fire a left or right click first), and no cursor / pinch / fist / thumbs-up / open-hand gesture looks like it.
Why two steps: Alt+F4 loses work when it fires by mistake. The first hold only asks.

Honest limits: thresholds are starting values from the landmark geometry (extended fingers sit at curl ratios of
1.5-1.8, folded ones at 0.7-1.2); how reliably a person forms the sign is not measured yet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from manux.compat.hand_mouse.motion_mode import hand_features

DEFAULTS: Dict[str, Any] = {
    "close_sign_enabled": 1.0,       # 0 = off
    "close_sign_hold": 0.5,          # s the sign is held still before the question appears
    "close_sign_confirm_hold": 0.5,  # s more it is held after the question (or made again within close_sign_window): then it closes
    "close_sign_window": 4.0,        # s the question stays open
    "close_sign_still": 0.08,        # palm centre may move this much (frame widths) during a hold
    "close_sign_grace": 0.3,         # s the sign may drop out of a hold (a noisy frame) without starting over
    "close_sign_up_ratio": 1.3,      # index must be at least this extended (curl ratio) ...
    "close_sign_pinky_ratio": 1.2,   # ... the little finger at least this
    "close_sign_fold_ratio": 1.15,   # middle and ring must be at most this
    "close_sign_gap": 0.25,          # and the extended ones this much above the folded ones
    "close_sign_quiet": 1.0,         # s after the last pinch / scroll / drag / click before a hold may start
}


@dataclass
class CloseEvent:
    kind: str                        # "ask" | "close" | "expire"
    info: Dict[str, Any] = field(default_factory=dict)


class CloseSign:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = dict(DEFAULTS)
        if config:
            self.config.update({k: v for k, v in config.items() if k in DEFAULTS})
        self.reset()

    def reset(self) -> None:
        self._since: Optional[float] = None
        self._anchor = (0.0, 0.0)
        self._lost_since: Optional[float] = None
        self._asked_at = -1e9
        self._need_release = False       # a window was just closed: the sign must be let go before it can ask again
        self._armed = False              # the sign was let go after the question (so the first hold does not also confirm)
        self.last_pose: Optional[bool] = None
        self.last_curl: Optional[tuple] = None

    def cfg(self, key: str) -> float:
        return float(self.config[key])

    def pose(self, f: Dict[str, Any]) -> bool:
        i, m, r, p = f["curl"]
        return (i >= self.cfg("close_sign_up_ratio") and p >= self.cfg("close_sign_pinky_ratio")
                and m <= self.cfg("close_sign_fold_ratio") and r <= self.cfg("close_sign_fold_ratio")
                and min(i, p) - max(m, r) >= self.cfg("close_sign_gap"))

    # ------------------------------------------------------------------ for the display
    def pending(self, now: float) -> float:
        """Seconds left to confirm (0 = no question open)."""
        return max(0.0, self.cfg("close_sign_window") - (now - self._asked_at))

    def progress(self, now: float) -> float:
        """0..1 while the sign is being held (towards the question, or towards closing)."""
        if self._since is None:
            return 0.0
        need = self.cfg("close_sign_confirm_hold") if self.pending(now) > 0 else self.cfg("close_sign_hold")
        return max(0.0, min(1.0, (now - self._since) / need))

    def holding(self, now: float, settle: float = 0.25) -> bool:
        """True once the sign has been held for `settle` s: the cursor and the pinches are suspended meanwhile
        (the folded fingers would otherwise run the cursor at several times its speed)."""
        return self._since is not None and now - self._since >= settle

    # ------------------------------------------------------------------ per frame
    def update(self, now: float, landmarks: Optional[Sequence[Any]], blocked: bool = False) -> Optional[CloseEvent]:
        if self.cfg("close_sign_enabled") < 1.0:
            return None
        expired = self._asked_at > -1e8 and self.pending(now) <= 0
        if expired:
            self._asked_at, self._armed = -1e9, False
        feats = hand_features(landmarks) if landmarks is not None else None
        ok = feats is not None and not blocked and self.pose(feats)
        self.last_pose = ok
        self.last_curl = tuple(round(c, 2) for c in feats["curl"]) if feats is not None else None
        if not ok:
            if self._since is not None and feats is not None and not blocked:
                if self._lost_since is None:
                    self._lost_since = now
                if now - self._lost_since <= self.cfg("close_sign_grace"):
                    return CloseEvent("expire") if expired else None
            self._since = None
            self._lost_since = None
            self._need_release = False
            if self.pending(now) > 0:
                self._armed = True       # let go once: the next hold counts as the confirmation
            return CloseEvent("expire") if expired else None
        self._lost_since = None
        if self._need_release:
            self._since = None
            return None
        palm = feats["palm"]
        if self._since is None or math.hypot(palm[0] - self._anchor[0], palm[1] - self._anchor[1]) > self.cfg("close_sign_still"):
            self._since, self._anchor = now, palm
            return None
        if self.pending(now) > 0:
            if now - self._since >= self.cfg("close_sign_confirm_hold"):
                self._asked_at, self._armed, self._since = -1e9, False, None
                self._need_release = True
                return CloseEvent("close")
            return None
        if now - self._since >= self.cfg("close_sign_hold"):
            self._asked_at, self._armed = now, False
            self._since = now            # keep holding: close_sign_confirm_hold later it closes; letting go cancels
            return CloseEvent("ask", {"curl": self.last_curl})
        return None
