"""Training center data, model report summaries, background training job, and export package.

Ported from `origin/codex/rasp-jun-training-center` (`src/training_center.py` + PR #71 selective
gesture training) into the v0.4 `data` layer (`manux.data.training_center`). Pure Python + numpy
(no Qt, no torch); `ui.bridge` accesses this module via services injected by `desktop.py`.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import re
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

import numpy as np

from manux.data import capture_plan as plans
from manux.data.catalog import ALL_TASKS, BY_KEY, CATEGORIES, DISPLAY_KEYS, NONE, TASKS, is_free, tasks_for
from manux.data.recorder import public_source
from manux.data.recording import read
from manux.core.contracts import RecordingIdentity
from manux.data.worker_job import WorkerJob

GOAL_REPS = 30            # repetitions per gesture worth aiming for (3 postures x 10)
GOAL_POSTURES = 3
GOAL_RATE = 0.8           # held-out recognition target
GOAL_FREE_MIN = 9.0       # minutes of "none" / free cursor movement
MAX_MISFIRES_PER_MIN = 0.5
STORAGE_LIMIT_MB = 500
SPECIALIST_SCHEMA = "manux-gesture-models/1"

NAMES: Dict[str, str] = {k: BY_KEY[k].name for k in DISPLAY_KEYS}
ALL_NAMES: Dict[str, str] = {t.key: t.name for t in ALL_TASKS}


def _frame_durations(frames: Sequence[Mapping[str, Any]]) -> np.ndarray:
    n = len(frames)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.array([1.0 / 30.0], dtype=np.float64)
    ts = np.array([float(f.get("t", i / 30.0)) for i, f in enumerate(frames)], dtype=np.float64)
    dt = np.clip(np.diff(ts), 0.0, 0.2)
    return np.append(dt, dt[-1] if len(dt) else 1.0 / 30.0)


def _usable(frame: Mapping[str, Any]) -> bool:
    world = frame.get("world")
    return isinstance(world, list) and len(world) == 21


def _summarise_file(path: Path) -> Dict[str, Any]:
    """Repetitions per gesture and condition, minutes of free moving, marks - cached next to the recording."""
    cache = path.with_name(path.name + ".summary.json")
    stat = path.stat()
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("summary_version") == 4 and data.get("size") == stat.st_size and data.get("mtime") == int(stat.st_mtime):
                return data
        except Exception:
            pass
    rec = read(path)
    if not rec.complete or rec.bad_lines:
        raise ValueError("끝나지 않은 녹화 (학습에서 빠짐)")
    meta, frames, events = rec.meta, rec.frames, rec.events
    reps: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    repetitions: Dict[Tuple[Any, ...], Dict[str, Any]] = defaultdict(lambda: {"do": 0.0, "rest": False})
    free: Dict[Tuple[str, str], float] = defaultdict(float)
    seconds = _frame_durations(frames)
    coverage: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in NAMES}
    valid_frames = 0
    for i, f in enumerate(frames):
        lab = f.get("label") or {}
        if not _usable(f):
            continue
        valid_frames += 1
        ck = plans.label_condition_key(lab)
        task = str(lab.get("task") or "")
        if lab.get("phase") in ("do", "rest") and task in BY_KEY and not is_free(task):
            key = (task, ck, lab.get("set"), lab.get("rep"))
            item = repetitions[key]
            item["new"] = bool(lab.get("condition_key"))
            if lab["phase"] == "do":
                item["do"] += float(seconds[i])
            else:
                item["rest"] = True
        elif lab.get("phase") == "free" and is_free(task):
            free[(task, ck)] += float(seconds[i])
        elif meta.get("mode") == "free" and lab.get("target") == NONE:
            free[(NONE, "use")] += float(seconds[i])
    for (task, ck, _, _), item in repetitions.items():
        if item["do"] > 0 and (not item.get("new") or (item["do"] >= 0.25 and item["rest"])):
            reps[task][ck] += 1
    for task, by_condition in reps.items():
        coverage.setdefault(task, {})
        coverage[task] = {ck: {"reps": n, "seconds": 0.0} for ck, n in by_condition.items()}
    for (task, ck), value in free.items():
        coverage.setdefault(task, {})[ck] = {"reps": 0, "seconds": value}
    pc, cam_note = public_source(meta)
    data = {
        "summary_version": 4,
        "file": path.name,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "mode": meta.get("mode"),
        "profile": meta.get("person") or meta.get("profile") or "",
        "split": meta.get("split") or "unclassified",
        "preset": meta.get("preset") or "pinch",
        "created": meta.get("created"),
        "pc": pc,
        "camera": cam_note,
        "frames": len(frames),
        "reps": {k: dict(v) for k, v in reps.items()},
        "free_minutes": {task: sum(v for (t, _), v in free.items() if t == task) / 60.0 for task in NAMES if is_free(task)},
        "coverage": coverage,
        "valid_frames": valid_frames,
        "seconds": float(seconds.sum()),
        "marks": sum(1 for e in events if e.get("kind") == "mark"),
    }
    try:
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def dataset_summary(rec_dir: Path, *, profile=None, split=None) -> Dict[str, Any]:
    rec_dir = Path(rec_dir)
    files = sorted(rec_dir.rglob("*.jsonl.gz")) if rec_dir.is_dir() else []
    per: Dict[str, Dict[str, Any]] = {k: {"reps": 0, "postures": {}, "minutes": 0.0} for k in NAMES}
    out_files = []
    coverage: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in NAMES}
    for p in files:
        try:
            s = _summarise_file(p)
        except Exception as exc:
            out_files.append({"file": p.name, "error": str(exc)})
            continue
        if (profile is not None and s.get("profile") != profile) or (split is not None and s.get("split") != split):
            continue
        out_files.append(s)
        for task, conditions in s.get("coverage", {}).items():
            target = coverage.setdefault(task, {})
            for key, values in conditions.items():
                current = target.setdefault(key, {"reps": 0, "seconds": 0.0})
                current["reps"] += values["reps"]
                current["seconds"] += values["seconds"]
        for task, by_posture in s["reps"].items():
            g = per.setdefault(task, {"reps": 0, "postures": {}, "minutes": 0.0})
            for posture, n in by_posture.items():
                g["reps"] += n
                g["postures"][posture] = g["postures"].get(posture, 0) + n
        for task, minutes in s["free_minutes"].items():
            per.setdefault(task, {"reps": 0, "postures": {}, "minutes": 0.0})["minutes"] += minutes
    newest = max((p.stat().st_mtime for p in files), default=0.0)
    total = sum(p.stat().st_size for p in files)
    return {"files": out_files, "per_gesture": per, "newest": newest, "total_bytes": total, "coverage": coverage}


def storage_text(summary: Mapping[str, Any]) -> Tuple[str, bool]:
    mb = summary.get("total_bytes", 0) / 1024 / 1024
    if mb > STORAGE_LIMIT_MB:
        return (f"녹화 총 {mb:.0f} MB: {STORAGE_LIMIT_MB} MB를 넘었어요. 팀 허브(tools/hub.py)로 공유하고 "
                f"로컬 녹화를 정리하는 걸 권해요 (docs/dataset-hub.md)", True)
    return f"녹화 총 {mb:.0f} MB / 권장 한도 {STORAGE_LIMIT_MB} MB", False


def _normalise_card_metrics(card: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure `per_gesture` exists whether the card came from v0.4 `train_tcn.py` (`held_out_results`) or v0.3."""
    if "per_gesture" in card and isinstance(card["per_gesture"], dict):
        return card
    folds = [r for r in card.get("held_out_results", []) if isinstance(r, dict) and r.get("status") == "evaluated"]
    if not folds:
        card.setdefault("per_gesture", {})
        return card
    # Prefer condition axis folds, then posture, then person
    for axis in ("condition", "posture", "person"):
        axis_folds = [r for r in folds if r.get("axis") == axis]
        if axis_folds:
            folds = axis_folds
            break
    totals: Dict[str, Dict[str, int]] = defaultdict(lambda: {"attempts": 0, "tcn_hits": 0, "rule_hits": 0})
    neutral_sec = 0.0
    tcn_misfires = 0.0
    rule_misfires = 0.0
    for fold in folds:
        for cls, row in (fold.get("per_gesture") or {}).items():
            totals[cls]["attempts"] += int(row.get("attempts", 0))
            totals[cls]["tcn_hits"] += int(row.get("tcn_hits", 0))
            totals[cls]["rule_hits"] += int(row.get("rule_hits", 0))
        ns = float(fold.get("neutral_seconds") or 0.0)
        if ns > 0:
            neutral_sec += ns
            if fold.get("tcn_misfires_per_minute") is not None:
                tcn_misfires += float(fold["tcn_misfires_per_minute"]) * (ns / 60.0)
            if fold.get("rule_misfires_per_minute") is not None:
                rule_misfires += float(fold["rule_misfires_per_minute"]) * (ns / 60.0)
    pg: Dict[str, Dict[str, Any]] = {}
    for cls, row in totals.items():
        att = row["attempts"]
        pg[cls] = {
            "recognised": row["tcn_hits"],
            "total": att,
            "rate": (row["tcn_hits"] / att) if att > 0 else None,
            "rules_rate": (row["rule_hits"] / att) if att > 0 else None,
            "train_accuracy": row.get("train_accuracy"),
        }
    if neutral_sec > 0:
        pg[NONE] = {
            "misfires_per_min": tcn_misfires * 60.0 / neutral_sec,
            "rules_misfires_per_min": rule_misfires * 60.0 / neutral_sec,
        }
    card["per_gesture"] = pg
    return card


