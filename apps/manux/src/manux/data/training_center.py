"""Training center data, model report summaries, background training job, and export package.

Ported from `origin/codex/rasp-jun-training-center` (`src/training_center.py` + PR #71 selective
gesture training) into the v0.4 `data` layer (`manux.data.training_center`). Pure Python + numpy
(no Qt, no torch); `ui.bridge` accesses this module via services injected by `desktop.py`.
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

import numpy as np

from manux.data import capture_plan as plans
from manux.data.catalog import ALL_TASKS, BY_KEY, CATEGORIES, DISPLAY_KEYS, NONE, TASKS, is_free
from manux.data.recorder import public_source
from manux.data.recording import read

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
            if data.get("summary_version") == 3 and data.get("size") == stat.st_size and data.get("mtime") == int(stat.st_mtime):
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
        "summary_version": 3,
        "file": path.name,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "mode": meta.get("mode"),
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


def dataset_summary(rec_dir: Path) -> Dict[str, Any]:
    rec_dir = Path(rec_dir)
    files = sorted(rec_dir.glob("*.jsonl.gz")) if rec_dir.is_dir() else []
    per: Dict[str, Dict[str, Any]] = {k: {"reps": 0, "postures": {}, "minutes": 0.0} for k in NAMES}
    out_files = []
    coverage: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in NAMES}
    for p in files:
        try:
            s = _summarise_file(p)
        except Exception as exc:
            out_files.append({"file": p.name, "error": str(exc)})
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
        info = _normalise_card_metrics(json.loads(info_path.read_text(encoding="utf-8")))
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
        elif task.label not in classes and not specialist:
            advice.append("이 동작을 녹화하고 다시 학습하면 모델에 추가됩니다")
        if advice:
            level = "need" if any(("부족" in a or "낮아요" in a or "많아요" in a) for a in advice) else "retrain"
        row["advice"] = " · ".join(advice) if advice else "좋아요"
        row["level"] = level
        rows.append(row)
    return rows


def terminate_training_process(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    else:
        proc.terminate()


def _finished_recordings(rec_dir: Path) -> Tuple[List[Path], List[str]]:
    good: List[Path] = []
    skipped: List[str] = []
    if not Path(rec_dir).is_dir():
        return good, skipped
    for path in sorted(Path(rec_dir).glob("*.jsonl.gz")):
        try:
            rec = read(path)
            if rec.complete and not rec.bad_lines and rec.meta.get("split") != "eval":
                good.append(path)
            elif not rec.complete or rec.bad_lines:
                skipped.append(path.name)
        except Exception:
            skipped.append(path.name)
    return good, skipped


class TrainingJob:
    """Runs `tools/train_tcn.py` in a background process (keeping torch out of the app process)."""

    def __init__(self, rec_dir: Path, model_path: Path, *,
                 tools_dir: Optional[Path] = None, cache_dir: Optional[Path] = None) -> None:
        self.rec_dir = Path(rec_dir)
        self.model_path = Path(model_path)
        self.tools_dir = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parents[3] / "tools"
        self.cache_dir = Path(cache_dir) if cache_dir is not None else self.model_path.parent.parent / "features"
        self.proc: Optional[subprocess.Popen] = None
        self.state: Dict[str, Any] = {"running": False}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self.state.get("running"))

    def start(self, epochs: int = 20, gestures: Optional[Sequence[str]] = None) -> bool:
        if self.running:
            return False
        if gestures is not None:
            if not gestures or any(key not in BY_KEY or BY_KEY[key].label == NONE for key in gestures):
                raise ValueError("학습할 동작을 선택하세요. 중립은 오답 자료로 함께 사용됩니다.")
        recordings, skipped = _finished_recordings(self.rec_dir)
        if not recordings:
            with self._lock:
                self.state = {
                    "running": False,
                    "error": "학습할 완료된 녹화가 없습니다. 먼저 안내 녹화를 진행하세요.",
                    "skipped": skipped,
                }
            return False
        script = self.tools_dir / "train_tcn.py"
        target_key = gestures[0] if gestures else ""
        target_label = BY_KEY[target_key].label if target_key else ""
        if target_label:
            out_path = self.model_path.parent / "gestures" / f"{target_label}.onnx"
        else:
            out_path = self.model_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            sys.executable,
            str(script),
            *(str(p) for p in recordings),
            "--output",
            str(out_path),
            "--epochs",
            str(epochs),
            "--cache-dir",
            str(self.cache_dir),
        ]
        if target_label:
            args.extend(["--classes", NONE, target_label])
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            args,
            cwd=str(self.tools_dir.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=flags,
        )
        with self._lock:
            self.state = {
                "running": True,
                "stage": ("선택 동작 학습 · " + NAMES.get(target_key, target_key)) if target_key else "데이터 읽는 중 및 평가·최종 학습 중",
                "target": target_key,
                "fold": 0,
                "folds": 0,
                "epoch": 0,
                "epochs": epochs,
                "progress": 0.15,
                "started": time.time(),
                "skipped": skipped,
                "log": [],
            }
        threading.Thread(target=self._read, args=(target_label, out_path), daemon=True).start()
        return True

    def _read(self, target_label: str, out_path: Path) -> None:
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
            if isinstance(ev, dict) and "event" in ev:
                self._apply(ev)
        code = self.proc.wait()
        with self._lock:
            self.state["running"] = False
            if code == 0:
                if target_label:
                    self._record_specialist(target_label, out_path)
                self.state["stage"] = "끝! 새 모델 저장됨"
                self.state["progress"] = 1.0
            elif not self.state.get("error"):
                tail = " / ".join(self.state.get("log", [])[-3:])
                self.state["error"] = "학습이 실패했어요" + (f": {tail}" if tail else "")
            self.state["finished"] = time.time()

    def _record_specialist(self, label: str, out_path: Path) -> None:
        bank_dir = out_path.parent
        entries = read_bank(bank_dir)
        entries[label] = {"model": out_path.name, "card": out_path.with_suffix(".json").name}
        manifest = bank_dir / "manifest.json"
        manifest.write_text(
            json.dumps({"schema": SPECIALIST_SCHEMA, "models": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not self.model_path.exists():
            shutil.copy2(out_path, self.model_path)
            shutil.copy2(out_path.with_suffix(".json"), self.model_path.with_suffix(".json"))

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
                 model_path: Optional[Path] = None) -> None:
        self.config_dir = Path(config_dir)
        self.data_dir = Path(data_dir)
        self.recordings_dir = Path(recordings_dir)
        self.plans_path = plans.plans_file(self.config_dir)
        self.model_path = Path(model_path) if model_path is not None else self.data_dir / "models" / "gesture_tcn.onnx"
        self.exports_dir = self.data_dir / "exports"
        self.job = TrainingJob(self.recordings_dir, self.model_path, cache_dir=self.data_dir / "features")

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
        return plans.duration(plan, focus)

    def load_plan(self, focus: str) -> Dict[str, Any]:
        return plans.load_plan(focus, self.plans_path)

    def save_plan(self, plan: Mapping[str, Any], focus: str) -> Dict[str, Any]:
        return plans.save_plan(plan, focus, self.plans_path)

    # training center operations
    def summary_and_rows(self) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]], Tuple[str, bool]]:
        summary = dataset_summary(self.recordings_dir)
        report = model_report(self.model_path)
        rows = gesture_rows(summary, report)
        storage = storage_text(summary)
        return summary, report, rows, storage

    def export(self) -> Path:
        return export_package(self.model_path, self.exports_dir)
