"""[손 모양 보여주기] for "이 손모양이면 저렇게 해줘" (src/llm_binding.py).

After a COUNTDOWN the hand is looked at for HOLD seconds; each frame's posture comes from the composite detector's
classifier (CompositeGestureDetector._detect_base_posture: pinches, thumbs-up, V, rock, open hand, fist). The most
frequent posture is "this hand shape". The AI never sees the hand: only the posture's name and gesture key.

2026-11-11: the result also carries a picture of the user's own hand (`shot`, a small crop with the joints drawn, from
a frame told as the result's shape) so the screen can show WHAT was looked at next to what it was told as. The
picture stays in memory: it is not saved to logs/pose_probe and not sent to the AI.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Optional

# postures that already are gestures in the catalogue (src/gesture_catalog.py)
POSTURE_TO_GESTURE = {p: p for p in ("thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky", "thumb_index_middle",
                                     "v_sign", "rock_sign", "thumbs_up")}
# postures the hand takes all the time: a command on "hold it" would fire in normal use
EVERYDAY_POSTURES = {"open_hand", "open_hand_back", "open_hand_down", "open_hand_up", "fist", "thumb_tucked"}
UNKNOWN_NAME = "목록에 없는 손 모양"


class PoseProbe:
    COUNTDOWN = 3.0      # s to get the hand ready after pressing the button
    HOLD = 2.0           # s looked at
    MIN_FRAMES = 10      # hand frames needed (2 s at 30 fps = 60)
    MIN_SHARE = .5       # a held shape: the winning shape's share of ALL the hand frames. Under it the look is "not
                         # in the list" (2026-11-11 rule from the user: no forcing it into a listed shape)
    MOVE = 1.0           # palm travel (hand sizes, wrist -> middle knuckle) that makes it a movement, not a held shape
    MOVE_SHARE = .35     # a moving hand changes shape on the way: the winning shape needs this share only
    CLIP_FRAMES = 90     # hand pictures kept for the replay of the look (2 s at 30 fps = 60)
    STRAIGHT = .6        # net travel / path length at or above this: a movement in one direction; under it: a drawn path

    def __init__(self) -> None:
        self.started: Optional[float] = None
        self.counts: Counter = Counter()
        self.frames = 0
        self.result: Optional[Dict[str, Any]] = None
        self.shot = None             # the hand picture of the last result (BGR array, see hand_shot)
        self.shot_seq = 0            # +1 per new picture, so the screen knows to reload it
        self._shots: Dict[str, list] = {}
        self.clip: list = []         # the look's hand pictures in order: the screen replays them like a GIF (memory only)
        self._clip: list = []

    @property
    def active(self) -> bool:
        return self.started is not None

    ROTATE = 60.0        # degrees the hand (or a thumbs-up's thumb) turns, with the palm staying put: a rotation
    ROTATE_STAY = 1.5    # hand sizes the palm may travel during a rotation

    def start(self, now: float) -> None:
        self.started, self.counts, self.frames, self.result = now, Counter(), 0, None
        self._raw: list = []         # palm (x, y) in image units
        self._sizes: list = []       # hand size per frame, same units
        self._angles: list = []      # (hand direction, thumb direction) in degrees, y up
        self._log: list = []         # what is saved to logs/pose_probe (joints only)
        self._shots = {}             # shape ("" = none) -> hand pictures of those frames
        self.shot = None
        self.clip, self._clip = [], []

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
        self.started, self.result, self.shot, self._shots = None, None, None, {}
        self.clip, self._clip = [], []

    def step(self, now: float, posture: Optional[str], palm=None, size: float = 0.0, angles=None, lm=None,
             shot=None) -> None:
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
            if shot is not None:
                self._shots.setdefault(posture, []).append(shot)
                if len(self._clip) < self.CLIP_FRAMES:
                    self._clip.append(shot)
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
        """2026-11-11 rule from the user ("리스트에 없는 동작은 억지로 끼워 맞추지 말고 없다고 하고 신규 동작으로"): a look
        that is no listed shape is told as such (`unknown`, posture ""), and the screen asks for a name for it
        (src/custom_postures.py). It replaces the 2026-11-10 rule (the nearest known shape, marked "approx")."""
        if self.started is None or now - self.started < self.COUNTDOWN + self.HOLD:
            return
        self.started = None
        if self.frames < self.MIN_FRAMES:
            self.result = {"state": "failed", "message": "손이 잘 보이지 않았어요. 다시 해 주세요."}
            return
        from manux.compat.hand_mouse.action_bindings import DIRECTION_NAMES_KO, POSTURE_NAMES_KO
        known = Counter({k: v for k, v in self.counts.items() if k})
        posture, n = known.most_common(1)[0] if known else ("", 0)
        share = n / self.frames
        base = {"state": "done", "posture": posture, "name": POSTURE_NAMES_KO.get(posture, posture),
                "share": round(share, 2), "approx": False, "everyday": False, "gesture_key": ""}
        none = {**base, "posture": "", "name": UNKNOWN_NAME, "unknown": True}
        direction, travel = self.movement()
        out_leg = self._out_leg_straightness()
        # a turn first, whatever the shapes on the way (a turning thumbs-up looks like a fist from the side: 17:32 log)
        thumb = self.counts.get("thumbs_up", 0) >= .2 * self.frames
        turn_dir, turn = self.rotation(thumb=thumb)
        if turn >= self.ROTATE and travel < self.ROTATE_STAY:
            p = "thumbs_up" if thumb else posture
            if not thumb and share < self.MOVE_SHARE:
                self.result = none
                return
            self.result = {**base, "kind": "rotate", "posture": p, "name": POSTURE_NAMES_KO.get(p, p),
                           "rotate": "rotate_" + turn_dir, "degrees": int(round(turn)),
                           "rotate_name": ("시계 방향" if turn_dir == "cw" else "반시계 방향") + f"으로 {int(round(turn))}° 돌리기"}
            return
        if travel >= self.MOVE:
            if share < self.MOVE_SHARE:                  # a movement, but of no listed hand shape
                self.result = none
                return
            # a slash or a chop goes out and comes back: the out-going leg is straight (2026-11-10 logs 173143/173155:
            # "손날 긋기" read as a curve because the whole path, with the way back, was not straight)
            if out_leg >= self.OUT_LEG or self._straightness() >= self.STRAIGHT:
                self.result = {**base, "kind": "motion", "direction": direction,
                               "direction_name": DIRECTION_NAMES_KO.get(direction, direction), "travel": round(travel, 1)}
                return
            from manux.compat.hand_mouse.draw_mode import ShapeStore, judge
            shape = judge(self.path, ShapeStore())
            if shape.get("key") and shape.get("ok"):
                self.result = {**base, "kind": "drawn", "shape_key": shape["key"], "shape_name": shape["name"],
                               "score": shape["score"], "gesture_key": shape["key"]}
                return
            # neither a straight stroke nor a drawing the app knows: not forced into the nearest one
            self.result = {**none, "posture": posture, "name": base["name"], "kind": "unknown_move",
                           "message": "목록에 없는 움직임이에요. 한 방향으로 곧게 움직이거나, 그리기 모드 설정에서 이 그림을 "
                                      "새 모양으로 등록해 주세요."}
            return
        if share < self.MIN_SHARE:
            self.result = none
            return
        self.result = {**base, "gesture_key": POSTURE_TO_GESTURE.get(posture, ""),
                       "everyday": posture in EVERYDAY_POSTURES}

    def finish_if_due(self, now: float) -> None:      # (wraps the look's end: the log is saved with every result)
        was = self.started
        frames, counts = self.frames, dict(self.counts)
        self._finish(now)
        if was is not None and self.started is None:
            if (self.result or {}).get("state") == "done" and frames:
                # every shape the look was read as, with its share of the hand frames: what else answers to this
                # hand (src/gesture_overlap.py, 2026-11-12)
                self.result["seen"] = {k: round(v / frames, 2) for k, v in counts.items() if k and v / frames >= .1}
            self._pick_shot()
            self._save()

    def _pick_shot(self) -> None:
        """The middle one of the pictures told as the result's shape (any shape if none was)."""
        shots = self._shots.get((self.result or {}).get("posture", "")) or max(self._shots.values(), key=len, default=[])
        self._shots = {}
        done = (self.result or {}).get("state") == "done"
        self.shot = shots[len(shots) // 2] if done and shots else None
        self.clip, self._clip = (self._clip if done else []), []
        self.shot_seq += 1           # also with no picture: the screen must drop the previous one

    def view(self, now: float) -> Optional[Dict[str, Any]]:
        if self.started is not None:
            t = now - self.started
            if t < self.COUNTDOWN:
                return {"state": "countdown", "seconds": int(self.COUNTDOWN - t) + 1}
            return {"state": "holding", "progress": round(min(1.0, (t - self.COUNTDOWN) / self.HOLD), 2)}
        return self.result


def relabelled(result: Dict[str, Any], label: str) -> Dict[str, Any]:
    """The result after the user said what the shown shape really is (saved as an example of `label`): from then on
    the look IS that shape - on the screen and for the AI. 2026-11-11 report (log 102251, a thumb stuck out sideways
    told as a fist, then named "옆 따봉"): the result stayed "주먹 쥐기", so a command asked for "이 손모양" would have
    been connected to the fist."""
    from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
    out = {**result, "taught": label}
    if label != result.get("posture") or result.get("unknown"):
        out.update(posture=label, name=POSTURE_NAMES_KO.get(label, label), unknown=False, approx=False,
                   gesture_key=POSTURE_TO_GESTURE.get(label, ""), everyday=label in EVERYDAY_POSTURES)
    return out


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


V_3D = 1.5               # ... and index and middle straight in 3D too (recorded V signs: every one of 3,386 at 1.7+)


def is_v_hand(landmarks, aspect: float = 1.0) -> bool:
    """`is_v` in the picture AND index / middle straight in 3D. 2026-11-11 ("옆따봉을 했는데 거꾸로 V 명령이 나오거나"):
    the picture alone calls a hand that points at the camera a V - its wrist-to-knuckle length shrinks, so every
    finger ratio grows. On the recordings 262 frames that were no V (thumb + middle pinches 145, idle 82, free 33)
    passed `is_v` with index or middle under 1.5 in 3D; no recorded V sign did."""
    if not is_v(_finger_ratios(landmarks)):
        return False
    r = finger_ratios_3d(landmarks, aspect)
    return min(r[0], r[1]) >= V_3D


V_TOGETHER = 1.45        # index and middle tips closer than this many knuckle gaps: held together, not a V


def v_spread(landmarks, aspect: float = 1.0) -> float:
    """How far apart the index and middle fingertips are, in gaps between their knuckles (the rule engine's
    "v_sign_spread"). Held together about 1.0-1.3; a V shown to the app 1.7-2.1, in the V recordings 2.2 at the median."""
    import math

    def d(a, b):
        return math.hypot(landmarks[a].x - landmarks[b].x, (landmarks[a].y - landmarks[b].y) * aspect)
    return d(8, 12) / max(1e-6, d(5, 9))


def v_shape(landmarks, aspect: float = 1.0) -> str:
    """"" (no V hand), "v_sign", "two_fingers" (index and middle straight but held together) or "v_sign_down".

    2026-11-11 request: "손가락이 붙었을 때랑 떨어졌을 때를 구분하게 하고 별도로 다른 동작을 연결". Only the upright hand is
    split. The upside-down V stays one shape whatever the spread: in the person's logged uses of it (146 frames before
    the command) the spread was 1.47 at the median, 53 % under 1.5: pointing down, the fingers are seen end-on and
    look together, so a split there would have stopped a gesture that works.
    A V whose spread is between V_TOGETHER and the rule engine's 1.8 is still a V here (7 % of the recorded V frames)."""
    if not is_v_hand(landmarks, aspect):
        return ""
    if fingers_down(landmarks):
        return "v_sign_down"
    return "two_fingers" if v_spread(landmarks, aspect) < V_TOGETHER else "v_sign"


def fingers_down(landmarks, tips=(8, 12)) -> bool:
    """The (index and middle) fingertips point down: below the wrist by a third of the hand size (image y grows down).
    An upside-down V is its own sign (2026-11-10 report): the on/off V and the composite postures tell them apart."""
    import math

    def xy(i):                       # (recordings keep the joints as [x, y, z] lists: the on/off V judged while a
        p = landmarks[i]             # recording is read for training crashed here, 2026-11-12)
        return (p.x, p.y) if hasattr(p, "x") else (p[0], p[1])
    wx, wy = xy(0)
    mx, my = xy(9)
    size = math.hypot(mx - wx, my - wy) or 1e-6
    return sum(xy(t)[1] for t in tips) / len(tips) - wy > size / 3


THUMB_OUT_STRAIGHT = .9  # thumb base -> tip over the sum of its segments (motion_mode.hand_features: bent .4-.7)
THUMB_OUT_LEN = .65      # thumb tip from its base, in palm lengths (folded .2-.35, stuck out .65-1.2)
_PATTERN_FINGER = {"index_point": "index", "l_sign": "index", "pinky_up": "pinky", "shaka": "pinky"}
THUMB_BELOW = .25        # thumbs-down: the thumb tip this many hand sizes under every other joint
THUMB_DOWN = -.7        # cos of the thumb's angle to "up" on the screen: pointing down


def pattern_posture(landmarks, aspect: float = 1.0) -> str:
    """Shapes told by WHICH fingers are out (2026-11-11 request: "예측 가능한 손 모양을 미리 추가"), the thumb counted too:

        index                 index_point (검지만 펴기)      index + thumb   l_sign (ㄴ자)
        little finger         pinky_up (새끼만 펴기)         little + thumb  shaka (전화 손 모양)
        thumb alone, down     thumbs_down (엄지 아래로)

    Every finger must be clearly straight or clearly folded, in the picture AND in 3D ("" otherwise). Not here on
    purpose: index + middle + ring ("3") is the same hand as the thumb + little-finger pinch (the thumb holds the little
    finger down), and index + little finger is the rock sign whatever the thumb does (it closes a window)."""
    import math
    r2, r3 = _finger_ratios(landmarks), finger_ratios_3d(landmarks, aspect)
    out =[a >= 1.55 and b >= 1.5 for a, b in zip(r2, r3)]
    folded = [a <= 1.25 and b <= 1.3 for a, b in zip(r2, r3)]
    if not all(o or f for o, f in zip(out, folded)):
        return ""
    from manux.compat.hand_mouse.motion_mode import hand_features
    f = hand_features(landmarks)
    thumb = f.get("thumb_straight", 0.0) >= THUMB_OUT_STRAIGHT and f.get("thumb_len", 0.0) >= THUMB_OUT_LEN
    pattern = tuple(out)
    if pattern == (True, False, False, False):
        return "l_sign" if thumb else "index_point"
    if pattern == (False, False, False, True):
        return "shaka" if thumb else "pinky_up"
    if pattern == (False, False, False, False) and thumb and f.get("thumb_up", 0.0) <= THUMB_DOWN:
        # ... and the thumb tip hangs clearly under the rest of the hand: a fist is the cursor's hand shape, and a fist
        # with the thumb lying along it measured "straight and pointing down" on 25 recorded fist frames
        size = math.hypot(landmarks[9].x - landmarks[0].x, (landmarks[9].y - landmarks[0].y) * aspect) or 1e-6
        lowest = max(landmarks[i].y for i in range(21) if i not in (2, 3, 4))
        hand_down = (landmarks[9].y - landmarks[0].y) * aspect / size        # 1 = the fist itself points down
        if (landmarks[4].y - lowest) * aspect >= THUMB_BELOW * size and hand_down < .5:
            return "thumbs_down"
    return ""


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


# An open hand has every finger straight at both of its joints (2026-11-11, user: "손펴기 판정이 너무 후하다. 손가락의 첫째
# 두번째 마디도 다 펴졌을 때를 기준으로"). The tip -> wrist ratio (1.55) let a hand with the fingers bent at the middle
# joints through: of the 9 "손 펴기 유지" commands that evening (logs/composite_fires 20:11-20:14) 4 were held with the
# middle joints bent 25-50 deg and the end joints 11-27 deg; the other 5 had every joint within 13 deg. Over the 983
# frames read as an open hand in that session's logs the two groups part at about 20 deg (straight: 744 frames, middle
# and end joints both <= 20; the rest bent >= 25 at the middle joint). Bend = 180 - the joint's angle, in 3D.
OPEN_BEND = 20.0         # deg, middle (PIP) and end (DIP) joints of every finger, to become an open hand
OPEN_BEND_KEEP = 25.0    # deg, while an open hand is already being held


def finger_bends(landmarks, aspect: float = .5625):
    """(middle joint, end joint) bend in degrees for index, middle, ring, pinky; 0 = straight."""
    import math
    p = [(q.x, q.y * aspect, getattr(q, "z", 0.0) or 0.0) for q in landmarks]

    def bend(a, b, c):
        v1 = [p[a][i] - p[b][i] for i in range(3)]
        v2 = [p[c][i] - p[b][i] for i in range(3)]
        n = math.hypot(*v1) * math.hypot(*v2)
        if n < 1e-12:
            return 0.0
        cos = sum(x * y for x, y in zip(v1, v2)) / n
        return 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos))))

    return [(bend(m, pip, dip), bend(pip, dip, tip)) for m, pip, dip, tip in
            ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))]


