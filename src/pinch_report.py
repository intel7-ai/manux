"""Per-distance report of a guided recording of one pinch (docs/far-hand-loss.md, "거리별 핀치").

For every posture/distance of the plan it counts, per repetition (a `do` phase):
- how often the rule engine itself fired the target action (`rule.engine_action`, recorded live before the guided capture
  puts the target in),
- how often the action also got through the TCN gate (`rule.gated`; with the gate on it is the action after the check, so
  "engine fired but gated says none" is a TCN refusal),
- the hand size (a stand-in for the distance), how long the first fire took and what else the engine did.

Only recordings made after 2026-11-09 have `engine_action`.
"""
from __future__ import annotations

import collections
import gzip
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from src.gesture_catalog import BY_KEY
from src.recording_plan import condition_key

IDLE_ACTIONS = (None, "none", "move_cursor", "pointer_wait")


def read_recording(path: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    meta: Dict[str, Any] = {}
    frames: List[Dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "meta":
                meta = r
            elif "t" in r:
                frames.append(r)
    return meta, frames


def has_engine_reading(frames: List[Dict[str, Any]], task_key: str) -> bool:
    return any((f.get("label") or {}).get("task") == task_key and "engine_action" in (f.get("rule") or {}) for f in frames)


def hand_size(lm, aspect: float = 720 / 1280) -> float:
    p = np.asarray(lm, float)[:, :2].copy()
    p[:, 1] *= aspect
    return max(float(np.linalg.norm(p[k] - p[0])) for k in (5, 9, 13, 17))


def condition_names(meta: Dict[str, Any]) -> Dict[str, str]:
    """condition_key -> the plan's name and note, so the report says "2m" and not a hash."""
    out: Dict[str, str] = {}
    for row in (meta.get("capture_plan") or {}).get("conditions", []):
        try:
            key = condition_key(row)
        except Exception:
            continue
        note = str(row.get("notes") or "").strip()
        out[key] = f"{row.get('name', '')}" + (f" [{note}]" if note else "")
    return out


def analyze(meta: Dict[str, Any], frames: List[Dict[str, Any]], task_key: str) -> List[Dict[str, Any]]:
    task = BY_KEY[task_key]
    want = task.action or task.label
    names = condition_names(meta)
    reps: Dict[tuple, List[Dict[str, Any]]] = collections.defaultdict(list)
    for f in frames:
        lab = f.get("label") or {}
        if lab.get("task") == task_key and lab.get("phase") == "do":
            reps[(lab.get("condition_key") or lab.get("condition"), lab.get("set"), lab.get("rep"))].append(f)

    per_condition: Dict[Any, List[Dict[str, Any]]] = collections.defaultdict(list)
    for (cond, _, rep), items in reps.items():
        rules = [f.get("rule") or {} for f in items]
        t0 = items[0]["t"]
        engine_hits = [f["t"] - t0 for f, r in zip(items, rules) if r.get("engine_action") == want]
        gated_hits = [f["t"] - t0 for f, r in zip(items, rules) if r.get("gated") == want]
        other = collections.Counter(r.get("engine_action") for r in rules if r.get("engine_action") not in IDLE_ACTIONS + (want,))
        sizes = [hand_size(f["lm"]) for f in items if f.get("lm")]
        per_condition[cond].append({
            "rep": rep, "seconds": items[-1]["t"] - t0, "frames": len(items),
            "no_hand": sum(1 for f in items if not f.get("lm")),
            "engine_fired": bool(engine_hits), "gate_passed": bool(gated_hits),
            "first_fire": min(engine_hits) if engine_hits else None,
            "size": float(np.median(sizes)) if sizes else None, "other": dict(other),
        })

    rows = []
    for cond, reps_ in per_condition.items():
        sizes = [r["size"] for r in reps_ if r["size"] is not None]
        latencies = [r["first_fire"] for r in reps_ if r["first_fire"] is not None]
        other: collections.Counter = collections.Counter()
        for r in reps_:
            other.update(r["other"])
        rows.append({
            "condition": names.get(cond, str(cond)), "reps": len(reps_),
            "engine_fired": sum(r["engine_fired"] for r in reps_),
            "gate_passed": sum(r["gate_passed"] for r in reps_),
            "size": float(np.median(sizes)) if sizes else None,
            "first_fire_median": float(np.median(latencies)) if latencies else None,
            "no_hand_share": sum(r["no_hand"] for r in reps_) / max(1, sum(r["frames"] for r in reps_)),
            "other_actions": dict(other),
        })
    rows.sort(key=lambda r: -(r["size"] or 0))      # near to far
    return rows


def format_report(rows: List[Dict[str, Any]], task_key: str) -> str:
    out = [f"{BY_KEY[task_key].name}: 회차마다 엔진이 목표 동작을 한 번이라도 냈는지 (가까운 자리부터)",
           f"{'자리':28s} {'손 크기':>7s} {'회차':>4s} {'엔진':>9s} {'TCN 통과':>9s} {'첫 반응':>8s} {'손 없음':>7s}  다른 동작"]
    for r in rows:
        size = f"{r['size']:.3f}" if r["size"] is not None else "-"
        first = f"{r['first_fire_median']:.2f}초" if r["first_fire_median"] is not None else "-"
        out.append(f"{r['condition'][:28]:28s} {size:>7s} {r['reps']:>4d} "
                   f"{r['engine_fired']:>3d}/{r['reps']:<3d}({r['engine_fired'] / max(1, r['reps']):>4.0%}) "
                   f"{r['gate_passed']:>3d}/{r['reps']:<3d}   {first:>8s} {r['no_hand_share']:>7.0%}  {r['other_actions'] or ''}")
    return "\n".join(out)


def newest_recording_with_engine_reading(rec_dir: Path, task_key: str) -> Optional[Path]:
    for path in sorted(rec_dir.glob("*.jsonl.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
        _, frames = read_recording(path)
        if has_engine_reading(frames, task_key):
            return path
    return None
