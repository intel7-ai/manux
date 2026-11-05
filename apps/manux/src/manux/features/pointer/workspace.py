"""Vision-space helpers for the index pointer (ported from v0.3 `hand-mouse/src/pointer_workspace.py`).

The logic is copied unchanged so the pointer feature gives the same output as v0.3 on the same
landmarks (see tests/unit/features/test_pointer_parity.py). Landmarks are objects with `.x`,
`.y`, `.z` (normalized image coordinates, docs/contracts.md section 1).
"""
from __future__ import annotations

import math

FINGERTIPS = (4, 8, 12, 16, 20)


def thumb_distances(landmarks, aspect):
    def distance(i, j):
        a, b = landmarks[i], landmarks[j]
        return math.hypot(a.x - b.x, (a.y - b.y) * aspect, a.z - b.z)

    width = distance(5, 17)
    if not math.isfinite(width) or width < .025:
        return ()
    ratios = tuple(distance(4, tip) / width for tip in FINGERTIPS[1:])
    return ratios if all(map(math.isfinite, ratios)) else ()


def thumb_contacts(ratios):
    """Return unambiguous thumb contacts by fingertip ID."""
    if any(.23 < r < .36 for r in ratios):
        return ()
    contacts = tuple(i for i, r in enumerate(ratios, 1) if r <= .23)
    return (0, *contacts) if contacts else ()


def pointer_axes(center, right, up, aspect):
    """Invert the measured right/down basis in aspect-corrected image units."""
    rx, ry = right[0] - center[0], (right[1] - center[1]) * aspect
    dx, dy = center[0] - up[0], (center[1] - up[1]) * aspect
    width, height = math.hypot(rx, ry), math.hypot(dx, dy)
    det = rx * dy - ry * dx
    if min(width, height) < .025 or det <= .5 * width * height or rx <= 0 or dy <= 0:
        return None
    # A comfortable stroke represents the same control distance on either axis.
    scale = .08 / det
    return (dy * scale, -dx * scale, -ry * scale, rx * scale)


def calibrated_pointer(point, axes, aspect):
    if axes is None:
        return point
    x, y = point[0] - .5, (point[1] - .5) * aspect
    xx, xy, yx, yy = axes
    # Preserve the image center and discard old relative-mode gain calibration.
    return (
        .5 + (xx * x + xy * y) / math.hypot(xx, xy),
        .5 + (yx * x + yy * y) / (math.hypot(yx, yy) * aspect),
    )


class IndexTipGuard:
    """Hold isolated jumps in the tip-to-middle-joint vector."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.offset = None
        self.pending = None

    def accept(self, landmarks, aspect):
        tip, joint = landmarks[8], landmarks[6]
        index_base, pinky_base = landmarks[5], landmarks[17]
        offset = (tip.x - joint.x, (tip.y - joint.y) * aspect)
        palm_width = math.hypot(index_base.x - pinky_base.x,
                               (index_base.y - pinky_base.y) * aspect)
        if not all(map(math.isfinite, (*offset, palm_width))):
            self.reset()
            return False
        limit = max(.012, .22 * palm_width)
        if self.offset is not None and math.dist(offset, self.offset) > limit:
            if self.pending is None or math.dist(offset, self.pending) > limit / 2:
                self.pending = offset
                return False
        self.offset, self.pending = offset, None
        return True


def is_pointing_pose(landmarks, curls, aspect):
    """Recognize a pointing finger even when it aims toward the webcam."""
    index, *others = curls
    if index >= 1.4 and max(others) <= 1.3:
        return True

    def straightness(base):
        chain = [(p.x, p.y * aspect, p.z) for p in landmarks[base:base + 4]]
        length = sum(math.dist(a, b) for a, b in zip(chain, chain[1:]))
        return math.dist(chain[0], chain[-1]) / length if length > .001 else math.nan

    return straightness(5) >= .90 and all(
        curl <= 1.3 or straightness(base) < .88
        for curl, base in zip(others, (9, 13, 17))
    )


class PointerPose:
    """Move while pointing and release a confirmed pinch to click."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = None
        self.release_since = None

    def update(self, now, *, pointing, click=None, released=False, pinching=False):
        if self.pending:
            if not released:
                self.release_since = None
            elif self.release_since is None:
                self.release_since = now
            if self.release_since is not None and now - self.release_since >= .035:
                action = self.pending
                self.reset()
                return action
            return "pointer_release"

        if click in ("left_click", "right_click"):
            self.pending = click
            return "pointer_release"
        if pinching:
            return "pointer_wait"
        return "move_cursor" if pointing else "pointer_wait"


def trigger_ratio(landmarks, aspect):
    """Thumb tip distance to the index finger's first segment (#5-#6), in palm widths.

    Option A in docs/design/decision-pointer-click.md: the thumb presses the side of
    the pointing index finger. NaN when the palm is too small or a point is unusable.
    """
    def point(i):
        p = landmarks[i]
        return p.x, p.y * aspect, p.z

    a, b, thumb = point(5), point(6), point(4)
    width = math.dist(point(5), point(17))
    seg = tuple(q - p for p, q in zip(a, b))
    length2 = sum(v * v for v in seg)
    if not math.isfinite(width) or width < .025 or not math.isfinite(length2) or length2 < 1e-8:
        return math.nan
    t = max(0.0, min(1.0, sum((c - p) * v for p, c, v in zip(a, thumb, seg)) / length2))
    nearest = tuple(p + t * v for p, v in zip(a, seg))
    ratio = math.dist(thumb, nearest) / width
    return ratio if math.isfinite(ratio) else math.nan


