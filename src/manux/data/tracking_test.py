"""Cursor tracking test (no clicks): move onto targets and hold still on them (S6).

Port of hand-mouse/src/tracking_test.py (v0.3, 2026-11-03) with the same targets, numbers and CSV,
so v0.3 and v0.4 results can be compared. The desktop runtime feeds it the cursor position the
Windows output placed each frame; the UI only shows `view()`.

Per target: time to reach (cursor inside the target for `dwell` s), straight distance vs travelled
path (path efficiency), cursor jitter in the last half of the hold (RMS px), and how long / how
often the hand was lost. Summary rows and per-target rows go to a CSV in the user's log folder.

Targets are placed in fractions of one monitor (anything with left / top / width / height in screen
pixels, e.g. `platform.monitors.Monitor`), so the window can draw them at any DPI scale while
hit-testing happens in screen pixels.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

SIZES_PX = (40, 70, 110)  # target diameters on the test monitor


class Area(Protocol):
    left: int
    top: int

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...


@dataclass
class Target:
    fx: float          # centre, fraction of the monitor width
    fy: float          # centre, fraction of the monitor height
    size_px: int       # diameter
    shown_at: Optional[float] = None
    start_pos: Optional[Tuple[float, float]] = None
    reached_at: Optional[float] = None
    timed_out: bool = False
    path_px: float = 0.0
    lost_s: float = 0.0
    lost_events: int = 0
    dwell_jitter_px: Optional[float] = None
    inside_since: Optional[float] = None
    inside_points: List[Tuple[float, float]] = field(default_factory=list)
    overshoots: int = 0
    beyond_target: bool = False


class TrackingTest:
    def __init__(self, monitor, count: int = 12, dwell: float = .5, timeout: float = 12.0,
                 seed: Optional[int] = None, margin: float = .08, hold_margin_px: float = 10.0,
                 targets: Optional[List[Target]] = None) -> None:
        self.monitor: Area = monitor  # left, top, width, height in screen px
        self.dwell = float(dwell)
        # Once on a target, a cursor this far past its edge still counts as holding, so
        # small targets stay reachable with some jitter (the jitter is still recorded).
        self.hold_margin_px = float(hold_margin_px)
        self.timeout = float(timeout)
        rng = random.Random(seed)
        self.targets: List[Target] = list(targets or ())
        prev = (.5, .5)
        for i in range(count if targets is None else 0):
            # Alternate far-ish jumps so every target needs a real movement
            for _ in range(50):
                fx, fy = rng.uniform(margin, 1 - margin), rng.uniform(margin, 1 - margin)
                if math.dist((fx, fy), prev) >= .3:
                    break
            prev = (fx, fy)
            self.targets.append(Target(fx, fy, SIZES_PX[i % len(SIZES_PX)]))
        self.index = 0
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.last_pos: Optional[Tuple[float, float]] = None
        self.last_t: Optional[float] = None
        self.hand_present = True
        self.paused = True  # control off: the dashboard tells the user to press Space
        self.frames = 0
        self.fps: List[float] = []
        self.detect_ms: List[float] = []
        self.saved: Optional[Path] = None
        self.result_text = ""

    # ------------------------------------------------------------------ geometry
    def center_px(self, t: Target) -> Tuple[float, float]:
        m = self.monitor
        return (m.left + t.fx * m.width, m.top + t.fy * m.height)

    def cursor_fraction(self, pos: Tuple[float, float]) -> Tuple[float, float]:
        m = self.monitor
        return ((pos[0] - m.left) / m.width, (pos[1] - m.top) / m.height)

    @property
    def current(self) -> Optional[Target]:
        return self.targets[self.index] if self.index < len(self.targets) else None

    @property
    def done(self) -> bool:
        return self.current is None

    # ------------------------------------------------------------------ per frame
    def update(self, now: float, pos: Tuple[float, float], *, hand: bool, active: bool,
               fps: float = 0.0, detect_ms: float = 0.0) -> None:
        """Feed one frame: cursor screen position, whether a hand is tracked, control on."""
        self.paused = not active
        if self.done or not active:
            self.last_t = None  # paused time is not counted
            return
        if self.started_at is None:
            self.started_at = now
        target = self.current
        if target.shown_at is None:
            target.shown_at = now
            target.start_pos = pos
            self.last_pos = pos
        dt = 0.0 if self.last_t is None else max(0.0, now - self.last_t)
        self.last_t = now
        self.frames += 1
        if fps:
            self.fps.append(float(fps))
        if hand and detect_ms:
            self.detect_ms.append(float(detect_ms))

        if not hand:
            if self.hand_present:
                target.lost_events += 1
            target.lost_s += dt
        self.hand_present = hand
        if self.last_pos is not None:
            target.path_px += math.dist(pos, self.last_pos)
        self.last_pos = pos

        cx, cy = self.center_px(target)
        radius = target.size_px / 2 + (self.hold_margin_px if target.inside_since is not None else 0.0)
        if hand and target.start_pos is not None:
            dx, dy = cx - target.start_pos[0], cy - target.start_pos[1]
            distance = math.hypot(dx, dy)
            if distance > radius:
                past = ((pos[0] - cx) * dx + (pos[1] - cy) * dy) / distance
                if past > radius and not target.beyond_target:
                    target.overshoots += 1
                target.beyond_target = past > radius
        inside = hand and math.dist(pos, (cx, cy)) <= radius
        if inside:
            if target.inside_since is None:
                target.inside_since = now
                target.inside_points = []
            target.inside_points.append(pos)
            if now - target.inside_since >= self.dwell:
                target.reached_at = now
                # Settled part only: the last half of the hold (entering a big target
                # while still sliding in is not jitter)
                settled = target.inside_points[len(target.inside_points) // 2:]
                target.dwell_jitter_px = _rms_spread(settled)
                self._next(now)
                return
        else:
            target.inside_since = None
        if now - target.shown_at >= self.timeout:
            target.timed_out = True
            self._next(now)

    def _next(self, now: float) -> None:
        self.index += 1
        if self.done:
            self.finished_at = now
        else:
            self.current.shown_at = None  # starts on the next frame, from the cursor there

    # ------------------------------------------------------------------ results
    def rows(self) -> List[Dict[str, Any]]:
        out = []
        for i, t in enumerate(self.targets, 1):
            if t.shown_at is None:
                continue
            cx, cy = self.center_px(t)
            distance = math.dist(t.start_pos, (cx, cy)) if t.start_pos else math.nan
            reach = (t.reached_at - t.shown_at - self.dwell) if t.reached_at is not None else None
            out.append({
                "target": i,
                "size_px": t.size_px,
                "distance_px": round(distance, 1),
                "reached": t.reached_at is not None,
                "time_to_reach_s": None if reach is None else round(max(0.0, reach), 3),
                "path_px": round(t.path_px, 1),
                "path_efficiency": (round(min(1.0, distance / t.path_px), 3)
                                    if t.path_px > 0 and math.isfinite(distance) else None),
                "dwell_jitter_px": None if t.dwell_jitter_px is None else round(t.dwell_jitter_px, 2),
                "lost_s": round(t.lost_s, 3),
                "lost_events": t.lost_events,
            })
        return out

    def summary(self) -> Dict[str, Any]:
        rows = self.rows()
        reached = [r for r in rows if r["reached"]]
        times = [r["time_to_reach_s"] for r in reached]
        jitter = [r["dwell_jitter_px"] for r in reached if r["dwell_jitter_px"] is not None]
        eff = [r["path_efficiency"] for r in rows if r["path_efficiency"] is not None]
        return {
            "targets": len(self.targets),
            "shown": len(rows),
            "reached": len(reached),
            "median_time_s": round(statistics.median(times), 3) if times else None,
            "mean_dwell_jitter_px": round(statistics.fmean(jitter), 2) if jitter else None,
            "median_path_efficiency": round(statistics.median(eff), 3) if eff else None,
            "lost_s": round(sum(r["lost_s"] for r in rows), 3),
            "lost_events": sum(r["lost_events"] for r in rows),
            "mean_fps": round(statistics.fmean(self.fps), 1) if self.fps else None,
            "mean_detect_ms": round(statistics.fmean(self.detect_ms), 1) if self.detect_ms else None,
            "duration_s": (round(self.finished_at - self.started_at, 2)
                           if self.finished_at is not None and self.started_at is not None else None),
        }

    def summary_text(self) -> str:
        s = self.summary()
        parts = [f"도달 {s['reached']}/{s['shown']}"]
        if s["median_time_s"] is not None:
            parts.append(f"중간 {s['median_time_s']:.2f}초")
        if s["mean_dwell_jitter_px"] is not None:
            parts.append(f"멈춤 떨림 {s['mean_dwell_jitter_px']:.1f}px")
        if s["median_path_efficiency"] is not None:
            parts.append(f"경로 효율 {s['median_path_efficiency']:.2f}")
        parts.append(f"끊김 {s['lost_events']}회 {s['lost_s']:.1f}초")
        return " · ".join(parts)

    def save_csv(self, directory: Path, settings: Dict[str, Any]) -> Path:
        """Write summary + settings, then one row per target. Returns the file path."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / time.strftime("tracking-%Y%m%d-%H%M%S.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for key, value in {**settings, **self.summary()}.items():
                w.writerow(["#", key, value])
            rows = self.rows()
            if rows:
                w.writerow(list(rows[0].keys()))
                for r in rows:
                    w.writerow(list(r.values()))
        return path


