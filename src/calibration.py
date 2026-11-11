"""
Personal calibration (2026-11-04): measure how THIS user's pinches look.

What the first real run (logs/calibration-20261104-*.json) taught:
- Hand-size normalisation does not work: contact (~0.02) and open (~0.10) thumb distances did not
  scale with any hand-size proxy (those change with the hand's angle, not only the distance).
- Only the THUMB+INDEX distance has a real "touching / apart" pair. With the middle, ring and pinky
  the user's resting hand keeps the thumb 0.01-0.03 from those tips all the time, so "open
  distance" cannot be measured per finger (and the engine itself tells them apart by "which tip is
  nearest the thumb", not by an absolute distance).

So the wizard now measures two different things:
1. Thumb+index: contact distance, and an open distance that must be a REAL opening (>= OPEN_MIN_INDEX).
   From these it sets `pinch_threshold` and `release_threshold`.
2. Thumb+middle / ring / pinky: how well the intended fingertip is the nearest one while pinching
   ("selectivity": share of contact frames, most common confuser, margin to the runner-up). That is
   reported, never turned into thresholds. A click threshold is only RAISED when the user's contact
   distance is larger than the current threshold allows (the click could not be reached at all).

Pure logic (no camera, Qt or OpenCV): feed (time, 21 landmarks | None) frames, read prompts, progress
and coaching, then `finish()`. `scripts/calibrate.py` drives it with the real camera.

Limits: a few repetitions from one short session are a handful of tries, not an accuracy measurement.
Adjacent frames of one pinch are not independent samples.
"""
from __future__ import annotations

import json
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

THUMB_TIP = 4
FINGER_TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
FINGER_LABELS = {"index": "검지", "middle": "중지", "ring": "약지", "pinky": "새끼"}
OTHER_FINGERS = ("middle", "ring", "pinky")
PALM_POINTS = (0, 5, 9, 13, 17)

# click thresholds of the three side fingers (raised only when the user's contact is out of reach)
ENTER_KEYS = {"index": "pinch_threshold", "middle": "click_threshold",
              "ring": "ring_click_threshold", "pinky": "pinky_click_threshold"}
DEFAULT_ENTER = {"middle": 0.052, "ring": 0.044, "pinky": 0.045}
LIMITS = (0.012, 0.12)          # a suggested distance outside this is not trusted
OPEN_MIN_INDEX = 0.055          # a thumb+index opening below this is "small": the engine's release limit is ~0.045-0.06
MIN_GAP = 0.008                 # contact vs open must differ by more than landmark noise (~0.003 per tip)
GOOD_SEPARATION = 2.0           # open / contact (index)
FAIR_SEPARATION = 1.5
MIN_REPS = 3
PREP_SECONDS = 2.5              # time to read the prompt and get ready before a step records anything
MIN_RUN_SECONDS = 0.15          # a contact shorter than this is a flicker, not a pinch
GOOD_SELECTIVITY = 0.85         # share of contact frames where the target tip is the nearest
FAIR_SELECTIVITY = 0.65


@dataclass(frozen=True)
class Step:
    key: str
    prompt: str
    seconds: float
    finger: Optional[str] = None


STEPS: Tuple[Step, ...] = (
    Step("rest", "편하게 손을 펴서 카메라에 보여주세요 (움직이지 마세요)", 3.0),
    Step("pinch_index", "엄지와 검지를 붙였다가 손가락을 크게 활짝 벌리기를 3번 (붙인 채 1초)", 10.0, "index"),
    Step("pinch_middle", "엄지와 중지를 붙였다 떼기를 3번 (평소 클릭하는 자세 그대로)", 9.0, "middle"),
    Step("pinch_ring", "엄지와 약지를 붙였다 떼기를 3번 (평소 우클릭하는 자세 그대로)", 9.0, "ring"),
    Step("pinch_pinky", "엄지와 새끼를 붙였다 떼기를 3번 (평소 쓰는 자세 그대로)", 9.0, "pinky"),
    Step("fist", "주먹을 쥐어 주세요", 3.0),
    Step("open", "손을 활짝 펴 주세요", 3.0),
)