def fingers_straight(landmarks, aspect: float = .5625, keep: bool = False) -> bool:
    """Every finger straight at both joints (OPEN_BEND, or OPEN_BEND_KEEP for a hand already held open)."""
    limit = OPEN_BEND_KEEP if keep else OPEN_BEND
    try:
        return all(a <= limit and b <= limit for a, b in finger_bends(landmarks, aspect))
    except Exception:
        return True


PROBE_PINCH = .35        # thumb tip -> finger tip / hand size for "touching" (recordings: real pinches p50 .17-.26)
PROBE_TRIPLE = .25       # thumb + index + middle: both tips this near ...
PROBE_TRIPLE_EVEN = 1.3  # ... and about equally near (else it is the nearer one's pinch)
PROBE_CURLED_PINCH = .25  # all fingers curled: thumb on the index tip this near = thumb + index, not a fist
PROBE_CLEAR = 1.4        # ... and the next closest finger at least this much farther (a loose hand has several near)


def hand_posture(landmarks, aspect: float = 1.0, taught: bool = True) -> str:
    """`_rule_posture`, replaced by a shape the user taught when that model is sure (src/posture_teach.py;
    `taught=False`: the rules alone). An open hand is named by where its palm faces (`faced`)."""
    rule = _rule_posture(landmarks, aspect)
    if not taught or landmarks is None or len(landmarks) < 21:
        return rule
    from manux.compat.hand_mouse.posture_teach import ASPECT, override
    return faced(override(landmarks, rule, aspect if aspect != 1.0 else ASPECT), landmarks, aspect)


