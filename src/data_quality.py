"""Post-recording data quality inspection and automated cleaning for hand-landmark sessions."""
from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np

from src.recorder import read_session
from src.recording_plan import label_condition_key


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


def _recompute_counts(frames: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in frames:
        if _valid_points(f.get("lm")) is None:
            counts["no_hand"] = counts.get("no_hand", 0) + 1
            continue
        lab = f.get("label")
        if isinstance(lab, dict):
            name = lab.get("target") if lab.get("target") is not None else lab.get("label")
        else:
            name = lab
        key = str(name or "unlabelled")
        counts[key] = counts.get(key, 0) + 1
    return counts


def clean_recording(
    path: Path | str,
    report: QualityReport | None = None,
    *,
    output_dir: Path | str | None = None,
    min_do_frames: int = 4,
    max_interp_gap: int = 3,
    max_joint_jump: float = 0.38,
    min_palm_size: float = 0.012,
    max_palm_size: float = 0.68,
) -> Path:
    """Clean a recording and write it to a separate path without modifying the original file.

    Cleaning steps:
    1. Synthesize local 3D ``world`` coordinates from ``lm`` if ``lm`` is valid but ``world`` is missing.
    2. Linearly interpolate 1~2 frame missing-hand dropouts inside the same segment.
    3. Smooth isolated single-frame joint jumps / abnormal-size spikes using neighbor averaging,
       or exclude unrecoverable outlier frames.
    4. Mask/remove empty or too-short ``do`` stretches so abortive gestures do not corrupt training labels.
    5. Remove remaining missing-hand frames while preserving original file intact.
    """
    src_path = Path(path)
    if report is None:
        report = check_recording(
            src_path,
            min_do_frames=min_do_frames,
            max_joint_jump=max_joint_jump,
            min_palm_size=min_palm_size,
            max_palm_size=max_palm_size,
        )

    meta, raw_frames, events = read_session(src_path)

    if output_dir is None:
        dest_dir = src_path.parent / "cleaned"
    else:
        dest_dir = Path(output_dir)
        if dest_dir.resolve() == src_path.parent.resolve():
            dest_dir = src_path.parent / "cleaned"
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / src_path.name
    if out_path.resolve() == src_path.resolve():
        out_path = dest_dir / f"{src_path.stem}.cleaned.gz"

    frames = [json.loads(json.dumps(f)) for f in raw_frames]
    n = len(frames)

    # Step 1: Ensure valid world coordinates when lm is valid
    lms: list[np.ndarray | None] = []
    worlds: list[np.ndarray | None] = []
    for f in frames:
        lm = _valid_points(f.get("lm"))
        world = _valid_points(f.get("world"))
        if lm is not None and world is None:
            world = lm - lm[0:1, :]
            f["world"] = np.round(world, 5).tolist()
        lms.append(lm)
        worlds.append(world)

    def _seg_key(idx: int) -> tuple[Any, Any, Any]:
        lab = frames[idx].get("label") or {}
        return (lab.get("phase"), lab.get("target"), lab.get("set"))

    # Step 2: Interpolate short (1..max_interp_gap) missing-hand dropouts within the same segment
    idx = 0
    while idx < n:
        if lms[idx] is None:
            end_idx = idx
            while end_idx + 1 < n and lms[end_idx + 1] is None:
                end_idx += 1
            gap_len = end_idx - idx + 1
            left = idx - 1
            right = end_idx + 1
            if (
                gap_len <= max_interp_gap
                and left >= 0
                and right < n
                and lms[left] is not None
                and lms[right] is not None
                and _seg_key(left) == _seg_key(right)
            ):
                dt_total = float(frames[right].get("t", 0.0)) - float(frames[left].get("t", 0.0))
                if 0.0 < dt_total <= 0.25 * (gap_len + 1):
                    for k in range(idx, end_idx + 1):
                        alpha = (k - left) / float(right - left)
                        interp_lm = (1.0 - alpha) * lms[left] + alpha * lms[right]
                        lms[k] = interp_lm
                        frames[k]["lm"] = np.round(interp_lm, 5).tolist()
                        if worlds[left] is not None and worlds[right] is not None:
                            interp_w = (1.0 - alpha) * worlds[left] + alpha * worlds[right]
                            worlds[k] = interp_w
                            frames[k]["world"] = np.round(interp_w, 5).tolist()
                        else:
                            interp_w = interp_lm - interp_lm[0:1, :]
                            worlds[k] = interp_w
                            frames[k]["world"] = np.round(interp_w, 5).tolist()
                        if isinstance(frames[left].get("label"), dict):
                            frames[k]["label"] = dict(frames[left]["label"])
            idx = end_idx + 1
        else:
            idx += 1

    # Step 3: Detect outliers (sudden joint jumps & abnormal hand sizes) and smooth or mark for removal
    drop_mask = np.zeros(n, dtype=bool)
    outlier_mask = np.zeros(n, dtype=bool)
    prev_valid: int | None = None
    for i in range(n):
        lm = lms[i]
        if lm is None:
            continue
        palm = _palm_size(lm)
        span = _hand_span(lm)
        is_bad_size = palm < min_palm_size or palm > max_palm_size or span < 0.02 or span > 0.98
        is_jump = False
        if np.any(np.abs(lm[:, :2]) > 2.5) or np.any(np.abs(lm[:, 2]) > 3.0):
            is_jump = True
        elif prev_valid is not None:
            dt = float(frames[i].get("t", 0.0)) - float(frames[prev_valid].get("t", 0.0))
            if 0.0 < dt <= 0.25 and lms[prev_valid] is not None:
                diffs = np.linalg.norm(lm - lms[prev_valid], axis=1)
                if float(diffs[0]) > max_joint_jump or float(np.max(diffs)) > max_joint_jump * 1.35:
                    is_jump = True
        if is_bad_size or is_jump:
            outlier_mask[i] = True
        else:
            prev_valid = i

    for i in range(n):
        if not outlier_mask[i]:
            continue
        # Smooth isolated 1-frame spike if both immediate neighbors are clean and in the same segment
        if (
            0 < i < n - 1
            and not outlier_mask[i - 1]
            and not outlier_mask[i + 1]
            and lms[i - 1] is not None
            and lms[i + 1] is not None
            and _seg_key(i - 1) == _seg_key(i) == _seg_key(i + 1)
        ):
            smoothed_lm = 0.5 * (lms[i - 1] + lms[i + 1])
            lms[i] = smoothed_lm
            frames[i]["lm"] = np.round(smoothed_lm, 5).tolist()
            if worlds[i - 1] is not None and worlds[i + 1] is not None:
                smoothed_w = 0.5 * (worlds[i - 1] + worlds[i + 1])
                worlds[i] = smoothed_w
                frames[i]["world"] = np.round(smoothed_w, 5).tolist()
            outlier_mask[i] = False
        else:
            drop_mask[i] = True

    # Step 4: Filter out missing-hand frames and unsmoothed outlier frames first
    cleaned_frames = [
        frames[i]
        for i in range(n)
        if lms[i] is not None and not drop_mask[i]
    ]
    # Fallback: if every single frame lacked a hand (e.g. synthetic empty test), keep non-dropped frames
    if not cleaned_frames and frames:
        cleaned_frames = [frames[i] for i in range(n) if not drop_mask[i]] or list(frames)

    # Step 4.5: 5-finger anatomical occlusion & bidirectional pinch-continuity calibration
    from src.finger_kinematics import reconstruct_hand_sequence
    cleaned_frames, num_occlusion_calibrated = reconstruct_hand_sequence(cleaned_frames, in_place=True)

    # Step 5: Mask too-short disconnected 'do' stretches on the surviving frames
    m = len(cleaned_frames)
    i = 0
    while i < m:
        lab = cleaned_frames[i].get("label") or {}
        if lab.get("phase") == "do":
            j = i
            while (
                j + 1 < m
                and (cleaned_frames[j + 1].get("label") or {}).get("phase") == "do"
                and 0.0 <= float(cleaned_frames[j + 1].get("t", 0.0)) - float(cleaned_frames[j].get("t", 0.0)) <= 0.25
            ):
                j += 1
            valid_in_do = sum(1 for k in range(i, j + 1) if _valid_points(cleaned_frames[k].get("lm")) is not None)
            if valid_in_do < min_do_frames:
                for k in range(i, j + 1):
                    lbl = dict(cleaned_frames[k].get("label") or {})
                    lbl["phase"] = "prep"
                    lbl["target"] = "idle"
                    lbl["label"] = "transition"
                    cleaned_frames[k]["label"] = lbl
            i = j + 1
        else:
            i += 1

    cleaned_meta = dict(meta)
    cleaned_meta["type"] = "meta"
    cleaned_meta.pop("complete", None)
    cleaned_meta.pop("end", None)
    cleaned_meta["cleaned"] = True
    cleaned_meta["cleaned_from"] = src_path.name
    cleaned_meta["occlusion_calibrated"] = True
    cleaned_meta["occlusion_calibrated_frames"] = int(num_occlusion_calibrated)
    cleaned_meta["quality_score"] = round(float(report.score), 4)
    cleaned_meta["quality_status"] = report.status

    last_t = float(cleaned_frames[-1].get("t", 0.0)) if cleaned_frames else 0.0
    end_record = {
        "type": "end",
        "t": round(last_t, 4),
        "frames": len(cleaned_frames),
        "counts": _recompute_counts(cleaned_frames),
        "marks": len(events),
    }

    part_path = out_path.with_name(out_path.name + ".part")
    with gzip.open(part_path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(cleaned_meta, ensure_ascii=False, separators=(",", ":")) + "\n")
        for f in cleaned_frames:
            fh.write(json.dumps(f, ensure_ascii=False, separators=(",", ":")) + "\n")
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        fh.write(json.dumps(end_record, ensure_ascii=False, separators=(",", ":")) + "\n")
    part_path.replace(out_path)
    return out_path


@dataclass
class FrameQualityScore:
    """Per-frame input quality score in [0.0, 1.0] for runtime gating and training weighting."""

    score: float
    reason: str
    joint_jump: float = 0.0
    scale_ratio: float = 1.0
    confidence: float = 1.0
    edge_margin: float = 0.5
    near_tracking_loss: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score), 4),
            "reason": self.reason,
            "joint_jump": round(float(self.joint_jump), 4),
            "scale_ratio": round(float(self.scale_ratio), 4),
            "confidence": round(float(self.confidence), 4),
            "edge_margin": round(float(self.edge_margin), 4),
            "near_tracking_loss": bool(self.near_tracking_loss),
        }


