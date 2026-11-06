"""Landmark quality inspection ported from Dan b3c3025 src/data_quality.py.
Warnings describe measured geometry, not gesture correctness. Cleaning lives in studio.py."""
from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np

from manux.data.recording import read
from manux.data.capture_plan import label_condition_key

def read_session(path):
    recording = read(path)
    if not recording.complete or recording.bad_lines:
        raise ValueError("완료되지 않았거나 손상된 녹화입니다.")
    return {**recording.meta, "complete": True}, recording.frames, recording.events


@dataclass
class QualityIssue:
    """A single quality issue or problematic frame segment detected in a recording."""

    kind: str
    severity: str  # "info" | "warning" | "fail"
    start_frame: int
    end_frame: int
    start_t: float
    end_t: float
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_t": round(float(self.start_t), 3),
            "end_t": round(float(self.end_t), 3),
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass
class QualityReport:
    """Summary of recording data quality check."""

    path: str
    mode: str
    score: float
    status: str  # "pass" | "warning" | "fail"
    issues: list[QualityIssue] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def problem_segments(self) -> list[dict[str, Any]]:
        return self.segments

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mode": self.mode,
            "score": round(float(self.score), 4),
            "status": self.status,
            "summary": self.summary,
            "segments": list(self.segments),
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": dict(self.metrics),
        }


def _valid_points(raw: Any) -> np.ndarray | None:
    if raw is None:
        return None
    try:
        arr = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if arr.shape != (21, 3) or not np.isfinite(arr).all():
        return None
    return arr


def _palm_size(lm: np.ndarray) -> float:
    wrist = lm[0]
    middle_mcp = lm[9]
    index_mcp = lm[5]
    pinky_mcp = lm[17]
    length = float(np.linalg.norm(middle_mcp - wrist))
    width = float(np.linalg.norm(index_mcp - pinky_mcp))
    return max(length, width * 0.9)


def _hand_span(lm: np.ndarray) -> float:
    span_x = float(np.ptp(lm[:, 0]))
    span_y = float(np.ptp(lm[:, 1]))
    return max(span_x, span_y)


def _contiguous_runs(indices: list[int]) -> list[tuple[int, int]]:
    if not indices:
        return []
    runs: list[tuple[int, int]] = []
    start = prev = indices[0]
    for idx in indices[1:]:
        if idx == prev + 1:
            prev = idx
        else:
            runs.append((start, prev))
            start = prev = idx
    runs.append((start, prev))
    return runs