# ---------------------------------------------------------------------------- palm or back of the hand to the camera
# 2026-11-11 request ("오른손·왼손 정보와 엄지 위치로 손등/손바닥을 구분 … 별개의 명령으로"). Two signs that do not depend
# on each other:
#  - the turn from the index knuckle to the little knuckle around the wrist, in the picture: one way for a palm, the other
#    for the back, and which is which follows from the hand's label (MediaPipe "Left" / "Right" of the picture);
#  - the thumb: it sits on the palm side, so its tip is nearer the camera than the index knuckle with the palm to the
#    camera and farther with the back to it.
# Measured on the 42 recordings (label "Left" = the right hand in the mirrored picture; 10,201 open-hand frames, every
# 5th): 62 % turn one way (> +.3), 31 % the other (< -.3) - hands pointing down 95 % the other way, pointing up 12 %, as a
# hand let hang or held across the body shows its back. With the first turn the thumb is nearer the camera in 100 % of
# the frames (median -.30 hand sizes), with the other it is farther in 76 % (median +.16). No recording was LABELLED palm
# or back, so this is two signs agreeing, not an accuracy.
BACK_TURN = .3           # sine of the knuckle turn, against the palm's way, from which the hand may be showing its back
BACK_THUMB = -.15        # ... and the thumb tip is not nearer the camera than this (hand sizes; palms: below -.21 in 90 %)
_HAND = __import__("threading").local()     # the label of the hand being judged, per thread (the runtime's loop sets it)


