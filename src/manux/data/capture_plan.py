"""Capture plans (`manux-capture-plan/1`): under which physical conditions to record, with what timing.

Ported from rasp-jun `origin/codex/rasp-jun-training-center` `src/recording_plan.py`. Same schema,
options, timing ranges, presets and `condition_key` hash (so a condition keeps the same key in v0.3
and v0.4 recordings). The difference: plans are stored in the **user's** config folder
(`settings.paths.resolve().config_dir`, passed in by the caller since `data` imports only `core`),
never inside the repository, and written atomically.

A plan is `{"schema": "manux-capture-plan/1", "conditions": [row, ...]}`; a row is a name, `enabled`,
`notes`, one value of each `OPTIONS` field and the `TIMINGS` (reps, prep / hold / rest s, free_seconds).
`condition_key(row)` hashes only the physical setup (the `OPTIONS` fields and notes), not the name,
order or timing, so evaluation can hold one physical condition out (`label.condition_key`).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Union

from manux.data.repository import repository_root

SCHEMA = "manux-capture-plan/1"
FILE_NAME = "capture-plans.json"
ALL = "all"                       # the plan used when no task is focused
PRESETS = ("basic", "positions", "diverse", "quick_click")
OPTIONS: Dict[str, list] = {
    "orientation": ["손바닥 정면", "손등 정면", "왼쪽으로 기울임", "오른쪽으로 기울임", "손끝 위", "손끝 아래", "옆면"],
    "position": ["중앙", "왼쪽 위", "위", "오른쪽 위", "왼쪽", "오른쪽", "왼쪽 아래", "아래", "오른쪽 아래", "자유 이동"],
    "distance": ["가까이 (약 30cm)", "보통 (약 60cm)", "멀리 (약 100cm)", "직접 지정"],
    "posture": ["바로 앉기", "뒤로 기대기", "몸을 왼쪽으로", "몸을 오른쪽으로", "서 있기", "직접 지정"],
    "camera": ["눈높이", "위에서 아래로", "아래에서 위로", "왼쪽 측면", "오른쪽 측면", "직접 지정"],
    "lighting": ["보통", "밝음", "어두움", "역광", "직접 지정"],
}
TIMINGS: Dict[str, tuple] = {"reps": (1, 100, 10), "prep": (0.5, 10, 1.5), "hold": (0.2, 10, 2.0),
                             "rest": (0.5, 10, 1.5), "free_seconds": (10, 900, 60)}
_TIMING_NAMES = {"reps": "반복 횟수", "prep": "준비 시간", "hold": "동작 시간", "rest": "쉬는 시간",
                 "free_seconds": "자유 동작 시간"}
_OPTION_NAMES = {"orientation": "손 방향", "position": "화면 위치", "distance": "거리", "posture": "몸 자세",
                 "camera": "카메라 각도", "lighting": "조명"}
_REPO = repository_root(Path(__file__))


def condition(**values: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {"name": "새 자세", "enabled": True, "notes": "",
                            **{k: v[0] for k, v in OPTIONS.items()}, **{k: v[2] for k, v in TIMINGS.items()}}
    base["distance"] = OPTIONS["distance"][1]
    base.update(values)
    return base


def preset(name: str = "basic") -> Dict[str, Any]:
    if name not in PRESETS:
        raise ValueError(f"녹화 설정 '{name}'은 없습니다. {', '.join(PRESETS)} 중에서 고르세요.")
    if name == "quick_click":
        return {"schema": SCHEMA, "conditions": [dict(row, name=f"{row['name']} · {hold:g}초 클릭",
                 hold=hold, reps=8, prep=1., rest=1.)
                 for row in preset("diverse")["conditions"] for hold in (.3, .5, .7)]}
    rows = [condition(name="정면 · 중앙"),
            condition(name="왼쪽 방향 · 왼쪽", orientation="왼쪽으로 기울임", position="왼쪽", posture="몸을 왼쪽으로"),
            condition(name="오른쪽 방향 · 오른쪽", orientation="오른쪽으로 기울임", position="오른쪽", posture="몸을 오른쪽으로")]
    if name == "diverse":
        rows += [condition(name="손등 · 위", orientation="손등 정면", position="위"),
                 condition(name="손끝 아래 · 아래", orientation="손끝 아래", position="아래"),
                 condition(name="가까이 · 왼쪽 위", position="왼쪽 위", distance=OPTIONS["distance"][0],
                           camera="위에서 아래로"),
                 condition(name="멀리 · 오른쪽 아래", position="오른쪽 아래", distance=OPTIONS["distance"][2],
                           posture="뒤로 기대기"),
                 condition(name="서서 · 측면", orientation="옆면", posture="서 있기", camera="왼쪽 측면"),
                 condition(name="조명 변화 · 자유 이동", position="자유 이동", lighting="어두움")]
    elif name == "positions":
        rows = [condition(name=f"화면 {p}", position=p) for p in OPTIONS["position"] if p != "자유 이동"]
    return {"schema": SCHEMA, "conditions": rows}


def normalise(plan: Any, *, require_enabled: bool = True) -> Dict[str, Any]:
    """A clean copy of `plan`, or ValueError with a message for the person editing it."""
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
                raise ValueError(f"지원하지 않는 {_OPTION_NAMES[k]} 값입니다: {row[k]!r}")
        for k, (lo, hi, default) in TIMINGS.items():
            value = raw.get(k, default)
            if isinstance(value, bool):
                raise ValueError(f"{_TIMING_NAMES[k]}: 숫자로 입력하세요.")
            try:
                n = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{_TIMING_NAMES[k]}: 숫자로 입력하세요.") from None
            if not math.isfinite(n) or not lo <= n <= hi or (k in ("reps", "free_seconds") and n != int(n)):
                raise ValueError(f"{_TIMING_NAMES[k]}: {lo}~{hi} 범위로 설정하세요.")
            row[k] = int(n) if k in ("reps", "free_seconds") else n
        out.append(row)
    if require_enabled and not any(r["enabled"] for r in out):
        raise ValueError("녹화할 자세를 하나 이상 선택하세요.")
    return {"schema": SCHEMA, "conditions": out}


def enabled(plan: Mapping[str, Any]) -> list:
    return [r for r in plan["conditions"] if r["enabled"]]


def condition_key(row: Mapping[str, Any]) -> str:
    """Names, order and timing do not define a physical condition; identical setups share one key.

    Byte-for-byte the same hash as rasp-jun's, so keys match across v0.3 and v0.4 recordings."""
    physical = {k: row[k] for k in OPTIONS}
    physical["notes"] = str(row.get("notes", "")).strip()
    encoded = json.dumps(physical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "pose-" + hashlib.sha256(encoded).hexdigest()[:16]


def label_condition_key(label: Mapping[str, Any]) -> str:
    """The condition group of a frame label: its key, `legacy-<index>` for old recordings, or `unassigned`."""
    if label.get("condition_key"):
        return str(label["condition_key"])
    idx = label.get("condition_index")
    return f"legacy-{idx}" if idx is not None else "unassigned"


def description(row: Mapping[str, Any]) -> str:
    return " · ".join(str(row[k]) for k in OPTIONS) + (f" · {row['notes']}" if row.get("notes") else "")


def duration(plan: Mapping[str, Any], tasks: Union[str, Iterable[Any]] = "") -> float:
    """Seconds of recording (without the waits for "start") for these tasks (or a catalog focus key)."""
    if isinstance(tasks, str):
        from manux.data.catalog import tasks_for
        tasks = tasks_for(tasks)
    tasks = tuple(tasks)
    total = 0.0
    for r in enabled(plan):
        for t in tasks:
            hold = max(r["hold"], getattr(t, "min_hold", 0.0))
            total += r["free_seconds"] if t.free else r["reps"] * (r["prep"] + hold + r["rest"])
    return total


# ---------------------------------------------------------------- storage (user config folder only)

def plans_file(config_dir: Path) -> Path:
    """Where the plans live: `<user config folder>/capture-plans.json`."""
    return Path(config_dir) / FILE_NAME


def _check_outside_repo(path: Path) -> None:
    if _REPO is not None and Path(path).resolve().is_relative_to(_REPO):
        raise ValueError(f"녹화 설정은 저장소 밖(사용자 설정 폴더)에 저장합니다: {path}")


def _read(path: Path) -> Dict[str, Any]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"plans": {}}
    except (OSError, ValueError) as exc:
        raise ValueError(f"녹화 설정 파일을 읽을 수 없습니다: {exc}") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("plans", {}), dict):
        raise ValueError("녹화 설정 파일 형식이 맞지 않습니다.")
    doc.setdefault("plans", {})
    return doc