def check_recording(
    path: Path | str,
    *,
    min_quality_score: float = 0.7,
    min_do_frames: int = 4,
    max_joint_jump: float = 0.38,
    min_palm_size: float = 0.012,
    max_palm_size: float = 0.68,
) -> QualityReport:
    """Inspect a finished `.jsonl.gz` recording and return a structured `QualityReport`."""
    rec_path = Path(path)
    try:
        meta, frames, _ = read_session(rec_path)
    except Exception as exc:
        issue = QualityIssue(
            kind="unreadable",
            severity="fail",
            start_frame=0,
            end_frame=0,
            start_t=0.0,
            end_t=0.0,
            message=f"녹화 파일을 읽을 수 없습니다: {exc}",
        )
        return QualityReport(
            path=str(rec_path),
            mode="unknown",
            score=0.0,
            status="fail",
            issues=[issue],
            segments=[issue.to_dict()],
            summary=f"품질 검사 실패: 파일을 읽을 수 없음 ({rec_path.name})",
            metrics={"total_frames": 0, "valid_frames": 0, "missing_hand_ratio": 1.0},
        )

    mode = str(meta.get("mode") or ("guided" if "-guided" in rec_path.name else "free"))
    total_frames = len(frames)
    if total_frames == 0:
        issue = QualityIssue(
            kind="insufficient_data",
            severity="fail",
            start_frame=0,
            end_frame=0,
            start_t=0.0,
            end_t=0.0,
            message="녹화된 프레임이 없습니다.",
        )
        return QualityReport(
            path=str(rec_path),
            mode=mode,
            score=0.0,
            status="fail",
            issues=[issue],
            segments=[issue.to_dict()],
            summary="품질 검사 실패: 녹화된 프레임이 0개입니다.",
            metrics={"total_frames": 0, "valid_frames": 0, "missing_hand_ratio": 1.0},
        )

    issues: list[QualityIssue] = []
    ts = np.asarray([float(f.get("t", 0)) for f in frames])
    if not np.isfinite(ts).all():
        raise ValueError("프레임 시각이 유효하지 않습니다.")
    if len(ts) > 1 and (np.diff(ts) <= 0).any():
        issues.append(QualityIssue("timeline", "fail", 0, total_frames-1, float(ts[0]), float(ts[-1]),
                                   "프레임 시각이 단조 증가하지 않습니다."))
    lms: list[np.ndarray | None] = []
    worlds: list[np.ndarray | None] = []
    valid_mask = np.zeros(total_frames, dtype=bool)
    active_mask = np.zeros(total_frames, dtype=bool)
    transition_mask = np.zeros(total_frames, dtype=bool)

    class_counts: dict[str, int] = {}
    posture_counts: dict[str, int] = {}
    abnormal_size_indices: list[int] = []
    outlier_indices: list[int] = []

    for i, f in enumerate(frames):
        lm = _valid_points(f.get("lm"))
        world = _valid_points(f.get("world"))
        lms.append(lm)
        worlds.append(world)

        has_hand = lm is not None
        valid_mask[i] = has_hand

        lab = f.get("label") or {}
        phase = str(lab.get("phase") or ("free" if mode == "free" else ""))
        target = lab.get("target")
        label_name = str(lab.get("label") or "")
        eff_label = str(target if target is not None else label_name)

        if phase not in ("waiting", "paused"):
            active_mask[i] = True

        if (
            eff_label in ("idle", "transition", "")
            or phase in ("prep", "waiting", "paused")
        ):
            transition_mask[i] = True

        if has_hand:
            if eff_label and eff_label not in ("idle", "transition"):
                class_counts[eff_label] = class_counts.get(eff_label, 0) + 1

            posture = (
                lab.get("posture")
                if lab.get("posture") is not None
                else (lab.get("condition_key") or lab.get("condition") or lab.get("condition_index"))
            )
            if posture is not None:
                p_key = str(posture)
                posture_counts[p_key] = posture_counts.get(p_key, 0) + 1

            palm = _palm_size(lm)
            span = _hand_span(lm)
            if palm < min_palm_size or palm > max_palm_size or span < 0.02 or span > 0.98:
                abnormal_size_indices.append(i)

    # 1. Incomplete file check
    missing_world = [i for i, (lm, world) in enumerate(zip(lms, worlds)) if lm is not None and world is None]
    if missing_world:
        issues.append(QualityIssue("missing_world", "warning", missing_world[0], missing_world[-1],
                                   float(ts[missing_world[0]]), float(ts[missing_world[-1]]),
                                   "화면 관절은 있으나 학습에 필요한 실제 3D 관절이 없는 프레임이 있습니다."))
    if not meta.get("complete", False):
        issues.append(
            QualityIssue(
                kind="incomplete",
                severity="fail",
                start_frame=0,
                end_frame=total_frames - 1,
                start_t=float(frames[0].get("t", 0.0)),
                end_t=float(frames[-1].get("t", 0.0)),
                message="녹화가 정상 종료(end)되지 않은 미완성 파일입니다.",
            )
        )

    # 2. Missing hand frame ratio check
    valid_frames = int(valid_mask.sum())
    active_count = int(active_mask.sum())
    if active_count > 0:
        active_missing = int((active_mask & ~valid_mask).sum())
        missing_hand_ratio = active_missing / active_count
    else:
        missing_hand_ratio = (total_frames - valid_frames) / max(1, total_frames)

    missing_active_indices = [i for i in range(total_frames) if active_mask[i] and not valid_mask[i]]
    for s_idx, e_idx in _contiguous_runs(missing_active_indices):
        run_len = e_idx - s_idx + 1
        if run_len >= 3 or missing_hand_ratio >= 0.2:
            sev = "fail" if missing_hand_ratio > 0.65 else ("warning" if run_len >= 5 or missing_hand_ratio > 0.2 else "info")
            issues.append(
                QualityIssue(
                    kind="missing_hand",
                    severity=sev,
                    start_frame=s_idx,
                    end_frame=e_idx,
                    start_t=float(frames[s_idx].get("t", 0.0)),
                    end_t=float(frames[e_idx].get("t", 0.0)),
                    message=f"손 미검출 구간 ({run_len}프레임, #{s_idx}~#{e_idx})",
                    details={"frames": run_len},
                )
            )

    # 3. Empty or too-short 'do' stretches (guided mode)
    short_do_count = 0
    i = 0
    while i < total_frames:
        lab = frames[i].get("label") or {}
        if lab.get("phase") == "do":
            j = i
            while j + 1 < total_frames and (frames[j + 1].get("label") or {}).get("phase") == "do":
                j += 1
            stretch_len = j - i + 1
            stretch_valid = int(valid_mask[i : j + 1].sum())
            t0 = float(frames[i].get("t", 0.0))
            t1 = float(frames[j].get("t", 0.0))
            duration = max(0.0, t1 - t0)
            task_name = str(lab.get("task") or lab.get("target") or "do")
            if stretch_valid == 0:
                short_do_count += 1
                issues.append(
                    QualityIssue(
                        kind="short_do",
                        severity="warning",
                        start_frame=i,
                        end_frame=j,
                        start_t=t0,
                        end_t=t1,
                        message=f"비어 있는 실행(do) 구간: '{task_name}' (#{i}~#{j}, 유효 손 프레임 0개)",
                        details={"task": task_name, "valid_frames": 0, "total_frames": stretch_len, "duration": duration},
                    )
                )
            elif stretch_valid < min_do_frames or (total_frames >= 20 and duration < 0.15):
                short_do_count += 1
                issues.append(
                    QualityIssue(
                        kind="short_do",
                        severity="warning",
                        start_frame=i,
                        end_frame=j,
                        start_t=t0,
                        end_t=t1,
                        message=f"너무 짧은 실행(do) 구간: '{task_name}' (#{i}~#{j}, 유효 {stretch_valid}프레임, {duration:.2f}초)",
                        details={"task": task_name, "valid_frames": stretch_valid, "total_frames": stretch_len, "duration": duration},
                    )
                )
            i = j + 1
        else:
            i += 1

    # 4. Sudden joint jumps / coordinate outliers
    prev_idx: int | None = None
    for idx in range(total_frames):
        lm = lms[idx]
        if lm is None:
            continue
        if np.any(np.abs(lm[:, :2]) > 2.5) or np.any(np.abs(lm[:, 2]) > 3.0):
            outlier_indices.append(idx)
            prev_idx = idx
            continue
        if prev_idx is not None:
            dt = float(frames[idx].get("t", 0.0)) - float(frames[prev_idx].get("t", 0.0))
            prev_lm = lms[prev_idx]
            if prev_lm is not None and 0.0 < dt <= 0.25:
                diffs = np.linalg.norm(lm - prev_lm, axis=1)
                wrist_jump = float(diffs[0])
                max_jump = float(np.max(diffs))
                if wrist_jump > max_joint_jump or max_jump > max_joint_jump * 1.35:
                    outlier_indices.append(idx)
        prev_idx = idx

    outlier_runs = _contiguous_runs(sorted(set(outlier_indices)))
    for s_idx, e_idx in outlier_runs:
        issues.append(
            QualityIssue(
                kind="joint_jump",
                severity="warning" if (e_idx - s_idx + 1) >= 2 or len(outlier_indices) / max(1, valid_frames) > 0.05 else "info",
                start_frame=s_idx,
                end_frame=e_idx,
                start_t=float(frames[s_idx].get("t", 0.0)),
                end_t=float(frames[e_idx].get("t", 0.0)),
                message=f"급격한 관절 점프/이상치 감지 (#{s_idx}~#{e_idx})",
                details={"frames": e_idx - s_idx + 1},
            )
        )

    # 5. Abnormal hand size runs
    for s_idx, e_idx in _contiguous_runs(abnormal_size_indices):
        run_len = e_idx - s_idx + 1
        issues.append(
            QualityIssue(
                kind="abnormal_size",
                severity="warning" if run_len >= 3 or len(abnormal_size_indices) / max(1, valid_frames) > 0.1 else "info",
                start_frame=s_idx,
                end_frame=e_idx,
                start_t=float(frames[s_idx].get("t", 0.0)),
                end_t=float(frames[e_idx].get("t", 0.0)),
                message=f"비정상적인 손 크기 감지 (#{s_idx}~#{e_idx}, 너무 작거나 큼)",
                details={"frames": run_len},
            )
        )

    # 6. Insufficient data per class or posture
    if valid_frames < 5:
        issues.append(
            QualityIssue(
                kind="insufficient_data",
                severity="fail" if valid_frames == 0 else "warning",
                start_frame=0,
                end_frame=total_frames - 1,
                start_t=float(frames[0].get("t", 0.0)),
                end_t=float(frames[-1].get("t", 0.0)),
                message=f"유효한 손 프레임 수가 너무 적습니다 ({valid_frames}프레임).",
                details={"valid_frames": valid_frames},
            )
        )
    elif mode == "guided":
        sparse_classes = [cls for cls, cnt in class_counts.items() if cls != "none" and cnt < min_do_frames]
        if not class_counts:
            issues.append(
                QualityIssue(
                    kind="insufficient_data",
                    severity="fail",
                    start_frame=0,
                    end_frame=total_frames - 1,
                    start_t=float(frames[0].get("t", 0.0)),
                    end_t=float(frames[-1].get("t", 0.0)),
                    message="라벨링된 유효 제스처 데이터가 없습니다.",
                    details={"class_counts": class_counts},
                )
            )
        elif sparse_classes:
            issues.append(
                QualityIssue(
                    kind="insufficient_data",
                    severity="warning",
                    start_frame=0,
                    end_frame=total_frames - 1,
                    start_t=float(frames[0].get("t", 0.0)),
                    end_t=float(frames[-1].get("t", 0.0)),
                    message=f"클래스별 데이터 부족: {', '.join(sparse_classes)}",
                    details={"sparse_classes": sparse_classes, "class_counts": class_counts},
                )
            )

    # 7. Excessive transition / idle ratio
    transition_idle_ratio = float(transition_mask.sum()) / max(1, total_frames)
    if mode == "guided" and total_frames >= 10 and transition_idle_ratio > 0.85:
        issues.append(
            QualityIssue(
                kind="excessive_transition",
                severity="warning" if transition_idle_ratio <= 0.95 else "fail",
                start_frame=0,
                end_frame=total_frames - 1,
                start_t=float(frames[0].get("t", 0.0)),
                end_t=float(frames[-1].get("t", 0.0)),
                message=f"전환(transition)/대기(idle) 비율 과다 ({transition_idle_ratio * 100:.0f}%)",
                details={"transition_idle_ratio": round(transition_idle_ratio, 4)},
            )
        )

    # Compute composite score in [0.0, 1.0]
    outlier_ratio = len(set(outlier_indices)) / max(1, valid_frames)
    abnormal_size_ratio = len(abnormal_size_indices) / max(1, valid_frames)

    penalty = 0.0
    penalty += min(0.65, missing_hand_ratio * 0.85)
    penalty += min(0.30, short_do_count * 0.12)
    penalty += min(0.35, outlier_ratio * 1.2)
    penalty += min(0.25, abnormal_size_ratio * 0.9)
    if mode == "guided" and total_frames >= 10 and transition_idle_ratio > 0.85:
        penalty += min(0.30, (transition_idle_ratio - 0.85) * 2.0)
    if valid_frames == 0 or not meta.get("complete", False):
        penalty = 1.0
    elif valid_frames < 5:
        penalty += 0.25

    score = float(np.clip(1.0 - penalty, 0.0, 1.0))

    has_fail_issue = any(iss.severity == "fail" for iss in issues)
    has_warn_issue = any(iss.severity == "warning" for iss in issues)

    if has_fail_issue or score < max(0.4, min_quality_score - 0.25):
        status = "fail"
    elif score < min_quality_score or has_warn_issue:
        status = "warning"
    else:
        status = "pass"

    segments = [iss.to_dict() for iss in issues]
    metrics = {
        "total_frames": total_frames,
        "valid_frames": valid_frames,
        "missing_hand_ratio": round(float(missing_hand_ratio), 4),
        "short_do_count": short_do_count,
        "outlier_count": len(set(outlier_indices)),
        "abnormal_size_count": len(abnormal_size_indices),
        "transition_idle_ratio": round(float(transition_idle_ratio), 4),
        "class_counts": class_counts,
        "posture_counts": posture_counts,
    }

    if status == "pass":
        summary = f"품질 양호 (점수 {score:.2f}, 유효 {valid_frames}/{total_frames}프레임)"
    elif status == "warning":
        reasons = ", ".join(dict.fromkeys(iss.message for iss in issues[:2])) or "일부 구간 보정 필요"
        summary = f"품질 주의 (점수 {score:.2f}): {reasons}"
    else:
        reasons = ", ".join(dict.fromkeys(iss.message for iss in issues[:2])) or "유효 데이터 부족"
        summary = f"품질 미달 (점수 {score:.2f}): {reasons}"

    return QualityReport(
        path=str(rec_path),
        mode=mode,
        score=score,
        status=status,
        issues=issues,
        segments=segments,
        summary=summary,
        metrics=metrics,
    )