def set_hand_label(label: str, arm: str = "") -> None:
    """Which hand the landmarks judged on this thread belong to: MediaPipe's "Left" / "Right" as the tracker gives it
    (the picture's, before the mirror is undone), "" = unknown: no hand is then told as showing its back.
    `arm`: the arm the hand sits on (`arm_label`), "" = not seen."""
    _HAND.label = label if label in ("Left", "Right") else ""
    _HAND.arm = arm if arm in ("Left", "Right") else ""


def hand_label() -> str:
    return getattr(_HAND, "label", "")


# Same day, the user's follow-up ("어깨·팔꿈치 정보도 있고, 엄지 좌우 위치 정보도 연계해서 보면 쉽게 구분 가능하잖아"): the
# arm tells which hand it is without the hand model's own left/right reading. On the recordings' open-hand frames that
# have Pose joints (10,060): of the 3,076 with the reversed turn the nearest Pose wrist was the labelled hand's in 3,015
# (2,544 clearly: within one hand size, visible, the other wrist twice as far) and clearly the other arm's in 4. So the
# reversed turn really is the same hand turned round, and when the arm confirms the hand the turn alone decides (the
# thumb's depth, which disagreed in 24 % of those frames, is then not asked). An arm that says the OTHER hand does not
# overrule the label: with the palm's turn that happened clearly in 314 frames (5 %), and would make each a "back".
ARM_NEAR = 1.0           # hand sizes from the hand's wrist to the Pose wrist at most
ARM_CLEAR = 2.0          # ... and the other Pose wrist at least this many times farther
ARM_VISIBLE = .5


