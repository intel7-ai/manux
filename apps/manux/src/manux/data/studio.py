"""Curated recordings and derived data for the v0.4 studio. No Qt, ML or OS output.

Dan b3c3025 inspired inspection/cleaning/augmentation; raw measurements are never invented.
All file identifiers are relative to one recording root and selections bind to content hashes.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
from uuid import uuid4

import numpy as np

from manux.data.recording import read
from manux.data.quality import check_recording, _valid_points


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def capture_hashes(path, recording=None):
    """A derived file and its source capture are the same independent trial."""
    recording = recording or read(path)
    lineage = recording.meta.get("lineage")
    result = {digest(path)}
    if lineage is not None:
        if not isinstance(lineage, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(lineage.get("capture_sha256", ""))):
            raise ValueError("녹화의 원본 계보가 잘못되었습니다.")
        result.add(lineage["capture_sha256"])
    return result


def recording_path(root, key):
    root = Path(root).resolve()
    if not isinstance(key, str) or not key or "\\" in key:
        raise ValueError("녹화 항목을 선택하세요.")
    path = root / key
    if PureWindowsPath(key).drive or Path(key).is_absolute() or ".." in Path(key).parts or not path.resolve().is_relative_to(root):
        raise ValueError("녹화 폴더 밖의 파일은 사용할 수 없습니다.")
    if any(p.startswith(".") for p in Path(key).parts) or not key.endswith(".jsonl.gz"):
        raise ValueError("완료된 녹화만 사용할 수 있습니다.")
    return path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
    try:
        with part.open("x", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def selections(root):
    try:
        value = json.loads((Path(root) / ".studio-selection.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def included(root, path):
    key = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    row = selections(root).get(key, {})
    return row.get("included", True) if row.get("sha256") == digest(path) else True


def select_recording(root, key, value):
    path = recording_path(root, key)
    rec = read(path)
    if rec.meta.get("split") != "train" or not rec.complete or rec.bad_lines:
        raise ValueError("학습용 완료 녹화만 포함·제외할 수 있습니다. 평가는 모든 별도 시도를 사용합니다.")
    state = selections(root)
    state[key] = {"sha256": digest(path), "included": bool(value)}
    atomic_json(Path(root) / ".studio-selection.json", state)


def trash_recording(root, key):
    path = recording_path(root, key)
    folder = Path(root) / ".trash" / uuid4().hex
    if not folder.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("휴지통 경로가 녹화 폴더 밖입니다.")
    folder.mkdir(parents=True)
    atomic_json(folder / "restore.json", {"key": key, "sha256": digest(path)})
    os.replace(path, folder / path.name)
    return "녹화를 휴지통으로 옮겼습니다. 원본은 recordings/.trash에 보존됩니다."


def inspect(root, key):
    path = recording_path(root, key)
    checksum = digest(path)
    rec = read(path)
    quality = check_recording(path).to_dict()
    quality["path"] = key
    if checksum != digest(path):
        raise ValueError("검사 중 녹화가 바뀌었습니다. 새로고침하세요.")
    times = [float(f.get("t", 0)) for f in rec.frames]
    width, height, known = rec.frame_size()
    return {"key": key, "name": path.name, "sha256": checksum,
            "frame_size": [width, height], "frame_aspect": height / width, "frame_size_known": known,
            "profile": str(rec.meta.get("person") or rec.meta.get("profile") or "미분류"),
            "split": rec.meta.get("split", "unclassified"), "preset": rec.meta.get("preset", "pinch"),
            "frames": len(rec.frames), "seconds": max(0., times[-1] - times[0]) if times else 0.,
            "included": included(root, path), "derived": (rec.meta.get("lineage") or {}).get("operation", "original"),
            "complete": rec.complete and not rec.bad_lines,
            "quality": quality, "targets": sorted({str((f.get("label") or {}).get("task") or (f.get("label") or {}).get("target") or "none") for f in rec.frames})}


def library(root, cancelled=lambda: False, progress=lambda *args: None):
    paths = [p for p in sorted(Path(root).rglob("*.jsonl.gz"))
             if not any(part.startswith(".") for part in p.relative_to(root).parts)]
    rows = []
    for i, path in enumerate(paths):
        if cancelled():
            raise ValueError("작업을 취소했습니다.")
        key = path.relative_to(root).as_posix()
        try:
            rows.append(inspect(root, key))
        except (OSError, ValueError, TypeError, IndexError):
            rows.append({"key": key, "name": path.name, "split": "unknown", "complete": False,
                         "included": False, "frames": 0, "seconds": 0, "profile": "", "derived": "original",
                         "targets": [], "quality": {"status": "fail", "summary": "파일 형식·완료 여부를 확인하세요.", "issues": []}})
        progress(i + 1, len(paths))
    return rows


def _segment(frame):
    lab = frame.get("label") or {}
    return tuple(lab.get(k) for k in ("task", "target", "phase", "set", "rep", "condition_key", "condition_index"))


def _write_derived(root, source, rec, frames, operation, params, checksum):
    if rec.meta.get("split") != "train" or rec.meta.get("lineage"):
        raise ValueError("보정·증강은 학습용 원본에만 적용하세요. 평가용과 파생본은 사용할 수 없습니다.")
    if checksum != digest(source):
        raise ValueError("처리 중 원본이 바뀌었습니다.")
    if not frames:
        raise ValueError("보존할 유효한 프레임이 없습니다.")
    meta = deepcopy(rec.meta)
    meta["lineage"] = {"capture_sha256": checksum, "operation": operation, "parameters": params}
    # Derived records are deliberately local; they must never masquerade as fresh captures on the hub.
    out = source.parent / "derived" / f"{source.name[:-9]}-{operation}-{uuid4().hex[:8]}.jsonl.gz"
    if not out.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("파생본 경로가 녹화 폴더 밖입니다.")
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    try:
        with gzip.open(part, "wt", encoding="utf-8") as file:
            for row in [meta, *frames, {"type": "end", "frames": len(frames), "counts": dict(Counter((f.get("label") or {}).get("target", "none") for f in frames)), "marks": 0}]:
                file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        if checksum != digest(source):
            raise ValueError("처리 중 원본이 바뀌었습니다.")
        os.replace(part, out)
        # No surprise change to the next training: explicitly include the new derived file in the UI.
        select_recording(root, out.relative_to(root).as_posix(), False)
    finally:
        part.unlink(missing_ok=True)
    return out.relative_to(root).as_posix()


def derive(root, key, operation, *, seed=None):
    """Clean short same-trial dropouts/spikes, or augment valid measured 3D only."""
    path = recording_path(root, key)
    checksum = digest(path)
    rec = read(path)
    if not rec.complete or rec.bad_lines:
        raise ValueError("완료된 녹화만 처리할 수 있습니다.")
    frames = deepcopy(rec.frames)
    timestamps = np.asarray([float(f.get("t", 0)) for f in frames])
    if not np.isfinite(timestamps).all() or (len(timestamps)>1 and (np.diff(timestamps)<=0).any()):
        raise ValueError("시각이 올바른 녹화만 변환할 수 있습니다.")
    if operation == "clean":
        report = check_recording(path)
        bad = set()
        for issue in report.issues:
            if issue.kind in ("joint_jump", "abnormal_size", "hand_size", "size_spike"):
                bad.update(range(issue.start_frame, issue.end_frame + 1))
        # Interpolate at most two frames within one label/repetition; never manufacture world from 2D.
        valid = lambda f: _valid_points(f.get("lm")) is not None and _valid_points(f.get("world")) is not None
        original = deepcopy(frames)
        for i, frame in enumerate(frames):
            if valid(original[i]) and i not in bad:
                continue
            frame.pop("lm", None)
            frame.pop("world", None)
            frame.pop("hand", None)
            frame["excluded_geometry"] = True
            left, right = i - 1, i + 1
            while left >= 0 and i - left <= 3 and (not valid(original[left]) or left in bad): left -= 1
            while right < len(frames) and right - i <= 3 and (not valid(original[right]) or right in bad): right += 1
            if (left < 0 or right >= len(frames) or right - left > 3
                    or not valid(original[left]) or not valid(original[right])
                    or _segment(original[left]) != _segment(frame) or _segment(frame) != _segment(original[right])):
                continue
            t0, t1 = float(original[left]["t"]), float(original[right]["t"])
            if not 0 < t1 - t0 <= .25:
                continue
            alpha = (float(frame["t"]) - t0) / (t1 - t0)
            if not 0 < alpha < 1:
                continue
            for field in ("lm", "world"):
                frame[field] = ((1 - alpha) * np.asarray(original[left][field]) + alpha * np.asarray(original[right][field])).round(6).tolist()
            frame["hand"] = deepcopy(original[left].get("hand"))
            frame["interpolated"] = True
            frame.pop("excluded_geometry", None)
        # Leave unrepaired frames missing: runtime resets still see gaps and no labels are rewritten.
        return _write_derived(root, path, rec, frames, operation, {"max_gap_frames": 2}, checksum)
    if operation != "augment":
        raise ValueError("알 수 없는 변환입니다.")
    rng = np.random.default_rng(seed)
    angle = math.radians(float(rng.uniform(-15, 15)))
    rotation = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
    scale, speed = float(rng.uniform(.9, 1.1)), float(rng.uniform(.9, 1.1))
    first = float(frames[0].get("t", 0)) if frames else 0
    for frame in frames:
        frame["t"] = first + (float(frame.get("t", first)) - first) / speed
        for field in ("lm", "world"):
            points = _valid_points(frame.get(field))
            if points is None:
                continue
            # In-plane rotation keeps trajectory directions; world remains based on measured world.
            transformed = points[0] + (points - points[0]) @ rotation.T * scale
            jitter = rng.normal(0, .001 if field == "world" else .002, points.shape)
            jitter[0] = 0
            frame[field] = (transformed + jitter).round(6).tolist()
    return _write_derived(root, path, rec, frames, operation, {"rotation_radians": angle, "scale": scale, "speed": speed}, checksum)


def training_paths(root, paths):
    """Honor curation; selected clean replaces original, augments supplement it."""
    selected = [p for p in paths if included(root, p)]
    clean = {}
    for p in selected:
        lineage = read(p).meta.get("lineage") or {}
        if lineage.get("operation") == "clean":
            capture = lineage["capture_sha256"]
            if capture in clean:
                raise ValueError("같은 원본의 보정본은 하나만 선택하세요.")
            clean[capture] = p
    return [p for p in selected if digest(p) not in clean]


def playback(root, key):
    rec = read(recording_path(root, key))
    if not rec.complete or rec.bad_lines:
        raise ValueError("완료된 녹화만 재생할 수 있습니다.")
    timestamps = np.asarray([float(f.get("t", 0)) for f in rec.frames])
    if not np.isfinite(timestamps).all() or (len(timestamps)>1 and (np.diff(timestamps)<=0).any()):
        raise ValueError("시각이 단조 증가하지 않아 재생할 수 없습니다.")
    frames, last_pose, pose_t = [], [], -1e9
    for f in rec.frames:
        lm = _valid_points(f.get("lm"))
        pose = f.get("pose")
        try:
            pose = pose if np.asarray(pose, dtype=float).shape in ((6, 3), (6, 4)) and np.isfinite(pose).all() else []
        except (TypeError, ValueError):
            pose = []
        t = float(f.get("t", 0))
        if pose: last_pose, pose_t = pose, t
        elif 0 <= t - pose_t <= .3: pose = last_pose
        center = lm[[0, 5, 9, 13, 17], :2].mean(0) if lm is not None else None
        zone = min(2, max(0, int(center[0] * 3))) + 3 * min(2, max(0, int(center[1] * 3))) if center is not None else -1
        frames.append({"t": t, "lm": lm.tolist() if lm is not None else [], "pose": pose, "pose_t": pose_t if pose else None,
                       "zone": zone, "label": f.get("label") or {}, "rule": f.get("rule") or {}, "interpolated": bool(f.get("interpolated"))})
    info = inspect(root, key)
    info["hand_frames"] = sum(bool(f["lm"]) for f in frames)
    events = [{"t": float(e.get("t", 0)), "text": str(e.get("kind", "event"))} for e in rec.events]
    events.extend({"t": i["start_t"], "text": i["message"]} for i in info["quality"]["issues"])
    return {"frames": frames, "events": sorted(events, key=lambda e: e["t"]), "recording": info}
