"""
Training center (2026-11-05): what has been recorded, how well the model knows each gesture, what to record next,
training in the background, and exporting the model for other apps. Used by the Qt screen (src/qt_dashboard.py)
and the console tools (scripts/training_report.py, scripts/export_model.py, studio.py). No Qt here.

The loop it supports: record -> train -> look at each gesture's score -> record more of the weak ones -> train again
-> export.
"""
from __future__ import annotations

import json
import copy
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from src.gesture_catalog import BY_KEY, NAMES, CLASSES, is_free

ROOT = Path(__file__).resolve().parents[1]
REC_DIR = ROOT / "recordings"
MODEL = ROOT / "models" / "gesture_tcn.onnx"
EXPORT_DIR = ROOT / "exports"

GOAL_REPS = 30            # repetitions per gesture worth aiming for (docs: 3 postures x 10-15)
GOAL_POSTURES = 3
GOAL_RATE = 0.8           # recognition in postures the model did not train on
GOAL_FREE_MIN = 9.0       # minutes of "nothing" (half-fist moving)
MAX_MISFIRES_PER_MIN = 0.5
STORAGE_LIMIT_MB = 500    # recordings travel with the code in git; past this, suggest a repository of their own


# ---------------------------------------------------------------- what has been recorded
def _summarise_file(path: Path) -> Dict[str, Any]:
    """Repetitions per gesture and posture, minutes of free moving, marks - cached next to the recording."""
    cache = path.with_name(path.name + ".summary.json")
    stat = path.stat()
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("summary_version") == 4 and data.get("size") == stat.st_size and data.get("mtime") == int(stat.st_mtime):
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
    click_phases = defaultdict(lambda: defaultdict(int))
    quick_reps = defaultdict(int)
    for i, f in enumerate(frames):
        lab = f.get("label") or {}
        if not usable(f):
            continue
        valid_frames += 1
        ck = label_condition_key(lab)
        task = lab.get("task")
        if task in ("thumb_middle", "thumb_ring") and (f.get("rule") or {}).get("action") == BY_KEY[task].action:
            click_phases[task][lab.get("phase", "unknown")] += 1
        if lab.get("phase") in ("do", "rest") and task in BY_KEY and not is_free(task):
            key = (lab["task"], ck, lab.get("set"), lab.get("rep"))
            item = repetitions[key]
            item["new"] = bool(lab.get("condition_key"))
            item["requested_hold"] = lab.get("requested_hold")
            if lab["phase"] == "do":
                item["do"] += seconds[i]
            else:
                item["rest"] = True
        elif lab.get("phase") == "free" and is_free(task):
            free[(task, ck)] += seconds[i]
        elif meta.get("mode") == "free" and lab.get("target") == "none":
            free[("none", "use")] += seconds[i]
    for (task, ck, _, _), item in repetitions.items():
        short = item.get("requested_hold") is not None and float(item["requested_hold"]) <= .7
        minimum = .15 if short else .25
        if item["do"] > 0 and (not item.get("new") or (item["do"] >= minimum and item["rest"])):
            reps[task][ck] += 1
            if short and item["rest"]:
                quick_reps[task] += 1
    for task, by_condition in reps.items():
        coverage[task] = {ck: {"reps": n, "seconds": 0.0} for ck, n in by_condition.items()}
    for (task, ck), value in free.items():
        coverage[task][ck] = {"reps": 0, "seconds": value}
    data = {
        "summary_version": 4,
        "click_phases": {task: dict(counts) for task, counts in click_phases.items()},
        "quick_reps": dict(quick_reps),
        "file": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime), "mode": meta.get("mode"),
        "created": meta.get("created"), "pc": public_source(meta)[0], "camera": public_source(meta)[1],
        "frames": len(frames), "reps": {k: dict(v) for k, v in reps.items()},
        "free_minutes": {task: sum(v for (t, _), v in free.items() if t == task) / 60 for task in NAMES if is_free(task)},
        "coverage": coverage, "valid_frames": valid_frames, "seconds": float(seconds.sum()),
        "marks": sum(1 for e in events if e.get("kind") == "mark"),
    }
    try:
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data


