"""Measure comfortable strokes, then try the proposed cursor settings on virtual targets."""
from __future__ import annotations

import json
import math
from statistics import median

from manux.data.tracking_test import Target, TrackingTest, view as tracking_view


SETTING_KEY = "app.pointer_calibration"
LIMITS = {
    "relative_gain_min": (.05, 3.0),
    "relative_gain_max": (.5, 6.0),
    "smoothing": (.15, .7),
    "relative_gain_y": (.75, 2.5),
    "relative_y_cross": (-.5, .5),
}
PHASES = ("rest", "horizontal", "vertical")
DURATIONS = {"rest": 2.0, "horizontal": 3.2, "vertical": 3.2}


def read_profiles(value: str) -> dict:
    """Only bounded numeric fields from a complete, versioned local profile are applied."""
    if len(value) > 8192:
        return {}
    try:
        data = json.loads(value)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("version") != 1:
        return {}
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    result = {}
    for key, values in list(profiles.items())[-8:]:
        if len(key) != 64 or not isinstance(values, dict) or values.keys() != LIMITS.keys():
            continue
        if not all(type(values[name]) in (int, float) and lo <= values[name] <= hi
                   for name, (lo, hi) in LIMITS.items()):
            continue
        if values["relative_gain_min"] <= values["relative_gain_max"]:
            result[key] = values
    return result


def write_profiles(profiles: dict) -> str:
    return json.dumps({"version": 1, "profiles": dict(list(profiles.items())[-8:])})


def _clamp(value, low, high):
    return min(high, max(low, value))


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def _span(values):
    return _percentile(values, .9) - _percentile(values, .1)


def recommend(samples, screen_aspect, base):
    """Coordinates are in camera-width units on both axes. Ignore stroke extremes."""
    horizontal = _span([x for x, _ in samples["horizontal"]])
    vertical_points = samples["vertical"]
    vertical_y = [y for _, y in vertical_points]
    vertical = _span(vertical_y)
    noise = max(_span(list(axis)) for axis in zip(*samples["rest"]))
    if horizontal < max(.025, 4 * noise) or vertical < max(.012, 4 * noise):
        raise ValueError("움직임과 떨림을 구분하기 어려워요. 손이 보이는 곳에서 편한 범위로 다시 움직여 주세요.")
    low, high = _percentile(vertical_y, .25), _percentile(vertical_y, .75)
    lower = [(x, y) for x, y in vertical_points if y <= low]
    upper = [(x, y) for x, y in vertical_points if y >= high]
    dx = median(x for x, _ in upper) - median(x for x, _ in lower)
    dy = median(y for _, y in upper) - median(y for _, y in lower)
    drift = dx / dy if dy > noise else 0.0
    fast = _clamp(.40 / horizontal, base.relative_gain_max * .75, max(2.5, base.relative_gain_max * 1.4))
    fast = _clamp(fast, *LIMITS["relative_gain_max"])
    return {
        "relative_gain_min": _clamp(min(base.relative_gain_min, fast), *LIMITS["relative_gain_min"]),
        "relative_gain_max": fast,
        "smoothing": _clamp(.45 - 28 * noise, .20, .45),
        "relative_gain_y": _clamp(screen_aspect * horizontal / vertical, *LIMITS["relative_gain_y"]),
        "relative_y_cross": _clamp(drift, *LIMITS["relative_y_cross"]),
    }