def compute_frame_quality_score(
    landmarks: Any,
    prev_landmarks: Any = None,
    *,
    dt: float = 0.033,
    confidence: float = 1.0,
    near_tracking_loss: bool = False,
) -> FrameQualityScore:
    """Compute per-frame input quality score in `[0.0, 1.0]` from 4 diagnostic signals:
    1. `joint_jump`: maximum 2D landmark displacement since the previous valid frame
    2. `scale_ratio`: sudden relative hand-size change (`max(palm_t/palm_{t-1}, palm_{t-1}/palm_t)`)
    3. `confidence`: detector/tracker hand confidence score
    4. `edge_margin`: minimum distance of any landmark to the `[0, 1]` screen boundary
    """
    pts = _valid_points(landmarks)
    if pts is None:
        return FrameQualityScore(
            score=0.0,
            reason="missing_hand",
            confidence=0.0,
            edge_margin=0.0,
            near_tracking_loss=True,
        )

    conf = float(np.clip(confidence if confidence is not None else 1.0, 0.0, 1.0))
    palm = _palm_size(pts)
    span = _hand_span(pts)
    xs, ys = pts[:, 0], pts[:, 1]
    edge_margin = float(min(np.min(xs), np.min(ys), 1.0 - np.max(xs), 1.0 - np.max(ys)))

    joint_jump = 0.0
    scale_ratio = 1.0
    prev_pts = _valid_points(prev_landmarks)
    if prev_pts is not None and 0.0 < dt <= 0.25:
        diffs_2d = np.linalg.norm(pts[:, :2] - prev_pts[:, :2], axis=1)
        joint_jump = float(np.max(diffs_2d))
        prev_palm = max(_palm_size(prev_pts), 1e-4)
        cur_palm = max(palm, 1e-4)
        scale_ratio = float(max(cur_palm / prev_palm, prev_palm / cur_palm))
    elif prev_pts is not None and dt > 0.25:
        near_tracking_loss = True

    penalty = 0.0
    reasons: list[tuple[float, str]] = []

    # 1. Joint jump penalty (normal med=0.0055, pre-loss p90=0.053, post-reentry med=0.078)
    if joint_jump > 0.048:
        p_jump = min(0.70, ((joint_jump - 0.048) / 0.095) * 0.65)
        if joint_jump > 0.135:
            p_jump = max(p_jump, 0.62)
        penalty += p_jump
        reasons.append((p_jump, "joint_jump"))

    # 2. Hand size sudden change or abnormal palm size penalty (normal med=1.009, pre-loss p90=1.159)
    p_scale = 0.0
    if scale_ratio > 1.14:
        p_scale = min(0.65, ((scale_ratio - 1.14) / 0.30) * 0.60)
        if scale_ratio > 1.38:
            p_scale = max(p_scale, 0.58)
    if palm < 0.014 or palm > 0.68 or span < 0.02 or span > 0.98:
        p_scale = max(p_scale, 0.60)
    if p_scale > 0.0:
        penalty += p_scale
        reasons.append((p_scale, "scale_spike"))

    # 3. Tracker confidence penalty (pre-loss p10=0.865)
    if conf < 0.85:
        p_conf = min(0.65, ((0.85 - conf) / 0.35) * 0.60)
        if conf < 0.55:
            p_conf = max(p_conf, 0.60)
        penalty += p_conf
        reasons.append((p_conf, "low_confidence"))

    # 4. Screen edge proximity penalty
    if edge_margin < 0.020:
        p_edge = min(0.45, ((0.020 - edge_margin) / 0.030) * 0.35)
        penalty += p_edge
        reasons.append((p_edge, "edge_proximity"))

    # 5. Pre/post tracking loss proximity penalty
    if near_tracking_loss:
        p_loss = 0.28 if (joint_jump > 0.030 or scale_ratio > 1.08 or conf < 0.90) else 0.15
        penalty += p_loss
        reasons.append((p_loss, "near_tracking_loss"))

    score = float(np.clip(1.0 - penalty, 0.0, 1.0))
    if not reasons:
        primary_reason = "ok"
    else:
        reasons.sort(key=lambda item: item[0], reverse=True)
        primary_reason = reasons[0][1]

    return FrameQualityScore(
        score=score,
        reason=primary_reason,
        joint_jump=joint_jump,
        scale_ratio=scale_ratio,
        confidence=conf,
        edge_margin=edge_margin,
        near_tracking_loss=near_tracking_loss,
    )


