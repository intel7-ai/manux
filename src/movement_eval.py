"""Offline Movement & Return-Stroke Evaluation Harness (`src/movement_eval.py`).

Replays existing recordings (`recordings/*.jsonl.gz`), deduplicated failure logs
(`field-logs/**/*.json`), and regression fixtures (`tests/fixtures/*.json`) through
`GestureEngine` and `MouseController(dry_run=True)` to quantify:
1. Stationary hand (`hand_speed < 0.05` frame widths/s) multiplier / gain stability:
   - `still_mult_within_seg_std_mean`: mean within-stationary-segment standard deviation
   - `still_mult_global_std`: global standard deviation across stationary frames
   - `still_mult_max_slew_per_s` & `still_mult_p95_slew_per_s`: `|Δmult| / dt`
2. Per-frame cursor step distribution (`step_px_mean`, `step_px_median`, `step_px_p90`,
   `step_px_p99`, `step_px_max`) and `cursor_max_step` cap trigger rate.
3. Hand-shape mode transition rate (`mode_switches_per_min`) and `< 0.5s` round-trip
   mode flicker rate (`flickers_per_min`).
4. Half-fist / fist moving (`speed > 0.15`) pinch/click/drag/scroll false fire count
   (`halffist_moving_frames`, `halffist_misfires`).
5. Return-stroke detection and drift quantification:
   - Return stroke count and frequency (`return_strokes_per_moving_min`, `return_strokes_per_total_min`)
   - Wrong-way cursor travel during return strokes (`return_cursor_path_px_total`,
     `return_cursor_path_px_mean`, `return_cursor_path_px_p90`, `return_cursor_opp_px_mean`)
   - Return duration (`return_duration_s_total`, `return_duration_s_mean`)
   - Cursor-to-hand ratio (`return_cursor_to_hand_ratio_mean`)
   - Clutch / return-suppression state coverage (`clutch_active_frames`, `return_suppressed_frames`)
6. Forward-stroke travel efficiency (`forward_strokes_count`, `forward_cursor_px_mean`,
   `forward_hand_dist_mean`, `forward_cursor_to_hand_ratio_mean`).

Note on interpretation:
All replay metrics come from 1 user / 1 phone camera logs (`recordings`, `field-logs`, `fixtures`).
They are relative before/after comparisons on identical recorded trajectories, not multi-user
ground-truth accuracy.
"""

from __future__ import annotations

import glob
import gzip
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.gesture_engine import GestureEngine, Point3D
from src.mouse_controller import MouseController

ROOT = Path(__file__).resolve().parents[1]

DATASET_LIMITATION_NOTE = (
    "1-user, 1-phone-camera offline log replay (recordings: 20 sessions / 155,077 frames, "
    "field-logs deduplicated: 105 segments / 18,904 frames, fixtures: 3 files / 254 frames). "
    "Values represent relative before/after comparisons on the exact same landmark trajectories, "
    "not multi-user ground-truth pointing accuracy."
)


