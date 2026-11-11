"""Stage 1 Evaluation Methodology, Parameterized Group Leave-Out Splitting, and Regression Harness Utilities.

Provides:
- Wilson 95% confidence intervals (`wilson_ci_95`) with explicit sample counts / neutral durations.
- Parameterized group leave-out splitting (`condition`, `session`, `person`, `camera`, `random`)
  that switches to `leave-person-out` and `leave-camera-out` without code changes once multi-user
  or multi-camera recordings are added, while honestly reporting single-user/single-camera limitations.
- Per-repetition reaction latency measurement (`first contact -> approval` in ms and frames).
- Fixture replay regression verification (`tests/fixtures/*.json`) across `GestureEngine` and `GestureGate`.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from src.capture_dataset import frame_durations, is_pinch_captured_on_screen
from src.gesture_catalog import BY_KEY, CLASSES, TASKS


SUPPORTED_SPLIT_MODES: Tuple[str, ...] = (
    "condition",
    "session",
    "person",
    "camera",
    "random",
)

DEFERRED_TECHNIQUES_SUMMARY: Dict[str, str] = {
    "Soft-DTW": (
        "향후 동적 스와이프/궤적형 커스텀 제스처(`swipe_*`, 궤적 서명 등)를 3~5회 소수 샷 템플릿 매칭으로 "
        "직접 정렬·비교해야 하고 고정 15프레임 윈도우 평균 프로토타입만으로 속도 편차 흡수가 부족할 때 도입한다."
    ),
    "TS2Vec": (
        "라벨 없는 자유 사용 녹화(`free` 모드)가 수십 시간 이상 축적되어 지도 대조학습(SupCon)만으로는 "
        "포착하기 어려운 시계열 표현을 자기지도 사전학습(self-supervised pretraining)으로 보강해야 할 때 도입한다."
    ),
    "BADGE": (
        "수만 개 이상의 미라벨 후보 윈도우 풀에서 불확실성과 표현 다양성을 동시에 고려해 사용자에게 "
        "라벨링 질의를 능동 학습(active learning)으로 자동 선별해야 할 만큼 후보 풀이 커질 때 도입한다."
    ),
    "K-Center Greedy": (
        "다중 사용자·다중 카메라 온보딩 데이터가 대량 축적되어 전체 임베딩 코어셋(coreset) 대표 표본만 "
        "압축 보관하거나 프로토타입 커버리지 빈틈을 기하학적으로 샘플링해야 할 때 도입한다."
    ),
}


def wilson_ci_95(
    successes: int,
    total: int,
    *,
    z: float = 1.959963984540054,
) -> Tuple[float, float]:
    """Compute exact 95% Wilson score confidence interval `(low, high)` in `[0.0, 1.0]`."""
    n = int(total)
    if n <= 0:
        return (0.0, 0.0)
    k = max(0, min(int(successes), n))
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n)))
    low = max(0.0, min(1.0, center - half))
    high = max(0.0, min(1.0, center + half))
    return (round(low, 4), round(high, 4))


def _normalize_camera_id(meta: Mapping[str, Any]) -> Tuple[str, bool]:
    """Return `(camera_id, has_explicit_camera_id)` from session metadata."""
    cam = meta.get("camera")
    if isinstance(cam, Mapping):
        explicit_id = cam.get("camera_id") or cam.get("id") or cam.get("device_id") or cam.get("model")
        if explicit_id:
            return (str(explicit_id).strip(), True)
        src = str(cam.get("source", "0")).strip()
        mode = cam.get("mode")
        mode_str = "x".join(str(x) for x in mode) if isinstance(mode, (list, tuple)) else str(mode or "")
        phone = "phone_web" if cam.get("phone_web") else "usb_or_vcam"
        note = str(cam.get("note") or "").strip()
        # Normalize away PC-name suffixes inside camera note so the physical camera setup is identified
        note_clean = re.sub(r"\s*\([^)]*\)", "", note).strip() or " phone_cam"
        return (f"{src}:{mode_str}:{phone}:{note_clean}", False)
    if isinstance(cam, str) and cam.strip():
        return (cam.strip(), False)
    return ("default_camera", False)


def extract_session_identity(session: Mapping[str, Any], session_idx: int = 0) -> Dict[str, Any]:
    """Extract normalized `(session_id, person_id, camera_id, conditions)` from a loaded session."""
    meta = session.get("meta") or {}
    path_str = str(session.get("path") or f"session-{session_idx}.jsonl.gz")
    session_id = Path(path_str).name

    explicit_person = (
        meta.get("person_id")
        or meta.get("person")
        or meta.get("user_id")
        or meta.get("user")
        or meta.get("subject")
    )
    if explicit_person:
        person_id = str(explicit_person).strip()
        has_explicit_person = True
    else:
        # Without explicit multi-user tags, all current recordings belong to the single primary operator
        person_id = "user_primary"
        has_explicit_person = False

    camera_id, has_explicit_camera = _normalize_camera_id(meta)
    pc_id = str(meta.get("pc") or "unknown_pc").strip()
    cond_arr = session.get("cond")
    y_arr = session.get("y")
    if isinstance(cond_arr, np.ndarray) and isinstance(y_arr, np.ndarray):
        conds = sorted({int(c) for c, y in zip(cond_arr, y_arr) if int(c) >= 0 and int(y) >= 0})
    elif isinstance(cond_arr, np.ndarray):
        conds = sorted({int(c) for c in cond_arr if int(c) >= 0})
    else:
        conds = []

    return {
        "session_idx": int(session_idx),
        "session_id": session_id,
        "person_id": person_id,
        "has_explicit_person": has_explicit_person,
        "camera_id": camera_id,
        "has_explicit_camera": has_explicit_camera,
        "pc_id": pc_id,
        "conditions": conds,
        "frames": len(session.get("frames") or []),
    }


def dataset_diversity_report(sessions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Inspect dataset diversity across users, cameras, sessions, and posture conditions."""
    identities = [extract_session_identity(s, idx) for idx, s in enumerate(sessions)]
    unique_persons = sorted({row["person_id"] for row in identities})
    unique_cameras = sorted({row["camera_id"] for row in identities})
    unique_pcs = sorted({row["pc_id"] for row in identities})
    unique_sessions = [row["session_id"] for row in identities]
    unique_conditions = sorted({c for row in identities for c in row["conditions"]})

    single_user = len(unique_persons) <= 1
    single_camera = len(unique_cameras) <= 1

    limitations: List[str] = []
    if single_user:
        limitations.append(
            "현재 녹화 데이터셋은 단일 사용자(1인) 환경에서 수집되어 사용자 간 손 크기·손가락 비율 일반화 검증에 한계가 있습니다. "
            "다중 사용자 녹화(`meta.person_id` 또는 `meta.user`)가 추가되면 코드 수정 없이 `--split-mode person`(`leave-person-out`)이 즉시 활성화됩니다."
        )
    if single_camera:
        limitations.append(
            "현재 녹화 데이터셋은 단일 카메라 구성(폰 카메라 1280x720@30fps 연결)에 치우쳐 있어 이종 렌즈·화각(FOV)·해상도 일반화 검증에 한계가 있습니다. "
            "다중 카메라 녹화(`meta.camera.id` 또는 상이한 카메라 소스/해상도)가 추가되면 코드 수정 없이 `--split-mode camera`(`leave-camera-out`)가 즉시 활성화됩니다."
        )

    return {
        "num_sessions": len(identities),
        "unique_persons": unique_persons,
        "num_persons": len(unique_persons),
        "single_user_limitation": single_user,
        "unique_cameras": unique_cameras,
        "num_cameras": len(unique_cameras),
        "single_camera_limitation": single_camera,
        "unique_pcs": unique_pcs,
        "unique_sessions": unique_sessions,
        "unique_conditions": unique_conditions,
        "num_conditions": len(unique_conditions),
        "supported_split_modes": list(SUPPORTED_SPLIT_MODES),
        "limitations": limitations,
        "sessions": identities,
    }


