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
            if data.get("summary_version") == 3 and data.get("size") == stat.st_size and data.get("mtime") == int(stat.st_mtime):
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
    for i, f in enumerate(frames):
        lab = f.get("label") or {}
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
    data = {
        "summary_version": 3,
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


def model_report(model: Path = MODEL) -> Optional[Dict[str, Any]]:
    info_path = Path(model).with_suffix(".json")
    if not Path(model).exists() or not info_path.exists():
        return None
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["mtime"] = Path(model).stat().st_mtime
    return info


# ---------------------------------------------------------------- what to do next
def gesture_rows(summary: Dict[str, Any], report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per gesture: data, scores and advice, for the screen."""
    rows = []
    pg = (report or {}).get("per_gesture", {})
    stale = report is not None and summary.get("newest", 0) > report.get("mtime", 0)
    for key, name in NAMES.items():
        task = BY_KEY[key]
        d = summary["per_gesture"].get(key, {"reps": 0, "postures": {}, "minutes": 0.0})
        m = (report or {}).get("per_task", {}).get(key, pg.get(key, {}) if key == task.label else {})
        classes = (report or {}).get("classes", list(pg))
        row = {"key": key, "name": name, "reps": d["reps"], "postures": len(d["postures"]),
               "category": task.category, "instruction": task.do,
               "model_text": "TCN 학습됨" if task.label in classes else "TCN 학습 전 · 현재 규칙으로 동작",
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
        elif task.label not in classes:
            advice.append("이 동작을 녹화하고 다시 학습하면 모델에 추가됩니다")
        if advice:
            level = "need" if any(("부족" in a or "낮아요" in a or "많아요" in a) for a in advice) else "retrain"
        row["advice"] = " · ".join(advice) if advice else "좋아요"
        row["level"] = level
        rows.append(row)
    return rows


# ---------------------------------------------------------------- training in the background
class TrainingJob:
    """Runs scripts/train_tcn.py --folds --progress in its own process (torch stays out of the app) and keeps its state."""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.state: Dict[str, Any] = {"running": False}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self.state.get("running"))

    def start(self, epochs: int = 25) -> bool:
        if self.running:
            return False
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        args = [sys.executable, str(ROOT / "scripts" / "train_tcn.py"), "--folds", "--progress", "--epochs", str(epochs)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", env=env, creationflags=flags)
        with self._lock:
            self.state = {"running": True, "stage": "데이터 읽는 중", "fold": 0, "folds": 0, "epoch": 0, "epochs": epochs,
                          "progress": 0.0, "started": time.time(), "log": []}
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
            if kind == "loaded":
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

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def stop(self) -> None:
        if self.proc is not None and self.running:
            self.proc.terminate()


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
import onnxruntime as ort

from tcn_features import frame_features, windows


class GestureModel:
    def __init__(self, folder="."):
        self.info = json.load(open(f"{{folder}}/gesture_tcn.json", encoding="utf-8"))
        self.sess = ort.InferenceSession(f"{{folder}}/gesture_tcn.onnx", providers=["CPUExecutionProvider"])
        self.frames = deque(maxlen=self.info["window"] + 1)

    def step(self, t, world, image, aspect):
        """t: seconds; world / image: 21 x (x, y, z) or None when no hand. Returns {{class: probability}}."""
        self.frames.append({{"t": t, "world": world, "lm": image}} if world else {{"t": t}})
        x = windows(frame_features(list(self.frames), aspect), self.info["window"])[-1:]
        logits = self.sess.run(None, {{"x": x.astype(np.float32)}})[0][0]
        p = np.exp(logits - logits.max())
        p /= p.sum()
        return dict(zip(self.info["classes"], p.tolist()))

# A gesture counts when its probability stays >= info["threshold"] for info["frames_in_a_row"] frames in a row.
'''


def export_package(out_root: Path = EXPORT_DIR, with_dataset: bool = True) -> Path:
    """exports/gesture-tcn-<time>/ (+ .zip): model, its info, the feature code, an example, a README, and the
    per-frame features + labels of every recording (dataset.npz) for training elsewhere."""
    report = model_report()
    if report is None:
        raise FileNotFoundError("학습한 모델이 없어요. 먼저 학습하세요.")
    stamp = time.strftime("%Y%m%d-%H%M")
    out = Path(out_root) / f"gesture-tcn-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODEL, out / "gesture_tcn.onnx")
    shutil.copy2(MODEL.with_suffix(".json"), out / "gesture_tcn.json")
    shutil.copy2(ROOT / "src" / "tcn_features.py", out / "tcn_features.py")
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