def dataset_summary(rec_dir: Path = REC_DIR) -> Dict[str, Any]:
    files = sorted(Path(rec_dir).glob("*.jsonl.gz"))
    per: Dict[str, Dict[str, Any]] = {k: {"reps": 0, "postures": {}, "minutes": 0.0} for k in NAMES}
    out_files = []
    coverage = {k: {} for k in NAMES}
    for p in files:
        try:
            s = _summarise_file(p)
        except Exception as exc:          # a broken / unfinished file must not hide the others
            out_files.append({"file": p.name, "error": str(exc)})
            continue
        out_files.append(s)
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
    newest = max((p.stat().st_mtime for p in files), default=0.0)
    total = sum(p.stat().st_size for p in files)
    return {"files": out_files, "per_gesture": per, "newest": newest, "total_bytes": total, "coverage": coverage}


def storage_text(summary: Dict[str, Any]) -> Tuple[str, bool]:
    """(text, too big) for the recordings kept in the git repository together with the code (user's choice,
    2026-11-05). Past STORAGE_LIMIT_MB: suggest moving them to a repository of their own."""
    mb = summary.get("total_bytes", 0) / 1024 / 1024
    if mb > STORAGE_LIMIT_MB:
        return (f"녹화 총 {mb:.0f} MB: {STORAGE_LIMIT_MB} MB를 넘었어요. 받기가 느려지니 학습 데이터를 "
                f"전용 저장소로 나누는 걸 권해요 (docs/training-studio.md)", True)
    return f"녹화 총 {mb:.0f} MB / 분리 권장 {STORAGE_LIMIT_MB} MB", False