def read_bank(folder: Path) -> Dict[str, Dict[str, str]]:
    manifest = Path(folder) / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        models = doc.get("models", {})
        return dict(models) if isinstance(models, dict) else {}
    except (OSError, ValueError):
        return {}


def model_report(model: Path) -> Optional[Dict[str, Any]]:
    model = Path(model)
    info_path = model.with_suffix(".json")
    bank_dir = model.parent / "gestures"
    entries = read_bank(bank_dir)
    if not (model.is_file() and info_path.is_file()) and not entries:
        return None
    if model.is_file() and info_path.is_file():
        raw = json.loads(info_path.read_text(encoding="utf-8"))
        validation = model.parent / "validation.json"
        if validation.is_file():
            report = json.loads(validation.read_text(encoding="utf-8"))
            candidate, baseline = report["candidate"], report["baseline"]
            pg = {}
            for name, metric in candidate["per_gesture"].items():
                total = metric["attempts"]
                pg[name] = {"recognised": metric["tcn_hits"], "total": total,
                            "rate": metric["tcn_hits"] / total,
                            "rules_rate": baseline["per_gesture"].get(name, {}).get("tcn_hits", 0) / total}
            pg[NONE] = {"misfires_per_min": candidate["tcn_misfires_per_minute"],
                        "rules_misfires_per_min": baseline["tcn_misfires_per_minute"]}
            raw["per_gesture"] = pg
            raw["evaluation_source"] = "independent_eval"
        info = _normalise_card_metrics(raw)
        info["mtime"] = model.stat().st_mtime
    else:
        info = {"classes": [NONE], "trained": "선택 학습 전용", "data": [], "per_gesture": {}, "mtime": 0.0}
    info["specialists"] = {}
    for label, entry in entries.items():
        card_file = bank_dir / entry.get("card", f"{label}.json")
        if card_file.is_file():
            try:
                card = _normalise_card_metrics(json.loads(card_file.read_text(encoding="utf-8")))
                card["mtime"] = card_file.stat().st_mtime
                info["specialists"][label] = card
                info["mtime"] = max(float(info.get("mtime", 0.0)), card["mtime"])
            except (OSError, ValueError):
                continue
    return info


