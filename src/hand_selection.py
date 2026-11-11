"""Select one physical hand from camera detections."""
import copy


def physical_side(detection, mirrored):
    side = detection.handedness
    if side not in ("Left", "Right") or not detection.score >= .75:
        return ""
    return {"Left": "Right", "Right": "Left"}[side] if mirrored else side


def selected_detection(detections, side, mirrored):
    matches = [hand for hand in detections if side and physical_side(hand, mirrored) == side]
    return matches[0] if len(matches) == 1 else None


def _center_and_size(detection):
    """Centre of the landmark box and its longer side, in normalized frame units."""
    xs = [p.x for p in detection.landmarks]
    ys = [p.y for p in detection.landmarks]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2), max(max(xs) - min(xs), max(ys) - min(ys))


class HandFollower:
    """Keeps the control hand when a small hand's left/right reading flips or loses confidence (docs/far-hand-loss.md, 22).

    The strict rule of selected_detection always comes first and is the only way to start following. Afterwards a single
    detection in about the same place and of about the same size counts as the same hand, whatever its reading says.
    The limits keep the other hand out: the gap since the last accepted frame, and the time since a strict reading."""

    def __init__(self, max_gap=.5, max_unconfirmed=3.0, reach=1.5, min_reach=.05, max_size_change=2.0):
        self.max_gap = max_gap
        self.max_unconfirmed = max_unconfirmed
        self.reach = reach
        self.min_reach = min_reach
        self.max_size_change = max_size_change
        self.reset()

    def reset(self):
        self.followed = False  # the last select() accepted a hand only through this class
        self._side = ""
        self._center = None
        self._size = 0.0
        self._seen_at = None
        self._confirmed_at = None

    def _remember(self, center, size, now):
        self._center, self._size, self._seen_at = center, size, now

    def select(self, detections, side, mirrored, now):
        if side != self._side:
            self.reset()
            self._side = side
        self.followed = False
        chosen = selected_detection(detections, side, mirrored)
        if chosen is not None:
            self._remember(*_center_and_size(chosen), now)
            self._confirmed_at = now
            return chosen
        if not side or len(detections) != 1 or self._center is None:
            return None
        if now - self._seen_at > self.max_gap or now - self._confirmed_at > self.max_unconfirmed:
            return None
        center, size = _center_and_size(detections[0])
        if size <= 0 or not 1 / self.max_size_change <= size / self._size <= self.max_size_change:
            return None
        limit = max(self.reach * self._size, self.min_reach)
        if abs(center[0] - self._center[0]) > limit or abs(center[1] - self._center[1]) > limit:
            return None
        self._remember(center, size, now)
        self.followed = True
        hand = copy.copy(detections[0])
        hand.handedness = {"Left": "Right", "Right": "Left"}[side] if mirrored else side
        return hand


class HandSelection:
    hold_seconds = 5.0

    def seconds_remaining(self, now):
        return self.hold_seconds if self.since is None else max(0.0, self.hold_seconds - (now - self.since))

    def reset(self):
        self.side = ""
        self.since = None
        self.last_at = None
        self.frames = 0

    def __init__(self):
        self.reset()

    def observe(self, detections, mirrored, now):
        side = physical_side(detections[0], mirrored) if len(detections) == 1 else ""
        if not side:
            self.reset()
            return ""
        if side != self.side or self.last_at is None or now - self.last_at > .25:
            self.reset()
            self.side = side
            self.since = now
        if self.last_at is None or now > self.last_at:
            self.last_at = now
            self.frames += 1
        return side if self.frames >= 5 and self.seconds_remaining(now) == 0 else ""