def arm_label(pose, landmarks, aspect: float = 1.0) -> str:
    """"Left" / "Right": the arm whose wrist the hand sits on, in the hand label's naming; "" when not clear.
    `pose`: Pose joints 11-16 as [x, y, visibility] (shoulders, elbows, wrists; left then right), or None."""
    import math
    if not pose or len(pose) < 6 or landmarks is None or len(landmarks) < 21:
        return ""
    w = landmarks[0]
    size = math.hypot(landmarks[9].x - w.x, (landmarks[9].y - w.y) * aspect)
    if size <= 1e-6:
        return ""
    d = {side: math.hypot(pose[i][0] - w.x, (pose[i][1] - w.y) * aspect) / size for side, i in (("Left", 4), ("Right", 5))}
    near, far = ("Left", "Right") if d["Left"] <= d["Right"] else ("Right", "Left")
    visible = float(pose[4 if near == "Left" else 5][2]) >= ARM_VISIBLE
    return near if visible and d[near] <= ARM_NEAR and d[far] >= ARM_CLEAR * d[near] else ""


def back_of_hand(landmarks, aspect: float = 1.0, label: Optional[str] = None, arm: Optional[str] = None) -> bool:
    """The back of the hand faces the camera: the knuckles turn against the palm's way for this hand, and either the arm
    confirms which hand it is or the thumb's depth agrees. False when the hand is unknown or edge-on."""
    import math
    label = hand_label() if label is None else label
    arm = getattr(_HAND, "arm", "") if arm is None else arm
    if label not in ("Left", "Right") or landmarks is None or len(landmarks) < 21:
        return False
    w = landmarks[0]
    a = (landmarks[5].x - w.x, (landmarks[5].y - w.y) * aspect)
    b = (landmarks[17].x - w.x, (landmarks[17].y - w.y) * aspect)
    turn = (a[0] * b[1] - a[1] * b[0]) / max(1e-9, math.hypot(*a) * math.hypot(*b))
    if (turn if label == "Left" else -turn) > -BACK_TURN:
        return False                               # the palm's way, or too edge-on to say
    if arm == label:
        return True                                # the arm says it is this hand: the turn is enough
    size =math.hypot(landmarks[9].x - w.x, (landmarks[9].y - w.y) * aspect)
    thumb = ((getattr(landmarks[4], "z", 0.0) or 0.0) - (getattr(landmarks[5], "z", 0.0) or 0.0)) / max(1e-9, size)
    return thumb >= BACK_THUMB


