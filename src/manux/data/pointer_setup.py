"""Guided palm calibration and virtual aim practice."""
from __future__ import annotations

import json
import math
from statistics import median

from manux.data.tracking_test import Target, TrackingTest, view as tracking_view

SETTING_KEY = "app.pointer_calibration"
ADJUSTMENTS = {"palm_x_gain": (.65, 1.65), "palm_y_gain": (.65, 1.65), "smoothing": (.15, .75)}
LIMITS = {**ADJUSTMENTS, "palm_left": (0., 1.), "palm_right": (0., 1.),
          "palm_up": (-90., 90.), "palm_down": (-90., 90.)}
PHASES = ("rest", "horizontal", "up", "down")
DURATIONS = {"rest": .8, "horizontal": 4., "up": 1.2, "down": 1.2}
PREP_SECONDS = .6
VERSION = 2


def _bounded(value, bounds):
    return type(value) in (int, float) and bounds[0] <= value <= bounds[1]


def read_profiles(value: str) -> dict:
    if len(value) > 8192:
        return {}
    try:
        data = json.loads(value)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    return {key: values for key, values in list(profiles.items())[-8:]
            if len(key) == 64 and isinstance(values, dict) and values.keys() == LIMITS.keys()
            and all(_bounded(values[name], bounds) for name, bounds in LIMITS.items())
            and values["palm_right"] - values["palm_left"] >= .035
            and abs(values["palm_up"] - values["palm_down"]) >= 8.}


def write_profiles(profiles: dict) -> str:
    return json.dumps({"version": VERSION, "profiles": dict(list(profiles.items())[-8:])})


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def recommend(samples):
    horizontal = [x for x, _ in samples["horizontal"]]
    left = max(0., _percentile(horizontal, .10))
    right = min(1., _percentile(horizontal, .90))
    neutral, up, down = (median(pitch for _, pitch in samples[phase])
                         for phase in ("rest", "up", "down"))
    if right - left < .035:
        raise ValueError("좌우 범위가 너무 작게 측정됐어요. 편한 양 끝까지 왕복해 주세요.")
    if (up - neutral) * (down - neutral) >= 0 or min(abs(up - neutral), abs(down - neutral)) < 3 or abs(up - down) < 8:
        raise ValueError("위·아래 기울기가 충분히 구분되지 않았어요. 팔을 받친 채 중립에서 양쪽으로 기울여 주세요.")
    rest_x, rest_pitch = zip(*samples["rest"])
    noise = max((_percentile(rest_x, .9) - _percentile(rest_x, .1)) / (right - left),
                (_percentile(rest_pitch, .9) - _percentile(rest_pitch, .1)) / abs(up - down))
    return dict(palm_left=left, palm_right=right, palm_up=up, palm_down=down,
                palm_x_gain=1., palm_y_gain=1., smoothing=max(.20, .45 - 1.5 * noise))


