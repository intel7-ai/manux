"""Replay a recorded pointer-setup round through the cursor mapping and score it.

    cd apps/manux
    uv run --python 3.12 python tools/pointer_trace.py <pointer-fine-*.json> [--set palm_hold=0 --set smoothing=.5]

Traces are written to `<log folder>/pointer-traces/` when an aim or fine round of the cursor setup finishes.
They hold numbers only (no images), but they are still personal: keep them out of the repository.

A trace (written by `PointerSetup`) holds the raw palm input of every frame (hand X, wrist pitch), the cursor
the user saw, and which target was shown. Replaying the same input with other cursor settings shows what
those settings would have done. It is open loop: the user's hand reacted to the recorded cursor, not to the
replayed one, so a replay predicts direction and size of a change, not the exact result.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import fields, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.cursor import CursorMapper
from manux.platform.monitors import Monitor, ScreenLayout

CONFIG_KEYS = {f.name for f in fields(WindowsOutputConfig)}


def load(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("frames"), list):
        raise ValueError(f"not a pointer trace: {path}")
    return data


def replay(trace: dict, overrides: Optional[Dict[str, float]] = None, guard: bool = False,
           knuckles: Optional[Tuple[int, ...]] = None) -> List[Tuple[int, int]]:
    """Cursor positions the given settings produce for the recorded input (one per frame).

    guard: feed the recorded click contact (7th column) so the cursor holds while a click's fingers touch.
    knuckles: recompute the pitch from the recorded joints (8th column) with these finger roots instead,
    rescaled onto the recorded pitch (least squares) so the calibrated mapping still fits."""
    left, top, width, height = trace["monitor"]
    layout = ScreenLayout([Monitor(left, top, left + width, top + height, primary=True)])
    values = {k: v for k, v in {**trace["values"], **(overrides or {})}.items() if k in CONFIG_KEYS}
    mapper = CursorMapper(layout, replace(WindowsOutputConfig(), **values))
    pitches = alt_pitches(trace, knuckles) if knuckles else [row[2] for row in trace["frames"]]
    out = []
    for row, pitch in zip(trace["frames"], pitches):
        t, x = row[0], row[1]
        contact = row[6] if guard and len(row) > 6 and trace.get("pinch") == "click_ratio" else None
        if x is None:
            mapper.pause()
        else:
            mapper.step_palm(x, pitch, t, contact=contact)
        out.append(tuple(mapper.pos))
    return out


def alt_pitches(trace: dict, knuckles: Tuple[int, ...]) -> List[Optional[float]]:
    """Pitch from other finger roots, mapped onto the recorded pitch's scale (needs the 8th joints column)."""
    from manux.perception.palm_pitch import palm_pitch
    order = (0, 5, 9, 13, 17)
    raw = []
    for row in trace["frames"]:
        joints = row[7] if len(row) > 7 else None
        if joints is None or row[2] is None:
            raw.append(None)
            continue
        world = {j: joints[3 * k:3 * k + 3] for k, j in enumerate(order)}
        raw.append(palm_pitch(world, knuckles))
    pairs = [(a, row[2]) for a, row in zip(raw, trace["frames"]) if a is not None and row[2] is not None]
    if len(pairs) < 10:
        raise ValueError("this trace has no joint column: record it again with the current app")
    ma = statistics.fmean(a for a, _ in pairs)
    mb = statistics.fmean(b for _, b in pairs)
    var = sum((a - ma) ** 2 for a, _ in pairs) or 1e-9
    slope = sum((a - ma) * (b - mb) for a, b in pairs) / var
    return [None if a is None else mb + slope * (a - ma) for a in raw]


def recorded(trace: dict) -> List[Tuple[int, int]]:
    return [(row[3], row[4]) for row in trace["frames"]]


def score(trace: dict, positions: Iterable[Tuple[float, float]]) -> dict:
    """Per-target reach time, overshoots and settled jitter for one cursor path over the recorded schedule."""
    left, top, width, height = trace["monitor"]
    dwell = float(trace.get("dwell", .3))
    frames = trace["frames"]
    segments: Dict[int, List[Tuple[float, Tuple[float, float], bool]]] = {}
    for row, pos in zip(frames, positions):
        segments.setdefault(row[5], []).append((row[0], pos, row[1] is not None))
    clicks = click_times(trace)
    rows = []
    for index, points in sorted(segments.items()):
        if index >= len(trace["targets"]):
            continue
        target = trace["targets"][index]
        center = (left + target["fx"] * width, top + target["fy"] * height)
        radius = target["size_px"] / 2
        start = points[0][1]
        dx, dy = center[0] - start[0], center[1] - start[1]
        distance = math.hypot(dx, dy)
        reached_at, inside_since, overshoots, beyond, peak = None, None, 0, False, 0.0
        for t, pos, hand in points:
            if distance > radius:
                past = ((pos[0] - center[0]) * dx + (pos[1] - center[1]) * dy) / distance
                if past > radius and not beyond:
                    overshoots += 1
                beyond = past > radius
                peak = max(peak, past - radius)
            if hand and math.dist(pos, center) <= radius:
                inside_since = t if inside_since is None else inside_since
                if reached_at is None and t - inside_since >= dwell:
                    reached_at = t
            else:
                inside_since = None
        settled = [p for t, p, _ in points if t >= points[-1][0] - .3]
        rows.append({"target": index, "reached": reached_at is not None,
                     "time_s": None if reached_at is None else round(reached_at - points[0][0] - dwell, 3),
                     "overshoots": overshoots, "overshoot_px": round(peak, 1),
                     "jitter_px": round(_spread(settled), 2), "wobble_px": round(_peak(settled), 1),
                     "clicks": sum(points[0][0] <= t <= points[-1][0] for t in clicks)})
    reached = [r for r in rows if r["reached"]]
    return {
        "targets": len(rows),
        "reached": len(reached),
        "median_time_s": round(statistics.median(r["time_s"] for r in reached), 3) if reached else None,
        "overshoots": sum(r["overshoots"] for r in rows),
        "max_overshoot_px": max((r["overshoot_px"] for r in rows), default=0.0),
        "mean_jitter_px": round(statistics.fmean(r["jitter_px"] for r in rows), 2) if rows else None,
        "max_wobble_px": max((r["wobble_px"] for r in rows), default=0.0),
        "rows": rows,
    }