class PointerSetup:
    def __init__(self, monitor, base, make_mapper, move_gain=1.0):
        self.monitor, self.base, self.make_mapper = monitor, base, make_mapper
        self.move_gain = move_gain
        self.phase = "rest"
        self.started = None
        self.progress = 0.0
        self.samples = {phase: [] for phase in PHASES}
        self.frames = 0
        self.last_point = None
        self.last_t = None
        self.hand_present = False
        self.values = {}
        self.note = ""
        self.test = None
        self.mapper = None
        self.round = 0

    def _begin_aim(self):
        self.round += 1
        direction = 1 if self.round % 2 else -1
        points = ((.50, .39, 110), (.50, .61, 110), (.38, .61, 90),
                  (.62, .61, 90), (.53, .46, 70), (.46, .49, 55))
        targets = [Target(.5 + (x - .5) * direction, .5 + (y - .5) * direction, size)
                   for x, y, size in points]
        self.test = TrackingTest(self.monitor, dwell=.28, timeout=6, hold_margin_px=4, targets=targets)
        self.mapper = self.make_mapper(self.values)
        self.phase = "aim"
        self.last_point = self.last_t = None

    def step(self, now, point):
        if self.phase not in (*PHASES, "aim"):
            return
        valid = point is not None and all(math.isfinite(v) for v in point)
        dt = None if self.last_t is None else now - self.last_t
        usable = valid and (dt is None or 0 < dt <= .15)
        if self.phase == "aim":
            self.last_t = now
            self.hand_present = usable
            self._aim(now, point if usable else None)
            return
        if usable and dt is not None and self.last_point is not None:
            dx = point[0] - self.last_point[0]
            dy = (point[1] - self.last_point[1]) * self.base.frame_aspect
            distance = math.hypot(dx, dy)
            usable = distance < max(.075, 2.5 * dt)
        self.last_t = now
        self.last_point = point if valid else None
        self.hand_present = usable
        if self.started is None:
            self.started = now
        elapsed = now - self.started
        self.progress = _clamp(elapsed / DURATIONS[self.phase], 0.0, 1.0)
        samples = self.samples[self.phase]
        if elapsed >= .55:
            self.frames += 1
            if usable:
                samples.append((point[0], point[1] * self.base.frame_aspect))
        if elapsed < DURATIONS[self.phase]:
            return
        if len(samples) < 12 or len(samples) < .75 * self.frames:
            self.phase = "retry"
            self.note = "손 추적이 자주 끊겼어요. 손 전체가 보이도록 한 뒤 다시 시도해 주세요."
            return
        index = PHASES.index(self.phase)
        if index < len(PHASES) - 1:
            self.phase = PHASES[index + 1]
            self.started, self.frames, self.progress = None, 0, 0.0
            return
        try:
            self.values = recommend(self.samples, self.monitor.height / self.monitor.width, self.base)
        except ValueError as exc:
            self.phase, self.note = "retry", str(exc)
            return
        self.samples.clear()
        self._begin_aim()

    def _aim(self, now, point):
        if point is None:
            self.mapper.pause()
        else:
            self.mapper.step(point, now, self.move_gain)
        self.test.update(now, self.mapper.pos, hand=point is not None, active=True)
        if not self.test.done:
            return
        stats = self.test.summary()
        if stats["reached"] < 4 or stats["lost_s"] > 2.0:
            self.phase = "retry"
            self.note = "표적을 충분히 확인하지 못했어요. 다시 측정하거나 기존 설정으로 계속할 수 있어요."
            return
        overshoots = sum(target.overshoots for target in self.test.targets)
        if self.round == 1 and (overshoots >= 3 or (stats["mean_dwell_jitter_px"] or 0) > 5):
            self.note = "지나치거나 흔들리는 움직임이 보여서 정밀 이동을 낮췄어요. 새 표적으로 확인해 주세요."
            self.adjust("steadier")
            return
        self.phase = "review"
        self.note = (f"표적 {stats['reached']}/{stats['targets']} 도달 · 지나침 {overshoots}회"
                     f" · 상하 배율 {self.values['relative_gain_y']:.2f}배")

    def adjust(self, choice):
        values = self.values
        if choice == "faster":
            values["relative_gain_max"] = min(6.0, values["relative_gain_max"] * 1.15)
        elif choice == "vertical":
            values["relative_gain_y"] = min(2.5, values["relative_gain_y"] * 1.15)
        elif choice == "steadier":
            values["relative_gain_min"] = max(.05, values["relative_gain_min"] * .85)
            values["smoothing"] = max(.15, values["smoothing"] * .85)
        self._begin_aim()

    def view(self):
        data = {"phase": self.phase, "note": self.note,
                "progress": round(self.progress, 2), "hand": self.hand_present}
        if self.phase == "aim":
            data.update(tracking_view(self.test, self.mapper.pos))
            data["aspect"] = self.monitor.width / self.monitor.height
        return data
