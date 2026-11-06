"""
Training center (2026-11-05): what has been recorded, how well the model knows each gesture, what to record next,
training in the background, and exporting the model for other apps. Used by the Qt screen (src/qt_dashboard.py)
and the console tools (scripts/training_report.py, scripts/export_model.py, studio.py). No Qt here.

The loop it supports: record -> train -> look at each gesture's score -> record more of the weak ones -> train again
-> export.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from src.gesture_catalog import BY_KEY, NAMES, CLASSES, is_free

ROOT = Path(__file__).resolve().parents[1]
REC_DIR = ROOT / "recordings"
MODEL = ROOT / "models" / "gesture_tcn.onnx"
EXPORT_DIR = ROOT / "exports"
BACKUP_DIR = ROOT / "models" / "backups"
SELECTION_FILE_NAME = "training_selection.json"

GOAL_REPS = 30            # repetitions per gesture worth aiming for (docs: 3 postures x 10-15)
GOAL_POSTURES = 3
GOAL_RATE = 0.8           # recognition in postures the model did not train on
GOAL_FREE_MIN = 9.0       # minutes of "nothing" (half-fist moving)
MAX_MISFIRES_PER_MIN = 0.5
STORAGE_LIMIT_MB = 500    # recordings travel with the code in git; past this, suggest a repository of their own


# ---------------------------------------------------------------- recording selection, augmentation settings & deletion
AUGMENT_STRENGTH_PRESETS: Dict[str, Dict[str, Any]] = {
    "light": {
        "key": "light",
        "label": "약하게 (회전 ±20° · 크기 ±8% · 시간 ±8%)",
        "rotation_deg": 20.0,
        "scale_range": 0.08,
        "jitter_std": 0.0015,
        "time_stretch_range": (0.92, 1.08),
        "frame_drop_prob": 0.03,
    },
    "normal": {
        "key": "normal",
        "label": "보통 (회전 ±35° · 크기 ±15% · 시간 ±15%)",
        "rotation_deg": 35.0,
        "scale_range": 0.15,
        "jitter_std": 0.003,
        "time_stretch_range": (0.85, 1.15),
        "frame_drop_prob": 0.05,
    },
    "strong": {
        "key": "strong",
        "label": "강하게 (회전 ±50° · 크기 ±22% · 시간 ±22%)",
        "rotation_deg": 50.0,
        "scale_range": 0.22,
        "jitter_std": 0.005,
        "time_stretch_range": (0.78, 1.22),
        "frame_drop_prob": 0.08,
    },
}


def _selection_path(rec_dir: Path = REC_DIR) -> Path:
    return Path(rec_dir) / SELECTION_FILE_NAME


def _normalize_aug_settings(raw: Any) -> Dict[str, Any]:
    d = raw if isinstance(raw, dict) else {}
    enabled = bool(d.get("enabled", True))
    strength = str(d.get("strength", "normal"))
    if strength not in AUGMENT_STRENGTH_PRESETS:
        strength = "normal"
    try:
        factor = int(d.get("factor", 2))
    except (TypeError, ValueError):
        factor = 2
    factor = max(1, min(8, factor))
    preset = AUGMENT_STRENGTH_PRESETS[strength]
    return {
        "enabled": enabled,
        "strength": strength,
        "strength_label": preset["label"],
        "factor": factor,
        "rotation_deg": preset["rotation_deg"],
        "scale_range": preset["scale_range"],
        "jitter_std": preset["jitter_std"],
        "time_stretch_range": preset["time_stretch_range"],
        "frame_drop_prob": preset["frame_drop_prob"],
    }


def load_training_selection(rec_dir: Path = REC_DIR) -> Dict[str, Any]:
    path = _selection_path(rec_dir)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                excluded = [str(x) for x in data.get("excluded", []) if x]
                aug = _normalize_aug_settings(data.get("augmentation"))
                return {"excluded": sorted(set(excluded)), "augmentation": aug}
        except Exception:
            pass
    return {"excluded": [], "augmentation": _normalize_aug_settings({})}


def save_training_selection(selection: Dict[str, Any], rec_dir: Path = REC_DIR) -> None:
    folder = Path(rec_dir)
    folder.mkdir(parents=True, exist_ok=True)
    current = load_training_selection(folder)
    excluded = sorted({Path(str(x)).name for x in selection.get("excluded", current.get("excluded", [])) if x})
    aug_raw = selection.get("augmentation", current.get("augmentation", {}))
    aug = _normalize_aug_settings(aug_raw)
    payload = {
        "excluded": excluded,
        "augmentation": {
            "enabled": aug["enabled"],
            "strength": aug["strength"],
            "factor": aug["factor"],
        },
    }
    _selection_path(folder).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_augmentation_settings(rec_dir: Path = REC_DIR) -> Dict[str, Any]:
    return _normalize_aug_settings(load_training_selection(rec_dir).get("augmentation"))


def set_augmentation_settings(
    *,
    enabled: Optional[bool] = None,
    strength: Optional[str] = None,
    factor: Optional[int] = None,
    rec_dir: Path = REC_DIR,
) -> Dict[str, Any]:
    sel = load_training_selection(rec_dir)
    aug = dict(sel.get("augmentation") or {})
    if enabled is not None:
        aug["enabled"] = bool(enabled)
    if strength is not None and str(strength) in AUGMENT_STRENGTH_PRESETS:
        aug["strength"] = str(strength)
    if factor is not None:
        aug["factor"] = max(1, min(8, int(factor)))
    sel["augmentation"] = aug
    save_training_selection(sel, rec_dir)
    return get_augmentation_settings(rec_dir)


def is_recording_selected(filename: str, rec_dir: Path = REC_DIR) -> bool:
    name = Path(filename).name
    excluded = set(load_training_selection(rec_dir).get("excluded", []))
    return name not in excluded


def set_recording_selected(filename: str, selected: bool, rec_dir: Path = REC_DIR) -> bool:
    name = Path(filename).name
    sel = load_training_selection(rec_dir)
    excluded = set(sel.get("excluded", []))
    if selected:
        excluded.discard(name)
    else:
        excluded.add(name)
    sel["excluded"] = sorted(excluded)
    save_training_selection(sel, rec_dir)
    return selected


def filter_selected_recording_paths(
    paths: List[Path | str],
    rec_dir: Optional[Path] = None,
    extra_excluded: Optional[List[str]] = None,
) -> List[str]:
    if not paths:
        return []
    folder = Path(rec_dir) if rec_dir is not None else Path(paths[0]).parent
    excluded = set(load_training_selection(folder).get("excluded", []))
    if extra_excluded:
        excluded.update(Path(x).name for x in extra_excluded if x)
    return [str(p) for p in paths if Path(p).name not in excluded]


def delete_recording_file(filename: str, rec_dir: Path = REC_DIR) -> bool:
    folder = Path(rec_dir)
    name = Path(filename).name
    if not name.endswith(".jsonl.gz"):
        return False
    target = folder / name
    removed = False
    for candidate in (
        target,
        folder / f"{name}.summary.json",
        folder / f"{name}.quality.json",
        folder / "cleaned" / name,
    ):
        if candidate.is_file():
            try:
                candidate.unlink()
                if candidate == target:
                    removed = True
            except OSError:
                pass
    stem = name[:-9]
    aug_dir = folder / "augmented"
    if aug_dir.is_dir():
        for aug in aug_dir.glob(f"{stem}_aug*.jsonl.gz"):
            try:
                aug.unlink()
            except OSError:
                pass
    set_recording_selected(name, True, folder)
    return removed


def get_recording_quality(path: Path, *, compute_if_missing: bool = True) -> Dict[str, Any]:
    q_cache = path.with_name(path.name + ".quality.json")
    if q_cache.is_file():
        try:
            data = json.loads(q_cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "score" in data and "status" in data:
                return data
        except Exception:
            pass
    if not compute_if_missing:
        return {"status": "unknown", "score": None, "summary": "품질 미검사", "issues": []}
    try:
        from src.data_quality import check_recording
        rep = check_recording(path)
        d = rep.to_dict()
        try:
            q_cache.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return d
    except Exception as exc:
        return {"status": "fail", "score": 0.0, "summary": f"품질 검사 실패: {exc}", "issues": [str(exc)]}


def evaluate_all_recordings_quality(rec_dir: Path = REC_DIR, *, force: bool = False, auto_exclude_failed: bool = False) -> List[Dict[str, Any]]:
    folder = Path(rec_dir)
    files = sorted(folder.glob("*.jsonl.gz"))
    results = []
    for p in files:
        q_cache = p.with_name(p.name + ".quality.json")
        if force and q_cache.is_file():
            try:
                q_cache.unlink()
            except OSError:
                pass
        q = get_recording_quality(p, compute_if_missing=True)
        if auto_exclude_failed and q.get("status") == "fail":
            set_recording_selected(p.name, False, folder)
        results.append({"file": p.name, "selected": is_recording_selected(p.name, folder), **q})
    return results


# ---------------------------------------------------------------- what has been recorded
_SCREEN_ZONE_LABELS: Dict[Tuple[int, int], str] = {
    (0, 0): "왼쪽 위",
    (1, 0): "위",
    (2, 0): "오른쪽 위",
    (0, 1): "왼쪽",
    (1, 1): "중앙",
    (2, 1): "오른쪽",
    (0, 2): "왼쪽 아래",
    (1, 2): "아래",
    (2, 2): "오른쪽 아래",
}


def _classify_screen_zone(px: float, py: float) -> str:
    """Classify normalized 2D camera coordinate (px, py in 0..1) into a 3x3 screen region."""
    gx = 0 if px < 0.36 else (2 if px > 0.64 else 1)
    gy = 0 if py < 0.36 else (2 if py > 0.64 else 1)
    return _SCREEN_ZONE_LABELS.get((gx, gy), "중앙")


def _format_screen_zones_summary(zone_counts: Dict[str, int], planned_positions: Sequence[str] = ()) -> str:
    """Format a human-readable summary of 3x3 camera screen regions covered in a recording."""
    total = sum(zone_counts.values())
    if total <= 0:
        if planned_positions:
            return "목표 위치: " + " · ".join(planned_positions)
        return "감지된 화면 영역 없음"
    ranked = sorted(zone_counts.items(), key=lambda kv: kv[1], reverse=True)
    top_parts = [f"{z}({int(round(100.0 * c / total))}%)" for z, c in ranked[:4] if c > 0]
    zone_str = f"화면 {len(ranked)}/9영역: " + " · ".join(top_parts)
    if len(ranked) > 4:
        zone_str += f" 외 {len(ranked) - 4}곳"
    if planned_positions:
        zone_str += f" (지정 위치: {', '.join(planned_positions[:3])})"
    return zone_str


def _build_recording_intent_fields(
    mode: Optional[str],
    focus_key: str,
    intended_tasks: List[str],
    reps_dict: Dict[str, Dict[str, int]],
    free_minutes: Dict[str, float],
    conditions: List[str],
) -> Dict[str, Any]:
    """Build explicit human-readable descriptions of which gesture(s) and postures a recording is capturing."""
    ordered_keys: List[str] = []
    if focus_key and focus_key in BY_KEY:
        ordered_keys.append(focus_key)
    for tk in intended_tasks:
        if tk in BY_KEY and tk not in ordered_keys:
            ordered_keys.append(tk)
    for tk, by_p in reps_dict.items():
        if sum(by_p.values()) > 0 and tk in BY_KEY and tk not in ordered_keys:
            ordered_keys.append(tk)
    for tk, mins in free_minutes.items():
        if mins > 0.01 and tk in BY_KEY and tk not in ordered_keys:
            ordered_keys.append(tk)
    if not ordered_keys and mode == "free":
        ordered_keys.append("none")

    non_free_keys = [k for k in ordered_keys if k != "none"]
    display_keys = non_free_keys + ([k for k in ordered_keys if k == "none"] if not non_free_keys or len(ordered_keys) <= 3 else [])

    gesture_parts: List[str] = []
    short_parts: List[str] = []
    instructions: List[str] = []
    for k in display_keys:
        g_name = NAMES.get(k, k)
        task_obj = BY_KEY.get(k)
        rep_cnt = sum((reps_dict.get(k) or {}).values())
        f_min = float(free_minutes.get(k, 0.0))
        if is_free(k):
            if f_min >= 0.1:
                gesture_parts.append(f"{g_name} (자유 {f_min:.1f}분)")
            else:
                gesture_parts.append(f"{g_name} (오작동 방지 이동)")
            short_parts.append(g_name)
        else:
            if rep_cnt > 0:
                gesture_parts.append(f"{g_name} ({rep_cnt}회 반복)")
                short_parts.append(f"{g_name} {rep_cnt}회")
            else:
                gesture_parts.append(f"{g_name} (목표 지정됨 · 완료 반복 0회)")
                short_parts.append(f"{g_name}")
        if task_obj and task_obj.do and task_obj.do not in instructions:
            instructions.append(f"{g_name}: {task_obj.do}" if len(display_keys) > 1 else task_obj.do)

    mode_ko = "자유 이동 녹화" if mode == "free" else ("집중 가이드 녹화" if focus_key else "통합 가이드 녹화")
    target_gestures_text = " · ".join(gesture_parts) if gesture_parts else ("자유 이동 (그냥 이동)" if mode == "free" else "지정된 동작 없음")
    target_short_text = " · ".join(short_parts[:2]) + (f" 외 {len(short_parts) - 2}개" if len(short_parts) > 2 else "")
    if not target_short_text:
        target_short_text = "자유 이동" if mode == "free" else "미지정"
    target_conditions_text = " · ".join(conditions) if conditions else ("일상 마우스 사용 자세" if mode == "free" else "기본 촬영 자세")
    target_instruction_text = " / ".join(instructions[:2]) if instructions else "카메라 앞에서 안내된 손동작을 수행합니다."

    return {
        "focus": focus_key,
        "recorded_mode_label": mode_ko,
        "intended_tasks": ordered_keys,
        "conditions": conditions,
        "target_gestures_text": target_gestures_text,
        "target_short_text": target_short_text,
        "target_conditions_text": target_conditions_text,
        "target_instruction_text": target_instruction_text,
    }


def _summarise_file(path: Path) -> Dict[str, Any]:
    """Repetitions per gesture and posture, minutes of free moving, marks - cached next to the recording."""
    cache = path.with_name(path.name + ".summary.json")
    stat = path.stat()
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("summary_version") == 5 and data.get("size") == stat.st_size and data.get("mtime") == int(stat.st_mtime):
                return data
        except Exception:
            pass
    from src.recorder import public_source, read_session

    meta, frames, events = read_session(path)
    if not meta.get("complete"):
        raise ValueError("끝나지 않은 녹화 (학습에서 빠짐)")
    reps: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    from src.capture_dataset import frame_durations, usable
    from src.recording_plan import label_condition_key
    repetitions = defaultdict(lambda: {"do": 0.0, "rest": False})
    free = defaultdict(float)
    seconds = frame_durations(frames)
    coverage = {k: {} for k in NAMES}
    valid_frames = 0
    pose_frames = 0
    intended_tasks: List[str] = []
    conditions: List[str] = []
    planned_positions: List[str] = []
    zone_counts: Dict[str, int] = defaultdict(int)
    for row_cfg in (meta.get("capture_plan") or {}).get("conditions") or []:
        if isinstance(row_cfg, dict) and row_cfg.get("enabled", True):
            pos_val = str(row_cfg.get("position") or "").strip()
            if pos_val and pos_val not in planned_positions:
                planned_positions.append(pos_val)
    focus_key = str(meta.get("focus") or (meta.get("capture_plan") or {}).get("focus") or "")
    for i, f in enumerate(frames):
        if isinstance(f.get("pose"), list) and len(f["pose"]) >= 6:
            pose_frames += 1
        lm_f = f.get("lm")
        if isinstance(lm_f, list) and len(lm_f) == 21:
            try:
                px = sum(float(lm_f[j][0]) for j in (0, 5, 9, 13, 17)) / 5.0
                py = sum(float(lm_f[j][1]) for j in (0, 5, 9, 13, 17)) / 5.0
                zone_counts[_classify_screen_zone(px, py)] += 1
            except Exception:
                pass
        lab = f.get("label") or {}
        pos_lbl = lab.get("position")
        if isinstance(pos_lbl, str) and pos_lbl.strip() and pos_lbl.strip() not in planned_positions:
            planned_positions.append(pos_lbl.strip())
        t_cand = lab.get("task") or lab.get("target")
        if isinstance(t_cand, str) and t_cand in BY_KEY and t_cand not in intended_tasks:
            intended_tasks.append(t_cand)
        cond_str = lab.get("condition")
        if isinstance(cond_str, str) and cond_str.strip() and cond_str.strip() not in conditions:
            conditions.append(cond_str.strip())
        if not usable(f):
            continue
        valid_frames += 1
        ck = label_condition_key(lab)
        task = lab.get("task")
        if lab.get("phase") in ("do", "rest") and task in BY_KEY and not is_free(task):
            key = (lab["task"], ck, lab.get("set"), lab.get("rep"))
            item = repetitions[key]
            item["new"] = bool(lab.get("condition_key"))
            if lab["phase"] == "do":
                item["do"] += seconds[i]
            else:
                item["rest"] = True
        elif lab.get("phase") == "free" and is_free(task):
            free[(task, ck)] += seconds[i]
        elif meta.get("mode") == "free" and lab.get("target") == "none":
            free[("none", "use")] += seconds[i]
    for (task, ck, _, _), item in repetitions.items():
        if item["do"] > 0 and (not item.get("new") or (item["do"] >= 0.25 and item["rest"])):
            reps[task][ck] += 1
    for task, by_condition in reps.items():
        coverage[task] = {ck: {"reps": n, "seconds": 0.0} for ck, n in by_condition.items()}
    for (task, ck), value in free.items():
        coverage[task][ck] = {"reps": 0, "seconds": value}
    reps_plain = {k: dict(v) for k, v in reps.items()}
    free_mins = {task: sum(v for (t, _), v in free.items() if t == task) / 60 for task in NAMES if is_free(task)}
    intent_fields = _build_recording_intent_fields(
        meta.get("mode"),
        focus_key,
        intended_tasks,
        reps_plain,
        free_mins,
        conditions,
    )
    screen_zones_dict = dict(zone_counts)
    screen_zones_text = _format_screen_zones_summary(screen_zones_dict, planned_positions)
    data = {
        "summary_version": 5,
        "file": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime), "mode": meta.get("mode"),
        "created": meta.get("created"), "pc": public_source(meta)[0], "camera": public_source(meta)[1],
        "frames": len(frames), "reps": reps_plain,
        "free_minutes": free_mins,
        "coverage": coverage, "valid_frames": valid_frames, "pose_frames": pose_frames,
        "screen_zones": screen_zones_dict,
        "screen_zones_text": screen_zones_text,
        "planned_positions": planned_positions,
        "seconds": float(seconds.sum()),
        "marks": sum(1 for e in events if e.get("kind") == "mark"),
        **intent_fields,
    }
    try:
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def dataset_summary(rec_dir: Path = REC_DIR, *, compute_quality: bool = True) -> Dict[str, Any]:
    folder = Path(rec_dir)
    files = sorted(folder.glob("*.jsonl.gz"))
    excluded = set(load_training_selection(folder).get("excluded", []))
    per: Dict[str, Dict[str, Any]] = {k: {"reps": 0, "postures": {}, "minutes": 0.0} for k in NAMES}
    out_files = []
    coverage = {k: {} for k in NAMES}
    selected_mtimes = []
    for p in files:
        is_sel = p.name not in excluded
        try:
            s = dict(_summarise_file(p))
        except Exception as exc:          # a broken / unfinished file must not hide the others
            out_files.append({
                "file": p.name, "error": str(exc), "selected": False,
                "quality_status": "fail", "quality_score": 0.0, "quality_summary": str(exc),
            })
            continue
        q = get_recording_quality(p, compute_if_missing=compute_quality)
        s["selected"] = is_sel
        s["quality_status"] = q.get("status", "unknown")
        s["quality_score"] = q.get("score")
        s["quality_summary"] = q.get("summary", "")
        s["quality_issues"] = q.get("issues", [])
        out_files.append(s)
        if not is_sel:
            continue
        selected_mtimes.append(p.stat().st_mtime)
        for task, conditions in s.get("coverage", {}).items():
            for key, values in conditions.items():
                current = coverage[task].setdefault(key, {"reps": 0, "seconds": 0.0})
                current["reps"] += values["reps"]
                current["seconds"] += values["seconds"]
        for task, by_posture in s["reps"].items():
            g = per.setdefault(task, {"reps": 0, "postures": {}, "minutes": 0.0})
            for posture, n in by_posture.items():
                g["reps"] += n
                g["postures"][posture] = g["postures"].get(posture, 0) + n
        for task, minutes in s["free_minutes"].items():
            per[task]["minutes"] += minutes
    newest = max(selected_mtimes, default=0.0)
    total = sum(p.stat().st_size for p in files)
    return {
        "files": out_files,
        "per_gesture": per,
        "newest": newest,
        "total_bytes": total,
        "coverage": coverage,
        "excluded_count": sum(1 for f in out_files if not f.get("selected", True) and "error" not in f),
    }


def storage_text(summary: Dict[str, Any]) -> Tuple[str, bool]:
    """(text, too big) for the recordings kept in the git repository together with the code (user's choice,
    2026-11-05). Past STORAGE_LIMIT_MB: suggest moving them to a repository of their own."""
    mb = summary.get("total_bytes", 0) / 1024 / 1024
    if mb > STORAGE_LIMIT_MB:
        return (f"녹화 총 {mb:.0f} MB: {STORAGE_LIMIT_MB} MB를 넘었어요. 받기가 느려지니 학습 데이터를 "
                f"전용 저장소로 나누는 걸 권해요 (docs/training-studio.md)", True)
    return f"녹화 총 {mb:.0f} MB / 분리 권장 {STORAGE_LIMIT_MB} MB", False


def model_report(model: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    model = Path(model) if model is not None else MODEL
    info_path = Path(model).with_suffix(".json")
    if not Path(model).exists() or not info_path.exists():
        return None
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["mtime"] = Path(model).stat().st_mtime
    from src.gesture_models import read_bank
    info["specialists"] = {}
    for label, entry in read_bank(Path(model).parent / "gestures").items():
        path = Path(model).parent / "gestures" / entry["card"]
        card = json.loads(path.read_text(encoding="utf-8"))
        card["mtime"] = path.stat().st_mtime
        info["specialists"][label] = card
    return info


# ---------------------------------------------------------------- what to do next
def gesture_rows(summary: Dict[str, Any], report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per gesture: data, scores and advice, for the screen."""
    rows = []
    pg = (report or {}).get("per_gesture", {})
    disabled = set((report or {}).get("disabled_gestures") or ())
    for key, name in NAMES.items():
        task = BY_KEY[key]
        d = summary["per_gesture"].get(key, {"reps": 0, "postures": {}, "minutes": 0.0})
        specialist = (report or {}).get("specialists", {}).get(task.label)
        is_disabled = (task.label in disabled) and (specialist is None)
        current = {} if is_disabled else (specialist or report or {})
        m = {} if is_disabled else current.get("per_task", {}).get(key, current.get("per_gesture", pg).get(task.label, {}))
        classes = [c for c in current.get("classes", list(pg)) if c not in disabled or specialist is not None]
        is_trained = bool(specialist) or (not is_disabled and task.label != "none" and task.label in classes)
        stale = bool(current) and summary.get("newest", 0) > current.get("mtime", 0)
        if specialist:
            model_text = "선택 동작 모델 · " + str(specialist.get("trained", ""))
        elif is_disabled:
            model_text = "학습 초기화됨 · 현재 규칙으로 동작"
        elif task.label in classes:
            model_text = "TCN 학습됨"
        else:
            model_text = "TCN 학습 전 · 현재 규칙으로 동작"
        row = {"key": key, "name": name, "reps": d["reps"], "postures": len(d["postures"]),
               "category": task.category, "instruction": task.do,
               "model_text": model_text,
               "can_train": task.label != "none", "label": task.label,
               "is_trained": is_trained, "is_disabled": is_disabled,
               "minutes": d.get("minutes", 0.0), "train_accuracy": m.get("train_accuracy"),
               "rate": m.get("rate"), "rules_rate": m.get("rules_rate"),
               "misfires_per_min": m.get("misfires_per_min"), "rules_misfires_per_min": m.get("rules_misfires_per_min")}
        advice, level = [], "ok"
        if task.free:
            data_ok = row["minutes"] >= GOAL_FREE_MIN
            row["data_text"] = f"{row['minutes']:.1f}분 / 목표 {GOAL_FREE_MIN:.0f}분"
            row["data_ratio"] = min(1.0, row["minutes"] / GOAL_FREE_MIN)
            if not data_ok:
                advice.append(f"이 이동 동작의 녹화가 부족해요 ({GOAL_FREE_MIN:.0f}분 이상 권장)")
            if row["misfires_per_min"] is not None and row["misfires_per_min"] > MAX_MISFIRES_PER_MIN:
                advice.append("오작동이 많아요: 반주먹 이동을 더 녹화하세요")
            score = row["misfires_per_min"]
            row["score_text"] = "아직 평가 없음" if score is None else f"오작동 {score:.1f}회/분"
            if score is not None and row["rules_misfires_per_min"] is not None:
                row["score_text"] += f" (규칙 {row['rules_misfires_per_min']:.1f})"
        else:
            data_ok = row["reps"] >= GOAL_REPS and row["postures"] >= GOAL_POSTURES
            row["data_text"] = f"{row['reps']}회 / 목표 {GOAL_REPS}회 · 자세 {row['postures']}/{GOAL_POSTURES}"
            row["data_ratio"] = min(1.0, row["reps"] / GOAL_REPS)
            if row["reps"] < GOAL_REPS:
                advice.append(f"데이터가 부족해요 ({GOAL_REPS}회 이상 권장)")
            if row["postures"] < GOAL_POSTURES:
                advice.append("여러 자세로 녹화하세요")
            if row["rate"] is not None and row["rate"] < GOAL_RATE:
                advice.append(f"인식률이 낮아요 ({row['rate'] * 100:.0f}%): 이 동작을 집중 녹화하세요")
            row["score_text"] = "아직 평가 없음" if row["rate"] is None else f"인식 {row['rate'] * 100:.0f}%"
            if row["rate"] is not None and row["rules_rate"] is not None:
                row["score_text"] += f" (규칙 {row['rules_rate'] * 100:.0f}%)"
        if row["train_accuracy"] is not None:
            row["fit_text"] = f"학습 {row['train_accuracy'] * 100:.0f}%"
        else:
            row["fit_text"] = "학습 전"
        if stale:
            advice.append("새 녹화가 있어요: 다시 학습하세요")
        if report is None:
            advice.append("아직 학습한 모델이 없어요")
        elif is_disabled:
            advice.append("학습이 초기화되어 규칙으로 동작 중입니다 (이 동작만 학습으로 다시 추가 가능)")
        elif task.label not in classes:
            advice.append("이 동작을 녹화하고 다시 학습하면 모델에 추가됩니다")
        if advice:
            level = "need" if any(("부족" in a or "낮아요" in a or "많아요" in a) for a in advice) else "retrain"
        row["advice"] = " · ".join(advice) if advice else "좋아요"
        row["level"] = level
        rows.append(row)
    return rows