class PointerSetup:
    def __init__(self, monitor, frame_width, make_mapper):
        self.monitor, self.frame_width, self.make_mapper = monitor, frame_width, make_mapper
        self.phase = "rest"
        self.capturing = False
        self.started = None
        self.progress = 0.
        self.samples = {phase: [] for phase in PHASES}
        self.frames = 0
        self.last_t = None
        self.point = None
        self.values = {}
        self.note = ""
        self.test = None
        self.mapper = None
        self.round = 0
        self.can_apply = False

    def capture(self):
        if self.phase in PHASES and not self.capturing and self.point is not None:
            self.capturing = True
            self.started, self.frames, self.progress, self.note = None, 0, 0., ""
            self.samples[self.phase].clear()

    def begin_aim(self):
        self.round += 1
        direction = 1 if self.round % 2 else -1
        points = ((.50, .28, 110), (.50, .72, 110), (.27, .72, 90),
                  (.73, .72, 90), (.59, .42, 70), (.49, .48, 55))
        targets = [Target(.5 + (x - .5) * direction, .5 + (y - .5) * direction, size)
                   for x, y, size in points]
        self.test = TrackingTest(self.monitor, dwell=.28, timeout=8, hold_margin_px=4, targets=targets)
        self.mapper = self.make_mapper(self.values)
        self.phase, self.last_t, self.note, self.can_apply = "aim", None, "", False

    def step(self, now, point):
        dt = None if self.last_t is None else now - self.last_t
        self.last_t = now
        self.point = point if point is not None and all(map(math.isfinite, point)) and (
            dt is None or 0 < dt <= .15) else None
        if self.phase in ("aim", "preview"):
            if self.point is None:
                self.mapper.pause()
            else:
                self.mapper.step_palm(*self.point, now)
            if self.phase == "aim":
                self._aim(now)
        elif self.phase in PHASES and self.capturing:
            self._capture(now)

    def _capture(self, now):
        if self.started is None:
            self.started = now
        elapsed = now - self.started - PREP_SECONDS
        if elapsed < 0:
            self.note = "자세를 준비해 주세요."
            return
        self.note = ""
        self.progress = min(1., elapsed / DURATIONS[self.phase])
        self.frames += 1
        samples = self.samples[self.phase]
        if self.point is not None:
            samples.append(self.point)
        if self.progress < 1.:
            return
        self.capturing = False
        if len(samples) < 6 or len(samples) < .75 * self.frames:
            self.note = "손이 자주 사라졌어요. 손 전체를 보여 준 뒤 이 단계를 다시 측정하세요."
            return
        if self.phase in ("rest", "up", "down"):
            angles = [pitch for _, pitch in samples]
            if _percentile(angles, .9) - _percentile(angles, .1) > 6.:
                self.note = "측정하는 동안 같은 기울기를 잠깐 유지해 주세요."
                return
        index = PHASES.index(self.phase)
        if index < len(PHASES) - 1:
            self.phase, self.progress = PHASES[index + 1], 0.
            return
        try:
            self.values = recommend(self.samples)
        except ValueError as exc:
            self.phase, self.note = "retry", str(exc)
            return
        self.samples.clear()
        self.begin_aim()

    def _aim(self, now):
        self.test.update(now, self.mapper.pos, hand=self.point is not None, active=True)
        if self.test.done:
            stats = self.test.summary()
            self.phase = "review"
            self.can_apply = stats["reached"] >= 4 and stats["lost_s"] <= 2.
            self.note = f"표적 {stats['reached']}/{stats['targets']} 도달"
            if not self.can_apply:
                self.note += " · 감도를 조정하거나 손 위치를 확인한 뒤 다시 시험하세요."

    def adjust(self, values):
        if self.phase not in ("aim", "review", "preview") or len(values) != 1:
            return
        key, value = next(iter(values.items()))
        if key not in ADJUSTMENTS or not _bounded(value, ADJUSTMENTS[key]):
            return
        self.values[key] = value
        self.mapper = self.make_mapper(self.values)
        self.phase, self.can_apply = "preview", False
        self.note = "이번 시험을 멈췄어요. 바뀐 값으로 움직여 보고 준비되면 다시 시험하세요."

    def view(self):
        data = {"phase": self.phase, "note": self.note, "capturing": self.capturing,
                "progress": round(self.progress, 2), "hand": self.point is not None,
                "pitch": None if self.point is None else round(self.point[1], 1),
                "canApply": self.can_apply, **self.values}
        if self.values:
            span_px = round((self.values["palm_right"] - self.values["palm_left"]) * self.frame_width)
            span_deg = round(abs(self.values["palm_up"] - self.values["palm_down"]), 1)
            data["rangeText"] = (f"측정 범위  ·  좌우 {span_px}px → 화면 {self.monitor.width}px"
                                 f"  ·  상하 {span_deg}° → 화면 {self.monitor.height}px")
        if self.phase in ("aim", "preview"):
            data.update(tracking_view(self.test, self.mapper.pos))
            data["aspect"] = self.monitor.width / self.monitor.height
            if self.phase == "preview":
                data["target"] = None
        return data