CLICK_CLOSE = 1.0    # 7th column: thumb-to-middle/ring tip distance over the pinch feature's click threshold
CLICK_OPEN = 1.35    # must open past this before the next click counts


def click_times(trace: dict) -> List[float]:
    """Times a click closed (thumb to middle or ring tip). Older traces recorded thumb-index: no clicks."""
    if trace.get("pinch") != "click_ratio":
        return []
    out, armed = [], True
    for row in trace["frames"]:
        pinch = row[6] if len(row) > 6 else None
        if pinch is None:
            continue
        if armed and pinch < CLICK_CLOSE:
            out.append(row[0])
            armed = False
        elif pinch > CLICK_OPEN:
            armed = True
    return out


def click_shifts(trace: dict, positions, aim_before: float = .4, rollback: float = .15) -> List[dict]:
    """Cursor shift at each click: from where it was `aim_before` s earlier (the aim) to where the click lands,
    without and with the sink's rollback (click lands where the cursor was `rollback` s earlier)."""
    times = [row[0] for row in trace["frames"]]
    positions = list(positions)

    def at(t):
        i = max(0, next((k for k in range(len(times) - 1, -1, -1) if times[k] <= t), 0))
        return positions[i]
    out = []
    for t in click_times(trace):
        aim = at(t - aim_before)
        out.append({"t": round(t, 2), "shift_px": round(math.dist(aim, at(t)), 1),
                    "with_rollback_px": round(math.dist(aim, at(t - rollback)), 1)})
    return out


def _peak(points: List[Tuple[float, float]]) -> float:
    """Largest distance from the settled mean: what decides whether a 20 px menu row stays under the cursor."""
    if len(points) < 2:
        return 0.0
    mx = statistics.fmean(p[0] for p in points)
    my = statistics.fmean(p[1] for p in points)
    return max(math.hypot(p[0] - mx, p[1] - my) for p in points)


def _spread(points: List[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mx = statistics.fmean(p[0] for p in points)
    my = statistics.fmean(p[1] for p in points)
    return math.sqrt(statistics.fmean((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in points))


def _parse_set(items):
    out = {}
    for item in items or ():
        key, _, value = item.partition("=")
        if key not in CONFIG_KEYS:
            raise SystemExit(f"unknown cursor setting: {key}")
        out[key] = float(value)
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="cursor setting to try (repeatable)")
    ap.add_argument("--rows", action="store_true", help="also print every target")
    ap.add_argument("--compare", action="store_true",
                    help="also replay with the click guard and with pitch from index+pinky roots only")
    args = ap.parse_args(argv)
    overrides = _parse_set(args.set)
    for path in args.files:
        trace = load(path)
        runs = [("recorded", recorded(trace)), ("replay", replay(trace))]
        if overrides:
            runs.append(("with " + " ".join(f"{k}={v:g}" for k, v in overrides.items()), replay(trace, overrides)))
        if args.compare and trace.get("pinch") == "click_ratio":
            runs.append(("click guard", replay(trace, overrides, guard=True)))
            try:
                runs.append(("index+pinky pitch", replay(trace, overrides, knuckles=(5, 17))))
                runs.append(("index+pinky + guard", replay(trace, overrides, guard=True, knuckles=(5, 17))))
            except ValueError as exc:
                print(f"  ({exc})")
        print(f"{path.name}  ({trace['kind']}, {len(trace['frames'])} frames)")
        for name, positions in runs:
            s = score(trace, positions)
            print(f"  {name:<28} reached {s['reached']}/{s['targets']}  median {s['median_time_s']}s  "
                  f"overshoots {s['overshoots']} (max {s['max_overshoot_px']}px)  jitter {s['mean_jitter_px']}px")
            shifts = click_shifts(trace, positions)
            if shifts:
                med = statistics.median
                print(f"      clicks {len(shifts)}: shift median {med(s['shift_px'] for s in shifts)}px "
                      f"max {max(s['shift_px'] for s in shifts)}px | with 0.15 s rollback median "
                      f"{med(s['with_rollback_px'] for s in shifts)}px max {max(s['with_rollback_px'] for s in shifts)}px")
            if args.rows:
                for r in s["rows"]:
                    print(f"      #{r['target']:<2} {'ok ' if r['reached'] else 'MISS'} {r['time_s']}s  "
                          f"over {r['overshoots']}/{r['overshoot_px']}px  jitter {r['jitter_px']}px"
                          + (f"  clicks {r['clicks']}" if r["clicks"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