def compute_sequence_quality_scores(
    frames: list[dict[str, Any]],
    *,
    loss_window: int = 2,
) -> tuple[np.ndarray, np.ndarray, list[FrameQualityScore]]:
    """Compute per-frame quality scores and training sample weights across a frame sequence.

    Frames within ``loss_window`` frames before or after a missing-hand dropout or frame gap (`dt > 0.12s`)
    are flagged with `near_tracking_loss=True` and downweighted in training `sample_weights`.
    """
    n = len(frames)
    scores = np.zeros(n, dtype=np.float32)
    weights = np.ones(n, dtype=np.float32)
    details: list[FrameQualityScore] = []
    if n == 0:
        return scores, weights, details

    pts_list = [_valid_points(f.get("lm")) for f in frames]
    has_hand = [p is not None for p in pts_list]
    times = [float(f.get("t", i / 30.0)) for i, f in enumerate(frames)]

    near_loss_mask = np.zeros(n, dtype=bool)
    for i in range(n):
        if not has_hand[i]:
            near_loss_mask[max(0, i - loss_window) : min(n, i + loss_window + 1)] = True
        elif i > 0 and has_hand[i - 1] and (times[i] - times[i - 1]) > 0.12:
            near_loss_mask[max(0, i - loss_window) : min(n, i + loss_window + 1)] = True

    prev_pts: np.ndarray | None = None
    prev_t: float | None = None
    for i, f in enumerate(frames):
        pts = pts_list[i]
        t_i = times[i]
        conf = float(f.get("score", 1.0) if f.get("score") is not None else 1.0)
        dt = (t_i - prev_t) if (prev_t is not None and t_i > prev_t) else 0.033
        fq = compute_frame_quality_score(
            pts,
            prev_pts,
            dt=dt,
            confidence=conf,
            near_tracking_loss=bool(near_loss_mask[i]),
        )
        details.append(fq)
        scores[i] = fq.score
        if pts is None:
            weights[i] = 0.0
        else:
            w = float(np.clip(fq.score, 0.15, 1.0))
            if near_loss_mask[i]:
                w = min(w, 0.25)
            weights[i] = w
            prev_pts = pts
            prev_t = t_i

    return scores, weights, details