# ---------------------------------------------------------------- training in the background
def terminate_training_process(proc):
    """Windows venv launchers have a child interpreter; stop that child as well."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    else:
        proc.terminate()


class TrainingJob:
    """Runs scripts/train_tcn.py --folds --progress in its own process (torch stays out of the app) and keeps its state."""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.state: Dict[str, Any] = {"running": False}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self.state.get("running"))

    def start(self, epochs: int = 25, gestures=None, *, offline_augment: Optional[bool] = None) -> bool:
        if self.running:
            return False
        if offline_augment is None:
            offline_augment = bool(get_augmentation_settings().get("enabled", True))
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        args = [sys.executable, str(ROOT / "scripts" / "train_tcn.py"), "--folds", "--progress", "--epochs", str(epochs)]
        if offline_augment:
            args.append("--offline-augment")
        if gestures is not None:
            if not gestures or any(key not in BY_KEY or BY_KEY[key].label == "none" for key in gestures):
                raise ValueError("학습할 동작을 선택하세요. 중립은 오답 자료로 함께 사용됩니다.")
            args.extend(["--gestures", *gestures])
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", env=env, creationflags=flags)
        with self._lock:
            self.state = {"running": True, "stage": "데이터 읽는 중", "fold": 0, "folds": 0, "epoch": 0, "epochs": epochs,
                          "progress": 0.0, "started": time.time(), "log": []}
        threading.Thread(target=self._read, daemon=True).start()
        return True

    def record_quality_result(self, report_dict: Dict[str, Any], message: str) -> None:
        with self._lock:
            self.state["last_quality"] = report_dict
            log = list(self.state.get("log", []))
            log.append(message)
            self.state["log"] = log[-20:]
            if report_dict.get("status") == "fail":
                self.state["quality_warning"] = message
            else:
                self.state.pop("quality_warning", None)

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                with self._lock:
                    self.state["log"] = (self.state.get("log", []) + [line])[-20:]
                continue
            self._apply(ev)
        code = self.proc.wait()
        with self._lock:
            self.state["running"] = False
            if code != 0 and not self.state.get("error"):
                self.state["error"] = "학습이 실패했어요: " + " / ".join(self.state.get("log", [])[-3:])
            self.state["finished"] = time.time()

    def _apply(self, ev: Dict[str, Any]) -> None:
        with self._lock:
            s = self.state
            kind = ev.get("event")
            if kind == "gesture_start":
                s["gesture_index"], s["gesture_count"] = ev["index"], ev["count"]
                s["target"] = ev["target"]
                s["stage"] = "선택 동작 학습 · " + NAMES.get(ev["target"], ev["target"])
            elif kind == "loaded":
                s["folds"] = int(ev.get("folds", 0))
                s["windows"] = ev.get("windows")
                s["stage"] = f"데이터 {ev.get('windows')}개 구간"
            elif kind == "fold_start":
                s["fold"] = int(ev.get("fold", 0))
                s["stage"] = f"평가 학습 {s['fold']}/{s['folds']} (자세 {ev.get('posture')} 빼고)"
                s["epoch"], s["losses"] = 0, []
            elif kind == "final_start":
                s["fold"] = s["folds"] + 1
                s["stage"] = "최종 모델 학습"
                s["epoch"], s["losses"] = 0, []
            elif kind == "epoch":
                s["epoch"], s["loss"] = int(ev["epoch"]), float(ev["loss"])
                s.setdefault("losses", []).append(float(ev["loss"]))
            elif kind == "fold_result":
                s.setdefault("results", []).append(ev)
            elif kind == "done":
                s["stage"] = "끝! 새 모델 저장됨"
                s["per_gesture"] = ev.get("per_gesture")
            elif kind == "skipped":
                s.setdefault("skipped", []).append(ev.get("file"))
            elif kind == "note":
                s["log"] = (s.get("log", []) + [ev.get("text", "")])[-20:]
            elif kind == "error":
                s["error"] = ev.get("text")
            steps = s["folds"] + 1
            done = (max(0, s["fold"] - 1) + s["epoch"] / max(1, s["epochs"])) / max(1, steps)
            s["progress"] = 1.0 if kind == "done" else round(min(0.99, done), 3)
            if kind != "done" and s.get("gesture_count"):
                s["progress"] = round((s["gesture_index"] - 1 + s["progress"]) / s["gesture_count"], 3)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def stop(self) -> None:
        if self.proc is not None and self.running:
            with self._lock:
                self.state["error"] = "학습 중단을 요청했습니다. 게시 전 모델은 적용되지 않습니다."
            terminate_training_process(self.proc)


# ---------------------------------------------------------------- export for other apps
EXAMPLE = '''"""
Use the exported gesture model in another program (only numpy and onnxruntime needed).

