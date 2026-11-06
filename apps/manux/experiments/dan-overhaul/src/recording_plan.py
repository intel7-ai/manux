"""Versioned, per-condition capture plans shared by the UI, recorder and dataset reader."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

SCHEMA = "manux-capture-plan/1"
PLAN_PATH = Path(__file__).resolve().parents[1] / "config" / "recording-plans.json"
OPTIONS = {
    "orientation": ["손바닥 정면", "손등 정면", "왼쪽으로 기울임", "오른쪽으로 기울임", "손끝 위", "손끝 아래", "옆면"],
    "position": ["중앙", "왼쪽 위", "위", "오른쪽 위", "왼쪽", "오른쪽", "왼쪽 아래", "아래", "오른쪽 아래", "자유 이동"],
    "distance": ["가까이 (약 30cm)", "보통 (약 60cm)", "멀리 (약 100cm)", "직접 지정"],
    "posture": ["바로 앉기", "뒤로 기대기", "몸을 왼쪽으로", "몸을 오른쪽으로", "서 있기", "직접 지정"],
    "camera": ["눈높이", "위에서 아래로", "아래에서 위로", "왼쪽 측면", "오른쪽 측면", "직접 지정"],
    "lighting": ["보통", "밝음", "어두움", "역광", "직접 지정"],
}
TIMINGS = {"reps": (1, 100, 10), "prep": (0.5, 10, 1.5), "hold": (0.5, 10, 2.0),
           "rest": (0.5, 10, 1.5), "free_seconds": (10, 900, 60)}


def condition(**values):
    base = {"name": "새 자세", "enabled": True, "notes": "",
            **{k: v[0] for k, v in OPTIONS.items()}, **{k: v[2] for k, v in TIMINGS.items()}}
    base["distance"] = OPTIONS["distance"][1]
    base.update(values)
    return base


def preset(name="basic"):
    rows = [condition(name="정면 · 중앙"),
            condition(name="왼쪽 방향 · 왼쪽", orientation="왼쪽으로 기울임", position="왼쪽", posture="몸을 왼쪽으로"),
            condition(name="오른쪽 방향 · 오른쪽", orientation="오른쪽으로 기울임", position="오른쪽", posture="몸을 오른쪽으로")]
    if name == "diverse":
        rows += [condition(name="손등 · 위", orientation="손등 정면", position="위"),
                 condition(name="손끝 아래 · 아래", orientation="손끝 아래", position="아래"),
                 condition(name="가까이 · 왼쪽 위", position="왼쪽 위", distance=OPTIONS["distance"][0], camera="위에서 아래로"),
                 condition(name="멀리 · 오른쪽 아래", position="오른쪽 아래", distance=OPTIONS["distance"][2], posture="뒤로 기대기"),
                 condition(name="서서 · 측면", orientation="옆면", posture="서 있기", camera="왼쪽 측면"),
                 condition(name="조명 변화 · 자유 이동", position="자유 이동", lighting="어두움")]
    elif name == "positions":
        rows = [condition(name=f"화면 {p}", position=p) for p in OPTIONS["position"] if p != "자유 이동"]
    return {"schema": SCHEMA, "conditions": rows}


def normalise(plan, *, require_enabled=True):
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise ValueError("녹화 설정 형식이 맞지 않습니다.")
    rows = plan.get("conditions")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        raise ValueError("자세는 1~30개로 설정하세요.")
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("자세 설정을 확인하세요.")
        row = condition()
        for k in ("name", "notes"):
            row[k] = str(raw.get(k, row[k])).strip()[:(80 if k == "name" else 240)]
        if not row["name"]:
            raise ValueError("자세 이름을 입력하세요.")
        row["enabled"] = bool(raw.get("enabled", True))
        for k, opts in OPTIONS.items():
            row[k] = raw.get(k, row[k])
            if row[k] not in opts:
                raise ValueError(f"지원하지 않는 자세 조건: {k}")
        for k, (lo, hi, default) in TIMINGS.items():
            n = float(raw.get(k, default))
            if not math.isfinite(n) or not lo <= n <= hi or (k in ("reps", "free_seconds") and n != int(n)):
                raise ValueError(f"{k}: {lo}~{hi} 범위로 설정하세요.")
            row[k] = int(n) if k in ("reps", "free_seconds") else n
        out.append(row)
    if require_enabled and not any(r["enabled"] for r in out):
        raise ValueError("녹화할 자세를 하나 이상 선택하세요.")
    return {"schema": SCHEMA, "conditions": out}


def condition_key(row):
    # Names, order and timing do not define a physical condition; identical setups stay in one evaluation fold.
    physical = {k: row[k] for k in OPTIONS}
    physical["notes"] = row.get("notes", "").strip()
    encoded = json.dumps(physical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "pose-" + hashlib.sha256(encoded).hexdigest()[:16]


def label_condition_key(label):
    if label.get("condition_key"):
        return str(label["condition_key"])
    idx = label.get("condition_index")
    return f"legacy-{idx}" if idx is not None else "unassigned"


def description(row):
    return " · ".join(row[k] for k in OPTIONS) + (f" · {row['notes']}" if row.get("notes") else "")


def duration(plan, focus):
    from src.gesture_catalog import tasks_for
    tasks = tasks_for(focus)
    total = 0.0
    for r in plan["conditions"]:
        if r["enabled"]:
            total += sum(r["free_seconds"] if t.free else r["reps"] * (r["prep"] + r["hold"] + r["rest"]) for t in tasks)
    return total


def load_plan(focus, path=PLAN_PATH):
    if not Path(path).exists():
        return preset()
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalise(doc.get("plans", {}).get(focus or "all", preset()))


def save_plan(plan, focus, path=PLAN_PATH):
    clean = normalise(plan)
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"plans": {}}
    doc.setdefault("plans", {})[focus or "all"] = clean
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)
    return copy.deepcopy(clean)