# Where the palm faces (2026-11-12 request: "기존 명령은 손바닥을 카메라로 향할때만 적용되게 하고, 손바닥을 바닥으로 할때나
# 손바닥을 위로 할때 다른 명령을 붙일수 있도록"). The palm's outward normal in 3D (wrist, index and little knuckles; its sign
# from the hand's label): y > 0 = toward the floor, z < 0 = toward the camera.
# Measured on that morning's 29 "손 펴기 유지" commands (logs/composite_fires, last second, label taken as "Left"): the
# 18 palm-to-camera holds had z -.59 .. -.90 and y +.32 .. +.67 (the camera looks at the hand from a little above); the
# 11 palm-to-the-floor holds were all seen from the back (z +.26 .. +.93), y +.41 .. +.92 in 10 and -.03 in one. Over
# the 1,216 open-hand frames of this PC's logs: of the 500 seen from the back 341 have y >= .35; of the 716 seen from the
# palm 35 (5 %) have y <= -.5 and 20 (3 %) y <= -.75. NO hand was recorded palm up, and no frame is labelled: the limits
# are a start.
FACE_DOWN = .35          # seen from the back and the normal this far toward the floor: the palm faces the floor
FACE_DOWN_EDGE = .6      # ... or seen edge-on (neither side clearly): this far toward the floor
FACE_EDGE = -.3          # normal z above this = not clearly the palm to the camera (palms: below -.5 in 97 %)
FACE_UP = -.6            # the normal this far toward the ceiling: the palm faces up
FACINGS = ("open_hand_back", "open_hand_down", "open_hand_up")


