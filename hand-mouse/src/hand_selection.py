"""Select one physical hand from camera detections."""


def physical_side(detection, mirrored):
    side = detection.handedness
    if side not in ("Left", "Right") or not detection.score >= .75:
        return ""
    return {"Left": "Right", "Right": "Left"}[side] if mirrored else side


def selected_detection(detections, side, mirrored):
    matches = [hand for hand in detections if side and physical_side(hand, mirrored) == side]
    return matches[0] if len(matches) == 1 else None


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
