"""Spatial hand association, conservative index checks and time-based smoothing.

The guards detect some discontinuities, not recognition accuracy. A plausible but
wrong model prediction remains possible; raw landmarks are always available.
"""
from collections import deque
from dataclasses import dataclass
import math
from statistics import median

from .filters import OneEuroFilter


@dataclass
class Hand:
    points: list[tuple[float, float, float]]
    label: str = ""
    label_score: float = 0.0  # handedness classification only, never tracking accuracy


def distance(a, b, aspect=1.0):
    return math.hypot(a[0] - b[0], (a[1] - b[1]) * aspect)


def valid(hand):
    return (len(hand.points) == 21 and
            all(len(p) == 3 and all(math.isfinite(v) for v in p) for p in hand.points))


class HandAssociation:
    """Keep one spatially continuous hand even if result order/labels change."""
    def __init__(self):
        self.serial = 0
        self.reset()

    def reset(self):
        self.center = self.size = self.seen = None
        self.pose = None
        self.ambiguous = False
        self.reason = "none"
        self.method = "new"

    def select(self, hands, now, aspect):
        candidates = []
        self.ambiguous = False
        self.reason = "none"
        self.method = "spatial"
        for hand in hands:
            if not valid(hand):
                continue
            center = tuple(sum(hand.points[i][j] for i in (0, 5, 9, 13, 17))/5 for j in (0, 1))
            size = max(distance(hand.points[5], hand.points[17], aspect),
                       distance(hand.points[0], hand.points[9], aspect))
            if size < .012 or not all(-.2 <= v <= 1.2 for v in center):
                continue
            candidates.append((hand, center, size))
        if not candidates:
            self.reason = "invalid_landmarks" if hands else "model_missing"
            return None
        continuous = self.seen is not None and now - self.seen < .3
        if continuous:
            cost = lambda c: distance(c[1], self.center, aspect) + .04 * abs(math.log(c[2]/self.size))
            candidates.sort(key=cost)
            selected = candidates[0]
            # Crossing hands cannot be assigned reliably by 2D location alone.
            if len(candidates) > 1 and abs(cost(candidates[0])-cost(candidates[1])) < .018:
                # Palm centers alone can tie for two incompatible predictions
                # over the same physical hand. Use recent pose continuity only
                # when it provides a clear margin; actual crossing stays ambiguous.
                def shape_error(candidate):
                    h,center,size=candidate
                    total=0.0
                    for i,(p,old) in enumerate(zip(h.points,self.pose)):
                        a=((p[0]-center[0])/size,(p[1]-center[1])*aspect/size)
                        b=((old[0]-self.center[0])/self.size,(old[1]-self.center[1])*aspect/self.size)
                        total+=math.dist(a,b)**2*(2 if i in (0,5,9,13,17) else 1)
                    return math.sqrt(total/26)
                ranked=sorted((shape_error(c),j,c) for j,c in enumerate(candidates[:2])) if self.pose else []
                if ranked and ranked[0][0]<.45 and ranked[1][0]-ranked[0][0]>.18:
                    selected=ranked[0][2]
                    self.method="pose_continuity"
                else:
                    self.ambiguous = True
                    self.reason = "identity_ambiguous"
                    return None
            if distance(selected[1], self.center, aspect) > max(.15, self.size * 1.8):
                self.reason = "position_discontinuity"
                return None
        else:
            selected = max(candidates, key=lambda c: c[2])
            self.serial += 1
            self.method="new"
        hand, self.center, self.size = selected
        self.pose=list(hand.points)
        self.seen = now
        return hand