def view(test: TrackingTest, cursor: Tuple[float, float]) -> Dict[str, Any]:
    """Everything the test window shows, as plain data (the UI never holds the test itself)."""
    t = test.current
    return {
        "done": test.done,
        "paused": test.paused,
        "index": test.index,
        "total": len(test.targets),
        "target": None if t is None else (t.fx, t.fy, t.size_px / test.monitor.width),
        "holding": t is not None and t.inside_since is not None,
        "hand_present": test.hand_present,
        "cursor": test.cursor_fraction(cursor),
        "summary": (test.result_text or test.summary_text()) if test.done else "",
    }


def _rms_spread(points: List[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mx = statistics.fmean(p[0] for p in points)
    my = statistics.fmean(p[1] for p in points)
    return math.sqrt(statistics.fmean((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in points))


ANCHOR_NAMES = {"index": "검지 끝", "palm": "손바닥"}


def result_line(meta: Dict[str, Any], when: str = "") -> str:
    """One-line summary of a run (settings + results) for the dashboard's recent list."""
    def get(key):
        value = meta.get(key)
        return None if value in (None, "", "None") else value

    head = [ANCHOR_NAMES.get(str(get("anchor")), str(get("anchor") or "?"))]
    if get("sensitivity_pct") is not None:
        head.append(f"{get('sensitivity_pct')}%")
    if str(get("hand_crop")) == "True":
        head.append("확대")
    parts = [f"도달 {get('reached') or 0}/{get('shown') or 0}"]
    if get("median_time_s") is not None:
        parts.append(f"{float(get('median_time_s')):.2f}초")
    if get("mean_dwell_jitter_px") is not None:
        parts.append(f"떨림 {float(get('mean_dwell_jitter_px')):.1f}px")
    parts.append(f"끊김 {get('lost_events') or 0}회")
    prefix = f"{when}  " if when else ""
    return prefix + " · ".join(head) + " — " + " · ".join(parts)


def load_recent_results(directory: Path, limit: int = 5) -> List[str]:
    """Summary lines of the newest tracking-*.csv files (newest first); [] if none/unreadable."""
    try:
        files = sorted(Path(directory).glob("tracking-*.csv"), reverse=True)[:limit]
    except OSError:
        return []
    lines = []
    for path in files:
        try:
            with open(path, encoding="utf-8", newline="") as f:
                meta = {row[1]: row[2] for row in csv.reader(f) if len(row) >= 3 and row[0] == "#"}
        except (OSError, UnicodeDecodeError, csv.Error):
            continue
        stamp = path.stem.replace("tracking-", "")  # YYYYmmdd-HHMMSS
        when = f"{stamp[4:6]}/{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}" if len(stamp) >= 13 else ""
        lines.append(result_line(meta, when))
    return lines