For every camera frame, give it MediaPipe Hand Landmarker's results for one hand:
  world  = result.hand_world_landmarks[i]  -> 21 (x, y, z) in metres
  image  = result.hand_landmarks[i]        -> 21 (x, y, z) normalised to the frame
and the frame aspect (height / width). It keeps the last {window} frames and returns the class probabilities.
"""
import json
from collections import deque

import numpy as np
from tcn_features import FEATURE_VERSION, frame_features, windows
from gesture_models import load_single, compose_predictor


class GestureModel:
    def __init__(self, folder="."):
        from pathlib import Path
        path = Path(folder) / "gesture_tcn.onnx"
        base, info = load_single(path, FEATURE_VERSION)
        self.predict, self.info = compose_predictor(path, base, info)
        self.frames = deque(maxlen=self.info["window"] + 1)

    def step(self, t, world, image, aspect):
        """t: seconds; world / image: 21 x (x, y, z) or None when no hand. Returns {{class: probability}}."""
        self.frames.append({{"t": t, "world": world, "lm": image}} if world else {{"t": t}})
        x = windows(frame_features(list(self.frames), aspect), self.info["window"])[-1:]
        p = self.predict(x)
        return dict(zip(self.info["classes"], p.tolist()))

# A gesture counts when its probability stays >= info["threshold"] for info["frames_in_a_row"] frames in a row.
'''


def export_package(out_root: Path = EXPORT_DIR, with_dataset: bool = True) -> Path:
    """exports/gesture-tcn-<time>/ (+ .zip): model, its info, the feature code, an example, a README, and the
    per-frame features + labels of every recording (dataset.npz) for training elsewhere."""
    report = model_report()
    if report is None:
        raise FileNotFoundError("학습한 모델이 없어요. 먼저 학습하세요.")
    from uuid import uuid4
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    out = Path(out_root) / f"gesture-tcn-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODEL, out / "gesture_tcn.onnx")
    shutil.copy2(MODEL.with_suffix(".json"), out / "gesture_tcn.json")
    shutil.copy2(ROOT / "src" / "tcn_features.py", out / "tcn_features.py")
    shutil.copy2(ROOT / "src" / "gesture_models.py", out / "gesture_models.py")
    from src.gesture_models import SCHEMA, read_bank
    entries = read_bank(MODEL.parent / "gestures")
    if entries:
        target = out / "gestures"
        target.mkdir()
        for entry in entries.values():
            for key in ("model", "card"):
                shutil.copy2(MODEL.parent / "gestures" / entry[key], target / entry[key])
        (target / "manifest.json").write_text(
            json.dumps({"schema": SCHEMA, "models": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "example_use.py").write_text(EXAMPLE.format(window=report["window"]), encoding="utf-8")
    pg = report.get("per_gesture", {})
    lines = [f"# 손짓 TCN 모델 ({report.get('trained')})", "",
             f"- 정답 종류: {', '.join(report['classes'])}", f"- 입력: 최근 {report['window']}프레임, 특징 {report['features']}개 ({report['feature_version']})",
             f"- 판정: 확률 {report['threshold']} 이상이 {report['frames_in_a_row']}프레임 연속", "",
             "## 데이터 출처"] + [f"- {d['file']} · {d.get('pc')} · {(d.get('camera') or {}).get('note', '')} · {d['frames']}프레임" for d in report.get("data", [])] + [
             "", "## 동작별 성적 (학습에 안 쓴 자세)"]
    for key, g in pg.items():
        if "rate" in g:
            pct = lambda v: "-" if v is None else f"{v * 100:.0f}%"  # noqa: E731
            lines.append(f"- {key}: 인식 {g['recognised']}/{g['total']} = {pct(g.get('rate'))} (규칙만 {pct(g.get('rules_rate'))})"
                         f" · 학습 데이터 정확도 {pct(g.get('train_accuracy'))}")
        else:
            lines.append(f"- {key}: 오작동 {g.get('misfires_per_min')}회/분 (규칙 {g.get('rules_misfires_per_min')}회/분)")
    lines += ["", "## 쓰는 법", "`example_use.py` 참고. 필요한 것: numpy, onnxruntime, MediaPipe Hand Landmarker(world landmarks 포함)."]
    if entries:
        lines += ["", "## 선택 학습 모델", "선택 모델은 해당 동작에 우선 적용되며 각 확률은 독립적입니다.",
                  ", ".join(entries)]
    if with_dataset:
        lines += ["", "## dataset.npz", "녹화 전체의 프레임별 특징 `x` (n, features), 정답 `y` (-1 = 학습 제외, 그 외 classes 순서), "
                  "자세 `posture`, 녹화 번호 `recording`. 다른 프레임워크에서 다시 학습할 때 쓴다."]
        _export_dataset(out / "dataset.npz", report)
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.make_archive(str(out), "zip", root_dir=out)
    return out


def _export_dataset(path: Path, report: Dict[str, Any]) -> None:
    import numpy as np

    from src.recorder import finished_recordings
    from src.capture_dataset import load_sessions

    classes = CLASSES
    xs, ys, ps, fs, tasks, segments = [], [], [], [], [], []
    sessions = load_sessions(finished_recordings(REC_DIR)[0], include_windows=False)
    if not sessions:
        raise ValueError("내보낼 완성된 녹화가 없습니다.")
    for i, session in enumerate(sessions):
        xs.append(session["features"])
        ys.append(session["y"])
        ps.append(session["cond"])
        fs.append(np.full(len(session["frames"]), i, dtype=np.int32))
        tasks.extend((f.get("label") or {}).get("task", "") for f in session["frames"])
        segments.append(session["segments"])
    np.savez_compressed(path, x=np.concatenate(xs), y=np.concatenate(ys), posture=np.concatenate(ps),
                        recording=np.concatenate(fs), classes=np.array(classes),
                        condition_keys=np.array(sessions[0]["condition_groups"]),
                        task=np.array(tasks), segment=np.concatenate(segments), model_classes=np.array(report["classes"]))


# ---------------------------------------------------------------- post-recording pipeline
def run_post_recording_pipeline(
    path: Path | str,
    config: Optional[Dict[str, Any]] = None,
    *,
    training_job: Optional[TrainingJob] = None,
    epochs: int = 25,
) -> Dict[str, Any]:
    """Execute the post-recording pipeline on a finished recording file:

    1. Quality check (`check_recording`) if `auto_quality_check` is enabled.
    2. If status is `fail`: record warning/log for the training center and stop immediately.
    3. If status is `warning` or `pass` and `auto_clean` is enabled: run `clean_recording`.
    4. If `auto_offline_augment` is enabled: run `augment_recording_file`.
    5. If `auto_retrain_after_recording` is enabled and quality >= `min_quality_score`: trigger retraining.
    """
    from src.data_quality import QualityReport, check_recording, clean_recording
    from src.offline_augment import augment_recording_file

    cfg = config or {}
    rec_path = Path(path)

    auto_quality_check = bool(cfg.get("auto_quality_check", True))
    auto_clean = bool(cfg.get("auto_clean", True))
    auto_offline_augment = bool(cfg.get("auto_offline_augment", True))
    auto_retrain = bool(cfg.get("auto_retrain_after_recording", False))
    min_quality_score = float(cfg.get("min_quality_score", 0.7))
    aug_factor = int(cfg.get("offline_augment_factor", 4))
    aug_rot = float(cfg.get("offline_augment_rotation_deg", 40.0))
    aug_scale = float(cfg.get("offline_augment_scale", 0.15))

    report: Optional[QualityReport] = None
    if auto_quality_check or auto_clean or auto_retrain:
        report = check_recording(rec_path, min_quality_score=min_quality_score)
        try:
            q_cache = rec_path.with_name(rec_path.name + ".quality.json")
            q_cache.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    if auto_quality_check and report is not None and report.status == "fail":
        try:
            set_recording_selected(rec_path.name, False, rec_path.parent)
        except Exception:
            pass
        msg = f"품질 미달로 학습 제외 ({report.score:.2f}): {report.summary}"
        if training_job is not None:
            training_job.record_quality_result(report.to_dict(), msg)
        return {
            "path": str(rec_path),
            "status": "fail",
            "score": report.score,
            "report": report,
            "cleaned_path": None,
            "augmented_paths": [],
            "retrain_eligible": False,
            "retrain_started": False,
            "message": msg,
        }

    cleaned_path: Optional[Path] = None
    if auto_clean:
        try:
            cleaned_path = clean_recording(rec_path, report)
        except Exception as exc:
            cleaned_path = None
            if report is not None:
                report.summary += f" (정제 실패: {exc})"

    augmented_paths: List[Path] = []
    if auto_offline_augment:
        src_for_aug = cleaned_path if cleaned_path is not None else rec_path
        try:
            augmented_paths = augment_recording_file(
                src_for_aug,
                factor=aug_factor,
                rotation_deg=aug_rot,
                scale_range=aug_scale,
            )
        except Exception:
            augmented_paths = []

    quality_sufficient = (
        report is None
        or (report.status != "fail" and report.score >= min_quality_score)
    )
    retrain_eligible = bool(auto_retrain and quality_sufficient)
    retrain_started = False
    if retrain_eligible and training_job is not None and not training_job.running:
        retrain_started = training_job.start(epochs=epochs, offline_augment=auto_offline_augment)

    parts = []
    if report is not None:
        parts.append(report.summary)
    if cleaned_path is not None:
        parts.append("자동 정제 완료")
    if augmented_paths:
        parts.append(f"오프라인 증강 {len(augmented_paths)}배 생성")
    if retrain_started:
        parts.append("자동 재학습 시작")
    elif auto_retrain and not quality_sufficient and report is not None:
        parts.append(f"품질 점수 부족({report.score:.2f}<{min_quality_score:.2f})으로 자동 재학습 보류")

    msg = " · ".join(parts) if parts else "녹화 후처리 완료"
    if training_job is not None and report is not None:
        training_job.record_quality_result(report.to_dict(), msg)

    return {
        "path": str(rec_path),
        "status": report.status if report is not None else "pass",
        "score": report.score if report is not None else 1.0,
        "report": report,
        "cleaned_path": str(cleaned_path) if cleaned_path is not None else None,
        "augmented_paths": [str(p) for p in augmented_paths],
        "retrain_eligible": retrain_eligible,
        "retrain_started": retrain_started,
        "message": msg,
    }


# ---------------------------------------------------------------- model backup, rollback & per-gesture reset
def backup_current_model(
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
    reason: str = "before-train",
    max_backups: int = 12,
) -> Optional[Path]:
    """Snapshot the current model + specialist bank before overwriting or resetting."""
    card = model.with_suffix(".json")
    bank_dir = model.parent / "gestures"
    has_main = model.is_file() and card.is_file()
    has_bank = (bank_dir / "manifest.json").is_file()
    if not has_main and not has_bank:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = Path(backup_dir) / f"backup-{stamp}-{reason}"
    suffix = 1
    while folder.exists():
        suffix += 1
        folder = Path(backup_dir) / f"backup-{stamp}-{reason}-{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    if has_main:
        shutil.copy2(model, folder / model.name)
        shutil.copy2(card, folder / card.name)
    if has_bank:
        dst_bank = folder / "gestures"
        dst_bank.mkdir(parents=True, exist_ok=True)
        for item in bank_dir.glob("*"):
            if item.is_file() and item.name != "training.lock":
                shutil.copy2(item, dst_bank / item.name)
    meta = {
        "id": folder.name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": time.time(),
        "reason": reason,
    }
    (folder / "backup_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    all_backups = sorted(
        [d for d in Path(backup_dir).glob("backup-*") if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for old in all_backups[max_backups:]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def _describe_backup_folder(folder: Path, source_kind: str) -> Optional[Dict[str, Any]]:
    card = folder / "gesture_tcn.json"
    bank_idx = folder / "gestures" / "manifest.json"
    if not card.is_file() and not bank_idx.is_file():
        return None
    trained = ""
    classes: List[str] = []
    disabled: List[str] = []
    rates: List[str] = []
    if card.is_file():
        try:
            c = json.loads(card.read_text(encoding="utf-8"))
            trained = str(c.get("trained", ""))
            classes = [str(x) for x in c.get("classes", []) if x != "none"]
            disabled = [str(x) for x in c.get("disabled_gestures", [])]
            pg = c.get("per_gesture", {})
            for cls in classes:
                if cls in disabled:
                    continue
                r = (pg.get(cls) or {}).get("rate")
                if r is not None:
                    rates.append(f"{cls}:{r * 100:.0f}%")
        except Exception:
            pass
    specialists: List[str] = []
    if bank_idx.is_file():
        try:
            idx = json.loads(bank_idx.read_text(encoding="utf-8"))
            specialists = list((idx.get("models") or {}).keys())
        except Exception:
            pass
    meta_file = folder / "backup_meta.json"
    reason = "내보내기 패키지" if source_kind == "export" else "자동 백업"
    created = trained or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(folder.stat().st_mtime))
    if meta_file.is_file():
        try:
            m = json.loads(meta_file.read_text(encoding="utf-8"))
            created = str(m.get("created_at") or created)
            r_code = str(m.get("reason", ""))
            reason_map = {
                "before-train": "학습 직전 자동 백업",
                "before-selected-train": "선택 학습 직전 백업",
                "before-reset": "동작 초기화 직전 백업",
                "manual": "수동 백업",
            }
            reason = reason_map.get(r_code, r_code or reason)
        except Exception:
            pass
    active_gestures = sorted(set([c for c in classes if c not in disabled] + specialists))
    summary_parts = [reason, f"동작 {len(active_gestures)}개"]
    if rates:
        summary_parts.append(" ".join(rates[:3]))
    return {
        "id": f"{source_kind}:{folder.name}",
        "name": folder.name,
        "source": source_kind,
        "path": str(folder),
        "created": created,
        "reason": reason,
        "gestures": active_gestures,
        "summary": f"{created} · {' · '.join(summary_parts)}",
        "mtime": folder.stat().st_mtime,
    }


def list_model_backups(backup_dir: Path = BACKUP_DIR, export_dir: Path = EXPORT_DIR) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if Path(backup_dir).is_dir():
        for d in Path(backup_dir).glob("backup-*"):
            if d.is_dir():
                info = _describe_backup_folder(d, "backup")
                if info is not None:
                    items.append(info)
    if Path(export_dir).is_dir():
        for d in Path(export_dir).glob("gesture-tcn-*"):
            if d.is_dir():
                info = _describe_backup_folder(d, "export")
                if info is not None:
                    items.append(info)
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def restore_model_backup(
    backup_id: str,
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
    export_dir: Path = EXPORT_DIR,
) -> Dict[str, Any]:
    """Restore `models/gesture_tcn.onnx` (+ `.json`) and `models/gestures/` from a backup or export folder."""
    if ":" in backup_id:
        kind, folder_name = backup_id.split(":", 1)
    else:
        kind, folder_name = "backup", backup_id
    src_root = Path(export_dir) if kind == "export" else Path(backup_dir)
    src_folder = src_root / Path(folder_name).name
    if not src_folder.is_dir():
        raise FileNotFoundError(f"백업을 찾을 수 없습니다: {backup_id}")

    src_model = src_folder / "gesture_tcn.onnx"
    src_card = src_folder / "gesture_tcn.json"
    src_bank = src_folder / "gestures"
    if not (src_model.is_file() and src_card.is_file()) and not (src_bank / "manifest.json").is_file():
        raise FileNotFoundError(f"백업 폴더에 복원 가능한 모델 파일이 없습니다: {src_folder.name}")

    model.parent.mkdir(parents=True, exist_ok=True)
    card = model.with_suffix(".json")
    if src_model.is_file() and src_card.is_file():
        shutil.copy2(src_model, model)
        shutil.copy2(src_card, card)

    bank_dir = model.parent / "gestures"
    if bank_dir.is_dir():
        for f in bank_dir.glob("*"):
            if f.is_file() and f.name != "training.lock":
                try:
                    f.unlink()
                except OSError:
                    pass
    if (src_bank / "manifest.json").is_file():
        bank_dir.mkdir(parents=True, exist_ok=True)
        for f in src_bank.glob("*"):
            if f.is_file() and f.name != "training.lock":
                shutil.copy2(f, bank_dir / f.name)

    return {"restored": src_folder.name, "path": str(src_folder)}


def reset_gesture_training(
    key: str,
    model: Path = MODEL,
    backup_dir: Path = BACKUP_DIR,
) -> Dict[str, Any]:
    """Reset the trained state of a single gesture so it falls back to rule-based detection immediately."""
    from src.gesture_models import remove_from_bank, training_lock
    if key not in BY_KEY:
        raise ValueError(f"알 수 없는 동작입니다: {key}")
    task = BY_KEY[key]
    label = task.label
    if label == "none":
        raise ValueError("이동·중립 동작은 개별 초기화 대상이 아닙니다.")

    backup_current_model(model=model, backup_dir=backup_dir, reason="before-reset")
    bank_dir = model.parent / "gestures"
    removed_specialist = False
    if (bank_dir / "manifest.json").is_file():
        with training_lock(bank_dir):
            removed_specialist = remove_from_bank(bank_dir, label)

    disabled_in_main = False
    card_path = model.with_suffix(".json")
    if card_path.is_file():
        try:
            card = json.loads(card_path.read_text(encoding="utf-8"))
            classes = card.get("classes", [])
            if label in classes:
                disabled = list(dict.fromkeys([*(card.get("disabled_gestures") or []), label]))
                card["disabled_gestures"] = disabled
                card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
                disabled_in_main = True
        except Exception:
            pass

    return {
        "key": key,
        "label": label,
        "name": task.name,
        "removed_specialist": removed_specialist,
        "disabled_in_main": disabled_in_main,
    }


# ---------------------------------------------------------------- model & recording simulator + data evaluation / calibration / augmentation
def evaluate_recording_in_simulator(
    filename: str,
    rec_dir: Path = REC_DIR,
    *,
    force_recheck: bool = False,
) -> Dict[str, Any]:
    """Evaluate raw and calibrated (cleaned) recording quality + augmentation status for the simulator."""
    from src.capture_dataset import discover_augmented_paths
    from src.data_quality import check_recording

    folder = Path(rec_dir)
    raw_path = folder / Path(filename).name
    if not raw_path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    cleaned_path = folder / "cleaned" / raw_path.name
    has_cleaned = cleaned_path.is_file()

    if force_recheck:
        raw_rep = check_recording(raw_path).to_dict()
        try:
            raw_path.with_name(raw_path.name + ".quality.json").write_text(
                json.dumps(raw_rep, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
    else:
        raw_rep = get_recording_quality(raw_path, compute_if_missing=True)

    active_rep = raw_rep
    if has_cleaned:
        try:
            active_rep = check_recording(cleaned_path).to_dict()
        except Exception:
            active_rep = raw_rep

    metrics = active_rep.get("metrics") or {}
    total_f = int(metrics.get("total_frames", 0))
    valid_f = int(metrics.get("valid_frames", 0))
    missing_pct = round(float(metrics.get("missing_hand_ratio", 0.0)) * 100.0, 1)
    outlier_cnt = int(metrics.get("outlier_count", 0))
    short_do_cnt = int(metrics.get("short_do_count", 0))
    abnormal_cnt = int(metrics.get("abnormal_size_count", 0))
    issues = list(active_rep.get("issues") or [])

    aug_paths = discover_augmented_paths([raw_path])
    aug_cfg = get_augmentation_settings(folder)
    aug_summary = (
        f"증강 {'사용 중' if aug_cfg['enabled'] else '꺼짐'} · "
        f"강도: {aug_cfg['strength_label']} · "
        f"수량: {aug_cfg['factor']}배 · "
        f"현재 생성된 증강본 {len(aug_paths)}개"
    )

    raw_score = float(raw_rep.get("score") or 0.0)
    act_score = float(active_rep.get("score") or 0.0)
    act_status = str(active_rep.get("status") or "unknown")
    if has_cleaned:
        eval_headline = (
            f"[보정본 적용 중] 품질 {act_score * 100:.0f}점 (원본 {raw_score * 100:.0f}점) · "
            f"유효 {valid_f}/{total_f}프레임 · 손 유실 {missing_pct}% · 관절 점프 {outlier_cnt}건 · 짧은 구간 {short_do_cnt}건"
        )
    else:
        eval_headline = (
            f"[원본 데이터] 품질 {act_score * 100:.0f}점 ({active_rep.get('summary', '')}) · "
            f"유효 {valid_f}/{total_f}프레임 · 손 유실 {missing_pct}% · 관절 점프 {outlier_cnt}건 · 짧은 구간 {short_do_cnt}건"
        )

    # Extract explicit target gesture & condition intent for this recording
    try:
        file_sum = _summarise_file(raw_path)
    except Exception:
        file_sum = {}
    if not file_sum.get("target_gestures_text"):
        try:
            from src.recorder import read_session
            rmeta, rframes, _ = read_session(raw_path)
            itasks: List[str] = []
            conds: List[str] = []
            p_pos: List[str] = []
            z_cnts: Dict[str, int] = defaultdict(int)
            p_cnt = 0
            for rf in rframes:
                if isinstance(rf.get("pose"), list) and len(rf["pose"]) >= 6:
                    p_cnt += 1
                lm_rf = rf.get("lm")
                if isinstance(lm_rf, list) and len(lm_rf) == 21:
                    try:
                        px = sum(float(lm_rf[j][0]) for j in (0, 5, 9, 13, 17)) / 5.0
                        py = sum(float(lm_rf[j][1]) for j in (0, 5, 9, 13, 17)) / 5.0
                        z_cnts[_classify_screen_zone(px, py)] += 1
                    except Exception:
                        pass
                rlab = rf.get("label") or {}
                pos_l = rlab.get("position")
                if isinstance(pos_l, str) and pos_l.strip() and pos_l.strip() not in p_pos:
                    p_pos.append(pos_l.strip())
                tcand = rlab.get("task") or rlab.get("target")
                if isinstance(tcand, str) and tcand in BY_KEY and tcand not in itasks:
                    itasks.append(tcand)
                cstr = rlab.get("condition")
                if isinstance(cstr, str) and cstr.strip() and cstr.strip() not in conds:
                    conds.append(cstr.strip())
            f_key = str(rmeta.get("focus") or (rmeta.get("capture_plan") or {}).get("focus") or "")
            file_sum = {
                "frames": len(rframes),
                "pose_frames": p_cnt,
                "screen_zones_text": _format_screen_zones_summary(dict(z_cnts), p_pos),
                **_build_recording_intent_fields(rmeta.get("mode"), f_key, itasks, {}, {}, conds),
            }
        except Exception:
            file_sum = {}

    pose_f = int(file_sum.get("pose_frames", 0))
    pose_pct = int(round(100.0 * pose_f / max(1, total_f or int(file_sum.get("frames", 1)))))

    return {
        "file": raw_path.name,
        "cleaned_exists": has_cleaned,
        "raw_score": round(raw_score, 3),
        "raw_status": str(raw_rep.get("status") or "unknown"),
        "raw_summary": str(raw_rep.get("summary") or ""),
        "active_score": round(act_score, 3),
        "active_status": act_status,
        "active_summary": str(active_rep.get("summary") or ""),
        "headline": eval_headline,
        "total_frames": total_f,
        "valid_frames": valid_f,
        "missing_pct": missing_pct,
        "outlier_count": outlier_cnt,
        "short_do_count": short_do_cnt,
        "abnormal_size_count": abnormal_cnt,
        "issues": issues[:12],
        "recorded_mode_label": str(file_sum.get("recorded_mode_label") or "가이드 녹화"),
        "target_gestures_text": str(file_sum.get("target_gestures_text") or "목표 동작 정보 확인 중"),
        "target_short_text": str(file_sum.get("target_short_text") or ""),
        "target_conditions_text": str(file_sum.get("target_conditions_text") or "기본 촬영 자세"),
        "target_instruction_text": str(file_sum.get("target_instruction_text") or ""),
        "screen_zones_text": str(file_sum.get("screen_zones_text") or "화면 영역 분석 중"),
        "pose_coverage_pct": pose_pct,
        "augmentation": {
            "enabled": aug_cfg["enabled"],
            "strength": aug_cfg["strength"],
            "strength_label": aug_cfg["strength_label"],
            "factor": aug_cfg["factor"],
            "existing_copies": len(aug_paths),
            "summary": aug_summary,
        },
    }


def calibrate_recording_data(
    filename: str,
    *,
    max_interp_gap: int = 3,
    max_joint_jump: float = 0.38,
    min_do_frames: int = 4,
    rec_dir: Path = REC_DIR,
) -> Dict[str, Any]:
    """Run automated data calibration (dropout interpolation, outlier smoothing, short-do masking) on a recording."""
    from src.data_quality import check_recording, clean_recording

    folder = Path(rec_dir)
    raw_path = folder / Path(filename).name
    if not raw_path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    raw_rep = check_recording(
        raw_path,
        min_do_frames=int(min_do_frames),
        max_joint_jump=float(max_joint_jump),
    )
    cleaned_path = clean_recording(
        raw_path,
        raw_rep,
        output_dir=folder / "cleaned",
        min_do_frames=int(min_do_frames),
        max_interp_gap=int(max_interp_gap),
        max_joint_jump=float(max_joint_jump),
    )
    clean_rep = check_recording(
        cleaned_path,
        min_do_frames=int(min_do_frames),
        max_joint_jump=float(max_joint_jump),
    )
    # Update the quality cache so the recording list also reflects calibrated status
    q_dict = clean_rep.to_dict()
    q_dict["summary"] = (
        f"보정 완료 ({raw_rep.score * 100:.0f}점→{clean_rep.score * 100:.0f}점): " + clean_rep.summary
    )
    try:
        raw_path.with_name(raw_path.name + ".quality.json").write_text(
            json.dumps(q_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass

    return {
        "file": raw_path.name,
        "cleaned_path": str(cleaned_path),
        "before_score": round(float(raw_rep.score), 3),
        "after_score": round(float(clean_rep.score), 3),
        "before_frames": int(raw_rep.metrics.get("total_frames", 0)),
        "after_frames": int(clean_rep.metrics.get("total_frames", 0)),
        "before_valid": int(raw_rep.metrics.get("valid_frames", 0)),
        "after_valid": int(clean_rep.metrics.get("valid_frames", 0)),
        "message": (
            f"'{raw_path.name}' 데이터 보정 완료: 품질 점수 {raw_rep.score * 100:.0f}점 → {clean_rep.score * 100:.0f}점 "
            f"(유효 프레임 {raw_rep.metrics.get('valid_frames', 0)} → {clean_rep.metrics.get('valid_frames', 0)}, "
            f"관절 점프 {raw_rep.metrics.get('outlier_count', 0)} → {clean_rep.metrics.get('outlier_count', 0)}건)"
        ),
    }


def reset_calibrated_recording(filename: str, rec_dir: Path = REC_DIR) -> Dict[str, Any]:
    """Remove the calibrated `cleaned/` copy for a recording and restore raw quality report."""
    from src.data_quality import check_recording

    folder = Path(rec_dir)
    raw_path = folder / Path(filename).name
    cleaned_path = folder / "cleaned" / raw_path.name
    removed = False
    if cleaned_path.is_file():
        try:
            cleaned_path.unlink()
            removed = True
        except OSError:
            pass
    if raw_path.is_file():
        rep = check_recording(raw_path)
        try:
            raw_path.with_name(raw_path.name + ".quality.json").write_text(
                json.dumps(rep.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
    return {
        "file": raw_path.name,
        "removed": removed,
        "message": f"'{raw_path.name}' 보정본을 초기화하고 원본 데이터로 되돌렸습니다.",
    }


def apply_recording_augmentation(
    filename: str,
    *,
    enabled: bool = True,
    strength: str = "normal",
    factor: int = 2,
    rec_dir: Path = REC_DIR,
) -> Dict[str, Any]:
    """Save augmentation settings (enabled, strength, factor) and generate/clear offline augmented copies for `filename`."""
    from src.capture_dataset import discover_augmented_paths, resolve_cleaned_path
    from src.offline_augment import augment_recording_file

    folder = Path(rec_dir)
    aug_cfg = set_augmentation_settings(
        enabled=bool(enabled),
        strength=str(strength),
        factor=int(factor),
        rec_dir=folder,
    )

    if not filename:
        return {
            "file": "",
            "settings": aug_cfg,
            "generated_files": [],
            "message": (
                f"학습 증강 설정 저장됨: {'사용' if aug_cfg['enabled'] else '사용 안 함'} · "
                f"강도 {aug_cfg['strength_label']} · 수량 {aug_cfg['factor']}배"
            ),
        }

    if filename == "__all__":
        from src.recorder import finished_recordings
        complete_paths, _ = finished_recordings(folder)
        selected_paths = [Path(p) for p in filter_selected_recording_paths(complete_paths, folder)]
        for old_aug in discover_augmented_paths(complete_paths):
            try:
                Path(old_aug).unlink()
            except OSError:
                pass
        if not aug_cfg["enabled"]:
            return {
                "file": "__all__",
                "settings": aug_cfg,
                "generated_files": [],
                "message": "전체 녹화 데이터 증강 사용 안 함으로 설정 (기존 증강본 모두 정리 완료)",
            }
        written_all: List[Path] = []
        for rp in selected_paths:
            src_for_aug = resolve_cleaned_path(rp, use_cleaned=True)
            written_all.extend(
                augment_recording_file(
                    src_for_aug,
                    factor=aug_cfg["factor"],
                    rotation_deg=aug_cfg["rotation_deg"],
                    scale_range=aug_cfg["scale_range"],
                    jitter_std=aug_cfg["jitter_std"],
                    time_stretch_range=aug_cfg["time_stretch_range"],
                    frame_drop_prob=aug_cfg["frame_drop_prob"],
                    output_dir=folder / "augmented",
                )
            )
        return {
            "file": "__all__",
            "settings": aug_cfg,
            "generated_files": [p.name for p in written_all],
            "message": (
                f"선택된 녹화 {len(selected_paths)}개 전체 증강 완료: {aug_cfg['strength_label']} · "
                f"각 {aug_cfg['factor']}배씩 총 {len(written_all)}개 증강본 생성됨"
            ),
        }

    raw_path = folder / Path(filename).name
    if not raw_path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    # Remove old augmented copies for this recording first
    for old_aug in discover_augmented_paths([raw_path]):
        try:
            Path(old_aug).unlink()
        except OSError:
            pass

    if not aug_cfg["enabled"]:
        return {
            "file": raw_path.name,
            "settings": aug_cfg,
            "generated_files": [],
            "message": f"'{raw_path.name}' 증강 사용 안 함으로 설정 (기존 증강본 정리 완료)",
        }

    src_for_aug = resolve_cleaned_path(raw_path, use_cleaned=True)
    written = augment_recording_file(
        src_for_aug,
        factor=aug_cfg["factor"],
        rotation_deg=aug_cfg["rotation_deg"],
        scale_range=aug_cfg["scale_range"],
        jitter_std=aug_cfg["jitter_std"],
        time_stretch_range=aug_cfg["time_stretch_range"],
        frame_drop_prob=aug_cfg["frame_drop_prob"],
        output_dir=folder / "augmented",
    )
    return {
        "file": raw_path.name,
        "settings": aug_cfg,
        "generated_files": [p.name for p in written],
        "message": (
            f"'{raw_path.name}' 데이터 증강 완료: {aug_cfg['strength_label']} · "
            f"{len(written)}배 증강본 생성됨 ({', '.join(p.name for p in written[:2])}{' 외' if len(written) > 2 else ''})"
        ),
    }


def _extract_rule_targets(rule: Any) -> List[str]:
    """Return canonical gesture keys detected by rule-based engine in a recorded frame's `rule` dict."""
    if not isinstance(rule, dict):
        return []
    found: List[str] = []
    targets = rule.get("targets")
    if isinstance(targets, dict):
        for k, v in targets.items():
            if v and k not in found:
                found.append(str(k))
    elif isinstance(targets, (list, tuple, set)):
        for k in targets:
            if isinstance(k, str) and k not in found:
                found.append(k)
    act = rule.get("action")
    gest = rule.get("gesture") or ""
    if act and act not in ("idle", "move", "none"):
        for t in BY_KEY.values():
            if t.action and t.action == act:
                if act == "drag_hold" and ((t.key == "thumb_index_middle") != (gest == "thumb_index_middle_pinch")):
                    continue
                if t.label not in found:
                    found.append(t.label)
    return found