class IndexGuard:
    """Reject isolated tip swaps; reacquire sustained geometry without interpolation."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = None
        self.lengths = deque(maxlen=9)
        self.suspect_since = None
        self.candidate = None

    def update(self, points, now, aspect):
        p = [(x, y*aspect, z*.5) for x, y, z in points]
        bone = math.dist(p[8], p[7])
        suspect = False
        if self.previous is not None:
            old = self.previous
            typical = median(self.lengths) if self.lengths else bone
            swap = (math.dist(p[8], old[12]) < .018 and math.dist(p[12], old[8]) < .018
                    and math.dist(old[8], old[12]) > .03 and math.dist(p[9], old[9]) < .02)
            broken = bone > max(.025, typical*2.5) or bone < typical*.2
            suspect = swap or (broken and math.dist(p[8], old[8]) > .025
                               and math.dist(p[7], old[7]) < .018)
        if suspect:
            if self.candidate is None or math.dist(self.candidate, p[8]) > .035:
                self.suspect_since = now
            self.candidate = p[8]
            if now - self.suspect_since < .12:
                return False, False
            self.lengths.clear()
            self.previous = p
            self.lengths.append(bone)
            self.suspect_since = self.candidate = None
            return True, True
        self.previous = p
        self.lengths.append(bone)
        self.suspect_since = self.candidate = None
        return True, False


class TrackingCore:
    def __init__(self, smoothing=.35):
        self.association = HandAssociation()
        self.smoothing = smoothing
        self.frames = self.present_frames = self.accepted_frames = self.rejected_frames = 0
        self.loss_events = self.reacquisitions = 0
        self.ever_seen = False
        self.reset()

    def reset(self):
        self.association.reset()
        self.guard = IndexGuard()
        self.filters = [[OneEuroFilter() for _ in range(3)] for _ in range(21)]
        self.previous_at = None
        self.previous_track = None
        self.was_visible = False
        self.index_was_valid = False

    def _clear_filters(self):
        for point in self.filters:
            for f in point:
                f.reset()
        self.guard.reset()

    def update(self, hands, now, width, height):
        self.frames += 1
        aspect = height/width
        hand = self.association.select(hands, now, aspect)
        base = dict(raw=None, filtered=None, index=None, raw_index=None, state="missing",
                    message="손을 카메라 안에 보여주세요", track_id=None, palm_px=0,
                    edge=False, tiny=False, hand_count=len(hands), reset=False, filter_offset_px=0,
                    loss_reason=self.association.reason,hand_association=self.association.method)
        if hand is None:
            if self.was_visible:
                self.loss_events += 1
            self.was_visible = False
            self.previous_at = None
            self._clear_filters()
            self.index_was_valid = False
            if self.association.ambiguous:
                base.update(state="ambiguous", message="두 손이 겹칩니다 · 한 손을 먼저 보여주세요")
            elif hands:
                base.update(message="손 후보는 있으나 이전 손과 연결을 확인 중입니다")
            return base
        self.present_frames += 1
        new_track = self.previous_track != self.association.serial
        gap = self.previous_at is None or now-self.previous_at > .2
        if new_track or gap:
            self._clear_filters()
            if self.ever_seen:
                self.reacquisitions += 1
            self.ever_seen = True
        self.previous_track = self.association.serial
        self.previous_at = now
        self.was_visible = True
        p = hand.points
        base.update(raw=p, raw_index=p[8][:2], track_id=self.association.serial,
                    palm_px=round(self.association.size*width), reset=new_track or gap)
        inside = 0 <= p[8][0] <= 1 and 0 <= p[8][1] <= 1
        if inside:
            accepted, reset = self.guard.update(p, now, aspect)
        else:
            accepted, reset = False, False
            self.guard.reset()
        if inside and not accepted:
            self.rejected_frames += 1
        if reset:
            for point in self.filters:
                for f in point:
                    f.reset()
            base["reset"] = True
        if accepted and not self.index_was_valid:
            # A cropped/suspect fingertip returns at its current position; do not
            # interpolate from off-screen or rejected geometry. Other joints keep continuity.
            for f in self.filters[8]:
                f.reset()
        self.index_was_valid = accepted
        amount = max(0.0, min(1.0, self.smoothing))
        filtered = []
        for index, point in enumerate(p):
            out = []
            for axis, value in enumerate(point):
                f = self.filters[index][axis]
                f.min_cutoff, f.beta, f.d_cutoff = 6.0-4.8*amount, 60.0-30.0*amount, 4.0
                # Same physical units for x and y; no cursor sensitivity/acceleration.
                physical = value*aspect if axis == 1 else value
                smooth = f(physical, now)
                out.append(value if amount == 0 else smooth/aspect if axis == 1 else smooth)
            filtered.append(tuple(out))
        if accepted:
            self.accepted_frames += 1
        edge = any(not (.02 <= p[i][0] <= .98 and .02 <= p[i][1] <= .98) for i in (0,4,8,12,16,20))
        tiny = base["palm_px"] < 70
        base.update(filtered=filtered, index=filtered[8][:2] if accepted else None, state="tracked",
                    message="손 추적 중", edge=edge, tiny=tiny,
                    filter_offset_px=round(distance(filtered[8], p[8], aspect)*width, 1))
        if edge:
            base["message"] = "손 일부가 화면 끝에 있습니다"
        elif tiny:
            base["message"] = "손이 작게 보입니다 · 조금 가까이 보여주세요"
        if not inside:
            base.update(state="cropped", message="검지 끝은 화면 밖 · 보이는 관절은 계속 추적합니다",
                        hidden_joints=[8], filter_offset_px=0,loss_reason="index_cropped")
        elif not accepted:
            base.update(state="suspect", message="검지 위치 확인 중 · 다른 관절은 계속 추적합니다",
                        hidden_joints=[8,12], filter_offset_px=0,loss_reason="index_suspect")
        return base

    def counters(self):
        return dict(frames=self.frames, detected_frames=self.present_frames,
                    accepted_frames=self.accepted_frames, rejected_frames=self.rejected_frames,
                    loss_events=self.loss_events, reacquisitions=self.reacquisitions)