def build_group_leave_out_folds(
    sessions: Sequence[Mapping[str, Any]],
    *,
    split_mode: str = "condition",
    held_out_filter: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Build parameterized leave-group-out folds for `condition`, `session`, `person`, or `camera`.

    Each fold in `folds` contains:
    - `fold_id`: group identifier held out for testing
    - `mode`: effective split mode (`condition`, `session`, `person`, `camera`)
    - `train_session_indices`: list of session indices included in training
    - `test_session_indices`: list of session indices included in evaluation
    - `train_conds`: list of condition IDs used for training (when `mode == 'condition'`, else all valid conds)
    - `test_conds`: list of condition IDs used for testing (when `mode == 'condition'`, else all valid conds)
    """
    mode = str(split_mode or "condition").strip().lower()
    if mode not in SUPPORTED_SPLIT_MODES:
        raise ValueError(f"지원하지 않는 분할 방식입니다: {split_mode} (지원: {SUPPORTED_SPLIT_MODES})")

    diversity = dataset_diversity_report(sessions)
    all_conds = diversity["unique_conditions"]
    identities = diversity["sessions"]
    n_sess = len(sessions)

    folds: List[Dict[str, Any]] = []
    degenerate_reason: Optional[str] = None

    if mode == "condition":
        target_conds = (
            [int(c) for c in held_out_filter if int(c) in all_conds]
            if held_out_filter is not None
            else list(all_conds)
        )
        for c in target_conds:
            train_conds = [x for x in all_conds if x != c]
            if not train_conds:
                continue
            folds.append({
                "fold_id": int(c),
                "name": f"condition:{c}",
                "mode": "condition",
                "train_session_indices": list(range(n_sess)),
                "test_session_indices": [
                    idx for idx, row in enumerate(identities) if int(c) in row["conditions"]
                ],
                "train_conds": train_conds,
                "test_conds": [int(c)],
            })
        if len(all_conds) <= 1:
            degenerate_reason = "단일 조건(posture)만 존재하여 leave-condition-out 분할을 수행할 수 없습니다."

    elif mode == "session":
        session_ids = [row["session_id"] for row in identities]
        allowed = set(str(x) for x in held_out_filter) if held_out_filter is not None else None
        for idx, sid in enumerate(session_ids):
            if allowed is not None and sid not in allowed and str(idx) not in allowed:
                continue
            train_idx = [j for j in range(n_sess) if j != idx]
            if not train_idx:
                continue
            folds.append({
                "fold_id": sid,
                "name": f"session:{sid}",
                "mode": "session",
                "train_session_indices": train_idx,
                "test_session_indices": [idx],
                "train_conds": list(all_conds),
                "test_conds": list(identities[idx]["conditions"] or all_conds),
            })
        if n_sess <= 1:
            degenerate_reason = "단일 녹화 세션만 존재하여 leave-session-out 분할을 수행할 수 없습니다."

    elif mode == "person":
        persons = diversity["unique_persons"]
        allowed = set(str(x) for x in held_out_filter) if held_out_filter is not None else None
        if len(persons) <= 1:
            degenerate_reason = (
                f"현재 데이터셋의 고유 사용자 수가 {len(persons)}명({persons})뿐이어서 "
                "leave-person-out 분할을 구성할 수 없습니다. 다중 사용자 녹화 추가 시 코드 변경 없이 즉시 동작합니다."
            )
        else:
            for pid in persons:
                if allowed is not None and pid not in allowed:
                    continue
                test_idx = [i for i, row in enumerate(identities) if row["person_id"] == pid]
                train_idx = [i for i, row in enumerate(identities) if row["person_id"] != pid]
                if not train_idx or not test_idx:
                    continue
                folds.append({
                    "fold_id": pid,
                    "name": f"person:{pid}",
                    "mode": "person",
                    "train_session_indices": train_idx,
                    "test_session_indices": test_idx,
                    "train_conds": list(all_conds),
                    "test_conds": list(all_conds),
                })

    elif mode == "camera":
        cameras = diversity["unique_cameras"]
        allowed = set(str(x) for x in held_out_filter) if held_out_filter is not None else None
        if len(cameras) <= 1:
            degenerate_reason = (
                f"현재 데이터셋의 고유 카메라 수가 {len(cameras)}개({cameras})뿐이어서 "
                "leave-camera-out 분할을 구성할 수 없습니다. 다중 카메라 녹화 추가 시 코드 변경 없이 즉시 동작합니다."
            )
        else:
            for cid in cameras:
                if allowed is not None and cid not in allowed:
                    continue
                test_idx = [i for i, row in enumerate(identities) if row["camera_id"] == cid]
                train_idx = [i for i, row in enumerate(identities) if row["camera_id"] != cid]
                if not train_idx or not test_idx:
                    continue
                folds.append({
                    "fold_id": cid,
                    "name": f"camera:{cid}",
                    "mode": "camera",
                    "train_session_indices": train_idx,
                    "test_session_indices": test_idx,
                    "train_conds": list(all_conds),
                    "test_conds": list(all_conds),
                })

    return {
        "split_mode": mode,
        "folds": folds,
        "num_folds": len(folds),
        "degenerate": degenerate_reason is not None,
        "degenerate_reason": degenerate_reason,
        "diversity": diversity,
    }


def measure_session_reaction_latency(
    session: Mapping[str, Any],
    conds: Optional[Sequence[int]],
    prob: Optional[np.ndarray] = None,
    thr: float = 0.7,
    k: int = 3,
) -> Dict[str, Dict[str, Any]]:
    """Measure per-task reaction latency (`first contact -> approval`) in ms and frames.

    - First contact frame (`first_contact_idx`): first frame in the repetition where the gesture
      contact/posture is visually present (`session['y'][i] == want` or `is_pinch_captured_on_screen`
      during `'do'` phase).
    - Approval frame (`approval_idx`): first frame in the repetition (`>= first_contact_idx`, or
      first firing frame in the repetition) where the detector approves the gesture (`k` consecutive
      frames `>= thr` for model `prob`, or rule action/target firing for rules).
    - `latency_ms`: `(t[approval_idx] - t[first_contact_idx]) * 1000.0` (0.0 if approved on the first
      contact frame).
    - `latency_frames`: `max(1, approval_idx - first_contact_idx + 1)` (number of contact frames elapsed
      through approval; e.g. `k=3` consecutive frames from first contact -> `3` frames, `~66.7 ms`).
    """
    frames = session.get("frames") or []
    classes = list(session.get("classes") or CLASSES)
    y_arr = session["y"]
    cond_arr = session["cond"]
    valid = y_arr >= 0
    if conds is not None:
        valid = valid & np.isin(cond_arr, conds)
    if not np.any(valid):
        return {}

    rep_frames: Dict[Tuple[int, str, Any, Any], List[int]] = defaultdict(list)
    for idx_i in np.flatnonzero(valid):
        i = int(idx_i)
        lab = frames[i].get("label") or {}
        group = int(cond_arr[i])
        task_key = lab.get("task")
        task = BY_KEY.get(task_key)
        if task and not task.free and task.label in classes and lab.get("phase") in ("do", "rest"):
            rep_frames[(group, task_key, lab.get("set"), lab.get("rep"))].append(i)

    rep_frames = {
        key: idx
        for key, idx in rep_frames.items()
        if any(
            (frames[i].get("label") or {}).get("phase") == "do"
            and y_arr[i] == classes.index(BY_KEY[key[1]].label)
            for i in idx
        )
    }
    if not rep_frames:
        return {}

    fired_by_class: Dict[int, np.ndarray] = {}
    if prob is not None:
        active = [c for c, name in enumerate(classes) if name != "none"]
        segs = session["segments"]
        resets = [False] * len(frames)
        previous = None
        for i, f in enumerate(frames):
            lab = f.get("label") or {}
            token = (int(segs[i]), lab.get("set"), lab.get("rep"))
            if token != previous:
                resets[i] = True
                previous = token
        for c in active:
            above = valid & (prob[:, c] >= thr)
            col_fired = np.zeros(len(frames), dtype=bool)
            run = 0
            for i in range(len(frames)):
                if resets[i]:
                    run = 0
                if above[i]:
                    run += 1
                    if run == k:
                        col_fired[i] = True
                else:
                    run = 0
            fired_by_class[c] = col_fired

    ms_by_task: Dict[str, List[float]] = defaultdict(list)
    frames_by_task: Dict[str, List[int]] = defaultdict(list)

    for (_, task_key, _, _), idx in rep_frames.items():
        task_spec = BY_KEY[task_key]
        want = classes.index(task_spec.label)
        # First contact frame inside this repetition
        contact_candidates = [
            i
            for i in idx
            if (frames[i].get("label") or {}).get("phase") == "do" and int(y_arr[i]) == want
        ]
        if not contact_candidates:
            continue
        first_contact = contact_candidates[0]

        approval_idx: Optional[int] = None
        if prob is not None:
            fired_indices = [i for i in idx if fired_by_class[want][i]]
            if fired_indices:
                after_contact = [i for i in fired_indices if i >= first_contact]
                approval_idx = after_contact[0] if after_contact else fired_indices[0]
        else:
            observed = [frames[i].get("rule") or {} for i in idx]
            modern = any(r.get("evaluated") for r in observed)
            if not modern and not task_spec.action:
                continue
            rule_fired = []
            for i in idx:
                r = frames[i].get("rule") or {}
                if modern:
                    if task_key in (r.get("targets") or []):
                        rule_fired.append(i)
                else:
                    if r.get("action") == task_spec.action:
                        rule_fired.append(i)
            if rule_fired:
                after_contact = [i for i in rule_fired if i >= first_contact]
                approval_idx = after_contact[0] if after_contact else rule_fired[0]

        if approval_idx is not None:
            t_contact = float(frames[first_contact].get("t", 0.0))
            t_approve = float(frames[approval_idx].get("t", t_contact))
            lat_ms = max(0.0, (t_approve - t_contact) * 1000.0)
            lat_frames = max(1, int(approval_idx - first_contact + 1))
            ms_by_task[task_key].append(lat_ms)
            frames_by_task[task_key].append(lat_frames)

    out: Dict[str, Dict[str, Any]] = {}
    for task_key, ms_list in ms_by_task.items():
        fr_list = frames_by_task[task_key]
        ms_arr = np.asarray(ms_list, dtype=np.float64)
        fr_arr = np.asarray(fr_list, dtype=np.float64)
        out[task_key] = {
            "count": len(ms_list),
            "ms_values": [round(float(v), 2) for v in ms_list],
            "frame_values": [int(v) for v in fr_list],
            "mean_ms": round(float(np.mean(ms_arr)), 1),
            "median_ms": round(float(np.median(ms_arr)), 1),
            "p90_ms": round(float(np.percentile(ms_arr, 90)), 1),
            "mean_frames": round(float(np.mean(fr_arr)), 2),
            "median_frames": round(float(np.median(fr_arr)), 1),
            "p90_frames": round(float(np.percentile(fr_arr, 90)), 1),
        }
    return out


def aggregate_latency_rows(
    result_rows: Sequence[Mapping[str, Any]],
    keys: Sequence[str],
    *,
    source: str = "tcn",
) -> Optional[Dict[str, Any]]:
    """Aggregate per-task latency samples across evaluation rows for the given task `keys`."""
    ms_all: List[float] = []
    fr_all: List[int] = []
    for row in result_rows or ():
        branch = row.get(source) or {}
        lat_map = branch.get("latency") or {}
        for k in keys:
            entry = lat_map.get(k)
            if not entry:
                continue
            ms_all.extend(float(v) for v in (entry.get("ms_values") or ()))
            fr_all.extend(int(v) for v in (entry.get("frame_values") or ()))
    if not ms_all:
        return None
    ms_arr = np.asarray(ms_all, dtype=np.float64)
    fr_arr = np.asarray(fr_all, dtype=np.float64)
    return {
        "samples": len(ms_all),
        "mean_ms": round(float(np.mean(ms_arr)), 1),
        "median_ms": round(float(np.median(ms_arr)), 1),
        "p90_ms": round(float(np.percentile(ms_arr, 90)), 1),
        "mean_frames": round(float(np.mean(fr_arr)), 2),
        "median_frames": round(float(np.median(fr_arr)), 1),
        "p90_frames": round(float(np.percentile(fr_arr, 90)), 1),
    }


def run_fixture_regressions(
    fixtures_dir: Optional[Path] = None,
    model_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Replay all `tests/fixtures/*.json` recordings through `GestureEngine` and `GestureGate`."""
    from types import SimpleNamespace
    from src.gesture_engine import GestureEngine, Point3D
    from src.gesture_gate import GestureGate, load_predictor

    root = Path(__file__).resolve().parents[1]
    fx_dir = Path(fixtures_dir) if fixtures_dir is not None else (root / "tests" / "fixtures")
    mdl_path = Path(model_path) if model_path is not None else (root / "models" / "gesture_tcn.onnx")

    gate: Optional[GestureGate] = None
    if mdl_path.is_file():
        try:
            pred_fn, pred_info = load_predictor(mdl_path)
            gate = GestureGate(pred_fn, pred_info)
        except Exception:
            gate = None

    checks: List[Dict[str, Any]] = []

    def _gate_step_and_filter(g: GestureGate, ts: float, pts: Optional[List[Point3D]], raw_lm: Any, aspect: float, res: Any) -> Any:
        hand_obj = SimpleNamespace(landmarks=pts, world=raw_lm) if pts else None
        g.step(ts, hand_obj, aspect)
        return g.filter(ts, res)

    # 1. half_fist_ring_contact.json: moving with a half fist where thumb touches curled ring finger
    f1 = fx_dir / "half_fist_ring_contact.json"
    if f1.is_file():
        data = json.loads(f1.read_text(encoding="utf-8"))
        engine = GestureEngine()
        engine.config.update({"record_fling_enabled": False})
        aspect = data["frame_px"][1] / data["frame_px"][0]
        engine.frame_aspect = aspect
        rule_actions: List[str] = []
        gated_actions: List[str] = []
        if gate is not None:
            gate.reset()
        for t, lm in data["frames"]:
            pts = [Point3D(*p) for p in lm] if lm else None
            ts = 100.0 + float(t)
            res = engine.process(pts, timestamp=ts)
            rule_actions.append(res.action)
            if gate is not None:
                g_res = _gate_step_and_filter(gate, ts, pts, lm, aspect, res)
                gated_actions.append(g_res.action)
        false_rc_rules = rule_actions.count("right_click")
        false_rc_gate = gated_actions.count("right_click")
        passed = (false_rc_rules == 0) and (false_rc_gate == 0)
        checks.append({
            "fixture": f1.name,
            "frames": len(data["frames"]),
            "passed": passed,
            "rule_false_right_clicks": false_rc_rules,
            "gated_false_right_clicks": false_rc_gate,
            "invariant": "right_click not fired during half-fist ring contact",
        })

    # 2. scroll_grab_stroke_and_return.json: scroll stroke up and return without scrolling back or dragging
    f2 = fx_dir / "scroll_grab_stroke_and_return.json"
    if f2.is_file():
        data = json.loads(f2.read_text(encoding="utf-8"))
        # Default engine (with side_rearm_ratio active)
        engine_default = GestureEngine()
        engine_default.config.update({"record_fling_enabled": False})
        engine_default.frame_aspect = data["frame_px"][1] / data["frame_px"][0]
        default_acts = set()
        for t, lm in data["frames"]:
            pts = [Point3D(*p) for p in lm] if lm else None
            r = engine_default.process(pts, timestamp=100.0 + float(t))
            default_acts.add(r.action)

        # Stroke return check (with side_rearm_ratio=0.0)
        engine_stroke = GestureEngine()
        engine_stroke.config.update({"record_fling_enabled": False, "side_rearm_ratio": 0.0})
        engine_stroke.frame_aspect = data["frame_px"][1] / data["frame_px"][0]
        stroke_actions: List[str] = []
        ys: List[float] = []
        for t, lm in data["frames"]:
            pts = [Point3D(*p) for p in lm] if lm else None
            r = engine_stroke.process(pts, timestamp=100.0 + float(t))
            stroke_actions.append(r.action)
            if r.action in ("scroll_grab", "drag_hold") and r.cursor_point is not None:
                ys.append(float(r.cursor_point[1]))
        came_back = (ys[-1] - min(ys)) if ys else 0.0
        passed = (
            ("drag_hold" not in default_acts)
            and ("scroll_grab" not in default_acts)
            and ("drag_hold" not in stroke_actions)
            and ("scroll_grab" in stroke_actions)
            and (came_back < 0.012)
        )
        checks.append({
            "fixture": f2.name,
            "frames": len(data["frames"]),
            "passed": passed,
            "return_scrollback": round(came_back, 4),
            "invariant": "no drag_hold misfire and return stroke scrollback < 0.012",
        })

    # 3. half_fist_moving.json: 2s of moving cursor with a half fist
    f3 = fx_dir / "half_fist_moving.json"
    if f3.is_file():
        data = json.loads(f3.read_text(encoding="utf-8"))
        engine = GestureEngine()
        engine.config.update({"record_fling_enabled": False})
        aspect = data["frame_px"][1] / data["frame_px"][0]
        engine.frame_aspect = aspect
        rule_actions = []
        gated_actions = []
        if gate is not None:
            gate.reset()
        for t, lm in data["frames"]:
            pts = [Point3D(*p) for p in lm] if lm else None
            ts = 100.0 + float(t)
            res = engine.process(pts, timestamp=ts)
            rule_actions.append(res.action)
            if gate is not None:
                g_res = _gate_step_and_filter(gate, ts, pts, lm, aspect, res)
                gated_actions.append(g_res.action)
        passed = (set(rule_actions) == {"move_cursor"}) and (
            not gated_actions or set(gated_actions) == {"move_cursor"}
        )
        checks.append({
            "fixture": f3.name,
            "frames": len(data["frames"]),
            "passed": passed,
            "rule_actions": sorted(set(rule_actions)),
            "gated_actions": sorted(set(gated_actions)),
            "invariant": "every frame remains move_cursor with zero false clicks",
        })

    all_passed = bool(checks) and all(c["passed"] for c in checks)
    return {
        "total_fixtures": len(checks),
        "passed_fixtures": sum(1 for c in checks if c["passed"]),
        "all_passed": all_passed,
        "gate_loaded": gate is not None,
        "checks": checks,
    }