def palm_normal(landmarks, aspect: float = 1.0, label: Optional[str] = None):
    """The unit vector out of the palm (x right, y down, z away from the camera), or None when the hand is unknown."""
    import math
    label = hand_label() if label is None else label
    if label not in ("Left", "Right") or landmarks is None or len(landmarks) < 21:
        return None

    def v(i):
        return (landmarks[i].x - landmarks[0].x, (landmarks[i].y - landmarks[0].y) * aspect,
                (getattr(landmarks[i], "z", 0.0) or 0.0) - (getattr(landmarks[0], "z", 0.0) or 0.0))
    a, b = v(5), v(17)
    n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    m = math.sqrt(sum(c * c for c in n))
    if m <= 0:
        return None
    s = -1.0 if label == "Left" else 1.0
    return tuple(s * c / m for c in n)


def faced(posture: str, landmarks, aspect: float = 1.0) -> str:
    """An open hand by where its palm faces: "open_hand" = the camera, "open_hand_down" = the floor, "open_hand_up" =
    the ceiling, "open_hand_back" = away (the back of the hand to the camera). Everything else stays; so does an open
    hand whose side (left / right) is not known."""
    if posture != "open_hand":
        return posture
    n = palm_normal(landmarks, aspect)
    if n is None:
        return posture
    if n[1] <= FACE_UP:
        return "open_hand_up"
    if back_of_hand(landmarks, aspect):
        return "open_hand_down" if n[1] >= FACE_DOWN else "open_hand_back"
    if n[2] > FACE_EDGE and n[1] >= FACE_DOWN_EDGE:
        return "open_hand_down"
    return posture