# Stage 8-1: 1-2 minute full user onboarding flow (open hand -> pinch -> move -> 30s normal movement)
ONBOARDING_STEPS: Tuple[Step, ...] = (
    Step("open_hand", "1단계 [손 펴기]: 편하게 손을 활짝 펴서 카메라 중앙에 보여주세요", 4.0),
    Step("pinch_index", "2단계 [검지 핀치]: 엄지와 검지를 붙였다가 활짝 벌리기를 3번 반복하세요", 10.0, "index"),
    Step("pinch_middle", "3단계 [중지 핀치]: 엄지와 중지를 붙였다 떼기를 3번 반복하세요", 9.0, "middle"),
    Step("pinch_ring", "4단계 [약지 핀치]: 엄지와 약지를 붙였다 떼기를 3번 반복하세요", 9.0, "ring"),
    Step("pinch_pinky", "5단계 [새끼 핀치]: 엄지와 새끼를 붙였다 떼기를 3번 반복하세요", 9.0, "pinky"),
    Step("move_hand", "6단계 [커서 이동]: 반주먹(기본 이동 자세)으로 화면 상하좌우로 손을 움직여 주세요", 6.0),
    Step("neutral_move_30s", "7단계 [30초 평소 이동]: 클릭하지 말고 평소 마우스를 움직이듯 30초간 자유롭게 움직여 주세요", 30.0),
)

FINGER_TO_GESTURE_LABEL: Dict[str, str] = {
    "index": "thumb_index",
    "middle": "thumb_middle",
    "ring": "thumb_ring",
    "pinky": "thumb_pinky",
}
HAND_SIZE_MIN_WARN = 0.085
HAND_SIZE_MAX_WARN = 0.620