def load_config(config_path: Optional[Path] = None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    path = config_path or (ROOT / "config" / "gestures.json")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if overrides:
        cfg.update(overrides)
    return cfg


def extract_sequences(root: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Load frame sequences from recordings, deduplicated field-logs, raw field-log windows, and fixtures."""
    base = root or ROOT
    sequences: Dict[str, List[Dict[str, Any]]] = {
        "recordings": [],
        "field_logs_dedup": [],
        "field_logs_windows": [],
        "fixtures": [],
    }

    # 1. Recordings (20 files)
    for path_str in sorted(glob.glob(str(base / "recordings" / "*.jsonl.gz"))):
        p = Path(path_str)
        frames: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {}
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                if obj.get("type") == "meta":
                    meta = obj
                elif obj.get("type") == "frame":
                    frames.append({
                        "t": float(obj["t"]),
                        "lm": obj.get("lm"),
                        "label": (obj.get("label") or {}).get("target", "none"),
                        "logged_rule": obj.get("rule"),
                        "logged_cursor": obj.get("cursor"),
                    })
        if frames:
            cam_mode = (meta.get("camera") or {}).get("mode") or [1280, 720]
            aspect = float(cam_mode[1]) / max(1.0, float(cam_mode[0])) if len(cam_mode) >= 2 else 0.5625
            sequences["recordings"].append({
                "name": p.name,
                "source": "recordings",
                "aspect": aspect,
                "frames": frames,
            })

    # 2. Field-logs (failure windows, deduplicated by absolute timestamp)
    all_fail_frames: Dict[float, Dict[str, Any]] = {}
    fail_aspect = 720.0 / 1280.0
    for path_str in sorted(glob.glob(str(base / "field-logs" / "**" / "*.json"), recursive=True)):
        p = Path(path_str)
        obj = json.loads(p.read_text(encoding="utf-8"))
        trig_t = float(obj.get("trigger_t", 0.0))
        meta = obj.get("meta") or {}
        fpx = meta.get("frame_px") or [1280, 720]
        aspect = float(fpx[1]) / max(1.0, float(fpx[0]))
        fail_aspect = aspect
        win_frames: List[Dict[str, Any]] = []
        for f in obj.get("frames", []):
            rel_t = float(f["t"])
            abs_t = round(trig_t + rel_t, 4)
            rec = {
                "t": abs_t,
                "lm": f.get("lm") if f.get("hand") else None,
                "label": "none",
                "logged_cursor": f.get("cursor"),
                "logged_gest": f.get("gest"),
                "paused": bool(f.get("paused", False)),
                "mode": f.get("mode", "palm"),
            }
            win_frames.append({**rec, "t": rel_t})
            if abs_t not in all_fail_frames:
                all_fail_frames[abs_t] = rec
        if win_frames:
            sequences["field_logs_windows"].append({
                "name": p.name,
                "source": "field_logs_windows",
                "aspect": aspect,
                "frames": win_frames,
            })

    sorted_abs = [all_fail_frames[k] for k in sorted(all_fail_frames.keys())]
    if sorted_abs:
        seg: List[Dict[str, Any]] = [sorted_abs[0]]
        seg_idx = 0
        for fr in sorted_abs[1:]:
            if fr["t"] - seg[-1]["t"] > 1.0:
                t0 = seg[0]["t"]
                sequences["field_logs_dedup"].append({
                    "name": f"field_seg_{seg_idx:03d}",
                    "source": "field_logs_dedup",
                    "aspect": fail_aspect,
                    "frames": [{**x, "t": round(x["t"] - t0, 4)} for x in seg],
                })
                seg_idx += 1
                seg = [fr]
            else:
                seg.append(fr)
        if seg:
            t0 = seg[0]["t"]
            sequences["field_logs_dedup"].append({
                "name": f"field_seg_{seg_idx:03d}",
                "source": "field_logs_dedup",
                "aspect": fail_aspect,
                "frames": [{**x, "t": round(x["t"] - t0, 4)} for x in seg],
            })

    # 3. Fixtures
    for path_str in sorted(glob.glob(str(base / "tests" / "fixtures" / "*.json"))):
        p = Path(path_str)
        obj = json.loads(p.read_text(encoding="utf-8"))
        fpx = obj.get("frame_px") or [1280, 720]
        aspect = float(fpx[1]) / max(1.0, float(fpx[0]))
        frames = []
        for item in obj.get("frames", []):
            frames.append({
                "t": float(item[0]),
                "lm": item[1],
                "label": "none",
            })
        if frames:
            sequences["fixtures"].append({
                "name": p.name,
                "source": "fixtures",
                "aspect": aspect,
                "frames": frames,
            })

    return sequences


def replay_sequence(seq: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Replay a single sequence through GestureEngine and MouseController(dry_run=True)."""
    engine = GestureEngine()
    engine.config = dict(cfg)
    engine.frame_aspect = seq["aspect"]
    mouse = MouseController(dry_run=True, screen_size=(1920, 1080))
    mouse.frame_aspect = seq["aspect"]

    trace: List[Dict[str, Any]] = []
    prev_t: Optional[float] = None
    prev_hand: Optional[Tuple[float, float]] = None
    prev_cursor: Tuple[int, int] = mouse.last_screen_pos

    for f in seq["frames"]:
        t = float(f["t"])
        lm = f.get("lm")
        if f.get("paused", False):
            prev_t = t
            continue
        dt = 1.0 / 30.0 if prev_t is None else max(1e-3, min(0.2, t - prev_t))
        prev_t = t

        if lm is None or len(lm) < 21:
            engine.process(None, timestamp=t)
            cpos = mouse.hand_lost(cfg, now=t)
            prev_hand = None
            prev_cursor = cpos
            trace.append({
                "t": t,
                "dt": dt,
                "has_hand": False,
                "action": "none",
                "gesture": "NONE",
                "is_moving": False,
                "hand_shape": "none",
                "dpi_multiplier": 1.0,
                "curl": None,
                "raw_curl": None,
                "hand_size": None,
                "hand_pt": None,
                "hand_dx": 0.0,
                "hand_dy": 0.0,
                "hand_speed": 0.0,
                "cursor": cpos,
                "cursor_dx": 0.0,
                "cursor_dy": 0.0,
                "cursor_step": 0.0,
                "capped": False,
                "carry_mag": 0.0,
                "clutch_state": getattr(mouse, "clutch_state", "inactive"),
                "return_state": getattr(mouse, "return_state", "none"),
                "label": f.get("label", "none"),
            })
            continue

        pts = [Point3D.from_any(pt) for pt in lm[:21]]
        hand_size = max(
            engine._dist_2d(pts[0], pts[5]),
            engine._dist_2d(pts[0], pts[9]),
            engine._dist_2d(pts[0], pts[17]),
            engine._dist_2d(pts[5], pts[17]) * 1.4,
        )
        ratios = engine.finger_curl_ratios(pts)
        mean_curl = sum(ratios) / 4.0

        res = engine.process(lm, timestamp=t)
        hpt = res.cursor_point if res.cursor_point is not None else engine.palm_anchor_point(pts)

        if prev_hand is not None and hpt is not None:
            hdx = float(hpt[0]) - prev_hand[0]
            hdy = (float(hpt[1]) - prev_hand[1]) * seq["aspect"]
            hspeed = math.hypot(hdx, hdy) / dt
        else:
            hdx, hdy, hspeed = 0.0, 0.0, 0.0
        prev_hand = (float(hpt[0]), float(hpt[1])) if hpt is not None else None

        cpos = mouse.apply(res, cfg, now=t)
        carry_after = mouse._rel_carry
        cdx = float(cpos[0] - prev_cursor[0])
        cdy = float(cpos[1] - prev_cursor[1])
        cstep = math.hypot(cdx, cdy)
        capped = math.hypot(*carry_after) > 1e-3 or cstep >= float(cfg.get("cursor_max_step", 0.12)) * 1920.0 - 1.0
        prev_cursor = cpos

        trace.append({
            "t": t,
            "dt": dt,
            "has_hand": True,
            "action": res.action,
            "gesture": res.active_gesture,
            "is_moving": bool(res.is_moving),
            "hand_shape": getattr(res, "hand_shape", "") or "",
            "curled": bool(res.active_gesture in ("no_pinch", "fist") and float(res.dpi_multiplier) > 1.15),
            "dpi_multiplier": float(mouse.dpi_multiplier),
            "curl": float(engine._curl_s) if engine._curl_s is not None else mean_curl,
            "raw_curl": mean_curl,
            "hand_size": float(hand_size),
            "hand_pt": (float(hpt[0]), float(hpt[1]) * seq["aspect"]) if hpt is not None else None,
            "hand_dx": hdx,
            "hand_dy": hdy,
            "hand_speed": hspeed,
            "cursor": cpos,
            "cursor_dx": cdx,
            "cursor_dy": cdy,
            "cursor_step": cstep,
            "capped": bool(capped),
            "carry_mag": math.hypot(*carry_after),
            "clutch_state": getattr(mouse, "clutch_state", "inactive"),
            "return_state": getattr(mouse, "return_state", "none"),
            "label": f.get("label", "none"),
        })
    return trace


def segment_strokes_and_returns(trace: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Segment active cursor movement into directional sub-strokes and pair forward->return strokes."""
    n = len(trace)
    forward_strokes: List[Dict[str, Any]] = []
    return_events: List[Dict[str, Any]] = []

    valid_pts = [f["hand_pt"] for f in trace if f.get("hand_pt") is not None and f.get("is_moving")]
    valid_sizes = [f["hand_size"] for f in trace if f.get("hand_size") is not None and f.get("is_moving")]
    if len(valid_pts) < 10:
        return forward_strokes, return_events
    center_x = statistics.median(p[0] for p in valid_pts)
    center_y = statistics.median(p[1] for p in valid_pts)
    med_size = statistics.median(valid_sizes) if valid_sizes else 0.12

    strokes: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        f = trace[i]
        if not (f["has_hand"] and f["action"] == "move_cursor" and f["hand_speed"] >= 0.04):
            i += 1
            continue
        start_idx = i
        end_idx = i
        sx, sy = f["hand_pt"]
        peak_speed = f["hand_speed"]
        j = i + 1
        while j < n:
            fj = trace[j]
            if not (fj["has_hand"] and fj["action"] == "move_cursor"):
                break
            if fj["t"] - trace[end_idx]["t"] > 0.25:
                break
            if fj["hand_speed"] < 0.03:
                if j + 1 >= n or trace[j + 1]["hand_speed"] < 0.04:
                    break
            cur_dx = fj["hand_pt"][0] - sx
            cur_dy = fj["hand_pt"][1] - sy
            cur_dist = math.hypot(cur_dx, cur_dy)
            step_dist = math.hypot(fj["hand_dx"], fj["hand_dy"])
            if cur_dist >= 0.025 and step_dist > 0.002:
                dot = (cur_dx * fj["hand_dx"] + cur_dy * fj["hand_dy"]) / (cur_dist * step_dist)
                if dot < -0.25:  # > ~105 deg reversal
                    break
            end_idx = j
            peak_speed = max(peak_speed, fj["hand_speed"])
            j += 1

        ex, ey = trace[end_idx]["hand_pt"]
        net_dx, net_dy = ex - sx, ey - sy
        net_dist = math.hypot(net_dx, net_dy)
        dur = max(1e-3, trace[end_idx]["t"] - trace[start_idx]["t"] + trace[start_idx]["dt"])
        if net_dist >= 0.025 and (end_idx - start_idx + 1) >= 2:
            c_start = trace[max(0, start_idx - 1)]["cursor"]
            c_end = trace[end_idx]["cursor"]
            c_net = math.hypot(float(c_end[0] - c_start[0]), float(c_end[1] - c_start[1]))
            strokes.append({
                "start_idx": start_idx,
                "end_idx": end_idx,
                "t0": trace[start_idx]["t"],
                "t1": trace[end_idx]["t"],
                "dur": dur,
                "start_pt": (sx, sy),
                "end_pt": (ex, ey),
                "net_dx": net_dx,
                "net_dy": net_dy,
                "net_dist": net_dist,
                "peak_speed": peak_speed,
                "mean_speed": net_dist / dur,
                "angle": math.atan2(net_dy, net_dx),
                "cursor_net_px": c_net,
            })
        i = max(i + 1, end_idx + 1)

    used_return = set()
    for k in range(len(strokes)):
        s_fwd = strokes[k]
        if k in used_return:
            continue
        if s_fwd["net_dist"] >= 0.035 and s_fwd["peak_speed"] >= 0.18:
            forward_strokes.append(s_fwd)
        if k + 1 >= len(strokes):
            continue
        if s_fwd["net_dist"] < 0.035 or s_fwd["peak_speed"] < 0.18:
            continue
        s_ret = strokes[k + 1]
        gap = s_ret["t0"] - s_fwd["t1"]
        if gap < 0.0 or gap > 0.65:
            continue
        if s_ret["net_dist"] < 0.030:
            continue

        ux, uy = s_fwd["net_dx"] / s_fwd["net_dist"], s_fwd["net_dy"] / s_fwd["net_dist"]
        rx, ry = s_ret["net_dx"] / s_ret["net_dist"], s_ret["net_dy"] / s_ret["net_dist"]
        cos_ang = max(-1.0, min(1.0, ux * rx + uy * ry))
        ang_deg = math.degrees(math.acos(cos_ang))

        d_start_before = math.hypot(
            s_ret["start_pt"][0] - s_fwd["start_pt"][0],
            s_ret["start_pt"][1] - s_fwd["start_pt"][1],
        )
        d_start_after = math.hypot(
            s_ret["end_pt"][0] - s_fwd["start_pt"][0],
            s_ret["end_pt"][1] - s_fwd["start_pt"][1],
        )
        d_center_before = math.hypot(s_ret["start_pt"][0] - center_x, s_ret["start_pt"][1] - center_y)
        d_center_after = math.hypot(s_ret["end_pt"][0] - center_x, s_ret["end_pt"][1] - center_y)

        toward_origin_or_center = (d_start_after < d_start_before) or (d_center_after < d_center_before)
        if ang_deg >= 105.0 and toward_origin_or_center:
            used_return.add(k + 1)
            ret_frames = trace[s_ret["start_idx"] : s_ret["end_idx"] + 1]
            fwd_frames = trace[s_fwd["start_idx"] : s_fwd["end_idx"] + 1]

            cursor_path_px = sum(f["cursor_step"] for f in ret_frames)
            c_start = trace[max(0, s_ret["start_idx"] - 1)]["cursor"]
            c_end = trace[s_ret["end_idx"]]["cursor"]
            c_net_dx = float(c_end[0] - c_start[0])
            c_net_dy = float(c_end[1] - c_start[1])
            c_net_px = math.hypot(c_net_dx, c_net_dy)

            fc_start = trace[max(0, s_fwd["start_idx"] - 1)]["cursor"]
            fc_end = trace[s_fwd["end_idx"]]["cursor"]
            fc_dx, fc_dy = float(fc_end[0] - fc_start[0]), float(fc_end[1] - fc_start[1])
            fc_mag = math.hypot(fc_dx, fc_dy)
            if fc_mag > 1.0:
                opp_px = max(0.0, -(c_net_dx * fc_dx + c_net_dy * fc_dy) / fc_mag)
            else:
                opp_px = c_net_px

            ret_sizes = [f["hand_size"] / med_size for f in ret_frames if f.get("hand_size")]
            fwd_sizes = [f["hand_size"] / med_size for f in fwd_frames if f.get("hand_size")]
            ret_curls = [f["curl"] for f in ret_frames if f.get("curl") is not None]
            ret_mults = [f["dpi_multiplier"] for f in ret_frames]
            suppressed_in_ret = sum(
                1 for f in ret_frames
                if f.get("clutch_state") == "active" or f.get("return_state") in ("pending", "confirmed")
            )

            return_events.append({
                "fwd_dist": s_fwd["net_dist"],
                "fwd_peak_speed": s_fwd["peak_speed"],
                "fwd_cursor_px": fc_mag,
                "pause_s": gap,
                "ret_dur_s": s_ret["dur"],
                "ret_hand_dist": s_ret["net_dist"],
                "ret_peak_speed": s_ret["peak_speed"],
                "ret_mean_speed": s_ret["mean_speed"],
                "speed_ratio": s_ret["mean_speed"] / max(1e-6, s_fwd["peak_speed"]),
                "angle_deg": ang_deg,
                "cursor_path_px": cursor_path_px,
                "cursor_net_px": c_net_px,
                "cursor_opp_px": opp_px,
                "cursor_to_hand_ratio": cursor_path_px / max(1.0, s_ret["net_dist"] * 1920.0),
                "ret_size_ratio_mean": statistics.mean(ret_sizes) if ret_sizes else 1.0,
                "ret_size_ratio_min": min(ret_sizes) if ret_sizes else 1.0,
                "fwd_size_ratio_mean": statistics.mean(fwd_sizes) if fwd_sizes else 1.0,
                "ret_curl_mean": statistics.mean(ret_curls) if ret_curls else 1.5,
                "ret_mult_mean": statistics.mean(ret_mults) if ret_mults else 1.0,
                "ret_frames": len(ret_frames),
                "ret_suppressed_frames": suppressed_in_ret,
            })

    return forward_strokes, return_events


def detect_return_strokes(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Backwards-compatible helper returning only the detected return-stroke events."""
    _, return_events = segment_strokes_and_returns(trace)
    return return_events


def _pct(arr: Sequence[float], q: float) -> float:
    if not arr:
        return 0.0
    s = sorted(arr)
    k = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return round(float(s[k]), 3)


def summarize_group(name: str, seqs: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    total_frames = 0
    hand_frames = 0
    moving_frames = 0
    total_duration_s = 0.0
    moving_duration_s = 0.0

    still_mults: List[float] = []
    still_slews: List[float] = []
    within_still_stds: List[float] = []

    steps_px: List[float] = []
    capped_count = 0
    over_300_count = 0

    mode_switches = 0
    flickers_05s = 0
    halffist_moving_frames = 0
    halffist_misfires = 0

    clutch_active_frames = 0
    return_suppressed_frames = 0

    all_forward_strokes: List[Dict[str, Any]] = []
    all_return_events: List[Dict[str, Any]] = []

    for seq in seqs:
        trace = replay_sequence(seq, cfg)
        if not trace:
            continue
        dur = max(0.0, trace[-1]["t"] - trace[0]["t"])
        total_duration_s += dur
        total_frames += len(trace)

        cur_still: List[float] = []
        prev_m: Optional[float] = None
        for idx, f in enumerate(trace):
            if f["has_hand"]:
                hand_frames += 1
            if f.get("clutch_state") == "active":
                clutch_active_frames += 1
            if f.get("return_state") in ("pending", "confirmed"):
                return_suppressed_frames += 1

            if f["is_moving"]:
                moving_frames += 1
                moving_duration_s += f["dt"]
                steps_px.append(f["cursor_step"])
                if f["capped"]:
                    capped_count += 1
                if f["cursor_step"] > 300.0:
                    over_300_count += 1

                if f["hand_speed"] < 0.05:
                    still_mults.append(f["dpi_multiplier"])
                    cur_still.append(f["dpi_multiplier"])
                    if prev_m is not None:
                        still_slews.append(abs(f["dpi_multiplier"] - prev_m) / f["dt"])
                else:
                    if len(cur_still) >= 3:
                        within_still_stds.append(statistics.pstdev(cur_still))
                    cur_still = []
                prev_m = f["dpi_multiplier"]
            else:
                if len(cur_still) >= 3:
                    within_still_stds.append(statistics.pstdev(cur_still))
                cur_still = []
                prev_m = None

            if idx > 0:
                pf = trace[idx - 1]
                if pf["has_hand"] and pf.get("curled", pf["hand_shape"] in ("half", "fist")) and pf["hand_speed"] > 0.15:
                    halffist_moving_frames += 1
                    if (
                        f["action"] in ("left_click", "right_click", "drag_hold", "scroll_grab", "task_switcher")
                        and f["label"] in ("none", "free", "idle")
                    ):
                        halffist_misfires += 1

        if len(cur_still) >= 3:
            within_still_stds.append(statistics.pstdev(cur_still))

        shapes = [
            (f["t"], f["hand_shape"])
            for f in trace
            if f["is_moving"] and f["hand_shape"] in ("open", "half", "fist", "precise", "normal", "fast")
        ]
        transitions: List[Tuple[float, str, str]] = []
        for (t0, s0), (t1, s1) in zip(shapes, shapes[1:]):
            if s0 != s1:
                mode_switches += 1
                if transitions and transitions[-1][1] == s1 and (t1 - transitions[-1][0]) <= 0.5:
                    flickers_05s += 1
                transitions.append((t1, s0, s1))

        fwd_st, ret_ev = segment_strokes_and_returns(trace)
        all_forward_strokes.extend(fwd_st)
        all_return_events.extend(ret_ev)

    mins = max(1e-6, total_duration_s / 60.0)
    mov_mins = max(1e-6, moving_duration_s / 60.0)
    ret_total_frames = sum(e.get("ret_frames", 0) for e in all_return_events)
    ret_supp_frames = sum(e.get("ret_suppressed_frames", 0) for e in all_return_events)

    return {
        "group": name,
        "num_sequences": len(seqs),
        "total_frames": total_frames,
        "hand_frames": hand_frames,
        "moving_frames": moving_frames,
        "total_duration_s": round(total_duration_s, 1),
        "moving_duration_s": round(moving_duration_s, 1),
        "still_frames": len(still_mults),
        "still_mult_global_std": round(statistics.pstdev(still_mults), 3) if len(still_mults) >= 2 else 0.0,
        "still_mult_within_seg_std_mean": round(statistics.mean(within_still_stds), 3) if within_still_stds else 0.0,
        "still_mult_max_slew_per_s": round(max(still_slews), 2) if still_slews else 0.0,
        "still_mult_p95_slew_per_s": _pct(still_slews, 0.95),
        "step_px_mean": round(statistics.mean(steps_px), 2) if steps_px else 0.0,
        "step_px_median": _pct(steps_px, 0.50),
        "step_px_p90": _pct(steps_px, 0.90),
        "step_px_p99": _pct(steps_px, 0.99),
        "step_px_max": round(max(steps_px), 2) if steps_px else 0.0,
        "cursor_max_step_triggers": capped_count,
        "cursor_max_step_pct": round(100.0 * capped_count / max(1, moving_frames), 3),
        "mode_switches": mode_switches,
        "mode_switches_per_min": round(mode_switches / mov_mins, 2),
        "flickers_05s": flickers_05s,
        "flickers_per_min": round(flickers_05s / mov_mins, 2),
        "halffist_moving_frames": halffist_moving_frames,
        "halffist_misfires": halffist_misfires,
        "forward_strokes_count": len(all_forward_strokes),
        "forward_cursor_px_mean": (
            round(statistics.mean(s["cursor_net_px"] for s in all_forward_strokes), 1)
            if all_forward_strokes else 0.0
        ),
        "forward_hand_dist_mean": (
            round(statistics.mean(s["net_dist"] for s in all_forward_strokes), 4)
            if all_forward_strokes else 0.0
        ),
        "forward_cursor_to_hand_ratio_mean": (
            round(
                statistics.mean(
                    s["cursor_net_px"] / max(1.0, s["net_dist"] * 1920.0) for s in all_forward_strokes
                ),
                2,
            )
            if all_forward_strokes else 0.0
        ),
        "return_strokes_count": len(all_return_events),
        "return_strokes_per_total_min": round(len(all_return_events) / mins, 2),
        "return_strokes_per_moving_min": round(len(all_return_events) / mov_mins, 2),
        "return_cursor_path_px_total": round(sum(e["cursor_path_px"] for e in all_return_events), 1),
        "return_cursor_path_px_mean": (
            round(statistics.mean(e["cursor_path_px"] for e in all_return_events), 1)
            if all_return_events else 0.0
        ),
        "return_cursor_path_px_p90": _pct([e["cursor_path_px"] for e in all_return_events], 0.90),
        "return_cursor_opp_px_mean": (
            round(statistics.mean(e["cursor_opp_px"] for e in all_return_events), 1)
            if all_return_events else 0.0
        ),
        "return_duration_s_total": round(sum(e["ret_dur_s"] for e in all_return_events), 2),
        "return_duration_s_mean": (
            round(statistics.mean(e["ret_dur_s"] for e in all_return_events), 3)
            if all_return_events else 0.0
        ),
        "return_cursor_to_hand_ratio_mean": (
            round(statistics.mean(e["cursor_to_hand_ratio"] for e in all_return_events), 2)
            if all_return_events else 0.0
        ),
        "return_angle_deg_median": _pct([e["angle_deg"] for e in all_return_events], 0.50),
        "return_angle_deg_p10": _pct([e["angle_deg"] for e in all_return_events], 0.10),
        "return_hand_dist_median": _pct([e["ret_hand_dist"] for e in all_return_events], 0.50),
        "return_hand_dist_p25": _pct([e["ret_hand_dist"] for e in all_return_events], 0.25),
        "return_pause_s_median": _pct([e["pause_s"] for e in all_return_events], 0.50),
        "return_speed_ratio_median": _pct([e["speed_ratio"] for e in all_return_events], 0.50),
        "return_size_ratio_median": _pct([e["ret_size_ratio_mean"] for e in all_return_events], 0.50),
        "return_size_ratio_min_median": _pct([e["ret_size_ratio_min"] for e in all_return_events], 0.50),
        "return_curl_median": _pct([e["ret_curl_mean"] for e in all_return_events], 0.50),
        "return_mult_median": _pct([e["ret_mult_mean"] for e in all_return_events], 0.50),
        "clutch_active_frames": clutch_active_frames,
        "return_suppressed_frames": return_suppressed_frames,
        "return_event_suppressed_frame_pct": (
            round(100.0 * ret_supp_frames / max(1, ret_total_frames), 2) if ret_total_frames else 0.0
        ),
    }


def evaluate_all(
    cfg: Optional[Dict[str, Any]] = None,
    sequences: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    include_raw_windows: bool = False,
) -> Dict[str, Any]:
    """Run full movement & return-stroke evaluation across all sequence groups."""
    config = cfg if cfg is not None else load_config()
    seqs = sequences if sequences is not None else extract_sequences()

    groups: Dict[str, Any] = {}
    keys = ["recordings", "field_logs_dedup", "fixtures"]
    if include_raw_windows:
        keys.insert(2, "field_logs_windows")

    for k in keys:
        groups[k] = summarize_group(k, seqs[k], config)

    combined_seqs = seqs["recordings"] + seqs["field_logs_dedup"] + seqs["fixtures"]
    groups["combined_unique"] = summarize_group("combined_unique", combined_seqs, config)

    return {
        "movement_scheme": str(config.get("movement_scheme", "legacy_shape")),
        "return_suppression": str(config.get("return_suppression", "off")),
        "dataset_limitation": DATASET_LIMITATION_NOTE,
        "groups": groups,
    }