_PINCH_JOINT_MAP: Dict[str, List[int]] = {
    "thumb_index": [4, 8],
    "thumb_middle": [4, 12],
    "thumb_ring": [4, 16],
    "thumb_index_middle": [4, 8, 12],
}
_SWIPE_DIR_MAP: Dict[str, str] = {
    "swipe_left": "left",
    "swipe_right": "right",
    "swipe_down": "down",
}
_SIGN_JOINT_MAP: Dict[str, List[int]] = {
    "v_sign": [8, 12],
    "rock_sign": [8, 20],
    "thumbs_up": [4],
}


def _compute_elbow_angle_deg(shoulder: Sequence[float], elbow: Sequence[float], wrist: Sequence[float]) -> int:
    """Compute interior angle (degrees) at the elbow between shoulder-elbow and wrist-elbow vectors."""
    import math
    ux, uy = float(shoulder[0]) - float(elbow[0]), float(shoulder[1]) - float(elbow[1])
    vx, vy = float(wrist[0]) - float(elbow[0]), float(wrist[1]) - float(elbow[1])
    nu = math.hypot(ux, uy)
    nv = math.hypot(vx, vy)
    if nu < 1e-6 or nv < 1e-6:
        return 0
    cos_a = max(-1.0, min(1.0, (ux * vx + uy * vy) / (nu * nv)))
    return int(round(math.degrees(math.acos(cos_a))))


