"""[손 모양 보여주기] for "이 손모양이면 저렇게 해줘" (src/llm_binding.py).

After a COUNTDOWN the hand is looked at for HOLD seconds; each frame's posture comes from the composite detector's
classifier (CompositeGestureDetector._detect_base_posture: pinches, thumbs-up, V, rock, open hand, fist). The most
frequent posture is "this hand shape". The AI never sees the hand: only the posture's name and gesture key.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Optional

# postures that already are gestures in the catalogue (src/gesture_catalog.py)
POSTURE_TO_GESTURE = {p: p for p in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle",
                                     "v_sign", "rock_sign", "thumbs_up")}
# postures the hand takes all the time: a command on "hold it" would fire in normal use
EVERYDAY_POSTURES = {"open_hand", "fist", "thumb_tucked"}


class PoseProbe:
    COUNTDOWN = 3.0      # s to get the hand ready after pressing the button
    HOLD = 2.0           # s looked at
    MIN_FRAMES = 10      # hand frames needed (2 s at 30 fps = 60)
    MIN_SHARE = .55      # the winning posture's share of the frames that got a shape
    MIN_KNOWN = .3       # ... and at least this share of the hand frames got one
    MOVE = 1.0           # palm travel (hand sizes, wrist -> middle knuckle) that makes it a movement, not a held shape
    MOVE_SHARE = .35     # a moving hand changes shape on the way: the winning shape needs this share only
    STRAIGHT = .6        # net travel / path length at or above this: a movement in one direction; under it: a drawn path

    def __init__(self) -> None:
        self.started: Optional[float] = None
        self.counts: Counter = Counter()
        self.frames = 0
        self.result: Optional[Dict[str, Any]] = None

    @property
    def active(self) -> bool:
        return self.started is not None

    NEAR_SHAPE = .5      # a drawn path this close to a shape is told as that shape (approx when under the real limit)
    ROTATE = 60.0        # degrees the hand (or a thumbs-up's thumb) turns, with the palm staying put: a rotation
    ROTATE_STAY = 1.5    # hand sizes the palm may travel during a rotation

    def start(self, now: float) -> None:
        self.started, self.counts, self.frames, self.result = now, Counter(), 0, None
        self._raw: list = []         # palm (x, y) in image units
        self._sizes: list = []       # hand size per frame, same units
        self._angles: list = []      # (hand direction, thumb direction) in degrees, y up
        self._log: list = []         # what is saved to logs/pose_probe (joints only)
        self.guesses: Counter = Counter()   # the nearest shape of frames with no clear shape

    @property
    def path(self):
        """The palm path in hand sizes, with ONE hand size for the whole look (the median) and lightly smoothed.
        2026-11-10: dividing by each frame's size blew a turning hand's path up (it looks small edge-on)."""
        if not self._raw:
            return []
        sizes = sorted(self._sizes)
        size = sizes[len(sizes) // 2] or 1.0
        pts = [(x / size, y / size) for x, y in self._raw]
        out = []
        for i in range(len(pts)):
            win = pts[max(0, i - 2):i + 3]
            out.append((sum(p[0] for p in win) / len(win), sum(p[1] for p in win) / len(win)))
        return out

    def rotation(self, thumb: bool = False):
        """("cw" / "ccw" as seen on the screen, degrees) of the largest turn from the start; the thumb's direction for
        a thumbs-up (turning the wrist rolls the thumb), else the hand's (wrist -> middle knuckle)."""
        vals = [a[1] if thumb else a[0] for a in self._angles]
        if len(vals) < 5:
            return None, 0.0
        unwrapped = [vals[0]]
        for v in vals[1:]:
            d = (v - unwrapped[-1] + 180) % 360 - 180
            unwrapped.append(unwrapped[-1] + d)
        smooth = [sorted(unwrapped[max(0, i - 2):i + 3])[len(unwrapped[max(0, i - 2):i + 3]) // 2]
                  for i in range(len(unwrapped))]
        delta = max((v - smooth[0] for v in smooth), key=abs)
        return ("ccw" if delta > 0 else "cw"), abs(delta)

    def cancel(self) -> None:
        self.started, self.result = None, None

    def step(self, now: float, posture: Optional[str], palm=None, size: float = 0.0, angles=None, lm=None) -> None:
        """One frame with a hand (`posture` "" = not recognised; `palm` its centre, `size` the hand size, same units;
        `angles` (hand, thumb) directions in degrees; `lm` the joints, only for the log)."""
        if self.started is None:
            return
        if now - self.started >= self.COUNTDOWN and posture is not None:
            self.frames += 1
            self.counts[posture] += 1
            if palm is not None and size > 0:
                self._raw.append((palm[0], palm[1]))
                self._sizes.append(size)
            if angles is not None:
                self._angles.append(angles)
            if lm is not None:
                self._log.append({"t": round(now - self.started, 3), "posture": posture, "lm": lm})
                if not posture:
                    self.guesses[nearest_posture(lm, self.aspect)] += 1
        self.finish_if_due(now)

    aspect = 720 / 1280      # frame height / width (set by the runtime)
    log_dir = None           # set by the runtime: every look is saved there (joints only) to see why it failed

    def _save(self) -> None:
        if not self.log_dir or not self._log:
            return
        try:
            import json
            import time
            from pathlib import Path
            d = Path(self.log_dir)
            d.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + ".json"
            (d / name).write_text(json.dumps({"result": self.result, "frames": self._log}, ensure_ascii=False), encoding="utf-8")
            old = sorted(d.glob("*.json"))[:-50]           # keep the last 50
            for f in old:
                f.unlink()
        except OSError:
            pass

    OUT_LEG = .7             # straightness of the way from the start to the farthest point that makes it a stroke

    def _out_leg_straightness(self) -> float:
        import math
        path = self.path
        if len(path) < 5:
            return 0.0
        far = max(range(len(path)), key=lambda i: math.dist(path[i], path[0]))
        leg = path[:far + 1]
        length = sum(math.dist(a, b) for a, b in zip(leg, leg[1:]))
        return math.dist(leg[0], leg[-1]) / length if length > 0 else 0.0

    def _straightness(self) -> float:
        import math
        length = sum(math.dist(a, b) for a, b in zip(self.path, self.path[1:]))
        _, travel = self.movement()
        return travel / length if length > 0 else 1.0

    def movement(self):
        """(direction, travel in hand sizes) of the palm's largest excursion from where it started; direction is one of
        src/action_bindings.DIRECTION_NAMES_KO (screen terms: right = the user's right in the mirrored picture)."""
        import math
        if len(self.path) < 5:
            return None, 0.0
        x0, y0 = self.path[0]
        far = max(self.path, key=lambda p: math.dist(p, (x0, y0)))
        dx, dy = far[0] - x0, far[1] - y0
        travel = math.hypot(dx, dy)
        angle = math.degrees(math.atan2(-dy, dx)) % 360          # 0 = right, 90 = up (image y grows down)
        names = ["right", "diag_up_right", "up", "diag_up_left", "left", "diag_down_left", "down", "diag_down_right"]
        return names[int((angle + 22.5) // 45) % 8], travel

    def _finish(self, now: float) -> None:
        """2026-11-10 rule from the user: a shape or movement that is hard to put in words is not "unrecognised" - it
        is told as the nearest one the app knows (marked "approx"). Only a hand that was not seen fails."""
        if self.started is None or now - self.started < self.COUNTDOWN + self.HOLD:
            return
        self.started = None
        if self.frames < self.MIN_FRAMES:
            self.result = {"state": "failed", "message": "손이 잘 보이지 않았어요. 다시 해 주세요."}
            return
        from src.action_bindings import DIRECTION_NAMES_KO, POSTURE_NAMES_KO
        known = Counter({k: v for k, v in self.counts.items() if k})
        combined = known + self.guesses                  # frames with no clear shape count as their nearest shape
        if combined:
            posture, n = combined.most_common(1)[0]
            share = n / sum(combined.values())
        else:
            posture, share = "open_hand", 0.0
        approx = share < self.MIN_SHARE or self.guesses.get(posture, 0) > known.get(posture, 0)
        name = POSTURE_NAMES_KO.get(posture, posture)
        base = {"state": "done", "posture": posture, "name": name, "share": round(share, 2), "approx": approx,
                "everyday": False, "gesture_key": ""}
        direction, travel = self.movement()
        out_leg = self._out_leg_straightness()
        # a turn first, whatever the shapes on the way (a turning thumbs-up looks like a fist from the side: 17:32 log)
        thumb = self.counts.get("thumbs_up", 0) >= .2 * self.frames
        turn_dir, turn = self.rotation(thumb=thumb)
        if turn >= self.ROTATE and travel < self.ROTATE_STAY:
            p = "thumbs_up" if thumb else posture
            self.result = {**base, "kind": "rotate", "posture": p, "name": POSTURE_NAMES_KO.get(p, p),
                           "rotate": "rotate_" + turn_dir, "degrees": int(round(turn)),
                           "rotate_name": ("시계 방향" if turn_dir == "cw" else "반시계 방향") + f"으로 {int(round(turn))}° 돌리기"}
            return
        if travel >= self.MOVE:
            # a slash or a chop goes out and comes back: the out-going leg is straight (2026-11-10 logs 173143/173155:
            # "손날 긋기" read as a curve because the whole path, with the way back, was not straight)
            if out_leg >= self.OUT_LEG:
                self.result = {**base, "kind": "motion", "direction": direction,
                               "direction_name": DIRECTION_NAMES_KO.get(direction, direction), "travel": round(travel, 1)}
                return
            if self._straightness() < self.STRAIGHT:
                from src.draw_mode import ShapeStore, judge
                shape = judge(self.path, ShapeStore())
                if shape.get("key") and shape.get("score", 0) >= self.NEAR_SHAPE:
                    self.result = {**base, "kind": "drawn", "shape_key": shape["key"], "shape_name": shape["name"],
                                   "score": shape["score"], "gesture_key": shape["key"],
                                   "approx": approx or not shape.get("ok")}
                    return
            self.result = {**base, "kind": "motion", "direction": direction,
                           "direction_name": DIRECTION_NAMES_KO.get(direction, direction), "travel": round(travel, 1),
                           "approx": approx or self._straightness() < self.STRAIGHT}
            return
        self.result = {**base, "gesture_key": POSTURE_TO_GESTURE.get(posture, ""),
                       "everyday": posture in EVERYDAY_POSTURES}

    def finish_if_due(self, now: float) -> None:      # (wraps the look's end: the log is saved with every result)
        was = self.started
        self._finish(now)
        if was is not None and self.started is None:
            self._save()

    def view(self, now: float) -> Optional[Dict[str, Any]]:
        if self.started is not None:
            t = now - self.started
            if t < self.COUNTDOWN:
                return {"state": "countdown", "seconds": int(self.COUNTDOWN - t) + 1}
            return {"state": "holding", "progress": round(min(1.0, (t - self.COUNTDOWN) / self.HOLD), 2)}
        return self.result


PINCH_CLOSE = .18        # thumb tip -> finger tip / hand size (src/virtual_hand.ShapePinch.CLOSE)
FINGER_OUT = .8          # the finger tip this many hand sizes from the wrist (a fist also brings the thumb onto the fingers)
_TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}


def shape_pinched(landmarks, aspect: float = 1.0) -> set:
    """Fingers touching the thumb, from the hand's shape alone (the rule engine's pinches are off in the virtual hand
    mode). `aspect` = height / width of the frame, so x and y are in the same units."""
    import math
    if landmarks is None or len(landmarks) < 21:
        return set()
    pt = [(p.x, p.y * aspect) for p in landmarks]
    size = math.dist(pt[0], pt[9])
    if size < 1e-6:
        return set()
    return {f for f, tip in _TIPS.items()
            if math.dist(pt[4], pt[tip]) / size < PINCH_CLOSE and math.dist(pt[0], pt[tip]) / size > FINGER_OUT}


V_GAP = .5               # index and middle this much straighter than ring and little finger


def is_v(ratios) -> bool:
    """Index and middle straight, ring and little finger clearly less so. Relative, not "folded under 1.25": upside
    down the folded ring finger measures 1.38 (2026-11-10 log; index 2.6, middle 3.0), which the fixed limit refused."""
    i, m, r, p = ratios
    return i >= 1.55 and m >= 1.55 and max(r, p) <= 1.6 and min(i, m) - max(r, p) >= V_GAP


def fingers_down(landmarks, tips=(8, 12)) -> bool:
    """The (index and middle) fingertips point down: below the wrist by a third of the hand size (image y grows down).
    An upside-down V is its own sign (2026-11-10 report): the on/off V and the composite postures tell them apart."""
    import math
    w = landmarks[0]
    size = math.hypot(landmarks[9].x - w.x, landmarks[9].y - w.y) or 1e-6
    return sum(landmarks[t].y for t in tips) / len(tips) - w.y > size / 3


def _finger_ratios(landmarks):
    """Tip -> wrist over knuckle -> wrist for index, middle, ring, pinky (about 1.9 straight, under 1.1 curled)."""
    import math
    w = landmarks[0]
    out = []
    for mcp, tip in ((5, 8), (9, 12), (13, 16), (17, 20)):
        dm = math.hypot(landmarks[mcp].x - w.x, landmarks[mcp].y - w.y)
        dt = math.hypot(landmarks[tip].x - w.x, landmarks[tip].y - w.y)
        out.append(dt / max(1e-5, dm))
    return out


PROBE_PINCH = .35        # thumb tip -> finger tip / hand size for "touching" (recordings: real pinches p50 .17-.26)
PROBE_TRIPLE = .25       # thumb + index + middle: both tips this near ...
PROBE_TRIPLE_EVEN = 1.3  # ... and about equally near (else it is the nearer one's pinch)
PROBE_CURLED_PINCH = .25  # all fingers curled: thumb on the index tip this near = thumb + index, not a fist
PROBE_CLEAR = 1.4        # ... and the next closest finger at least this much farther (a loose hand has several near)


def hand_posture(landmarks, aspect: float = 1.0) -> str:
    """The posture names of the composite detector (src/action_bindings.POSTURE_NAMES_KO), from the landmarks alone.

    Order matters (2026-11-10, measured on the recordings): a V sign is held with the thumb on the ring finger (.29), so
    the finger pattern (V, rock) comes before the pinches; thumbs-up before fist. Pinch = the fingertip nearest the
    thumb, near enough and clearly nearer than the next one (no "finger stretched out" rule: a ring or pinky pinch
    curls the finger)."""
    import math
    if landmarks is None or len(landmarks) < 21:
        return ""
    r = _finger_ratios(landmarks)
    straight = [v >= 1.55 for v in r]
    curled = [v <= 1.25 for v in r]
    if is_v(r):
        return "v_sign_down" if fingers_down(landmarks) else "v_sign"
    if straight[0] and straight[3] and curled[1] and curled[2]:
        return "rock_sign"
    try:
        from src.motion_mode import custom_posture_ok, hand_features
        if custom_posture_ok("thumbs_up", hand_features(landmarks)):
            return "thumbs_up"
    except Exception:
        pass
    pt = [(p.x, p.y * aspect) for p in landmarks]
    size = math.dist(pt[0], pt[9])
    if size < 1e-6:
        return ""
    near = sorted((math.dist(pt[4], pt[tip]) / size, f) for f, tip in _TIPS.items())
    if (near[1][0] < PROBE_TRIPLE and near[1][0] < near[0][0] * PROBE_TRIPLE_EVEN
            and {near[0][1], near[1][1]} == {"index", "middle"}):
        return "thumb_index_middle"
    if near[0][0] < PROBE_PINCH and near[1][0] >= near[0][0] * PROBE_CLEAR and not all(curled):
        return "thumb_" + near[0][1]
    if all(curled):
        # a thumb + index pinch with the other fingers curled (the scroll pinch) is not a fist: the thumb is on the index
        # tip, clearly nearer it than the middle tip (recordings: .16 and .36 at the median; no fist was recorded)
        ti = math.dist(pt[4], pt[8]) / size
        tm = math.dist(pt[4], pt[12]) / size
        if ti < PROBE_CURLED_PINCH and ti < tm * .6:
            return "thumb_index"
        return fist_or_bent(landmarks, aspect)
    if all(straight):
        if palm_sideways(landmarks, aspect) >= KNIFE:
            return "knife_hand"           # the hand's edge to the camera (손날), thumb anywhere
        thumb_in = math.dist(pt[4], pt[9]) / size
        return "thumb_tucked" if thumb_in < .45 else "open_hand"
    return ""


FIST_3D = 1.2            # a fist: every fingertip curls back (3D tip -> wrist / knuckle -> wrist, logs .6-1.0)
BENT_3D = 1.3            # fingers bent forward at the knuckles, pointing at the camera (logs 1.3-1.6): short in 2D only


def finger_ratios_3d(landmarks, aspect: float = 1.0):
    """Like _finger_ratios in 3D (MediaPipe z is on x's scale; y scaled by height / width)."""
    import math

    def p(i):
        q = landmarks[i]
        return (q.x, q.y * aspect, getattr(q, "z", 0.0) or 0.0)
    w = p(0)
    return [math.dist(p(t), w) / max(1e-6, math.dist(p(m), w)) for m, t in ((5, 8), (9, 12), (13, 16), (17, 20))]


def fist_or_bent(landmarks, aspect: float = 1.0) -> str:
    """All fingers look short in the picture: a fist (curled back), or fingers bent forward at the knuckles (the
    'ㄱ' hand, 2026-11-10 report "이건 주먹쥐기가 아닌데"), or "" in between."""
    r = finger_ratios_3d(landmarks, aspect)
    if max(r) <= FIST_3D:
        return "fist"
    if r[0] >= BENT_3D and r[1] >= BENT_3D:
        return "fingers_bent"
    return ""


# the shapes a hand nearly always resembles, by how straight each finger is in 3D (index, middle, ring, little)
NEAREST = {"fist": (1.0, 1.0, 1.0, 1.0), "fingers_bent": (1.45, 1.45, 1.35, 1.3), "open_hand": (1.95, 2.0, 1.95, 1.8),
           "v_sign": (1.9, 2.0, 1.05, 1.0), "rock_sign": (1.9, 1.05, 1.0, 1.8)}


def nearest_posture(lm, aspect: float = 720 / 1280) -> str:
    """The known shape nearest to a hand that matched none (joints as [x, y, z] lists or objects)."""
    from types import SimpleNamespace
    pts = [p if hasattr(p, "x") else SimpleNamespace(x=p[0], y=p[1], z=p[2] if len(p) > 2 else 0.0) for p in lm]
    r = finger_ratios_3d(pts, aspect)
    return min(NEAREST, key=lambda k: sum(abs(a - b) for a, b in zip(r, NEAREST[k])))


KNIFE = .6               # palm facing sideways (3D): the knife hand. Logs: 손날 .70-.72, a hand facing the camera .15;
                         # 15% of the recorded open hands are turned this far


def palm_sideways(landmarks, aspect: float = 1.0) -> float:
    """0 = the palm faces the camera (or up / down), 1 = it faces left or right: the edge of the hand toward the camera.
    From the palm plane's normal in 3D (wrist, index and little knuckles)."""
    import math

    def v(i):
        return (landmarks[i].x - landmarks[0].x, (landmarks[i].y - landmarks[0].y) * aspect,
                (getattr(landmarks[i], "z", 0.0) or 0.0) - (getattr(landmarks[0], "z", 0.0) or 0.0))
    a, b = v(5), v(17)
    n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    m = math.sqrt(sum(c * c for c in n))
    return abs(n[0]) / m if m > 0 else 0.0