class ThumbTrigger:
    """Option A click state machine for the index pointer (not measured on real hands yet).

    - thumb comes near the index side: the cursor freezes
    - press starts: the cursor jumps back to where it was `rewind` seconds earlier
    - quick press and release without moving: left click
    - press held still for `long_press` seconds: right click (once), then wait for release
    - press held while the index tip moves: left-button drag until release

    `update()` returns an action for MouseController.apply. When `warp` is set after a
    call, the caller should put the cursor there before applying the action.
    Every threshold is a starting guess that must be tuned on a real webcam.
    """

    DEFAULTS = {
        "trigger_press_ratio": .28,
        "trigger_release_ratio": .40,
        "trigger_approach_ratio": .50,
        "trigger_long_press": .55,
        "trigger_drag_move": .35,
        "trigger_rewind": .10,
    }

    def __init__(self, **settings):
        self.settings = dict(self.DEFAULTS)
        self.configure(settings)
        self.history = []
        self.reset()

    def configure(self, config):
        for key in self.DEFAULTS:
            try:
                value = float(config.get(key, self.settings[key]))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                self.settings[key] = value

    def reset(self):
        self.state = "idle"
        self.press_at = None
        self.press_tip = None
        self.fired = False
        self.warp = None
        self.ratio = math.nan

    def record(self, now, screen_pos):
        """Remember where the cursor was, for the rewind on press."""
        self.history.append((now, screen_pos))
        keep = now - max(.3, 2 * self.settings["trigger_rewind"])
        while len(self.history) > 2 and self.history[1][0] < keep:
            self.history.pop(0)

    def _rewound(self, now):
        target = now - self.settings["trigger_rewind"]
        best = None
        for when, pos in self.history:
            if when <= target:
                best = pos
        return best if best is not None else (self.history[0][1] if self.history else None)

    def update(self, now, landmarks, aspect, *, pointing):
        s = self.settings
        self.warp = None
        self.ratio = ratio = trigger_ratio(landmarks, aspect)
        tip = landmarks[8]
        width = math.dist((landmarks[5].x, landmarks[5].y * aspect),
                          (landmarks[17].x, landmarks[17].y * aspect))

        if not math.isfinite(ratio) or width < .025:
            dragging = self.state == "drag"
            self.reset()
            return "pointer_wait" if dragging else ("move_cursor" if pointing else "pointer_wait")

        if self.state in ("press", "drag"):
            if ratio >= s["trigger_release_ratio"]:
                action = "left_click" if self.state == "press" and not self.fired else "pointer_wait"
                self.reset()
                return action
            if self.state == "drag":
                return "drag_hold"
            moved = math.dist((tip.x, tip.y * aspect), self.press_tip) / width
            if not self.fired and moved >= s["trigger_drag_move"]:
                self.state = "drag"
                return "drag_hold"
            if not self.fired and now - self.press_at >= s["trigger_long_press"]:
                self.fired = True
                return "right_click"
            return "pointer_wait"

        if ratio <= s["trigger_press_ratio"]:
            self.state = "press"
            self.press_at = now
            self.press_tip = (tip.x, tip.y * aspect)
            self.fired = False
            self.warp = self._rewound(now)
            return "pointer_wait"
        if ratio <= s["trigger_approach_ratio"]:
            self.state = "approach"
            return "pointer_wait"
        self.state = "idle"
        return "move_cursor" if pointing else "pointer_wait"


class MoveLatch:
    """Keep "move cursor" while the index tip is moving, until it has been still a while.

    Reported 2026-11-03: while moving with the index finger, a resting thumb contact (or a
    blurred frame) switched the pointer into the pre-click state and froze the cursor.
    While latched, the caller moves the cursor and runs no click detection at all; a
    frame that fails the pointing check or a short hand loss does not end the latch.

    Speed is the index tip's displacement over `window` seconds, in frame widths per
    second (y scaled by the frame aspect). Over `pointer_move_speed` starts the latch;
    under `pointer_still_speed` counts as still; `pointer_move_latch` seconds of being
    still ends it. All three are starting guesses to tune on a real webcam.
    """

    DEFAULTS = {
        "pointer_move_latch": .75,
        "pointer_move_speed": .20,
        "pointer_still_speed": .08,
    }

    def __init__(self, window: float = .10, **settings):
        self.window = window
        self.settings = dict(self.DEFAULTS)
        self.configure(settings)
        self.reset()

    def configure(self, config):
        for key in self.DEFAULTS:
            try:
                value = float(config.get(key, self.settings[key]))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                self.settings[key] = value

    def reset(self):
        self.latched = False
        self.last_motion = None
        self.samples = []
        self.speed = 0.0

    def update(self, now, tip, aspect):
        """Feed the index tip (normalized x, y); return True while the move latch holds."""
        s = self.settings
        point = (float(tip[0]), float(tip[1]) * aspect)
        if not all(map(math.isfinite, point)):
            return self.expire(now)
        self.samples.append((now, point))
        while len(self.samples) > 2 and self.samples[1][0] <= now - self.window:
            self.samples.pop(0)
        then, old = self.samples[0]
        self.speed = math.dist(point, old) / (now - then) if now - then >= self.window / 2 else 0.0
        if self.speed >= s["pointer_move_speed"]:
            self.latched = True
            self.last_motion = now
        elif self.latched and self.speed >= s["pointer_still_speed"]:
            self.last_motion = now  # still moving, just slower
        return self.expire(now)

    def expire(self, now):
        """End the latch after `pointer_move_latch` s without movement (also during hand loss)."""
        if self.latched and now - self.last_motion >= self.settings["pointer_move_latch"]:
            self.latched = False
        return self.latched