def analyze_lost_mid_frame_logs(failures_dir: Path | str) -> dict[str, Any]:
    """Forensic analysis across `*-lost_mid_frame.json` failure logs in `failures_dir`."""
    fdir = Path(failures_dir)
    all_files = sorted(fdir.glob("*.json"))
    lost_files = sorted(fdir.glob("*-lost_mid_frame.json"))

    handedness_filter_drop = 0
    camera_fps_drop = 0
    size_change_20pct = 0
    low_score_below_085 = 0
    lost_ms_list: list[float] = []

    normal_jumps, pre_jumps, post_jumps = [], [], []
    normal_scales, pre_scales, post_scales = [], [], []
    normal_scores, pre_scores = [], []
    normal_edges, pre_edges = [], []
    normal_q, pre_q, post_q = [], [], []

    for fp in lost_files:
        doc = json.loads(fp.read_text(encoding="utf-8"))
        summ = doc.get("summary") or {}
        lost_ms_list.append(float(summ.get("lost_ms", 0.0)))
        if int(summ.get("raw_hands_during_loss", 0)) > 0:
            handedness_filter_drop += 1
        if int(summ.get("camera_drops_in_window", 0)) > 0:
            camera_fps_drop += 1
        sc = float(summ.get("size_change_0.4s", 1.0))
        if sc < 0.80 or sc > 1.20:
            size_change_20pct += 1
        if float(summ.get("score_min_0.4s", 1.0)) < 0.85:
            low_score_below_085 += 1

        frames = doc.get("frames") or []
        loss_indices = [i for i, fr in enumerate(frames) if not fr.get("hand")]
        if not loss_indices:
            continue
        first_loss = loss_indices[0]
        last_loss = first_loss
        while last_loss + 1 < len(frames) and not frames[last_loss + 1].get("hand"):
            last_loss += 1

        for k in range(max(1, first_loss - 3), first_loss):
            if frames[k].get("lm") and frames[k - 1].get("lm"):
                dt = max(1e-3, float(frames[k].get("t", 0.0)) - float(frames[k - 1].get("t", 0.0)))
                fq = compute_frame_quality_score(
                    frames[k]["lm"],
                    frames[k - 1]["lm"],
                    dt=dt,
                    confidence=float(frames[k].get("score", 1.0)),
                    near_tracking_loss=True,
                )
                pre_jumps.append(fq.joint_jump)
                pre_scales.append(fq.scale_ratio)
                pre_scores.append(fq.confidence)
                pre_edges.append(fq.edge_margin)
                pre_q.append(fq.score)

        if first_loss > 0 and last_loss + 1 < len(frames):
            if frames[first_loss - 1].get("lm") and frames[last_loss + 1].get("lm"):
                fq_post = compute_frame_quality_score(
                    frames[last_loss + 1]["lm"],
                    frames[first_loss - 1]["lm"],
                    dt=0.05,
                    confidence=float(frames[last_loss + 1].get("score", 1.0)),
                    near_tracking_loss=True,
                )
                post_jumps.append(fq_post.joint_jump)
                post_scales.append(fq_post.scale_ratio)
                post_q.append(fq_post.score)

        for k in range(1, max(1, first_loss - 10)):
            if frames[k].get("lm") and frames[k - 1].get("lm"):
                dt = max(1e-3, float(frames[k].get("t", 0.0)) - float(frames[k - 1].get("t", 0.0)))
                fq_norm = compute_frame_quality_score(
                    frames[k]["lm"],
                    frames[k - 1]["lm"],
                    dt=dt,
                    confidence=float(frames[k].get("score", 1.0)),
                    near_tracking_loss=False,
                )
                normal_jumps.append(fq_norm.joint_jump)
                normal_scales.append(fq_norm.scale_ratio)
                normal_scores.append(fq_norm.confidence)
                normal_edges.append(fq_norm.edge_margin)
                normal_q.append(fq_norm.score)

    def _stats(arr: list[float]) -> dict[str, float]:
        if not arr:
            return {"count": 0}
        a = np.asarray(arr, dtype=np.float64)
        return {
            "count": int(len(a)),
            "min": round(float(np.min(a)), 4),
            "p10": round(float(np.percentile(a, 10)), 4),
            "median": round(float(np.median(a)), 4),
            "p90": round(float(np.percentile(a, 90)), 4),
            "max": round(float(np.max(a)), 4),
        }

    return {
        "total_failure_logs": len(all_files),
        "lost_mid_frame_logs": len(lost_files),
        "cause_breakdown": {
            "handedness_filter_drop": handedness_filter_drop,
            "camera_fps_drop": camera_fps_drop,
            "size_change_over_20pct": size_change_20pct,
            "confidence_below_085": low_score_below_085,
        },
        "lost_ms": _stats(lost_ms_list),
        "joint_jump": {
            "normal": _stats(normal_jumps),
            "pre_loss_3f": _stats(pre_jumps),
            "post_reentry_1f": _stats(post_jumps),
        },
        "scale_ratio": {
            "normal": _stats(normal_scales),
            "pre_loss_3f": _stats(pre_scales),
            "post_reentry_1f": _stats(post_scales),
        },
        "confidence": {
            "normal": _stats(normal_scores),
            "pre_loss_3f": _stats(pre_scores),
        },
        "edge_margin": {
            "normal": _stats(normal_edges),
            "pre_loss_3f": _stats(pre_edges),
        },
        "quality_score": {
            "normal": _stats(normal_q),
            "pre_loss_3f": _stats(pre_q),
            "post_reentry_1f": _stats(post_q),
        },
    }


