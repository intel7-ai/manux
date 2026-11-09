"""Guided palm calibration and virtual aim practice."""
from __future__ import annotations

import json
import math
import time
from statistics import median

from manux.data.tracking_test import Target, TrackingTest, view as tracking_view

SETTING_KEY = "app.pointer_calibration"
ADJUSTMENTS = {"palm_x_gain": (.65, 1.65), "palm_y_gain": (.65, 1.65), "smoothing": (.15, .75),
               "palm_precision": (.25, 1.)}
DEFAULT_HOLD = 0.  # neutral zone radius as a share of screen height (0 = off; fine-round traces: any zone slows one-cell moves)
# Cursor gain of the slowest moves (gain rises with speed to 1). 0.4 cut holding wobble in fine traces but felt
# heavy in real use; 0.7 is the light default and the fist (0.35x) is the precision gesture.
DEFAULT_PRECISION = .7
LIMITS = {**ADJUSTMENTS, "palm_left": (0., 1.), "palm_right": (0., 1.),
          "palm_up": (-180., 180.), "palm_down": (-180., 180.)}
PHASES = ("rest", "horizontal", "up", "down")
DURATIONS = {"rest": .8, "horizontal": 4., "up": 1.2, "down": 1.2}
PREP_SECONDS = .6
TRACE_VERSION = 1
KNUCKLE_JOINTS = (0, 5, 9, 13, 17)   # wrist and the four finger roots, 8th trace column (x, y, z each)
# Fine round: 40 px cells (Minesweeper, toolbar buttons), one cell per step in all four directions
FINE_CELL_PX = 40
FINE_ORIGIN = (.5, .45)
FINE_VIEW_PX = 640
# Menu round: rows of a menu like Minesweeper's difficulty list, held still for a second each
MENU_ROW_PX = 24
MENU_DWELL = 1.0
MENU_PATH = (0, 1, 2, 1, 3, 4, 3, 2, 0, -1, 0, 2)
FINE_PATH = ((0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1),
             (0, -1), (1, -1), (1, 0), (0, 0), (0, 1))
VERSION = 3  # 3: pitch is the camera's vertical-plane angle (no fold at upright); v2 angles are not comparable


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
    # Early v3 profiles were saved before the neutral zone existed: give them the default.
    # Profiles saved before low-speed precision get the default; the retired neutral zone is dropped.
    profiles = {key: {"palm_precision": DEFAULT_PRECISION,
                      **{k: v for k, v in values.items() if k != "palm_hold"}} if isinstance(values, dict) else values
                for key, values in profiles.items()}
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
        raise ValueError(f"좌우 범위가 너무 작게 측정됐어요(카메라 폭의 {right - left:.1%}). "
                         "편한 양 끝까지 왕복해 주세요.")
    if (up - neutral) * (down - neutral) >= 0 or min(abs(up - neutral), abs(down - neutral)) < 3 or abs(up - down) < 8:
        raise ValueError(f"위·아래 기울기가 충분히 구분되지 않았어요(중립 {neutral:.0f}° · 위 {up:.0f}° · "
                         f"아래 {down:.0f}°). 팔을 받친 채 중립에서 양쪽으로 기울여 주세요.")
    rest_x, rest_pitch = zip(*samples["rest"])
    noise = max((_percentile(rest_x, .9) - _percentile(rest_x, .1)) / (right - left),
                (_percentile(rest_pitch, .9) - _percentile(rest_pitch, .1)) / abs(up - down))
    return dict(palm_left=left, palm_right=right, palm_up=up, palm_down=down,
                palm_x_gain=1., palm_y_gain=1., smoothing=max(.20, .45 - 1.5 * noise),
                palm_precision=DEFAULT_PRECISION)