def load_plan(focus: str, path: Path) -> Dict[str, Any]:
    """The saved plan for `focus` (a task key, or "" for all), else the `basic` preset."""
    plans = _read(path)["plans"]
    return normalise(plans[focus or ALL]) if (focus or ALL) in plans else preset()


def save_plan(plan: Mapping[str, Any], focus: str, path: Path) -> Dict[str, Any]:
    """Validate, then replace the stored plan for `focus` atomically. A bad plan leaves the file as it was."""
    clean = normalise(plan)
    path = Path(path)
    _check_outside_repo(path)
    doc = _read(path)
    doc["plans"][focus or ALL] = clean
    doc["schema"] = SCHEMA
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".tmp")
    with pending.open("w", encoding="utf-8") as file:
        json.dump(doc, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(pending, path)
    return copy.deepcopy(clean)


def plan_from(choice: str, focus: str = "", config_dir: Optional[Path] = None) -> Dict[str, Any]:
    """A plan from a CLI/UI choice: a preset name, `saved` (the user's stored plan) or a JSON file.

    A file may hold one plan (`{"schema": ..., "conditions": [...]}`) or a plans document
    (`{"plans": {focus: plan}}`, as `save_plan` writes)."""
    if choice in PRESETS:
        return normalise(preset(choice))
    if choice == "saved":
        if config_dir is None:
            raise ValueError("저장된 녹화 설정을 읽으려면 설정 폴더가 필요합니다.")
        return load_plan(focus, plans_file(config_dir))
    path = Path(choice)
    if not path.is_file():
        raise ValueError(f"녹화 설정 '{choice}'을 찾을 수 없습니다. {', '.join(PRESETS)}, saved 또는 JSON 파일 경로를 쓰세요.")
    doc = _read(path)
    if doc.get("conditions") is not None:
        return normalise(doc)
    plans = doc["plans"]
    key = focus if focus in plans else ALL
    if key not in plans:
        raise ValueError(f"{path.name}에 '{focus or ALL}' 녹화 설정이 없습니다.")
    return normalise(plans[key])