def find_confident_learning_candidates(
    sessions: list[dict[str, Any]],
    classes: list[str],
    *,
    output_path: Path | str | None = None,
    max_candidates: int = 250,
) -> dict[str, Any]:
    """Identify suspected label-error / high-loss frames via Confident Learning out-of-sample probabilities.

    Never deletes samples automatically; writes a human-reviewable report (`models/confident_learning_candidates.json`)
    with per-class self-confidence thresholds, the Confident Joint matrix, and ranked candidate frames.
    """
    from src.spatial_mlp import ensure_session_spatial_features, train_spatial_mlp

    num_classes = len(classes)
    records_meta: list[tuple[int, int]] = []
    xs_list: list[np.ndarray] = []
    ys_list: list[np.ndarray] = []
    cond_list: list[np.ndarray] = []

    for s_idx, s in enumerate(sessions):
        sf = ensure_session_spatial_features(s)
        y = np.asarray(s["y"], dtype=np.int64)
        cond = np.asarray(s["cond"], dtype=np.int64)
        valid = (y >= 0) & (y < num_classes) & np.any(sf != 0.0, axis=1)
        idx_valid = np.flatnonzero(valid)
        if len(idx_valid) == 0:
            continue
        xs_list.append(sf[idx_valid])
        ys_list.append(y[idx_valid])
        cond_list.append(cond[idx_valid])
        for f_i in idx_valid:
            records_meta.append((s_idx, int(f_i)))

    if not xs_list:
        return {"total_evaluated": 0, "candidates": []}

    x_all = np.concatenate(xs_list, axis=0)
    y_all = np.concatenate(ys_list, axis=0)
    c_all = np.concatenate(cond_list, axis=0)
    n_total = len(y_all)
    oos_probs = np.zeros((n_total, num_classes), dtype=np.float64)

    unique_conds = sorted({int(c) for c in c_all if int(c) >= 0})
    if len(unique_conds) >= 3:
        # 5-fold group cross-validation across condition groups for out-of-sample probabilities
        n_folds = min(5, len(unique_conds))
        fold_groups = [unique_conds[i::n_folds] for i in range(n_folds)]
        for held_conds in fold_groups:
            te_mask = np.isin(c_all, held_conds)
            tr_mask = ~te_mask
            if not np.any(te_mask) or not np.any(tr_mask):
                continue
            model_f, _ = train_spatial_mlp(
                x_all,
                y_all,
                classes=classes,
                precomputed_split=(tr_mask, te_mask),
                compute_random_comparison=False,
            )
            oos_probs[te_mask] = model_f.predict_proba(x_all[te_mask])
        unassigned_mask = c_all < 0
        if np.any(unassigned_mask):
            model_full, _ = train_spatial_mlp(
                x_all,
                y_all,
                classes=classes,
                groups=c_all,
                split_mode="group_leave_out",
                compute_random_comparison=False,
            )
            oos_probs[unassigned_mask] = model_full.predict_proba(x_all[unassigned_mask])
    else:
        model_full, _ = train_spatial_mlp(
            x_all,
            y_all,
            classes=classes,
            groups=c_all,
            split_mode="group_leave_out",
            compute_random_comparison=False,
        )
        oos_probs = model_full.predict_proba(x_all)

    # Per-class expected self-confidence threshold t_c (Northcutt et al.)
    class_thresholds: dict[str, float] = {}
    t_vec = np.zeros(num_classes, dtype=np.float64)
    for c_idx, cls_name in enumerate(classes):
        cls_mask = y_all == c_idx
        if np.any(cls_mask):
            t_c = float(np.mean(oos_probs[cls_mask, c_idx]))
        else:
            t_c = 0.5
        t_vec[c_idx] = max(0.25, min(0.92, t_c))
        class_thresholds[cls_name] = round(float(t_vec[c_idx]), 4)

    # Confident Joint matrix C[given_label, predicted_true_label]
    confident_joint = np.zeros((num_classes, num_classes), dtype=np.int64)
    candidates: list[dict[str, Any]] = []

    for idx in range(n_total):
        given_c = int(y_all[idx])
        probs_i = oos_probs[idx]
        pred_c = int(np.argmax(probs_i))
        pred_prob = float(probs_i[pred_c])
        self_prob = float(probs_i[given_c])

        if pred_prob >= t_vec[pred_c]:
            confident_joint[given_c, pred_c] += 1

        if pred_c != given_c and pred_prob >= t_vec[pred_c] and self_prob < 0.35:
            ce_loss = float(-np.log(max(self_prob, 1e-6)))
            margin = float(pred_prob - self_prob)
            s_idx, f_idx = records_meta[idx]
            s = sessions[s_idx]
            rec_name = Path(str(s.get("path", f"session_{s_idx}"))).name
            frame = s["frames"][f_idx]
            lab = frame.get("label") or {}
            q_scores = s.get("quality_scores")
            q_val = float(q_scores[f_idx]) if q_scores is not None and f_idx < len(q_scores) else 1.0

            given_name = classes[given_c]
            pred_name = classes[pred_c]
            if q_val < 0.55:
                issue_kind = "low_input_quality_artifact"
            elif given_name != "none" and pred_name == "none":
                issue_kind = "unpinched_or_missed_execution"
            elif given_name == "none" and pred_name != "none":
                issue_kind = "lingering_gesture_in_neutral"
            else:
                issue_kind = "adjacent_finger_confusion"

            candidates.append({
                "recording": rec_name,
                "frame_index": f_idx,
                "t": round(float(frame.get("t", 0.0)), 3),
                "condition": int(c_all[idx]),
                "phase": str(lab.get("phase") or ""),
                "raw_label": str(lab.get("raw_target") or lab.get("target") or given_name),
                "given_label": given_name,
                "suggested_label": pred_name,
                "self_confidence": round(self_prob, 4),
                "suggested_confidence": round(pred_prob, 4),
                "confidence_margin": round(margin, 4),
                "cross_entropy_loss": round(ce_loss, 4),
                "input_quality_score": round(q_val, 4),
                "issue_kind": issue_kind,
            })

    candidates.sort(key=lambda item: (item["confidence_margin"], item["cross_entropy_loss"]), reverse=True)
    by_kind: dict[str, int] = {}
    by_pair: dict[str, int] = {}
    for cand in candidates:
        k = cand["issue_kind"]
        by_kind[k] = by_kind.get(k, 0) + 1
        pair_key = f"{cand['given_label']} -> {cand['suggested_label']}"
        by_pair[pair_key] = by_pair.get(pair_key, 0) + 1

    report_doc = {
        "schema": "manux-confident-learning/1",
        "policy": "human_review_only_no_auto_delete",
        "total_evaluated_frames": int(n_total),
        "total_flagged_candidates": len(candidates),
        "flagged_ratio": round(len(candidates) / max(1, n_total), 4),
        "class_self_confidence_thresholds": class_thresholds,
        "confident_joint": {
            classes[i]: {classes[j]: int(confident_joint[i, j]) for j in range(num_classes)}
            for i in range(num_classes)
        },
        "counts_by_issue_kind": by_kind,
        "counts_by_confusion_pair": dict(sorted(by_pair.items(), key=lambda kv: kv[1], reverse=True)[:15]),
        "top_candidates": candidates[:max_candidates],
    }
    if output_path is not None:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_doc