def gesture_rows(summary: Mapping[str, Any], report: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One row per gesture in `DISPLAY_KEYS`: data, scores and advice for the Training Center UI."""
    rows = []
    pg = (report or {}).get("per_gesture", {})
    for key, name in NAMES.items():
        task = BY_KEY[key]
        d = summary["per_gesture"].get(key, {"reps": 0, "postures": {}, "minutes": 0.0})
        specialist = (report or {}).get("specialists", {}).get(task.label)
        current = specialist or report or {}
        m = current.get("per_task", {}).get(key, current.get("per_gesture", pg).get(task.label, {}))
        classes = current.get("classes", list(pg))
        stale = bool(current) and summary.get("newest", 0) > current.get("mtime", 0)
        row = {
            "key": key,
            "name": name,
            "reps": d["reps"],
            "postures": len(d["postures"]),
            "category": task.category,
            "instruction": task.do,
            "model_text": ("선택 동작 모델 · " + str(specialist.get("trained", "")) if specialist else
                           "TCN 학습됨" if task.label in classes else "TCN 학습 전 · 현재 규칙으로 동작"),
            "can_train": task.label != NONE and task.gesture is not None,
            "label": task.label,
            "minutes": d.get("minutes", 0.0),
            "train_accuracy": m.get("train_accuracy"),
            "rate": m.get("rate"),
            "rules_rate": m.get("rules_rate"),
            "misfires_per_min": m.get("misfires_per_min"),
            "rules_misfires_per_min": m.get("rules_misfires_per_min"),
        }
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
                row["score_text"] += f" (이전 기준 {row['rules_misfires_per_min']:.1f})"
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
                row["score_text"] += f" (이전 기준 {row['rules_rate'] * 100:.0f}%)"
        if row["train_accuracy"] is not None:
            row["fit_text"] = f"학습 {row['train_accuracy'] * 100:.0f}%"
        else:
            row["fit_text"] = "별도 평가" if current.get("evaluation_source") else "학습 점수 미측정"
        if stale:
            advice.append("새 녹화가 있어요: 다시 학습하세요")
        if report is None:
            advice.append("아직 학습한 모델이 없어요")
        elif task.label not in classes and not specialist:
            advice.append("이 동작을 녹화하고 다시 학습하면 모델에 추가됩니다")
        if advice:
            level = "need" if any(("부족" in a or "낮아요" in a or "많아요" in a) for a in advice) else "retrain"
        row["advice"] = " · ".join(advice) if advice else "좋아요"
        row["level"] = level
        rows.append(row)
    return rows


def recording_sets(folder: Path, identity: RecordingIdentity):
    """Team train data and the current user's separate eval trials, one preset, no duplicates."""
    train, evaluation, seen = [], [], set()
    for path in sorted(Path(folder).rglob("*.jsonl.gz")):
        try:
            rec = read(path)
            if not rec.complete or rec.bad_lines or rec.meta.get("preset", "pinch") != identity.preset:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            split = rec.meta.get("split")
            if split == "train":
                train.append(path)
            elif split == "eval" and str(rec.meta.get("person") or rec.meta.get("profile") or "").lower() == identity.profile.lower():
                evaluation.append(path)
        except (OSError, ValueError, TypeError):
            continue
    return train, evaluation


def _atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class TrainingJob(WorkerJob):
    """Unified model training + separate validation + atomic installation in the worker."""

    def __init__(self, rec_dir, model_path, *, tools_dir=None, cache_dir=None,
                 specs=None, identity=None, store_dir=None, on_done=None):
        model_path = Path(model_path)
        super().__init__(model_path.parent / "jobs", on_done=on_done, tools_dir=tools_dir)
        self.rec_dir = Path(rec_dir)
        self.model_path = model_path
        self.cache_dir = Path(cache_dir) if cache_dir else model_path.parent / "features"
        self.store_dir = Path(store_dir) if store_dir else model_path.parent / "personal"
        self.specs, self.identity = specs or {}, identity

    def start(self, epochs=20, gestures=None):
        try:
            identity = self.identity() if self.identity else RecordingIdentity("local")
            if not self.specs:
                raise ValueError("학습 가능한 동작 구성이 없습니다.")
            if gestures is not None and (not gestures or any(k not in BY_KEY or BY_KEY[k].label == NONE for k in gestures)):
                raise ValueError("학습할 동작을 선택하세요.")
            return self.start_request({"kind": "train", "recordings_dir": str(self.rec_dir),
                "profile": identity.profile, "preset": identity.preset, "mode": identity.preset if identity.preset != "trigger" else "pointer",
                "store": str(self.store_dir), "cache_dir": str(self.cache_dir), "specs": self.specs,
                "epochs": max(1, int(epochs)), "focus_labels": sorted({BY_KEY[k].label for k in gestures}) if gestures else []})
        except (ValueError, OSError) as error:
            return self.fail(str(error))


def export_package(model_path: Path, out_root: Path) -> Path:
    """Export the trained model, its card, any specialist models, and a README into `out_root/gesture-tcn-<stamp>.zip`."""
    model_path = Path(model_path)
    report = model_report(model_path)
    if report is None:
        raise FileNotFoundError("학습한 모델이 없어요. 먼저 학습하세요.")
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    out = Path(out_root) / f"gesture-tcn-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    if model_path.is_file() and model_path.with_suffix(".json").is_file():
        shutil.copy2(model_path, out / "gesture_tcn.onnx")
        shutil.copy2(model_path.with_suffix(".json"), out / "gesture_tcn.json")
        if (model_path.parent / "validation.json").is_file():
            shutil.copy2(model_path.parent / "validation.json", out / "validation.json")
    bank_dir = model_path.parent / "gestures"
    entries = read_bank(bank_dir)
    if entries:
        target = out / "gestures"
        target.mkdir(exist_ok=True)
        for entry in entries.values():
            for key in ("model", "card"):
                src = bank_dir / entry[key]
                if src.is_file():
                    shutil.copy2(src, target / entry[key])
        (target / "manifest.json").write_text(
            json.dumps({"schema": SPECIALIST_SCHEMA, "models": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    pg = report.get("per_gesture", {})
    lines = [
        f"# MANUX 손짓 TCN 모델 ({report.get('trained')})",
        "",
        f"- 정답 종류: {', '.join(report.get('classes', []))}",
        f"- 입력: 최근 {report.get('window', 15)}프레임, 특징 {report.get('features', 133)}개 ({report.get('feature_version', 'local3d-v1')})",
        f"- 판정: 확률 {report.get('threshold', 0.7)} 이상이 {report.get('frames_in_a_row', 3)}프레임 연속",
        "",
        "## 데이터 출처",
    ] + [
        f"- {d['file']} · {d.get('pc')} · {(d.get('camera') or {}).get('note', '')} · {d['frames']}프레임"
        for d in report.get("data", [])
    ] + ["", "## 동작별 성적 (학습에 안 쓴 조건)"]
    for key, g in pg.items():
        if "rate" in g:
            pct = lambda v: "-" if v is None else f"{v * 100:.0f}%"  # noqa: E731
            lines.append(
                f"- {key}: 인식 {g.get('recognised', 0)}/{g.get('total', 0)} = {pct(g.get('rate'))} "
                f"(규칙만 {pct(g.get('rules_rate'))})"
            )
        else:
            lines.append(
                f"- {key}: 오작동 {g.get('misfires_per_min')}회/분 (규칙 {g.get('rules_misfires_per_min')}회/분)"
            )
    if entries:
        lines += ["", "## 선택 학습 모델", ", ".join(entries)]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.make_archive(str(out), "zip", root_dir=out)
    return out


class TrainingCenterService:
    """Service bundle injected into `ui.bridge.Bridge` by `desktop.py` so `ui` never imports `data`."""

    def __init__(self, config_dir: Path, data_dir: Path, recordings_dir: Path, *,
                 model_path: Optional[Path] = None, specs=None, on_ready=None, env_files=()) -> None:
        self.config_dir = Path(config_dir)
        self.data_dir = Path(data_dir)
        self.recordings_dir = Path(recordings_dir)
        self.plans_path = plans.plans_file(self.config_dir)
        self.model_path = Path(model_path) if model_path is not None else self.data_dir / "models" / "gesture_tcn.onnx"
        self.exports_dir = self.data_dir / "exports"
        self.store_dir = self.data_dir / "models" / "personal"
        self.profile = ""
        self.split = "train"
        self.preset_name = "pinch"
        self.repo = "se4nchoi/manux-datasets"
        self.env_files = tuple(map(Path, env_files)) + (self.config_dir / ".env",)
        self.preferences_path = self.config_dir / "training-center.json"
        try:
            saved = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("invalid preferences")
            self.profile = str(saved.get("profile", "")).lower()
        except (OSError, ValueError, TypeError):
            pass
        self.job = TrainingJob(self.recordings_dir, self.model_path, cache_dir=self.data_dir / "features",
                               specs=specs, identity=self.identity, store_dir=self.store_dir, on_done=on_ready)
        self.hub_job = WorkerJob(self.data_dir / "hub-jobs")

    def identity(self) -> RecordingIdentity:
        return RecordingIdentity(self.profile, self.preset_name, self.split)

    def configure(self, profile: str) -> None:
        identity = RecordingIdentity(profile.strip().lower(), self.preset_name, self.split)
        _atomic_text(self.preferences_path, json.dumps({"profile": identity.profile}))
        self.profile = identity.profile

    def hub_action(self, action: str):
        try:
            identity = self.identity()
            if self.job.running:
                raise ValueError("학습이 끝난 뒤 허브 작업을 시작하세요.")
            return self.hub_job.start_request({"kind": "hub", "action": action, "profile": identity.profile,
                "preset": identity.preset, "repo": self.repo, "config_dir": str(self.config_dir),
                "env_files": [str(path) for path in self.env_files], "recordings_dir": str(self.recordings_dir)})
        except (ValueError, OSError) as error:
            return self.hub_job.fail(str(error))

    def current_model(self):
        try:
            pointer = json.loads((self.store_dir / "active.json").read_text(encoding="utf-8"))
            version = pointer.get("version", "")
            if re.fullmatch(r"[0-9a-f]{32}", str(version)):
                return self.store_dir / "versions" / version / "model.onnx"
        except (OSError, ValueError, TypeError):
            pass
        return self.model_path

    def close(self):
        self.job.stop()
        self.hub_job.stop()

    # catalog & capture_plan helpers for RecordingPlanEditor and Bridge
    categories = CATEGORIES
    total_tasks = len(TASKS)
    options = plans.OPTIONS
    names = NAMES
    all_names = ALL_NAMES

    def has_task(self, key: str) -> bool:
        return key in BY_KEY

    def task_info(self, key: str) -> Optional[Dict[str, Any]]:
        task = BY_KEY.get(key)
        if task is None:
            return None
        return {
            "key": task.key,
            "name": task.name,
            "do": task.do,
            "free": task.free,
            "direction": task.direction,
            "label": task.label,
            "gesture": task.gesture,
        }

    def is_free(self, key: str) -> bool:
        return is_free(key)

    def preset(self, name: str = "basic") -> Dict[str, Any]:
        return plans.preset(name)

    def condition(self, **values: Any) -> Dict[str, Any]:
        return plans.condition(**values)

    def condition_key(self, row: Mapping[str, Any]) -> str:
        return plans.condition_key(row)

    def normalise(self, plan: Any, *, require_enabled: bool = True) -> Dict[str, Any]:
        return plans.normalise(plan, require_enabled=require_enabled)

    def duration(self, plan: Mapping[str, Any], focus: str = "") -> float:
        return plans.duration(plan, tasks_for(focus))

    @staticmethod
    def _plan_key(focus):
        if isinstance(focus, str):
            return focus
        return "selection-" + hashlib.sha256("|".join(sorted(focus)).encode()).hexdigest()[:16]

    def load_plan(self, focus: str) -> Dict[str, Any]:
        return plans.load_plan(self._plan_key(focus), self.plans_path)

    def save_plan(self, plan: Mapping[str, Any], focus: str) -> Dict[str, Any]:
        return plans.save_plan(plan, self._plan_key(focus), self.plans_path)

    # training center operations
    def summary_and_rows(self) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]], Tuple[str, bool]]:
        summary = dataset_summary(self.recordings_dir)
        summary["coverage"] = dataset_summary(self.recordings_dir, profile=self.profile, split=self.split)["coverage"]
        report = model_report(self.current_model())
        rows = gesture_rows(summary, report)
        mode = self.preset_name if self.preset_name != "trigger" else "pointer"
        for row in rows:
            declaration = self.job.specs.get(BY_KEY[row["key"]].gesture, {})
            if mode not in declaration.get("modes", []):
                row["can_train"] = False
                if BY_KEY[row["key"]].label != NONE:
                    row["advice"] = "녹화·공유 가능 · 이 모드의 직접 학습 적용은 아직 지원하지 않습니다."
        storage = storage_text(summary)
        return summary, report, rows, storage

    def export(self) -> Path:
        return export_package(self.current_model(), self.exports_dir)
