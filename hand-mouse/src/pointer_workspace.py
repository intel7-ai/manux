"""Vision-space helpers for the index pointer preset."""
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