def model_report(model: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    from src.tcn_models import model_info
    return model_info(MODEL if model is None else model)


# ---------------------------------------------------------------- what to do next
def gesture_rows(summary: Dict[str, Any], report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per gesture: data, scores and advice, for the screen."""
    rows = []
    pg = (report or {}).get("per_gesture", {})
    for key, name in NAMES.items():
        task = BY_KEY[key]
        trained_at = (report or {}).get("class_mtime", {}).get(task.label, (report or {}).get("mtime", 0))
        related = [f for f in summary.get("files", []) if not f.get("error")
                   and (any(f.get("reps", {}).get(t.key) for t in BY_KEY.values() if t.label == task.label)
                        or (task.free and f.get("free_minutes", {}).get(key, 0) > 0))]
        stale = report is not None and max((f.get("mtime", 0) for f in related),
                                          default=summary.get("newest", 0) if not summary.get("files") else 0) > trained_at
        d = summary["per_gesture"].get(key, {"reps": 0, "postures": {}, "minutes": 0.0})
        m = (report or {}).get("per_task", {}).get(key, pg.get(key, {}) if key == task.label else {})
        classes = (report or {}).get("classes", list(pg))
        row = {"key": key, "name": name, "reps": d["reps"], "postures": len(d["postures"]),
               "category": task.category, "instruction": task.do,
               "label": task.label, "free": task.free,
               "tcn_available": not task.free and task.label in classes,
               "can_train": not task.free and any(summary["per_gesture"].get(t.key, {}).get("reps", 0) > 0
                                                   for t in BY_KEY.values() if t.label == task.label),
               "recordings": len(related),
               "model_text": "선택 동작 모델" if task.label in (report or {}).get("selected_classes", []) else
                             "TCN 학습됨" if task.label in classes else "TCN 학습 전 · 현재 규칙으로 동작",
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
            row["score_text"] = "아직 자세 평가 없음" if row["rate"] is None else f"자세 분류 {row['rate'] * 100:.0f}%"
            if row["rate"] is not None and row["rules_rate"] is not None:
                row["score_text"] += f" (규칙 {row['rules_rate'] * 100:.0f}%)"
        quality = (report or {}).get("task_quality", {}).get(key) or (
            (report or {}).get("class_quality", {}).get(key, {}) if key == task.label else {})
        row["interaction_text"] = quality.get("text", "실제 동작 전달 평가 없음 · 자세 분류율은 클릭 성공률이 아닙니다.") if not task.free else ""
        if row["train_accuracy"] is not None:
            row["fit_text"] = f"학습 {row['train_accuracy'] * 100:.0f}%"
        else:
            row["fit_text"] = "학습 전"
        if stale:
            advice.append("새 녹화가 있어요: 다시 학습하세요")
        if report is None:
            advice.append("아직 학습한 모델이 없어요")
        elif task.label not in classes:
            advice.append("이 동작을 녹화하고 다시 학습하면 모델에 추가됩니다")
        if key in ("thumb_middle", "thumb_ring") and related:
            quick = sum(f.get("quick_reps", {}).get(key, 0) for f in related)
            if quick < 20:
                advice.append(f"짧은 클릭 검증 녹화 {quick}/20회 · 집중 녹화의 각도×속도 추천안을 사용하세요")
            prep = sum(f.get("click_phases", {}).get(key, {}).get("prep", 0) for f in related)
            performed = sum(f.get("click_phases", {}).get(key, {}).get("do", 0) for f in related)
            if prep > performed and prep >= 5:
                advice.append(f"준비 중 규칙 클릭 후보 {prep}회: 동작 시작 시점 확인 필요")
        if advice:
            level = "need" if any(("부족" in a or "낮아요" in a or "많아요" in a) for a in advice) else "retrain"
        row["advice"] = " · ".join(advice) if advice else "좋아요"
        row["level"] = level
        rows.append(row)
    return rows


# ---------------------------------------------------------------- training in the background
def terminate_training_process(proc):
    """Stop the Windows venv child interpreter as well as its launcher."""
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

    def start(self, epochs: int = 25, tasks=None, gestures=None) -> bool:
        if self.running:
            return False
        if tasks is not None and gestures is not None:
            raise ValueError("tasks와 gestures 중 하나로 동작을 지정하세요.")
        if gestures is not None:
            tasks = gestures
        if tasks is not None:
            from src.capture_dataset import training_selection
            tasks, _ = training_selection(tasks)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        args = [sys.executable, str(ROOT / "scripts" / "train_tcn.py"), "--folds", "--progress", "--epochs", str(epochs)]
        if gestures is not None:
            args += ["--gestures", *gestures]
        elif tasks is not None:
            args += ["--tasks", *tasks]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", env=env, creationflags=flags)
        with self._lock:
            self.state = {"running": True, "stage": "데이터 읽는 중", "phase": "loading", "fold": 0, "folds": 0,
                          "epoch": 0, "epochs": epochs, "completed_epochs": 0, "epoch_seconds": [],
                          "evaluated_folds": [], "evaluation_seconds": [], "events": [],
                          "progress": 0.0, "started": time.time(), "log": [], "tasks": tasks}
            self._event("학습을 시작했습니다. 녹화를 읽는 중입니다.")
        threading.Thread(target=self._read, daemon=True).start()
        return True

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                ev = None
            if not isinstance(ev, dict):
                with self._lock:
                    self.state["log"] = (self.state.get("log", []) + [line])[-200:]
                continue
            self._apply(ev)
        code = self.proc.wait()
        self._finish(code)

    def _finish(self, code):
        with self._lock:
            self.state["running"] = False
            if self.state.get("cancelled") and not self.state.get("success"):
                self.state["stage"] = "학습을 멈췄어요. 기존 모델을 유지합니다."
                self.state["phase"] = "cancelled"
                self._event(self.state["stage"])
            elif code != 0 and not self.state.get("error"):
                self.state["error"] = "학습이 실패했어요: " + " / ".join(self.state.get("log", [])[-3:])
            elif code == 0 and not self.state.get("success") and not self.state.get("error"):
                self.state["error"] = "학습 완료 결과를 받지 못했습니다. 진행 기록을 확인하세요."
            if self.state.get("error"):
                self.state["phase"], self.state["stage"] = "error", "학습에 실패했습니다"
                self._event(self.state["error"])
            self.state["finished"] = time.time()

    def _event(self, text):
        """Caller holds _lock. Keep a bounded human-readable history separate from raw process output."""
        line = time.strftime("%H:%M:%S") + "  " + text
        self.state["events"] = (self.state.get("events", []) + [line])[-100:]

    def _apply(self, ev: Dict[str, Any]) -> None:
        with self._lock:
            s = self.state
            now = time.time()
            kind = ev.get("event")
            if kind == "loaded":
                s["folds"] = int(ev.get("folds", 0))
                for key in ("windows", "recordings", "postures", "per_class"):
                    s[key] = ev.get(key, {} if key == "per_class" else [] if key == "postures" else 0)
                s["stage"] = f"데이터 {ev.get('windows')}개 구간"
                self._event(f"데이터 읽기 완료 · 녹화 {s['recordings']}개 · 학습 구간 {s['windows']:,}개")
            elif kind in ("fold_start", "final_start"):
                s["fold"] = int(ev.get("fold", 0)) if kind == "fold_start" else s["folds"] + 1
                s["stage"] = (f"자세별 평가 학습 {s['fold']}/{s['folds']} (자세 {ev.get('posture')} 제외)"
                              if kind == "fold_start" else "최종 모델 학습")
                s["phase"] = "training"
                s["epoch"], s["losses"] = 0, []
                s.pop("loss", None)
                s["last_epoch_at"] = now
                s["completed_epochs"] = max(s.get("completed_epochs", 0), (s["fold"] - 1) * s["epochs"])
                self._event(s["stage"] + " 시작")
            elif kind == "epoch":
                delta = now - s.get("last_epoch_at", now)
                if delta > 0:
                    s["epoch_seconds"] = (s.get("epoch_seconds", []) + [delta])[-20:]
                s["last_epoch_at"] = now
                s["epoch"], s["loss"] = int(ev["epoch"]), float(ev["loss"])
                s.setdefault("losses", []).append(float(ev["loss"]))
                s["completed_epochs"] = (max(1, s["fold"]) - 1) * s["epochs"] + s["epoch"]
                self._event(f"반복 {s['epoch']}/{s['epochs']} 완료 · 손실 {s['loss']:.4f}")
            elif kind == "evaluation_start":
                s["phase"], s["evaluation_started"] = "evaluating", now
                s["stage"] = (f"자세 {ev.get('posture')} 인식률 평가 중" if ev.get("posture") is not None
                              else "최종 모델 성적 계산 중")
                self._event(s["stage"])
            elif kind in ("evaluation_done", "fold_skip"):
                fold = int(ev.get("fold", s["fold"]))
                if fold not in s.setdefault("evaluated_folds", []):
                    s["evaluated_folds"].append(fold)
                if kind == "fold_skip":
                    s["completed_epochs"] = max(s.get("completed_epochs", 0), fold * s["epochs"])
                    self._event(f"평가 {fold}회차 보류 · 다른 자세에 필요한 동작 데이터가 부족합니다.")
                else:
                    seconds = max(0, now - s.get("evaluation_started", now))
                    s.setdefault("evaluation_seconds", []).append(seconds)
                    self._event(f"평가 {fold}회차 완료")
            elif kind == "export_start":
                s["phase"], s["stage"] = "saving", "모델 저장·실행 검증 중"
                self._event(s["stage"])
            elif kind == "fold_result":
                s.setdefault("results", []).append(ev)
            elif kind == "done":
                s["stage"] = "끝! 선택한 동작 학습 완료" if ev.get("tasks") else "끝! 새 모델 저장됨"
                s["published"] = ev.get("published", True)
                s["publication_issues"] = ev.get("publication_issues", [])
                s["model"] = ev.get("model", "")
                if not s["published"]:
                    s["stage"] = "학습 완료 · 구분 성능 기준 미달로 기존 모델 유지"
                s["success"] = True
                s["phase"] = "done"
                s["finished"] = now
                s["per_gesture"] = ev.get("per_gesture")
                self._event(s["stage"])
                for issue in s["publication_issues"]:
                    self._event(issue)
            elif kind == "skipped":
                s.setdefault("skipped", []).append(ev.get("file"))
                self._event("끝나지 않았거나 읽을 수 없는 녹화 제외: " + str(ev.get("file")))
            elif kind == "note":
                self._event(ev.get("text", ""))
            elif kind == "error":
                s["error"] = ev.get("text")
                s["phase"] = "error"
                self._event(s["error"])
            total = max(1, (s["folds"] + 1) * s["epochs"])
            trained = s.get("completed_epochs", 0) / total
            evaluated = len(s.get("evaluated_folds", [])) / max(1, s["folds"])
            done = (.96 * trained + .02 * evaluated) if s["folds"] else .98 * trained
            if kind == "export_start":
                done = .98
            s["progress"] = 1.0 if kind == "done" else round(min(.99, max(s.get("progress", 0), done)), 3)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.state)

    def stop(self) -> None:
        if self.proc is not None and self.running:
            with self._lock:
                if self.state.get("success"):
                    return
                self.state["cancelled"] = True
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
from tcn_features import temporal_inputs
from tcn_models import load_predictor
from pathlib import Path


class GestureModel:
    def __init__(self, folder="."):
        self.predict, self.info = load_predictor(Path(folder) / "gesture_tcn.onnx")
        self.frames = deque(maxlen=self.info["window"] + 1)

    def step(self, t, world, image, aspect):
        """t: seconds; world / image: 21 x (x, y, z) or None when no hand. Returns {{class: probability}}."""
        self.frames.append({{"t": t, "world": world, "lm": image}} if world is not None else {{"t": t}})
        features, x, _ = temporal_inputs(list(self.frames), aspect, self.info["window"])
        p = self.predict(x[-1:])
        if not features[-1, -1]:
            p[:] = 0
            p[self.info["classes"].index("none")] = 1
        return dict(zip(self.info["classes"], p.tolist()))

# Use info["class_decisions"][gesture] for its threshold and required consecutive frames.
# Each selected gesture keeps its own confirmer; scores across models need not sum to 1.
'''


def export_package(out_root: Path = EXPORT_DIR, with_dataset: bool = True) -> Path:
    """exports/gesture-tcn-<time>/ (+ .zip): model, its info, the feature code, an example, a README, and the
    per-frame features + labels of every recording (dataset.npz) for training elsewhere."""
    report = model_report()
    if report is None:
        raise FileNotFoundError("학습한 모델이 없어요. 먼저 학습하세요.")
    from src.tcn_models import copy_bundle
    import uuid
    stamp = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    out = Path(out_root) / f"gesture-tcn-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    copy_bundle(MODEL, out)
    shutil.copy2(ROOT / "src" / "tcn_features.py", out / "tcn_features.py")
    shutil.copy2(ROOT / "src" / "tcn_models.py", out / "tcn_models.py")
    shutil.copy2(ROOT / "src" / "gesture_models.py", out / "gesture_models.py")
    (out / "active-model-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
            lines.append(f"- {key}: 자세 분류 {g['recognised']}/{g['total']} = {pct(g.get('rate'))} (규칙만 {pct(g.get('rules_rate'))})"
                         f" · 학습 데이터 정확도 {pct(g.get('train_accuracy'))}")
        else:
            lines.append(f"- {key}: 오작동 {g.get('misfires_per_min')}회/분 (규칙 {g.get('rules_misfires_per_min')}회/분)")
    lines += ["", "## 동작 전달 비교"] + [f"- {key}: {quality.get('text', '평가 없음')}" for key, quality in report.get("class_quality", {}).items() if key != "none"]
    lines += ["", "## 쓰는 법", "`example_use.py` 참고. 필요한 것: numpy, onnxruntime, MediaPipe Hand Landmarker(world landmarks 포함).",
              "새 모델은 앱에서 시험 모드로 시작합니다. 이 예시는 점수 계산만 제공하며 OS 동작이나 앱의 적용 설정을 내보내지 않습니다.",
              "선택 학습한 동작은 selected/ 모델과 gesture_tcn.selected.json을 함께 사용합니다. 폴더 전체를 복사하세요.",
              "동작마다 독립 판정하므로 점수의 합은 1이 아닐 수 있습니다. 판정 기준은 class_decisions를 사용하세요."]
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