def dist2d(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _xy(p: Any) -> Tuple[float, float]:
    return (p.x, p.y) if hasattr(p, "x") else (p[0], p[1])


def percentile(values: Sequence[float], q: float) -> float:
    v = sorted(values)
    if not v:
        return math.nan
    pos = (len(v) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


# ---------------------------------------------------------------------- one finger's pinch distances
@dataclass
class FingerResult:
    finger: str
    ok: bool = False               # the numbers are usable (for the index: thresholds may be set)
    quality: str = "none"          # good | fair | poor | none
    reps: int = 0
    contact: float = math.nan      # upper end of the contact distances (p90)
    open: float = math.nan         # lower end of the open distances (p10)
    separation: float = math.nan   # open / contact
    enter: float = math.nan
    release: float = math.nan
    reason: str = ""
    # side fingers: is the intended tip the nearest one while pinching?
    selectivity: float = math.nan
    confuser: str = ""
    confuser_share: float = 0.0
    margin: float = math.nan
    selectivity_quality: str = "none"
    small_open: bool = False       # the user opens less than the engine's release limit


def analyze_pinch(series: Sequence[Tuple[float, float]], min_open: float = 0.0,
                  min_range: float = 0.02) -> FingerResult:
    """`series` = (t, thumb-to-fingertip distance) while the user pinched and released a few times.

    `min_open`: the distance a release must reach to count (the thumb+index opening must be real).
    `min_range`: the smallest spread between the closest and widest distances that counts as movement.
    """
    res = FingerResult(finger="")
    if len(series) < 15:
        res.reason = "손이 충분히 보이지 않았습니다"
        return res
    ds = [d for _, d in series]
    lo, hi = percentile(ds, 0.05), percentile(ds, 0.95)
    if hi < min_open:
        res.reason = f"충분히 벌리지 않았습니다 (가장 많이 벌린 거리 {hi:.3f}, {min_open:.3f} 이상 필요)"
        return res
    if hi - lo < min_range:
        res.reason = "붙이고 떼는 움직임이 거의 없었습니다"
        return res
    cut_lo = lo + 0.3 * (hi - lo)
    cut_hi = max(hi - 0.3 * (hi - lo), min_open)
    contact = [d for d in ds if d <= cut_lo]
    opened = [d for d in ds if d >= cut_hi]
    reps, run_start, last_t, in_run = 0, None, None, False
    for t, d in series:           # contact runs that last long enough, separated by real openings
        if d <= cut_lo:
            if not in_run:
                in_run, run_start = True, t
            last_t = t
        elif d >= cut_hi and in_run:
            if last_t - run_start >= MIN_RUN_SECONDS:
                reps += 1
            in_run = False
    if in_run and last_t - run_start >= MIN_RUN_SECONDS:
        reps += 1
    res.reps = reps
    res.contact = percentile(contact, 0.90)
    res.open = percentile(opened, 0.10) if opened else hi
    res.separation = res.open / res.contact if res.contact > 0 else math.inf
    gap = res.open - res.contact
    res.enter = res.contact + 0.35 * gap
    res.release = max(res.contact + 0.55 * gap, res.enter + MIN_GAP)
    res.small_open = res.open < OPEN_MIN_INDEX
    if reps < MIN_REPS:
        res.quality, res.reason = "none", f"붙임이 {reps}번만 확인됐습니다 ({MIN_REPS}번 필요)"
        return res
    res.quality = ("good" if res.separation >= GOOD_SEPARATION
                   else "fair" if res.separation >= FAIR_SEPARATION else "poor")
    if res.quality == "poor":
        res.reason = "붙은 거리와 벌린 거리가 너무 가까워 구분이 어렵습니다"
        return res
    if gap < MIN_GAP:
        res.reason = f"붙임과 벌림 차이({gap:.3f})가 손가락 위치 흔들림 수준이라 안정적으로 구분할 수 없습니다"
        return res
    if not (LIMITS[0] <= res.enter <= LIMITS[1]):
        res.reason = f"계산값 {res.enter:.3f}이 정상 범위를 벗어났습니다"
        return res
    res.ok = True
    return res


def analyze_selectivity(frames: Sequence[Tuple[float, Dict[str, float]]], target: str) -> Dict[str, Any]:
    """While pinching `target`, how often is its tip the nearest of middle / ring / pinky to the thumb?

    Contact frames = the closest quarter of the target's distances (and within 0.06). The engine decides
    among these three fingers by "nearest to the thumb", so this is the share it would pick correctly.
    """
    out: Dict[str, Any] = {"frames": 0, "selectivity": math.nan, "confuser": "", "confuser_share": 0.0,
                           "margin": math.nan, "quality": "none"}
    sides = [f for f in OTHER_FINGERS]
    closest = [(min(d[f] for f in sides if f in d), d) for _, d in frames if any(f in d for f in sides)]
    if len(closest) < 15:
        return out
    # contact frames = the thumb is on SOME side fingertip (closest quarter, within 0.06), whichever it is,
    # so a pinch that landed on the wrong finger still counts against the intended one
    cut = min(percentile([c for c, _ in closest], 0.25), 0.06)
    contact = [d for c, d in closest if c <= cut]
    out["frames"] = len(contact)
    if len(contact) < 8:
        return out
    wins, rivals, margins = 0, {}, []
    for d in contact:
        others = {f: v for f, v in d.items() if f in OTHER_FINGERS and f != target}
        if not others or target not in d:
            continue
        nearest = min(others, key=others.get)
        margins.append(others[nearest] - d[target])
        if d[target] <= others[nearest]:
            wins += 1
        else:
            rivals[nearest] = rivals.get(nearest, 0) + 1
    n = len(margins)
    if n == 0:
        return out
    out["selectivity"] = wins / n
    out["margin"] = percentile(margins, 0.5)
    if rivals:
        rival = max(rivals, key=rivals.get)
        out["confuser"], out["confuser_share"] = rival, rivals[rival] / n
    q = out["selectivity"]
    out["quality"] = "good" if q >= GOOD_SELECTIVITY else "fair" if q >= FAIR_SELECTIVITY else "poor"
    return out


@dataclass
class CalibrationResult:
    updates: Dict[str, float] = field(default_factory=dict)
    fingers: Dict[str, FingerResult] = field(default_factory=dict)
    jitter: float = math.nan
    warnings: List[str] = field(default_factory=list)
    complete: bool = False
    hand_size: float = math.nan
    hand_size_status: str = "ok"
    lighting_status: str = "ok"
    neutral_move_seconds: float = 0.0
    onboarding_adaptation: Dict[str, Any] = field(default_factory=dict)

    def summary_lines(self) -> List[str]:
        names = {"good": "좋음", "fair": "보통", "poor": "구분 어려움", "none": "측정 실패"}
        lines = []
        idx = self.fingers.get("index")
        if idx is not None:
            if idx.quality in ("good", "fair", "poor"):
                lines.append(f"검지: {names[idx.quality]} (붙임 {idx.contact:.3f} / 벌림 {idx.open:.3f}, {idx.reps}번)"
                             + (f" → 진입 {idx.enter:.3f}, 해제 {idx.release:.3f}" if idx.ok else f" — 저장 안 함: {idx.reason}"))
            else:
                lines.append(f"검지: {names['none']} — {idx.reason}")
        for f in OTHER_FINGERS:
            r = self.fingers.get(f)
            if r is None:
                continue
            if math.isnan(r.selectivity):
                lines.append(f"{FINGER_LABELS[f]}: 측정 실패 — {r.reason or '붙인 구간이 충분하지 않았습니다'}")
                continue
            conf = (f", {FINGER_LABELS[r.confuser]}으로 읽힌 비율 {r.confuser_share:.0%}" if r.confuser else "")
            lines.append(f"{FINGER_LABELS[f]}: {names[r.selectivity_quality]} (의도한 손가락이 가장 가까운 비율 "
                         f"{r.selectivity:.0%}{conf}, 여유 {r.margin:.3f}, 붙임 {r.contact:.3f})")
        for key, value in self.updates.items():
            if key not in ("pinch_threshold", "release_threshold"):
                lines.append(f"{key}: {value} (현재 기준으로는 붙여도 닿지 않아 올림)")
        if not math.isnan(self.hand_size):
            lines.append(f"손 크기(화면 비율): {self.hand_size:.3f} ({self.hand_size_status}) · 조명 상태: {self.lighting_status}")
        if self.onboarding_adaptation.get("updated_classes"):
            cls_str = ", ".join(self.onboarding_adaptation["updated_classes"])
            lines.append(f"맞춤 프로토타입/OOD 보정 완료: {cls_str}")
        if not math.isnan(self.jitter):
            lines.append(f"손 떨림(정지 상태): {self.jitter:.4f} — 참고용, 자동으로 반영하지 않습니다")
        lines += [f"주의: {w}" for w in self.warnings]
        return lines


class CalibrationSession:
    def __init__(self, steps: Sequence[Step] = STEPS) -> None:
        self.steps = tuple(steps)
        self.index = 0
        self._step_t0: Optional[float] = None
        self.series: Dict[str, List[Tuple[float, float]]] = {s.key: [] for s in self.steps}
        self.multi: Dict[str, List[Tuple[float, Dict[str, float]]]] = {s.key: [] for s in self.steps}
        self.rest_points: List[Tuple[float, Tuple[float, float]]] = []
        self.fist_dists: Dict[str, List[float]] = {f: [] for f in FINGER_TIPS}
        self.frames: Dict[str, int] = {s.key: 0 for s in self.steps}
        self.lost: Dict[str, int] = {s.key: 0 for s in self.steps}
        self.last_distance: Optional[float] = None   # thumb to the step's fingertip, for the live bar
        self.hand_sizes: List[float] = []
        self.pulled_back_hand_sizes: List[float] = []
        self.hand_scores: List[float] = []
        self.pinch_landmarks: Dict[str, List[Tuple[float, float, Any]]] = {
            s.key: [] for s in self.steps if s.finger is not None
        }
        self.neutral_landmarks: List[Tuple[float, Any]] = []

    # ------------------------------------------------------------------ progress
    @property
    def done(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def step(self) -> Optional[Step]:
        return None if self.done else self.steps[self.index]

    def progress(self, now: float) -> Tuple[int, int, float]:
        """(step number from 1, total steps, fraction of the current step)."""
        if self.done:
            return len(self.steps), len(self.steps), 1.0
        if self._step_t0 is None:
            return self.index + 1, len(self.steps), 0.0
        return (self.index + 1, len(self.steps),
                min(1.0, max(0.0, now - self._step_t0 - PREP_SECONDS) / self.step.seconds))

    def preparing(self, now: float) -> float:
        """Seconds of 'get ready' left in the current step (0 = recording), or -1 while waiting for a hand."""
        if self.done:
            return 0.0
        if self._step_t0 is None:
            return -1.0
        return max(0.0, PREP_SECONDS - (now - self._step_t0))

    def _min_open(self, step: Step) -> float:
        return 0.0

    def reps_so_far(self) -> int:
        """Pinch repetitions seen in the current pinch step (to show 1/3, 2/3 ...)."""
        s = self.step
        if s is None or s.finger is None:
            return 0
        res = analyze_pinch(self.series[s.key], self._min_open(s),
                            0.012 if s.finger == "index" else 0.008)
        return res.reps

    def coach(self) -> str:
        """A hint for the current step ('' = nothing to say). Shown live so the user can correct at once."""
        s = self.step
        if s is None or s.finger is None:
            return ""
        ser = self.series[s.key]
        if len(ser) < 45:
            return ""
        recent = [d for t, d in ser if t >= ser[-1][0] - 2.5]
        if s.finger == "index" and max(recent) < 0.02 and self.reps_so_far() < MIN_REPS:
            return "손가락을 더 크게 활짝 벌려 주세요"
        if max(recent) - min(recent) < 0.008:
            return "붙였다 떼는 동작을 해 주세요"
        return ""

    # ------------------------------------------------------------------ input
    def feed(self, t: float, landmarks: Optional[Sequence[Any]], hand_score: float = 0.95) -> None:
        s = self.step
        if s is None:
            return
        if self._step_t0 is None:
            if landmarks is None:
                return                      # the clock of a step starts when the hand is first seen
            self._step_t0 = t
        if landmarks is not None and s.finger is not None:
            thumb = _xy(landmarks[THUMB_TIP])
            self.last_distance = dist2d(thumb, _xy(landmarks[FINGER_TIPS[s.finger]]))
        if t - self._step_t0 < PREP_SECONDS:
            return                          # get-ready time: nothing is recorded
        rec_t0 = self._step_t0 + PREP_SECONDS
        if landmarks is None:
            self.lost[s.key] += 1
        else:
            self.frames[s.key] += 1
            self.hand_scores.append(float(hand_score))
            xs_all = [_xy(landmarks[i])[0] for i in range(min(21, len(landmarks)))]
            ys_all = [_xy(landmarks[i])[1] for i in range(min(21, len(landmarks)))]
            if xs_all and ys_all:
                span = max(max(xs_all) - min(xs_all), max(ys_all) - min(ys_all))
                self.hand_sizes.append(float(span))
            thumb = _xy(landmarks[THUMB_TIP])
            if s.finger is not None:
                dists = {f: dist2d(thumb, _xy(landmarks[tip])) for f, tip in FINGER_TIPS.items()}
                self.series[s.key].append((t, dists[s.finger]))
                self.multi[s.key].append((t, dists))
                self.pinch_landmarks.setdefault(s.key, []).append((t, dists[s.finger], landmarks))
            elif s.key in ("rest", "open_hand"):
                xs = [_xy(landmarks[i]) for i in PALM_POINTS]
                self.rest_points.append((t, (sum(p[0] for p in xs) / 5, sum(p[1] for p in xs) / 5)))
                self.neutral_landmarks.append((t, landmarks))
            elif s.key in ("move_hand", "neutral_move_30s", "open"):
                self.neutral_landmarks.append((t, landmarks))
            elif s.key == "fist":
                for f, tip in FINGER_TIPS.items():
                    self.fist_dists[f].append(dist2d(thumb, _xy(landmarks[tip])))
            elif s.key in ("depth_clutch", "pull_back"):
                if xs_all and ys_all:
                    span = max(max(xs_all) - min(xs_all), max(ys_all) - min(ys_all))
                    self.pulled_back_hand_sizes.append(float(span))
        elapsed = t - rec_t0
        enough = (s.finger is not None and elapsed >= 4.0 and self.reps_so_far() >= MIN_REPS
                  and elapsed >= s.seconds * 0.6)
        if elapsed >= s.seconds or enough:
            self.index += 1
            self._step_t0 = None

    # ------------------------------------------------------------------ result
    def finish(
        self,
        current: Optional[Dict[str, Any]] = None,
        spatial_model: Optional[Any] = None,
    ) -> CalibrationResult:
        """`current` = the config in use (side-finger thresholds are compared with the user's contact).
        When `spatial_model` is provided, Stage 8-1 onboarding also computes blended user prototypes and OOD thresholds.
        """
        current = current or {}
        out = CalibrationResult(complete=self.done)
        for s in self.steps:
            if s.finger is None:
                continue
            if s.finger == "index":
                r = analyze_pinch(self.series[s.key], 0.0, 0.012)
            else:
                r = analyze_pinch(self.series[s.key], 0.0, 0.008)
                sel = analyze_selectivity(self.multi[s.key], s.finger)
                r.selectivity, r.confuser = sel["selectivity"], sel["confuser"]
                r.confuser_share, r.margin = sel["confuser_share"], sel["margin"]
                r.selectivity_quality = sel["quality"]
                r.ok = not math.isnan(sel["selectivity"])
            r.finger = s.finger
            out.fingers[s.finger] = r
            seen = self.frames[s.key] + self.lost[s.key]
            if self.frames[s.key] and self.lost[s.key] > 0.5 * seen:
                out.warnings.append(f"{FINGER_LABELS[s.finger]} 단계에서 손이 절반 넘게 사라졌습니다")
            if s.finger == "index":
                if r.ok:
                    out.updates[ENTER_KEYS["index"]] = round(r.enter, 4)
                    out.updates["release_threshold"] = round(r.release, 4)
                    if r.small_open:
                        cur_rel = float(current.get("release_threshold", 0.045)) + float(current.get("scroll_release_margin", 0.015))
                        out.warnings.append(
                            f"검지를 뗄 때 벌림이 작습니다 ({r.open:.3f}). 지금 해제 기준({cur_rel:.3f})보다 작아서 "
                            f"손가락을 뗐는데도 pinch가 유지될 수 있습니다. 계산된 기준({r.release:.3f})은 위치 흔들림에 "
                            f"민감할 수 있으니 써 보고 확인하세요")
                continue
            if r.selectivity_quality == "poor":
                out.warnings.append(f"{FINGER_LABELS[s.finger]} 핀치가 {FINGER_LABELS.get(r.confuser, '다른 손가락')}과 "
                                    f"자주 혼동됩니다 ({r.selectivity:.0%} 정확) — 임계값으로는 해결되지 않습니다")
            # raise-only: the contact itself is beyond the current threshold, so the click could not land
            now_thr = float(current.get(ENTER_KEYS[s.finger], DEFAULT_ENTER[s.finger]))
            if r.reps >= MIN_REPS and not math.isnan(r.contact) and r.contact > 0.85 * now_thr:
                want = min(LIMITS[1], r.contact * 1.3)
                out.updates[ENTER_KEYS[s.finger]] = round(want, 4)
        out.jitter = self._jitter()
        if math.isnan(out.jitter):
            out.warnings.append("손 떨림을 재지 못했습니다")
        for f, vals in self.fist_dists.items():
            enter = out.updates.get(ENTER_KEYS[f], current.get(ENTER_KEYS[f], DEFAULT_ENTER.get(f, 0.025)))
            if vals and enter is not None and percentile(vals, 0.5) <= float(enter):
                out.warnings.append(f"주먹에서 엄지-{FINGER_LABELS[f]} 거리가 핀치 기준 이하입니다 (주먹이 핀치로 읽힐 수 있음)")

        # Stage 8-1: Hand size & lighting / detection diagnostics
        if self.hand_sizes:
            out.hand_size = round(percentile(self.hand_sizes, 0.5), 4)
            out.updates["calibrated_hand_size"] = out.hand_size
            if out.hand_size < HAND_SIZE_MIN_WARN:
                out.hand_size_status = "too_small"
                out.warnings.append(
                    f"손 크기가 너무 작습니다 ({out.hand_size:.3f} < {HAND_SIZE_MIN_WARN:.3f}) — 카메라에 손을 더 가까이 두세요"
                )
            elif out.hand_size > HAND_SIZE_MAX_WARN:
                out.hand_size_status = "too_large"
                out.warnings.append(
                    f"손이 화면에 너무 꽉 찼습니다 ({out.hand_size:.3f} > {HAND_SIZE_MAX_WARN:.3f}) — 카메라에서 조금 떨어져 주세요"
                )
        total_seen = sum(self.frames.values())
        total_lost = sum(self.lost.values())
        lost_share = total_lost / max(total_seen + total_lost, 1)
        mean_score = sum(self.hand_scores) / len(self.hand_scores) if self.hand_scores else 0.95
        if lost_share > 0.25 or mean_score < 0.72:
            out.lighting_status = "low_light_or_unstable"
            out.warnings.append(
                f"조명이 어둡거나 손 인식이 불안정합니다 (인식 신뢰도 {mean_score:.0%}, 손실률 {lost_share:.0%}) — 주변 조명을 밝게 해 주세요"
            )
        if len(self.neutral_landmarks) >= 2:
            out.neutral_move_seconds = round(
                max(0.0, self.neutral_landmarks[-1][0] - self.neutral_landmarks[0][0]), 2
            )

        # Stage 8-1: Blended user prototypes & OOD thresholds on frozen encoder
        if spatial_model is not None and hasattr(spatial_model, "apply_user_onboarding_prototypes"):
            import numpy as np
            from src.spatial_features import extract_spatial_features

            labeled_feats: Dict[str, Any] = {}
            for s in self.steps:
                if s.finger is None:
                    continue
                g_lbl = FINGER_TO_GESTURE_LABEL.get(s.finger)
                pts = self.pinch_landmarks.get(s.key, [])
                if not g_lbl or not pts:
                    continue
                dists_only = [d for _, d, _ in pts]
                contact_cut = percentile(dists_only, 0.35) + 0.004
                contact_vecs = []
                for _, d_val, lm in pts:
                    if d_val <= contact_cut:
                        vec = extract_spatial_features(lm)
                        if vec is not None:
                            contact_vecs.append(vec)
                if contact_vecs:
                    labeled_feats[g_lbl] = np.asarray(contact_vecs, dtype=np.float64)

            neutral_vecs = []
            for _, lm in self.neutral_landmarks:
                vec = extract_spatial_features(lm)
                if vec is not None:
                    neutral_vecs.append(vec)
            neutral_arr = np.asarray(neutral_vecs, dtype=np.float64) if neutral_vecs else None
            if labeled_feats or neutral_arr is not None:
                out.onboarding_adaptation = spatial_model.apply_user_onboarding_prototypes(
                    labeled_feats,
                    neutral_features=neutral_arr,
                    blend_beta=0.35,
                )

        if not self.done:
            out.warnings.append("모든 단계를 끝내지 않았습니다")
        if self.hand_sizes and self.pulled_back_hand_sizes:
            clutch_cal = calibrate_depth_clutch(self.hand_sizes, self.pulled_back_hand_sizes)
            if clutch_cal.get("ok"):
                out.updates["clutch_depth_enter_ratio"] = clutch_cal["clutch_depth_enter_ratio"]
                out.updates["clutch_depth_exit_ratio"] = clutch_cal["clutch_depth_exit_ratio"]
                out.updates["calibrated_hand_size"] = clutch_cal["calibrated_hand_size"]
        return out

    def feed_depth_clutch_sample(self, landmarks_or_size: Any, *, pulled_back: bool = True) -> None:
        """Onboarding calibration hook for explicit depth clutch (`return_suppression="clutch"`)."""
        if isinstance(landmarks_or_size, (int, float)):
            span = float(landmarks_or_size)
        elif landmarks_or_size is not None and len(landmarks_or_size) >= 21:
            xs_all = [_xy(landmarks_or_size[i])[0] for i in range(21)]
            ys_all = [_xy(landmarks_or_size[i])[1] for i in range(21)]
            span = max(max(xs_all) - min(xs_all), max(ys_all) - min(ys_all))
        else:
            return
        if span <= 1e-6:
            return
        if pulled_back:
            self.pulled_back_hand_sizes.append(span)
        else:
            self.hand_sizes.append(span)

    def _jitter(self) -> float:
        pts = self.rest_points
        if len(pts) < 30:
            return math.nan
        # frame-to-frame step of the palm centre while the hand rests; /sqrt(2) = per-frame noise
        steps = [dist2d(pts[i][1], pts[i + 1][1]) for i in range(len(pts) - 1)]
        return percentile(steps, 0.5) / math.sqrt(2)


def calibrate_depth_clutch(
    forward_sizes: Sequence[float],
    pulled_back_sizes: Sequence[float],
) -> Dict[str, Any]:
    """
    Compute personalized `clutch_depth_enter_ratio` and `clutch_depth_exit_ratio`
    from normal working distance (`forward_sizes`) vs pulled-back clutch posture
    (`pulled_back_sizes`).
    """
    fwd = [float(x) for x in forward_sizes if float(x) > 1e-4]
    back = [float(x) for x in pulled_back_sizes if float(x) > 1e-4]
    if len(fwd) < 3 or len(back) < 3:
        return {"ok": False, "reason": "insufficient_samples"}
    fwd_med = percentile(fwd, 0.5)
    back_med = percentile(back, 0.5)
    ratio = back_med / max(1e-6, fwd_med)
    if ratio >= 0.95:
        return {
            "ok": False,
            "reason": "insufficient_depth_separation",
            "forward_median": round(fwd_med, 4),
            "pulled_back_median": round(back_med, 4),
            "ratio": round(ratio, 4),
        }
    # Place entry threshold 60% of the way from forward (1.0) to pulled-back (ratio),
    # and exit threshold 35% of the way from forward (1.0) to pulled-back (ratio) for clean hysteresis.
    enter_r = max(0.65, min(0.93, 1.0 - 0.60 * (1.0 - ratio)))
    exit_r = max(enter_r + 0.03, min(0.97, 1.0 - 0.35 * (1.0 - ratio)))
    return {
        "ok": True,
        "calibrated_hand_size": round(fwd_med, 4),
        "pulled_back_hand_size": round(back_med, 4),
        "clutch_depth_enter_ratio": round(enter_r, 4),
        "clutch_depth_exit_ratio": round(exit_r, 4),
    }


# ---------------------------------------------------------------------- saving
def apply_updates(path: Path, updates: Dict[str, float], report: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Merge `updates` into the JSON config; the old file is kept as `<name>.bak-<time>`. Returns the backup.
    (The wizard saves profiles now; this stays for scripts that want to edit a config file directly.)"""
    path = Path(path)
    data: Dict[str, Any] = {}
    backup = None
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    data.update(updates)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if report is not None:
        (path.parent / "calibration.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return backup


def raw_dump(session: "CalibrationSession") -> Dict[str, Any]:
    """The measured series of a session (numbers only, ~10 samples/s) for diagnosing a bad result."""
    steps = {}
    for st in session.steps:
        ser = session.series.get(st.key, [])
        entry: Dict[str, Any] = {"frames": session.frames[st.key], "lost": session.lost[st.key]}
        if ser:
            ds = [d for _, d in ser]
            entry.update(seconds=round(ser[-1][0] - ser[0][0], 1), min=round(min(ds), 4), max=round(max(ds), 4),
                         p05=round(percentile(ds, 0.05), 4), p50=round(percentile(ds, 0.5), 4),
                         p95=round(percentile(ds, 0.95), 4),
                         series=[round(d, 3) for _, d in ser[::3]])
            multi = session.multi.get(st.key, [])
            if multi and st.finger != "index":
                entry["others_p50"] = {f: round(percentile([d[f] for _, d in multi], 0.5), 4) for f in FINGER_TIPS}
        steps[st.key] = entry
    fist = {f: {"p50": round(percentile(v, 0.5), 4), "min": round(min(v), 4)}
            for f, v in session.fist_dists.items() if v}
    return {"steps": steps, "fist": fist, "rest_frames": len(session.rest_points)}


def report_dict(result: CalibrationResult) -> Dict[str, Any]:
    def num(x: float, digits: int = 4) -> Optional[float]:
        return None if math.isnan(x) else round(x, digits)

    return {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updates": result.updates,
        "jitter": num(result.jitter, 5),
        "hand_size": num(result.hand_size, 4),
        "hand_size_status": result.hand_size_status,
        "lighting_status": result.lighting_status,
        "neutral_move_seconds": round(result.neutral_move_seconds, 2),
        "onboarding_adaptation": result.onboarding_adaptation,
        "warnings": result.warnings,
        "fingers": {f: {"quality": r.quality, "reps": r.reps, "ok": r.ok, "reason": r.reason,
                        "contact": num(r.contact), "open": num(r.open), "separation": num(r.separation, 2),
                        "selectivity": num(r.selectivity, 3), "confuser": r.confuser,
                        "confuser_share": round(r.confuser_share, 3), "margin": num(r.margin)}
                    for f, r in result.fingers.items()},
    }