def unless_bound(posture: Optional[str], bound) -> Optional[str]:
    """손 펴기 (`open_hand`) is the palm to the camera only (2026-11-12; before, the back counted as the open hand while
    nothing was bound to it). The back and the palm-to-the-floor hand are both seen from the back and differ only by a
    tilt, so when a gesture is bound to just one of the two (`bound`: the shapes in use) either reading counts as it."""
    for mine, other in (("open_hand_back", "open_hand_down"), ("open_hand_down", "open_hand_back")):
        if posture == mine and mine not in bound and other in bound:
            return other
    return posture


def _rule_posture(landmarks, aspect: float = 1.0) -> str:
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
    v = v_shape(landmarks, aspect)
    if v:
        return v
    if straight[0] and straight[3] and curled[1] and curled[2]:
        return "rock_sign"
    try:
        from manux.compat.hand_mouse.motion_mode import custom_posture_ok, hand_features
        if custom_posture_ok("thumbs_up", hand_features(landmarks)):
            return "thumbs_up"
    except Exception:
        pass
    pt = [(p.x, p.y * aspect) for p in landmarks]
    size = math.dist(pt[0], pt[9])
    if size < 1e-6:
        return ""
    near = sorted((math.dist(pt[4], pt[tip]) / size, f) for f, tip in _TIPS.items())
    # which fingers are out (pattern_posture). The pointing hand rests the thumb on the folded middle finger (.18-.26),
    # which read as thumb + middle (2026-11-11 log 100157, 57 of 57 frames). Not when the thumb is on the tip of the
    # finger that is out: that is its pinch
    shape = pattern_posture(landmarks, aspect)
    if shape and not (near[0][1] == _PATTERN_FINGER.get(shape) and near[0][0] < PROBE_PINCH):
        return shape
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
        if thumb_in < .45:
            return "thumb_tucked"
        return "open_hand" if fingers_straight(landmarks, aspect) else ""   # joints bent: not an open hand yet
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
           "v_sign": (1.9, 2.0, 1.05, 1.0), "rock_sign": (1.9, 1.05, 1.0, 1.8), "index_point": (2.2, 1.15, .95, 1.0)}


def nearest_posture(lm, aspect: float = 720 / 1280) -> str:
    """The known shape nearest to a hand that matched none (joints as [x, y, z] lists or objects)."""
    from types import SimpleNamespace
    pts = [p if hasattr(p, "x") else SimpleNamespace(x=p[0], y=p[1], z=p[2] if len(p) > 2 else 0.0) for p in lm]
    r = finger_ratios_3d(pts, aspect)
    best = min(NEAREST, key=lambda k: sum(abs(a - b) for a, b in zip(r, NEAREST[k])))
    # the table has no direction: an upside-down V whose folded ring finger measured 1.6-2.0 (2026-11-11 log 091241,
    # 40 of 61 frames) was told as the upright V
    if best == "v_sign" and fingers_down(pts):
        return "v_sign_down"
    return best


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


SHOT_SIZE = 160          # px, the square hand picture of a result