def _build_playback_frames(
    raw_frames: List[Dict[str, Any]],
    features: Any,
    probs: Optional[Any],
    thresholds_by_label: Dict[str, float],
    default_thr: float,
    issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build per-frame hand + arm/shoulder skeleton & event descriptors for the interactive simulator media player."""
    import math
    from src.capture_dataset import CLASSES
    n = len(raw_frames)
    if n == 0:
        return []
    t0 = float(raw_frames[0].get("t", 0.0))
    non_none_indices = [idx for idx, c in enumerate(CLASSES) if c != "none"]

    issue_by_frame: Dict[int, str] = {}
    for iss in issues:
        sf = int(iss.get("start_frame", 0))
        ef = int(iss.get("end_frame", sf))
        msg = str(iss.get("message", ""))
        for fi in range(max(0, sf), min(n, ef + 1)):
            if fi not in issue_by_frame:
                issue_by_frame[fi] = msg

    out: List[Dict[str, Any]] = []
    smoothed_pose: Optional[List[List[float]]] = None
    last_pose_t: float = -10.0

    for i, rf in enumerate(raw_frames):
        cur_t = float(rf.get("t", t0))
        lm_raw = rf.get("lm")
        hand_ok = isinstance(lm_raw, list) and len(lm_raw) == 21
        lm_2d = (
            [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in lm_raw]
            if hand_ok
            else []
        )
        palm_pt = (
            [
                round(sum(lm_2d[j][0] for j in (0, 5, 9, 13, 17)) / 5.0, 4),
                round(sum(lm_2d[j][1] for j in (0, 5, 9, 13, 17)) / 5.0, 4),
            ]
            if hand_ok
            else None
        )
        hand_meta = rf.get("hand")
        handedness = str(hand_meta[0]) if isinstance(hand_meta, list) and hand_meta else "Right"

        # Extract & smooth MediaPipe Pose joints 11..16:
        # [0]=L_shoulder(11), [1]=R_shoulder(12), [2]=L_elbow(13), [3]=R_elbow(14), [4]=L_wrist(15), [5]=R_wrist(16)
        pose_raw = rf.get("pose")
        raw_pose_ok = isinstance(pose_raw, list) and len(pose_raw) >= 6 and all(
            isinstance(pt, (list, tuple)) and len(pt) >= 2 for pt in pose_raw[:6]
        )
        if raw_pose_ok:
            cur_pose: List[List[float]] = []
            for j_idx in range(6):
                pt = pose_raw[j_idx]
                px, py = float(pt[0]), float(pt[1])
                pv = float(pt[2]) if len(pt) >= 3 else 1.0
                if smoothed_pose is not None and (cur_t - last_pose_t) < 0.6:
                    ox, oy, ov = smoothed_pose[j_idx]
                    alpha = 0.35 if pv >= 0.45 else 0.12
                    px = ox + alpha * (px - ox)
                    py = oy + alpha * (py - oy)
                cur_pose.append([round(px, 4), round(py, 4), round(pv, 3)])
            smoothed_pose = cur_pose
            last_pose_t = cur_t

        pose_present = smoothed_pose is not None and (cur_t - last_pose_t) <= 0.45
        pose_joints = smoothed_pose if pose_present else []
        active_arm: Optional[Dict[str, Any]] = None

        if pose_present and len(pose_joints) >= 6:
            # Left arm = (0, 2, 4), Right arm = (1, 3, 5)
            l_s, r_s, l_e, r_e, l_w, r_w = pose_joints[:6]
            if hand_ok:
                hwx, hwy = lm_2d[0][0], lm_2d[0][1]
                d_left = math.hypot(l_w[0] - hwx, l_w[1] - hwy)
                d_right = math.hypot(r_w[0] - hwx, r_w[1] - hwy)
                side = "left" if d_left <= d_right else "right"
            else:
                v_left = l_s[2] + l_e[2] + l_w[2]
                v_right = r_s[2] + r_e[2] + r_w[2]
                side = "left" if v_left >= v_right else "right"

            s_pt, e_pt, w_pt = (l_s, l_e, l_w) if side == "left" else (r_s, r_e, r_w)
            other_s, other_e, other_w = (r_s, r_e, r_w) if side == "left" else (l_s, l_e, l_w)
            wrist_for_angle = [lm_2d[0][0], lm_2d[0][1], w_pt[2]] if hand_ok else w_pt
            elbow_deg = _compute_elbow_angle_deg(s_pt, e_pt, wrist_for_angle)
            active_arm = {
                "source": "pose",
                "side": side,
                "side_ko": "왼팔" if side == "left" else "오른팔",
                "shoulder": s_pt,
                "elbow": e_pt,
                "wrist": w_pt,
                "other_shoulder": other_s,
                "other_elbow": other_e,
                "other_wrist": other_w,
                "elbow_deg": elbow_deg,
            }
        elif hand_ok:
            # Anatomical fallback estimate when Pose was not captured in this frame
            hwx, hwy = lm_2d[0][0], lm_2d[0][1]
            mcpx, mcpy = lm_2d[9][0], lm_2d[9][1]
            dx, dy = hwx - mcpx, hwy - mcpy
            hand_len = max(0.06, math.hypot(dx, dy))
            ux, uy = dx / hand_len, dy / hand_len
            side = "left" if handedness.lower().startswith("l") else "right"
            lat = -1.0 if side == "left" else 1.0
            ex = round(hwx + ux * (hand_len * 2.1) + lat * 0.04, 4)
            ey = round(hwy + uy * (hand_len * 2.1) + 0.06, 4)
            sx = round(ex - lat * (hand_len * 0.55), 4)
            sy = round(ey - hand_len * 1.95, 4)
            s_pt = [sx, sy, 0.35]
            e_pt = [ex, ey, 0.35]
            w_pt = [hwx, hwy, 0.8]
            active_arm = {
                "source": "estimated",
                "side": side,
                "side_ko": ("왼팔" if side == "left" else "오른팔") + "(추정)",
                "shoulder": s_pt,
                "elbow": e_pt,
                "wrist": w_pt,
                "other_shoulder": [round(sx - lat * (hand_len * 1.8), 4), sy, 0.25],
                "other_elbow": [],
                "other_wrist": [],
                "elbow_deg": _compute_elbow_angle_deg(s_pt, e_pt, w_pt),
            }

        lab = rf.get("label") or {}
        target_lbl = str(lab.get("target") or "none")
        task_lbl = str(lab.get("task") or target_lbl or "none")
        condition_str = str(lab.get("condition") or "")
        target_pos_str = str(lab.get("position") or "")
        screen_zone = _classify_screen_zone(palm_pt[0], palm_pt[1]) if palm_pt else ""
        palm_xy_pct = [int(round(palm_pt[0] * 100.0)), int(round(palm_pt[1] * 100.0))] if palm_pt else []
        raw_cur = rf.get("cursor")
        cursor_pt = [int(raw_cur[0]), int(raw_cur[1])] if isinstance(raw_cur, list) and len(raw_cur) == 2 else []
        phase = str(lab.get("phase") or "")
        rep_val = lab.get("rep")
        rep_num = int(rep_val) if isinstance(rep_val, int) else -1
        phase_ko = {
            "do": f"동작 수행 중" + (f" (#{rep_num}회차)" if rep_num > 0 else ""),
            "rest": "동작 사이 휴식/대기",
            "prepare": "자세 준비 구간",
            "free": "자유 이동 (오작동 방지)",
            "use": "일반 마우스 사용",
        }.get(phase, phase or "대기")

        rule = rf.get("rule") or {}
        rule_targets = _extract_rule_targets(rule)
        rule_action = str(rule.get("action") or "idle")

        best_cls = "none"
        best_p = 0.0
        model_hit = False
        if probs is not None and i < len(probs):
            for ci in non_none_indices:
                pv = float(probs[i, ci])
                if pv > best_p:
                    best_p = pv
                    best_cls = CLASSES[ci]
            lbl_thr = float(thresholds_by_label.get(best_cls, default_thr))
            model_hit = best_p >= lbl_thr and best_cls != "none"

        vx = float(features[i, 130]) if features is not None and i < len(features) else 0.0
        vy = float(features[i, 131]) if features is not None and i < len(features) else 0.0

        active_ev = "none"
        if model_hit:
            active_ev = best_cls
        elif rule_targets:
            active_ev = BY_KEY[rule_targets[0]].label if rule_targets[0] in BY_KEY else rule_targets[0]
        elif phase == "do" and target_lbl not in ("none", "idle", ""):
            active_ev = target_lbl

        pinch_joints = _PINCH_JOINT_MAP.get(active_ev, [])
        swipe_dir = _SWIPE_DIR_MAP.get(active_ev, "")
        sign_joints = _SIGN_JOINT_MAP.get(active_ev, [])

        if not pinch_joints and hand_ok and features is not None and i < len(features):
            d_idx, d_mid, d_ring = float(features[i, 126]), float(features[i, 127]), float(features[i, 128])
            if d_idx < 0.24 and d_mid < 0.24:
                pinch_joints = [4, 8, 12]
            elif d_idx < 0.22:
                pinch_joints = [4, 8]
            elif d_mid < 0.22:
                pinch_joints = [4, 12]
            elif d_ring < 0.22:
                pinch_joints = [4, 16]

        if not swipe_dir and hand_ok:
            if vx < -1.35:
                swipe_dir = "left"
            elif vx > 1.35:
                swipe_dir = "right"
            elif vy > 1.35:
                swipe_dir = "down"

        if pinch_joints:
            ev_kind = "pinch"
            ev_name = NAMES.get(active_ev, "핀치 감지") if active_ev in _PINCH_JOINT_MAP else "손가락 핀치 근접"
            ev_text = f"PINCH · {ev_name}"
        elif swipe_dir:
            ev_kind = "swipe"
            dir_ko = {"left": "← 왼쪽 스와이프", "right": "→ 오른쪽 스와이프", "down": "↓ 아래로 스와이프"}.get(swipe_dir, "스와이프")
            ev_text = f"SWIPE · {dir_ko}"
        elif sign_joints:
            ev_kind = "sign"
            ev_text = f"SIGN · {NAMES.get(active_ev, active_ev)}"
        else:
            ev_kind = "none"
            ev_text = "대기 / 커서 이동" if hand_ok else ("팔·어깨만 감지됨" if pose_present else "손 감지 안 됨")

        out.append({
            "idx": i,
            "t": round(cur_t - t0, 2),
            "hand_present": hand_ok,
            "handedness": handedness,
            "lm": lm_2d,
            "palm": palm_pt,
            "screen_zone": screen_zone,
            "palm_xy_pct": palm_xy_pct,
            "cursor": cursor_pt,
            "target_position": target_pos_str,
            "pose_present": pose_present,
            "pose_joints": pose_joints,
            "active_arm": active_arm,
            "task": task_lbl,
            "task_name": NAMES.get(task_lbl, "중립/이동" if task_lbl in ("none", "idle", "") else task_lbl),
            "condition": condition_str,
            "target": target_lbl,
            "target_name": NAMES.get(target_lbl, "중립/이동" if target_lbl in ("none", "idle", "") else target_lbl),
            "phase": phase,
            "phase_ko": phase_ko,
            "rep": rep_num,
            "model_cls": best_cls,
            "model_name": NAMES.get(best_cls, best_cls) if best_cls != "none" else "중립",
            "model_prob": round(best_p, 3),
            "model_hit": model_hit,
            "rule_action": rule_action,
            "rule_targets": rule_targets,
            "rule_names": [NAMES.get(k, k) for k in rule_targets],
            "vx": round(vx, 2),
            "vy": round(vy, 2),
            "pinch_joints": pinch_joints,
            "swipe_dir": swipe_dir,
            "sign_joints": sign_joints,
            "event_kind": ev_kind,
            "event_text": ev_text,
            "issue_text": issue_by_frame.get(i, ""),
        })
    return out


def simulate_model_on_recording(
    filename: str,
    rec_dir: Path = REC_DIR,
    model: Path = MODEL,
    *,
    use_cleaned: bool = True,
) -> Dict[str, Any]:
    """Replay a recorded session through the current trained model (and specialist bank) vs. recorded rules."""
    import numpy as np
    from src.capture_dataset import CLASSES, load_sessions, resolve_cleaned_path
    from src.gesture_gate import DEFAULT_THRESHOLD
    from src.gesture_models import compose_predictor, load_single, read_bank
    from src.recorder import read_session
    from src.tcn_features import FEATURE_VERSION

    folder = Path(rec_dir)
    path = folder / Path(filename).name
    if not path.is_file():
        raise FileNotFoundError(f"녹화 파일을 찾을 수 없습니다: {filename}")

    eval_info = evaluate_recording_in_simulator(path.name, rec_dir=folder, force_recheck=False)
    sim_path = resolve_cleaned_path(path, use_cleaned=use_cleaned)
    using_cleaned = sim_path.resolve() != path.resolve()
    _, raw_frames, _ = read_session(sim_path)

    card_path = model.with_suffix(".json")
    win = 15
    if card_path.is_file():
        try:
            win = int(json.loads(card_path.read_text(encoding="utf-8")).get("window", 15))
        except Exception:
            win = 15

    sessions = load_sessions([str(sim_path)], win, use_cleaned=False)
    if not sessions or len(sessions[0]["x"]) == 0:
        pb_frames = _build_playback_frames(raw_frames, None, None, {}, DEFAULT_THRESHOLD, eval_info.get("issues", []))
        return {
            "file": path.name,
            "using_cleaned": using_cleaned,
            "frames": len(raw_frames),
            "duration_s": round(len(raw_frames) / 30.0, 1),
            "has_model": False,
            "summary": "시뮬레이션 가능한 손 랜드마크 구간이 없습니다.",
            "gestures": [],
            "timeline": [],
            "reps": [],
            "playback_frames": pb_frames,
            "quality": eval_info,
            "augmentation": eval_info["augmentation"],
        }
    session = sessions[0]

    bank_dir = model.parent / "gestures"
    base_pred = None
    info: Dict[str, Any] = {"classes": ["none"], "window": win, "feature_version": FEATURE_VERSION}
    disabled: set[str] = set()
    if model.is_file() and card_path.is_file():
        try:
            base_pred, info = load_single(model, FEATURE_VERSION)
            disabled = set(info.get("disabled_gestures") or ())
        except Exception:
            base_pred = None

    specialists = read_bank(bank_dir) if (bank_dir / "manifest.json").is_file() else {}
    if base_pred is None and not specialists:
        pb_frames = _build_playback_frames(
            raw_frames, session.get("features"), None, {}, DEFAULT_THRESHOLD, eval_info.get("issues", [])
        )
        return {
            "file": path.name,
            "using_cleaned": using_cleaned,
            "frames": int(len(session["x"])),
            "duration_s": round(float(len(session["x"])) / 30.0, 1),
            "has_model": False,
            "summary": "로드 가능한 학습 모델이 없습니다. 먼저 학습을 실행하세요.",
            "gestures": [],
            "timeline": [],
            "reps": [],
            "playback_frames": pb_frames,
            "quality": eval_info,
            "augmentation": eval_info["augmentation"],
        }

    if base_pred is None:
        base_pred = lambda _x: np.array([1.0], dtype=np.float64)
    predict_one, merged_info = compose_predictor(model, base_pred, info)
    active_classes = list(merged_info.get("classes", []))
    threshold = float(merged_info.get("threshold", DEFAULT_THRESHOLD))
    thresholds_by_label = dict(merged_info.get("thresholds") or {})

    # Evaluate across all windows in session["x"] (shape: (N, win, features))
    xs = session["x"]
    n_frames = len(xs)
    probs = np.zeros((n_frames, len(CLASSES)), dtype=np.float64)
    cls_to_global = {c: CLASSES.index(c) for c in active_classes if c in CLASSES}
    for i in range(n_frames):
        sc = predict_one(xs[i:i + 1])
        for local_idx, cname in enumerate(active_classes):
            g_idx = cls_to_global.get(cname)
            if g_idx is not None:
                probs[i, g_idx] = float(sc[local_idx])

    for lbl in disabled:
        if lbl in CLASSES and lbl not in specialists:
            probs[:, CLASSES.index(lbl)] = 0.0

    y = session["y"]
    frames = session["frames"]
    t0 = float(frames[0].get("t", 0.0)) if n_frames else 0.0
    t_end = float(frames[-1].get("t", t0)) if n_frames else 0.0
    duration_s = max(0.1, t_end - t0) if t_end > t0 else round(n_frames / 30.0, 1)

    # Identify contiguous positive action blocks (or distinct label["rep"] runs)
    rep_blocks: List[List[int]] = []
    cur_block: List[int] = []
    cur_key: Optional[Tuple[int, Any]] = None
    for i in range(n_frames):
        cls_id = int(y[i])
        if cls_id > 0:
            lab_rep = (frames[i].get("label") or {}).get("rep")
            key = (cls_id, lab_rep)
            if cur_block and key == cur_key:
                cur_block.append(i)
            else:
                if cur_block:
                    rep_blocks.append(cur_block)
                cur_block = [i]
                cur_key = key
        else:
            if cur_block:
                rep_blocks.append(cur_block)
                cur_block = []
                cur_key = None
    if cur_block:
        rep_blocks.append(cur_block)

    rep_details = []
    per_label_reps: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "model_ok": 0, "rule_ok": 0})
    for r_id, idx in enumerate(rep_blocks):
        if not idx:
            continue
        cls_id = int(y[idx[0]])
        lbl = CLASSES[cls_id] if 0 <= cls_id < len(CLASSES) else "none"
        if lbl == "none":
            continue
        lbl_thr = float(thresholds_by_label.get(lbl, threshold))
        peak_p = float(probs[idx, cls_id].max())
        mean_p = float(probs[idx, cls_id].mean())
        model_hit = peak_p >= lbl_thr
        rule_hit = any(lbl in _extract_rule_targets(frames[i].get("rule")) for i in idx)
        t_rel = round(float(frames[idx[0]].get("t", t0)) - t0, 1)
        per_label_reps[lbl]["total"] += 1
        if model_hit:
            per_label_reps[lbl]["model_ok"] += 1
        if rule_hit:
            per_label_reps[lbl]["rule_ok"] += 1
        rep_details.append({
            "rep": r_id + 1,
            "label": lbl,
            "name": NAMES.get(lbl, lbl),
            "time_s": t_rel,
            "start_frame": int(idx[0]),
            "end_frame": int(idx[-1]),
            "peak_prob": round(peak_p, 3),
            "mean_prob": round(mean_p, 3),
            "model_ok": model_hit,
            "rule_ok": rule_hit,
        })

    gesture_summaries = []
    seen_labels: set[str] = set()
    for key, name in NAMES.items():
        task = BY_KEY[key]
        lbl = task.label
        if lbl == "none" or lbl not in CLASSES or lbl in seen_labels:
            continue
        seen_labels.add(lbl)
        c_idx = CLASSES.index(lbl)
        col = probs[:, c_idx]
        lbl_thr = float(thresholds_by_label.get(lbl, threshold))
        is_active = (lbl in active_classes) and (lbl not in disabled or lbl in specialists)
        if not is_active and per_label_reps[lbl]["total"] == 0 and float(col.max()) < 0.05:
            continue
        other_mask = (y == 0)
        false_fires = 0
        was_high = False
        for i in range(n_frames):
            if bool(other_mask[i]):
                high = float(col[i]) >= lbl_thr
                if high and not was_high:
                    false_fires += 1
                was_high = high
            else:
                was_high = False
        rep_stat = per_label_reps[lbl]
        total_r = rep_stat["total"]
        m_rate = (rep_stat["model_ok"] / total_r) if total_r > 0 else None
        r_rate = (rep_stat["rule_ok"] / total_r) if total_r > 0 else None
        gesture_summaries.append({
            "key": key,
            "label": lbl,
            "name": name,
            "trained": is_active,
            "reps": total_r,
            "model_hits": rep_stat["model_ok"],
            "rule_hits": rep_stat["rule_ok"],
            "model_rate": round(m_rate, 3) if m_rate is not None else None,
            "rule_rate": round(r_rate, 3) if r_rate is not None else None,
            "max_prob": round(float(col.max()), 3),
            "mean_prob": round(float(col.mean()), 3),
            "false_fires": false_fires,
        })

    bins = min(80, max(1, n_frames))
    timeline = []
    non_none_indices = [i for i, c in enumerate(CLASSES) if c != "none"]
    for b in range(bins):
        s_i = int(b * n_frames / bins)
        e_i = max(s_i + 1, int((b + 1) * n_frames / bins))
        chunk = probs[s_i:e_i]
        best_cls = "none"
        best_p = 0.0
        for ci in non_none_indices:
            p_val = float(chunk[:, ci].max())
            if p_val > best_p:
                best_p = p_val
                best_cls = CLASSES[ci]
        target_ids = [int(v) for v in y[s_i:e_i] if int(v) > 0]
        target_lbl = CLASSES[target_ids[0]] if target_ids and target_ids[0] < len(CLASSES) else "none"
        lbl_thr = float(thresholds_by_label.get(best_cls, threshold))
        timeline.append({
            "bin": b,
            "prob": round(best_p, 3),
            "predicted": best_cls if best_p >= lbl_thr else "none",
            "predicted_name": NAMES.get(best_cls, best_cls) if best_p >= lbl_thr else "중립",
            "target": target_lbl,
            "target_name": NAMES.get(target_lbl, target_lbl) if target_lbl != "none" else "이동/중립",
            "confirmed": best_p >= lbl_thr,
        })

    total_reps = sum(g["reps"] for g in gesture_summaries)
    total_hits = sum(g["model_hits"] for g in gesture_summaries)
    total_rule_hits = sum(g["rule_hits"] for g in gesture_summaries)
    total_ff = sum(g["false_fires"] for g in gesture_summaries)
    src_tag = "보정본" if using_cleaned else "원본"
    if total_reps > 0:
        summary_text = (
            f"{path.name} [{src_tag}] ({duration_s:.1f}초, {n_frames}프레임) · "
            f"모델 인식 {total_hits}/{total_reps}회 ({total_hits / total_reps * 100:.0f}%) vs "
            f"규칙 {total_rule_hits}/{total_reps}회 ({total_rule_hits / total_reps * 100:.0f}%) · "
            f"중립 구간 오작동 {total_ff}회"
        )
    else:
        summary_text = (
            f"{path.name} [{src_tag}] ({duration_s:.1f}초, {n_frames}프레임 자유/이동 녹화) · "
            f"중립 구간 모델 오작동 {total_ff}회 (임계값 {threshold:.2f})"
        )

    pb_frames = _build_playback_frames(
        raw_frames,
        session.get("features"),
        probs,
        thresholds_by_label,
        threshold,
        eval_info.get("issues", []),
    )

    return {
        "file": path.name,
        "using_cleaned": using_cleaned,
        "frames": n_frames,
        "duration_s": round(duration_s, 1),
        "threshold": threshold,
        "has_model": True,
        "summary": summary_text,
        "recorded_mode_label": eval_info.get("recorded_mode_label", "가이드 녹화"),
        "target_gestures_text": eval_info.get("target_gestures_text", ""),
        "target_short_text": eval_info.get("target_short_text", ""),
        "target_conditions_text": eval_info.get("target_conditions_text", ""),
        "target_instruction_text": eval_info.get("target_instruction_text", ""),
        "pose_coverage_pct": eval_info.get("pose_coverage_pct", 0),
        "gestures": gesture_summaries,
        "timeline": timeline,
        "reps": rep_details,
        "playback_frames": pb_frames,
        "quality": eval_info,
        "augmentation": eval_info["augmentation"],
    }


# ---------------------------------------------------------------- app self-diagnostics & bug detection
def run_app_diagnostics(
    rec_dir: Path = REC_DIR,
    model: Path = MODEL,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Inspect model integrity, config schema, MediaPipe assets, dataset quality, and recent logs for bugs."""
    from src.gesture_engine import DEFAULT_CONFIG, DEFAULT_CONFIG_PATH
    from src.gesture_models import load_single, read_bank
    from src.tcn_features import FEATURES, FEATURE_VERSION

    checks: List[Dict[str, Any]] = []

    # 1. Config check
    cfg_file = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if cfg_file.is_file():
        try:
            raw_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            bad_keys = []
            for k in ("pinch_threshold", "release_threshold", "click_threshold", "min_quality_score"):
                if k in raw_cfg:
                    val = float(raw_cfg[k])
                    if not (0.0 < val <= 1.0):
                        bad_keys.append(f"{k}={val}")
            if bad_keys:
                checks.append({
                    "name": "설정 파일 (config/gestures.json)",
                    "status": "warn",
                    "detail": f"비정상 임계값 감지: {', '.join(bad_keys)}",
                })
            else:
                checks.append({
                    "name": "설정 파일 (config/gestures.json)",
                    "status": "ok",
                    "detail": f"정상 ({len(raw_cfg)}개 항목 로드됨)",
                })
        except Exception as exc:
            checks.append({
                "name": "설정 파일 (config/gestures.json)",
                "status": "fail",
                "detail": f"JSON 파싱 오류: {exc}",
            })
    else:
        checks.append({
            "name": "설정 파일 (config/gestures.json)",
            "status": "ok",
            "detail": f"기본 설정 사용 중 ({len(DEFAULT_CONFIG)}개 기본값)",
        })

    # 2. MediaPipe hand landmarker asset check
    mp_model = ROOT / "models" / "hand_landmarker.task"
    if mp_model.is_file() and mp_model.stat().st_size > 100_000:
        checks.append({
            "name": "MediaPipe 손 추적 모델",
            "status": "ok",
            "detail": f"{mp_model.name} ({mp_model.stat().st_size / (1024 * 1024):.1f} MB)",
        })
    else:
        checks.append({
            "name": "MediaPipe 손 추적 모델",
            "status": "fail",
            "detail": "models/hand_landmarker.task 파일이 없거나 손상되었습니다.",
        })

    # 3. TCN ONNX & Specialist Bank live inference check
    card_path = model.with_suffix(".json")
    bank_dir = model.parent / "gestures"
    if model.is_file() and card_path.is_file():
        try:
            import numpy as np
            pred, card = load_single(model, FEATURE_VERSION)
            win = int(card.get("window", 15))
            n_feat = int(card.get("features", FEATURES))
            dummy = np.zeros((1, win, n_feat), dtype=np.float32)
            out = pred(dummy)
            disabled = card.get("disabled_gestures") or []
            specialists = read_bank(bank_dir) if (bank_dir / "manifest.json").is_file() else {}
            spec_info = f", 개별 동작 모델 {len(specialists)}개" if specialists else ""
            dis_info = f", 초기화됨 {len(disabled)}개" if disabled else ""
            if out.shape == (len(card.get("classes", [])),) and np.isfinite(out).all():
                checks.append({
                    "name": "TCN 동작 모델 (ONNX 추론 검증)",
                    "status": "ok",
                    "detail": f"정상 추론 확인 (클래스 {len(card.get('classes', []))}개{spec_info}{dis_info})",
                })
            else:
                checks.append({
                    "name": "TCN 동작 모델 (ONNX 추론 검증)",
                    "status": "fail",
                    "detail": f"비정상 출력 형태 또는 NaN 감지: shape={out.shape}",
                })
        except Exception as exc:
            checks.append({
                "name": "TCN 동작 모델 (ONNX 추론 검증)",
                "status": "fail",
                "detail": f"모델 로드/추론 오류: {exc}",
            })
    else:
        checks.append({
            "name": "TCN 동작 모델 (ONNX 추론 검증)",
            "status": "warn",
            "detail": "아직 학습된 gesture_tcn.onnx 모델이 없습니다.",
        })

    # 4. Recordings & quality integrity check
    folder = Path(rec_dir)
    rec_files = sorted(folder.glob("*.jsonl.gz")) if folder.is_dir() else []
    if not rec_files:
        checks.append({
            "name": "녹화 데이터셋 품질 및 무결성",
            "status": "warn",
            "detail": "녹화된 .jsonl.gz 데이터 파일이 없습니다.",
        })
    else:
        excluded = set(load_training_selection(folder).get("excluded", []))
        broken, failed_q, warn_q, ok_q = [], [], [], 0
        for p in rec_files:
            try:
                _summarise_file(p)
            except Exception:
                broken.append(p.name)
                continue
            q = get_recording_quality(p, compute_if_missing=True)
            st = q.get("status")
            if st == "fail":
                failed_q.append(p.name)
            elif st == "warning":
                warn_q.append(p.name)
            else:
                ok_q += 1
        active_count = len([p for p in rec_files if p.name not in excluded and p.name not in broken])
        if broken:
            checks.append({
                "name": "녹화 데이터셋 품질 및 무결성",
                "status": "fail",
                "detail": f"손상된 파일 {len(broken)}개 발견: {', '.join(broken[:3])}",
            })
        elif failed_q:
            unexcluded_fails = [f for f in failed_q if f not in excluded]
            status_code = "warn" if unexcluded_fails else "ok"
            checks.append({
                "name": "녹화 데이터셋 품질 및 무결성",
                "status": status_code,
                "detail": f"총 {len(rec_files)}개 중 학습 선택 {active_count}개 (양호 {ok_q}, 주의 {len(warn_q)}, 불량 {len(failed_q)})",
            })
        else:
            checks.append({
                "name": "녹화 데이터셋 품질 및 무결성",
                "status": "ok",
                "detail": f"총 {len(rec_files)}개 파일 무결성 통과 · 학습 선택 {active_count}개 (양호 {ok_q}, 주의 {len(warn_q)})",
            })

    # 5. PyTorch / CUDA training environment check
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
        dev = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
        checks.append({
            "name": "PyTorch 학습 엔진 및 가속기",
            "status": "ok",
            "detail": f"PyTorch {torch.__version__} · {'CUDA GPU (' + dev + ')' if cuda_ok else 'CPU 모드'}",
        })
    except Exception as exc:
        checks.append({
            "name": "PyTorch 학습 엔진 및 가속기",
            "status": "fail",
            "detail": f"PyTorch 로드 실패: {exc}",
        })

    # 6. Recent failure/bug log inspection
    log_dir = ROOT / "logs"
    fail_logs = sorted(log_dir.glob("failures-*.jsonl")) if log_dir.is_dir() else []
    recent_failures = 0
    if fail_logs:
        try:
            lines = fail_logs[-1].read_text(encoding="utf-8", errors="ignore").splitlines()
            recent_failures = len(lines)
        except Exception:
            pass
    checks.append({
        "name": "런타임 오류 및 오작동 기록",
        "status": "ok" if recent_failures == 0 else "warn",
        "detail": "최근 기록된 런타임 예외 없음" if recent_failures == 0 else f"최근 로그에 사용자 오작동 표시 {recent_failures}건 기록됨",
    })

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    overall = "fail" if n_fail > 0 else ("warn" if n_warn > 0 else "ok")
    summary_msg = (
        f"진단 완료 ({time.strftime('%H:%M:%S')}): "
        f"정상 {len(checks) - n_fail - n_warn} · 주의 {n_warn} · 오류 {n_fail}"
    )
    return {
        "overall": overall,
        "summary": summary_msg,
        "checks": checks,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