def _zoom(data, monitor):
    """The setup page shows the whole screen ~3x smaller; for 40 px cells show a 640 px wide window
    around the grid instead, so cells and cursor look close to their real size."""
    span_x = min(1., FINE_VIEW_PX / monitor.width)
    span_y = min(1., span_x * monitor.width / monitor.height * 9 / 16)
    x0, y0 = FINE_ORIGIN[0] - span_x / 2, FINE_ORIGIN[1] - span_y / 2
    out = {"aspect": (span_x * monitor.width) / (span_y * monitor.height), "zoomed": True}
    if data.get("target") is not None:
        fx, fy, size = data["target"]
        out["target"] = ((fx - x0) / span_x, (fy - y0) / span_y, size / span_x)
    if data.get("cursor") is not None:
        cx, cy = data["cursor"]
        out["cursor"] = (min(1., max(0., (cx - x0) / span_x)), min(1., max(0., (cy - y0) / span_y)))
    return out


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
        self.aim_ok = False
        self.kind = "aim"
        self.trace = []
        self.trace_dir = None   # set by the runtime: where finished rounds are written for replay

    def capture(self):
        if self.phase in PHASES and not self.capturing and self.point is not None:
            self.capturing = True
            self.started, self.frames, self.progress, self.note = None, 0, 0., ""
            self.samples[self.phase].clear()

    def begin_aim(self, fine=False, kind=None):
        kind = kind or ("fine" if fine else "aim")
        self.round += 1
        if kind == "menu":
            # A menu column: 24 px rows, move to a neighbouring row and stay on it for a second
            h = self.monitor.height
            targets = [Target(FINE_ORIGIN[0], FINE_ORIGIN[1] + row * MENU_ROW_PX / h, MENU_ROW_PX)
                       for row in MENU_PATH]
            self.test = TrackingTest(self.monitor, dwell=MENU_DWELL, timeout=8, hold_margin_px=0, targets=targets)
        elif kind == "fine":
            # One cell at a time, like moving between Minesweeper cells or small buttons
            w, h = self.monitor.width, self.monitor.height
            cells = [(FINE_ORIGIN[0] + cx * FINE_CELL_PX / w, FINE_ORIGIN[1] + cy * FINE_CELL_PX / h)
                     for cx, cy in FINE_PATH]
            targets = [Target(x, y, FINE_CELL_PX) for x, y in cells]
            self.test = TrackingTest(self.monitor, dwell=.35, timeout=6, hold_margin_px=0, targets=targets)
        else:
            self.aim_ok = False
            direction = 1 if self.round % 2 else -1
            points = ((.50, .28, 110), (.50, .72, 110), (.27, .72, 90),
                      (.73, .72, 90), (.59, .42, 70), (.49, .48, 55))
            targets = [Target(.5 + (x - .5) * direction, .5 + (y - .5) * direction, size)
                       for x, y, size in points]
            self.test = TrackingTest(self.monitor, dwell=.28, timeout=8, hold_margin_px=4, targets=targets)
        self.kind = kind
        self.trace = []
        self.mapper = self.make_mapper(self.values)
        self.phase, self.last_t, self.note, self.can_apply = "aim", None, "", False

    def step(self, now, point, pinch=None, world=None):
        self.pinch = pinch
        # wrist + knuckle joints (world coords) for the trace: lets the replay tool try other pitch definitions
        self.knuckles = None if world is None else [round(v, 5) for i in KNUCKLE_JOINTS for v in world[i][:3]]
        dt = None if self.last_t is None else now - self.last_t
        self.last_t = now
        self.point = point if point is not None and all(map(math.isfinite, point)) and (
            dt is None or 0 < dt <= .15) else None
        if self.phase in ("aim", "preview"):
            if self.point is None:
                self.mapper.pause()
            else:
                self.mapper.step_palm(*self.point, now, contact=self.pinch)
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
            spread = _percentile(angles, .9) - _percentile(angles, .1)
            if spread > 6.:
                self.note = f"측정하는 동안 같은 기울기를 잠깐 유지해 주세요(흔들림 {spread:.0f}° · 허용 6°)."
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
        index = self.test.index
        self.test.update(now, self.mapper.pos, hand=self.point is not None, active=True)
        x, pitch = self.point if self.point is not None else (None, None)
        # full precision: a replay with the same settings must reproduce the recorded cursor exactly
        self.trace.append((now, x, pitch, self.mapper.pos[0], self.mapper.pos[1], index, getattr(self, "pinch", None),
                           getattr(self, "knuckles", None)))
        if not self.test.done:
            return
        stats = self.test.summary()
        self.phase = "review"
        if self.kind in ("fine", "menu"):
            overshoots = sum(t.overshoots for t in self.test.targets)
            self.can_apply = getattr(self, "aim_ok", False)
            self.note = (f"{'메뉴 줄' if self.kind == 'menu' else '한 칸 이동'} {stats['reached']}/{stats['targets']}"
                         + (f" · 중간 {stats['median_time_s']:.2f}초" if stats["median_time_s"] is not None else "")
                         + f" · 넘침 {overshoots}회"
                         + (f" · 멈춤 흔들림 {stats['mean_dwell_jitter_px']:.1f}px"
                            if self.kind == "menu" and stats["mean_dwell_jitter_px"] is not None else ""))
        else:
            self.can_apply = self.aim_ok = stats["reached"] >= 4 and stats["lost_s"] <= 2.
            self.note = f"표적 {stats['reached']}/{stats['targets']} 도달"
            if not self.can_apply:
                self.note += " · 감도를 조정하거나 손 위치를 확인한 뒤 다시 시험하세요."
        saved = self._save_trace()
        if saved is not None:
            self.note += f" · 기록 {saved.name}"

    def trace_data(self):
        """The round as plain data: raw palm input per frame plus the target schedule (no images)."""
        return {"version": TRACE_VERSION, "kind": self.kind, "pinch": "click_ratio",
                "monitor": [self.monitor.left, self.monitor.top, self.monitor.width, self.monitor.height],
                "values": dict(self.values), "dwell": self.test.dwell, "hold_margin_px": self.test.hold_margin_px,
                "targets": [{"fx": t.fx, "fy": t.fy, "size_px": t.size_px, "shown_at": t.shown_at,
                             "reached_at": t.reached_at, "overshoots": t.overshoots} for t in self.test.targets],
                "frames": [list(row) for row in self.trace]}

    def _save_trace(self):
        if self.trace_dir is None or not self.trace:
            return None
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            path = self.trace_dir / time.strftime(f"pointer-{self.kind}-%Y%m%d-%H%M%S.json")
            path.write_text(json.dumps(self.trace_data()), encoding="utf-8")
            return path
        except OSError:
            return None

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
            if self.kind in ("fine", "menu"):
                data.update(_zoom(data, self.monitor))
            if self.phase == "preview":
                data["target"] = None
        return data
