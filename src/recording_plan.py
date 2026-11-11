"""Versioned, per-condition capture plans shared by the UI, recorder and dataset reader."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

SCHEMA = "manux-capture-plan/1"
PLAN_PATH = Path(__file__).resolve().parents[1] / "config" / "recording-plans.json"
STATIC_ORIENTATIONS = ["손바닥 정면", "손등 정면", "왼쪽으로 기울임", "오른쪽으로 기울임", "손끝 위", "손끝 아래", "옆면"]
MOTION_ORIENTATIONS = ["기본 속도·보통 폭", "빠르고 짧게 끊어 쓸기", "크고 넓게 쓸기", "동작 고유 손모양 유지"]
OPTIONS = {
    "orientation": STATIC_ORIENTATIONS + MOTION_ORIENTATIONS,
    "position": ["중앙", "왼쪽 위", "위", "오른쪽 위", "왼쪽", "오른쪽", "왼쪽 아래", "아래", "오른쪽 아래", "자유 이동"],
    # The first three keep their places (presets use their indexes); the new ones go in front of "직접 지정".
    "distance": ["가까이 (약 30cm)", "보통 (약 60cm)", "멀리 (약 100cm)", "약 1.5m", "약 2m", "약 2.5m", "약 3m",
                 "약 3.5m", "약 4m", "약 5m", "직접 지정"],
    "posture": ["바로 앉기", "뒤로 기대기", "몸을 왼쪽으로", "몸을 오른쪽으로", "서 있기", "직접 지정"],
    "camera": ["눈높이", "위에서 아래로", "아래에서 위로", "왼쪽 측면", "오른쪽 측면", "직접 지정"],
    "lighting": ["보통", "밝음", "어두움", "역광", "직접 지정"],
}
ORIENTATION_GUIDES = {
    "손바닥 정면": "손바닥이 카메라를 정면으로 마주보게 편하게 든 기본 자세",
    "손등 정면": "손등이 카메라 쪽을 보이도록 손을 뒤집은 자세",
    "왼쪽으로 기울임": "손목을 살짝 돌려 손끝·손바닥이 왼쪽(10~11시 방향, 약 20~30°)으로 기울어진 자세",
    "오른쪽으로 기울임": "손목을 살짝 돌려 손끝·손바닥이 오른쪽(1~2시 방향, 약 20~30°)으로 기울어진 자세",
    "손끝 위": "손가락 끝이 천장(12시 방향)을 향하도록 똑바로 세운 자세",
    "손끝 아래": "손목에 힘을 빼고 손가락 끝이 아래(6시 방향)로 살짝 처진 자세",
    "옆면": "엄지나 새끼손가락 옆날이 카메라를 향하도록 세운 옆모습 자세",
    "기본 속도·보통 폭": "손바닥을 억지로 정면 고정하지 말고, 이 모션 고유의 손 모양을 유지한 채 평소 속도와 보통 폭(약 20~30cm)으로 1회 또렷하게 쓸어 멈추세요",
    "빠르고 짧게 끊어 쓸기": "이 모션의 손 모양을 유지하며 손목·팔을 조금 더 경쾌하고 빠르게 휙 쓸고 끝에서 확실히 멈추세요 (쉬기 때 천천히 복귀)",
    "크고 넓게 쓸기": "이 모션의 손 모양을 유지하며 시작점부터 끝점까지 이동 폭을 조금 더 넓게(약 30~45cm) 가져가며 1회 쓸어 멈추세요",
    "동작 고유 손모양 유지": "손바닥 정면을 강제하지 않고, 해당 동작이 요구하는 고유한 손가락·손목 방향을 자연스럽게 유지하세요",
}
POSTURE_GUIDES = {
    "바로 앉기": "의자에 편하게 정면으로 앉은 자세",
    "뒤로 기대기": "등받이에 편하게 기댄 자세",
    "몸을 왼쪽으로": "몸이나 팔을 살짝 왼쪽으로 기대거나 옮긴 자세",
    "몸을 오른쪽으로": "몸이나 팔을 살짝 오른쪽으로 기대거나 옮긴 자세",
    "서 있기": "일어서서 손을 든 자세",
    "직접 지정": "사용자 지정 자세",
}
TIMINGS = {"reps": (1, 100, 1), "prep": (0.0, 10, 0.0), "hold": (0.5, 60, 15.0),
           "rest": (0.0, 10, 0.0), "free_seconds": (10, 900, 60)}


def orientation_guide(orientation: str, focus: str = "") -> str:
    orient_str = str(orientation or "")
    if focus:
        from src.gesture_catalog import BY_KEY, is_motion_task
        task = BY_KEY.get(focus)
        if task is not None and is_motion_task(focus):
            style_desc = ORIENTATION_GUIDES.get(orient_str, "")
            if orient_str in STATIC_ORIENTATIONS or not style_desc:
                style_desc = ORIENTATION_GUIDES["기본 속도·보통 폭"]
            return f"{task.do} · [{style_desc}]"
        if focus == "palm_side":
            return "엄지만 손바닥 안으로 접고 네 손가락을 나란히 펴서 옆(가로 방향)으로 향하게 유지하세요 (손바닥 정면이 아닌 가로 손 모양)"
        if focus == "thumbs_up":
            return "네 손가락을 자연스럽게 감싸 쥐고 엄지만 위로 곧게 세운 상태를 유지하세요"
    return ORIENTATION_GUIDES.get(orient_str, "")


def posture_guide(posture: str) -> str:
    return POSTURE_GUIDES.get(str(posture or ""), "")


def condition(**values):
    base = {"name": "새 자세", "enabled": True, "notes": "",
            **{k: v[0] for k, v in OPTIONS.items()}, **{k: v[2] for k, v in TIMINGS.items()}}
    base["position"] = "자유 이동"
    base["distance"] = OPTIONS["distance"][1]
    base.update(values)
    return base


# Which distances the "거리 코스" walks through, near to far (names of OPTIONS["distance"]): 1 m, 2 m, 3 m
DISTANCE_COURSE = ("멀리 (약 100cm)", "약 2m", "약 3m")
_DISTANCE_SHORT = {"가까이 (약 30cm)": "약 30cm", "보통 (약 60cm)": "약 60cm", "멀리 (약 100cm)": "약 1m"}


def distance_label(value: str) -> str:
    """How a distance is said on screen: "멀리 (약 100cm)" is 1 m."""
    return _DISTANCE_SHORT.get(str(value), str(value))

# Recommended counts and times per kind of gesture (the user raises or lowers them). hold = how long the hand does it
# in one repetition; a click is a short press, the others (scroll, drag, window switcher) are held and moved slowly.
_PINCH_CLICK = ("thumb_middle", "thumb_ring")
_PINCH_HOLD = ("thumb_index", "thumb_pinky", "thumb_middle_drag", "thumb_index_middle")
_STATIC_SIGNS = ("palm_side", "thumbs_up", "v_sign", "rock_sign")
_FREE_SECONDS = {"none": 120, "open_move": 60, "fist_move": 60,
                 "near_thumb_index": 60, "near_thumb_middle": 60, "near_thumb_ring": 60, "near_thumb_pinky": 60}


def task_timings(focus: str):
    """Recommended timings of a focused recording, or None for "all gestures" and the motions (they have their own)."""
    if focus in _PINCH_CLICK:
        return {"reps": 12, "prep": 2.0, "hold": 1.5, "rest": 2.0}
    if focus in _PINCH_HOLD:
        return {"reps": 10, "prep": 2.0, "hold": 3.0, "rest": 2.0}
    if focus in _STATIC_SIGNS:
        return {"reps": 8, "prep": 2.0, "hold": 2.5, "rest": 1.5}
    if focus in _FREE_SECONDS:
        return {"free_seconds": _FREE_SECONDS[focus]}
    return None


def _distance_rows(focus: str):
    """One posture per distance, near to far: the person walks back a step between sets (rec_auto_next gives the time)."""
    from src.gesture_catalog import is_motion_task
    if focus and is_motion_task(focus):
        orientation = "기본 속도·보통 폭"
    elif focus in ("palm_side", "thumbs_up"):
        orientation = "동작 고유 손모양 유지"
    else:
        orientation = STATIC_ORIENTATIONS[0]
    n = len(DISTANCE_COURSE)
    return [condition(name=f"거리 코스 {i}/{n} · {distance_label(dist)}", orientation=orientation, position="자유 이동", distance=dist,
                      posture="바로 앉기", camera="눈높이")
            for i, dist in enumerate(DISTANCE_COURSE, 1)]


def preset(name="basic", focus=""):
    """A plan of ready-made postures. name: basic | diverse | positions | distance (a course from near to far)."""
    from src.gesture_catalog import is_motion_task
    plan = {"schema": SCHEMA, "conditions": _distance_rows(focus)} if name == "distance" else _preset_raw(name, focus)
    timings = task_timings(focus)
    if timings and not (focus and is_motion_task(focus)):
        for row in plan["conditions"]:
            row.update(timings)
    elif name == "distance" and focus and is_motion_task(focus):
        for row in plan["conditions"]:
            row.update({"reps": 8, "prep": 0.5, "hold": 1.2, "rest": 1.0})
    return plan


def _preset_raw(name="basic", focus=""):
    from src.gesture_catalog import is_motion_task
    if focus and is_motion_task(focus):
        if name == "positions":
            rows = [
                condition(
                    name=f"화면 {p} 구역에서 모션 쓸기",
                    orientation="기본 속도·보통 폭",
                    position=p,
                    reps=6,
                    prep=0.5,
                    hold=1.2,
                    rest=1.0,
                )
                for p in ("중앙", "왼쪽", "오른쪽", "위", "아래")
            ]
            return {"schema": SCHEMA, "conditions": rows}
        rows = [
            condition(
                name="1코스 · 평소 속도·보통 폭 쓸기 (기본)",
                orientation="기본 속도·보통 폭",
                position="자유 이동",
                posture="바로 앉기",
                reps=10,
                prep=0.5,
                hold=1.2,
                rest=1.0,
            ),
            condition(
                name="2코스 · 빠르고 간결하게 끊어 쓸기",
                orientation="빠르고 짧게 끊어 쓸기",
                position="자유 이동",
                posture="바로 앉기",
                reps=10,
                prep=0.5,
                hold=1.0,
                rest=1.0,
            ),
            condition(
                name="3코스 · 크고 넓게 쓸기 · 편한 자세",
                orientation="크고 넓게 쓸기",
                position="자유 이동",
                posture="뒤로 기대기",
                reps=10,
                prep=0.5,
                hold=1.5,
                rest=1.0,
            ),
        ]
        if name == "diverse":
            rows += [
                condition(
                    name="4코스 · 가까이서 쓸기 (약 30cm)",
                    orientation="기본 속도·보통 폭",
                    position="자유 이동",
                    distance=OPTIONS["distance"][0],
                    posture="바로 앉기",
                    reps=8,
                    prep=0.5,
                    hold=1.2,
                    rest=1.0,
                ),
                condition(
                    name="5코스 · 멀리서 쓸기 (약 100cm)",
                    orientation="크고 넓게 쓸기",
                    position="자유 이동",
                    distance=OPTIONS["distance"][2],
                    posture="뒤로 기대기",
                    reps=8,
                    prep=0.5,
                    hold=1.5,
                    rest=1.0,
                ),
                condition(
                    name="6코스 · 몸을 옆으로 기댄 채 쓸기",
                    orientation="기본 속도·보통 폭",
                    position="자유 이동",
                    posture="몸을 왼쪽으로",
                    reps=8,
                    prep=0.5,
                    hold=1.2,
                    rest=1.0,
                ),
            ]
        return {"schema": SCHEMA, "conditions": rows}

    if focus in ("palm_side", "thumbs_up"):
        label_prefix = "엄지 접고 손 가로" if focus == "palm_side" else "엄지 위로"
        rows = [
            condition(name=f"전체 화면 · {label_prefix} 기본 자세 (정면)", orientation="동작 고유 손모양 유지", position="자유 이동", posture="바로 앉기"),
            condition(name=f"전체 화면 · {label_prefix} · 몸/팔 왼쪽", orientation="동작 고유 손모양 유지", position="자유 이동", posture="몸을 왼쪽으로"),
            condition(name=f"전체 화면 · {label_prefix} · 편하게 기대기", orientation="동작 고유 손모양 유지", position="자유 이동", posture="뒤로 기대기"),
        ]
        if name == "diverse":
            rows += [
                condition(name=f"{label_prefix} · 가까이 (30cm)", orientation="동작 고유 손모양 유지", position="자유 이동", distance=OPTIONS["distance"][0]),
                condition(name=f"{label_prefix} · 멀리 (100cm)", orientation="동작 고유 손모양 유지", position="자유 이동", distance=OPTIONS["distance"][2], posture="뒤로 기대기"),
                condition(name=f"{label_prefix} · 몸/팔 오른쪽", orientation="동작 고유 손모양 유지", position="자유 이동", posture="몸을 오른쪽으로"),
            ]
        elif name == "positions":
            rows = [condition(name=f"화면 {p} · {label_prefix}", orientation="동작 고유 손모양 유지", position=p) for p in OPTIONS["position"] if p != "자유 이동"]
        return {"schema": SCHEMA, "conditions": rows}

    rows = [condition(name="전체 화면 · 손바닥 정면 (기본)", position="자유 이동"),
            condition(name="전체 화면 · 손목 왼쪽 살짝 회전 (10시 방향)", orientation="왼쪽으로 기울임", position="자유 이동", posture="몸을 왼쪽으로"),
            condition(name="전체 화면 · 손목 오른쪽 살짝 회전 (2시 방향)", orientation="오른쪽으로 기울임", position="자유 이동", posture="몸을 오른쪽으로")]
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


def description(row, focus: str = ""):
    base = " · ".join(row[k] for k in OPTIONS) + (f" · {row['notes']}" if row.get("notes") else "")
    guide = orientation_guide(row.get("orientation", ""), focus=focus)
    return f"{base} ({guide})" if guide else base


def resolve_condition_group_name(key_or_obj, idx: int = 0, path=PLAN_PATH) -> str:
    """Resolve a condition_groups entry (dict or 'pose-...' key) into a readable Korean label."""
    if isinstance(key_or_obj, dict):
        raw = str(key_or_obj.get("name") or key_or_obj.get("description") or f"자세 #{idx + 1}")
        return raw.split(" (손")[0].strip()
    key_str = str(key_or_obj or "").strip()
    if not key_str or not key_str.startswith("pose-"):
        return key_str or f"자세 #{idx + 1}"

    candidates = []
    for p_name in ("basic", "diverse", "positions"):
        candidates.extend(preset(p_name).get("conditions", []))
        candidates.extend(preset(p_name, focus="swipe_left").get("conditions", []))
        candidates.extend(preset(p_name, focus="palm_side").get("conditions", []))
    # Include legacy basic preset positions (중앙/왼쪽/오른쪽) so older recordings resolve cleanly
    candidates.extend([
        condition(name="손바닥 정면 · 중앙", position="중앙"),
        condition(name="손목 왼쪽 회전 (10시) · 좌측", orientation="왼쪽으로 기울임", position="왼쪽", posture="몸을 왼쪽으로"),
        condition(name="손목 오른쪽 회전 (2시) · 우측", orientation="오른쪽으로 기울임", position="오른쪽", posture="몸을 오른쪽으로"),
    ])
    try:
        if Path(path).is_file():
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            for pl in (doc.get("plans") or {}).values():
                if isinstance(pl, dict):
                    candidates.extend(pl.get("conditions") or [])
    except Exception:
        pass
    for row in candidates:
        try:
            if not isinstance(row, dict):
                continue
            norm_row = condition(**{k: row[k] for k in row if k in OPTIONS or k in ("name", "notes")})
            if condition_key(norm_row) == key_str:
                return str(norm_row.get("name") or f"자세 #{idx + 1}").split(" (손")[0].strip()
        except Exception:
            continue
    return f"자세 #{idx + 1}"


def duration(plan, focus):
    from src.gesture_catalog import tasks_for
    tasks = tasks_for(focus)
    total = 0.0
    for r in plan["conditions"]:
        if r["enabled"]:
            total += sum(r["free_seconds"] if t.free else r["reps"] * (r["prep"] + r["hold"] + r["rest"]) for t in tasks)
    return total


def _is_legacy_static_plan_for_task(saved_plan: dict, focus: str) -> bool:
    """Detect if a motion or sideways gesture still has a legacy '손바닥 정면 / 손목 회전' static preset saved."""
    from src.gesture_catalog import is_motion_task
    if not focus or not isinstance(saved_plan, dict):
        return False
    rows = saved_plan.get("conditions") or []
    if not rows:
        return False
    if is_motion_task(focus):
        return any(
            str(r.get("orientation", "")) in STATIC_ORIENTATIONS
            or "손바닥 정면" in str(r.get("name", ""))
            or "손목 왼쪽" in str(r.get("name", ""))
            for r in rows
            if isinstance(r, dict)
        )
    if focus in ("palm_side", "thumbs_up"):
        return all(
            str(r.get("orientation", "")) in ("손바닥 정면", "왼쪽으로 기울임", "오른쪽으로 기울임")
            and ("손바닥 정면" in str(r.get("name", "")) or "손목 " in str(r.get("name", "")))
            for r in rows
            if isinstance(r, dict)
        )
    return False


def load_plan(focus, path=PLAN_PATH):
    from src.gesture_catalog import BY_KEY
    task = BY_KEY.get(focus or "")
    default_plan = preset("basic", focus=focus or "")
    if task and task.direction and not any(r.get("orientation") in MOTION_ORIENTATIONS for r in default_plan["conditions"]):
        for r in default_plan["conditions"]:
            r.update({"reps": 10, "prep": 0.5, "hold": 1.2, "rest": 1.0})
    if not Path(path).exists():
        return default_plan
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    saved = doc.get("plans", {}).get(focus or "all")
    if saved is None or _is_legacy_static_plan_for_task(saved, focus or ""):
        return default_plan
    return normalise(saved)


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
