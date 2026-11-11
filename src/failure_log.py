"""
Failure recording: keep the last few seconds of tracking data, save them when it goes wrong.

Proposed 2026-11-04: instead of guessing why tracking drops (blur, darkness, frame edge,
hand too far), save what the tracker saw around each bad moment. Only numbers are kept:
21 joint positions per frame, confidence, cursor, speed, brightness, crop. No images.

Saved automatically when the hand vanishes while moving or in the middle of the frame
(not when it leaves through an edge on purpose), and manually with F / the dashboard
button for anything else that felt wrong (a misfired click...). Files go to
logs/failures/ (git-ignored) as JSON, oldest deleted beyond `max_files`.
"""
from __future__ import annotations

import csv
import json
import math
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

EDGE_MARGIN = .06  # a hand whose box ends this close to a frame edge may simply have left


def hand_box(landmarks: Sequence) -> Tuple[float, float, float, float]:
    xs = [p.x for p in landmarks]
    ys = [p.y for p in landmarks]
    return min(xs), min(ys), max(xs), max(ys)


def left_through_edge(landmarks: Sequence, margin: float = EDGE_MARGIN) -> bool:
    x0, y0, x1, y1 = hand_box(landmarks)
    return x0 <= margin or y0 <= margin or x1 >= 1 - margin or y1 >= 1 - margin


def summarize(frames: List[Dict[str, Any]], kind: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Numbers and candidate causes for one event, computed from its frames (t relative to trigger).

    The hints are candidates to check against the raw frames, not a diagnosis.
    """
    out: Dict[str, Any] = {}
    for f in frames:  # files from before schema 2 have no "box": derive it
        if f.get("hand") and "box" not in f and f.get("lm"):
            xs, ys = [q[0] for q in f["lm"]], [q[1] for q in f["lm"]]
            f["box"] = [min(xs), min(ys), max(xs), max(ys)]
    fw, fh = (meta.get("frame_px") or [1280, 720])[:2]
    aspect = float(fh) / max(1.0, float(fw))
    hand_before = [f for f in frames if f["t"] < 0 and f.get("hand")]
    last = hand_before[-1] if hand_before else None
    # the loss run: consecutive hand-less frames starting at the first one at/after the trigger
    run: List[Dict[str, Any]] = []
    recovered_at = None
    for f in frames:
        if f["t"] < -1e-6:
            continue
        if not f.get("hand"):
            run.append(f)
        elif run:
            recovered_at = f["t"]
            break
    if kind != "manual" and run:
        end = recovered_at if recovered_at is not None else frames[-1]["t"]
        out["lost_ms"] = round((end - run[0]["t"]) * 1000)
        out["recovered"] = recovered_at is not None
        out["lost_frames"] = len(run)
    paths: Dict[str, int] = {}
    raw_seen = 0
    for f in run:
        paths[f.get("path", "?")] = paths.get(f.get("path", "?"), 0) + 1
        raw_seen += 1 if f.get("raw") else 0
    out["paths_during_loss"] = paths
    out["raw_hands_during_loss"] = raw_seen
    if last is not None and "box" in last:
        x0, y0, x1, y1 = last["box"]
        out["box_size"] = [round(x1 - x0, 3), round(y1 - y0, 3)]
        out["box_center"] = [round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3)]
        out["edge_gap"] = round(min(x0, y0, 1 - x1, 1 - y1), 3)
        out["speed"] = last.get("speed")
        out["score_last"] = last.get("score")
        out["last_hand_t"] = last["t"]
        if last.get("clutch_state") is not None:
            out["clutch_state_last"] = last.get("clutch_state")
        if last.get("return_state") is not None:
            out["return_state_last"] = last.get("return_state")
        if last.get("hand_shape_mode") is not None:
            out["hand_shape_mode_last"] = last.get("hand_shape_mode")
        recent = [f for f in hand_before if f["t"] >= last["t"] - 0.4 and "box" in f]
        if len(recent) >= 2:
            a, b = recent[0], recent[-1]

            def size(f: Dict[str, Any]) -> float:
                return max(f["box"][2] - f["box"][0], (f["box"][3] - f["box"][1]) * aspect)

            def centre(f: Dict[str, Any]) -> Tuple[float, float]:
                return (f["box"][0] + f["box"][2]) / 2, (f["box"][1] + f["box"][3]) / 2

            out["size_change_0.4s"] = round(size(b) / size(a), 2) if size(a) > 0 else None
            dt = max(1e-3, b["t"] - a["t"])
            (ax, ay), (bx, by) = centre(a), centre(b)
            out["box_speed"] = round(((bx - ax) ** 2 + (by - ay) ** 2) ** .5 / dt, 2)
            out["score_min_0.4s"] = min(f.get("score", 0.0) for f in recent)
        crop = last.get("crop")
        out["crop_used"] = bool(crop)
        if crop:
            bx0, by0, bx1, by1 = x0 * fw, y0 * fh, x1 * fw, y1 * fh
            side = max(1, crop[2] - crop[0])
            out["box_in_crop_margin"] = round(min(bx0 - crop[0], by0 - crop[1], crop[2] - bx1, crop[3] - by1) / side, 3)
    # Summarize return-stroke / clutch state around the trigger
    near_trigger = [f for f in frames if -0.8 <= f["t"] <= 0.2]
    clutch_states = [f.get("clutch_state") for f in near_trigger if f.get("clutch_state") is not None]
    return_states = [f.get("return_state") for f in near_trigger if f.get("return_state") is not None]
    if clutch_states:
        out["clutch_active_frames"] = sum(1 for c in clutch_states if c == "active")
    if return_states:
        out["return_states_seen"] = sorted(set(str(r) for r in return_states))
    drift_vals = [f.get("return_drift_px") for f in near_trigger if f.get("return_drift_px") is not None]
    if drift_vals:
        out["return_drift_px"] = round(float(max(drift_vals)), 1)
    dts = [f["dt_ms"] for f in frames if f.get("dt_ms")]
    if dts:
        out["max_dt_ms"] = max(dts)
    fps = [f["fps"] for f in frames if f.get("fps")]
    if fps:
        out["min_fps"] = min(fps)
    det = [f["detect_ms"] for f in frames if f.get("detect_ms")]
    if det:
        out["max_detect_ms"] = max(det)
    bright = [f["brightness"] for f in frames if f.get("brightness") is not None]
    if bright:
        out["min_brightness"] = min(bright)
    drops = [f["dropped"] for f in frames if f.get("dropped") is not None]
    if len(drops) >= 2:
        out["camera_drops_in_window"] = max(drops) - min(drops)
    hints: List[str] = []
    if kind == "return_drift":
        hints.append("전진 이동 직후 반대 방향 복귀(리클러칭) 중 커서가 함께 끌려감")
    elif kind == "return_blocked_fp":
        hints.append("사용자 표시: 의도한 이동이 복귀 억제/클러치에 의해 차단됨 (오탐 차단)")
    elif kind != "manual":
        if run and raw_seen:
            hints.append("손은 검출됐지만 제어 손 필터(좌/우)에서 제외됨: handedness 뒤바뀜 의심")
        if out.get("edge_gap") is not None and out["edge_gap"] < 0.1:
            hints.append("손이 화면 가장자리 근처에서 사라짐")
        if out.get("box_in_crop_margin") is not None and out["box_in_crop_margin"] < 0.1:
            hints.append("손이 크롭 가장자리에 붙어 있었음: 크롭이 손을 못 따라갔을 수 있음")
        if out.get("size_change_0.4s") is not None and out["size_change_0.4s"] < 0.8:
            hints.append("직전 0.4초 사이 손 박스가 20% 넘게 줄어듦: 손을 돌리거나 멀어짐")
        if out.get("box_speed") is not None and out["box_speed"] > 1.0:
            hints.append("직전 손 이동이 빠름(초당 화면 1폭 이상): 모션 블러 의심")
        if out.get("max_dt_ms") is not None and out["max_dt_ms"] > 100:
            hints.append("프레임 간격이 100ms 넘게 벌어진 구간이 있음: 처리 지연/카메라 정지")
        if out.get("camera_drops_in_window"):
            hints.append("카메라 프레임이 버려짐")
        if out.get("min_brightness") is not None and out["min_brightness"] < 70:
            hints.append("어두움")
        if out.get("score_min_0.4s") is not None and out["score_min_0.4s"] < 0.85:
            hints.append("끊기기 직전 손 신뢰도가 낮았음")
        if run and "crop_miss>full" in paths:
            hints.append("크롭 검출 실패 후 전체 프레임 재검출도 실패")
        if out.get("box_size") and min(out["box_size"]) < 0.08:
            hints.append("손 박스가 매우 가늘거나 작음: 옆으로 돌린 손/먼 손")
        if not hints:
            hints.append("뚜렷한 후보 없음: 프레임별 landmark를 직접 확인")
    out["hints"] = hints
    return out


class FailureRecorder:
    def __init__(self, directory: Path, before: float = 3.0, after: float = 2.0,
                 cooldown: float = 3.0, max_files: int = 200) -> None:
        self.directory = Path(directory)
        self.before = float(before)
        self.after = float(after)
        self.cooldown = float(cooldown)
        self.max_files = int(max_files)
        self.samples: Deque[Dict[str, Any]] = deque()
        self.pending: Optional[Dict[str, Any]] = None  # trigger waiting for its "after" frames
        self.last_trigger = -1e9
        self.saved: List[Path] = []
        self.counts: Dict[str, int] = {}
        self.last_error = ""
        # automatic detection state
        self._had_hand = False
        self._last_landmarks = None
        self._tracked_since: Optional[float] = None
        self._last_hand_t: Optional[float] = None

    # ------------------------------------------------------------------ per frame
    def record(self, t: float, *, landmarks: Optional[Sequence], score: float = 0.0,
               moving: bool = False, **extra: Any) -> Optional[str]:
        """Add one frame. Returns the kind of an automatic trigger it caused, if any."""
        sample = {"t": round(t, 4), "hand": landmarks is not None, "moving": bool(moving)}
        if landmarks is not None:
            sample["lm"] = [[round(p.x, 4), round(p.y, 4), round(p.z, 4)] for p in landmarks]
            sample["score"] = round(float(score), 3)
            x0, y0, x1, y1 = hand_box(landmarks)
            sample["box"] = [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]
            self._last_hand_t = t
        elif self._last_hand_t is not None:
            sample["since_hand_ms"] = round((t - self._last_hand_t) * 1000, 0)
        sample.update({k: v for k, v in extra.items() if v is not None})
        self.samples.append(sample)
        # Keep `before` seconds; while a save is pending, keep everything from its window start
        keep_from = t - self.before
        if self.pending is not None:
            keep_from = min(keep_from, self.pending["t"] - self.before)
        while self.samples and self.samples[0]["t"] < keep_from:
            self.samples.popleft()

        kind = None
        if landmarks is not None:
            if not self._had_hand:
                self._tracked_since = t
            self._last_landmarks = landmarks
            if moving:
                drift_px = self._detect_return_drift(t, sample)
                if drift_px is not None:
                    sample["return_drift_px"] = round(float(drift_px), 1)
                    if self.trigger(t, "return_drift", note=f"drift_px={drift_px:.0f}"):
                        kind = "return_drift"
        elif self._had_hand and self._last_landmarks is not None:
            tracked_for = t - (self._tracked_since or t)
            if moving:
                kind = "lost_while_moving"
            elif tracked_for >= 1.0 and not left_through_edge(self._last_landmarks):
                kind = "lost_mid_frame"
            if kind is not None and not self.trigger(t, kind):
                kind = None
        self._had_hand = landmarks is not None
        return kind

    def _detect_return_drift(self, t: float, current: Dict[str, Any]) -> Optional[float]:
        """Detect unsuppressed return-stroke cursor drift after a large forward stroke.

        Triggers when:
        1) Clutch/return suppression is not currently blocking movement (`clutch_state != "active"`
           and `return_state not in ("pending", "confirmed")`),
        2) In the preceding 0.85 s window there was a forward cursor movement >= 220 px followed by
           a sharp reversal (angle >= 120 deg) where the hand moves back toward frame center (0.5, 0.5)
           and the cursor gets dragged >= 180 px along the return direction.
        """
        if current.get("clutch_state") == "active" or current.get("return_state") in ("pending", "confirmed"):
            return None
        cur_pos = current.get("cursor")
        cur_box = current.get("box")
        if not cur_pos or not cur_box or len(cur_pos) < 2 or len(cur_box) < 4:
            return None
        win = [
            s for s in self.samples
            if t - 0.85 <= s["t"] <= t and s.get("hand") and s.get("cursor") and s.get("box")
        ]
        if len(win) < 6:
            return None
        start = win[0]
        sx, sy = float(start["cursor"][0]), float(start["cursor"][1])
        # Find the apex (turning point) with maximum distance from window start
        apex_idx = -1
        max_fwd_px = 0.0
        for idx in range(2, len(win) - 2):
            cx, cy = float(win[idx]["cursor"][0]), float(win[idx]["cursor"][1])
            d = math.hypot(cx - sx, cy - sy)
            if d > max_fwd_px:
                max_fwd_px = d
                apex_idx = idx
        if apex_idx < 0 or max_fwd_px < 220.0:
            return None
        apex = win[apex_idx]
        if t - float(apex["t"]) < 0.08 or t - float(apex["t"]) > 0.50:
            return None
        ax, ay = float(apex["cursor"][0]), float(apex["cursor"][1])
        ex, ey = float(cur_pos[0]), float(cur_pos[1])
        fwd_vx, fwd_vy = ax - sx, ay - sy
        ret_vx, ret_vy = ex - ax, ey - ay
        ret_px = math.hypot(ret_vx, ret_vy)
        if ret_px < 180.0:
            return None
        cos_ang = (fwd_vx * ret_vx + fwd_vy * ret_vy) / max(1e-6, max_fwd_px * ret_px)
        angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_ang))))
        if angle_deg < 120.0:
            return None
        # Check that hand box center moved back toward camera frame center (0.5, 0.5)
        ab = apex["box"]
        ahx, ahy = 0.5 * (float(ab[0]) + float(ab[2])), 0.5 * (float(ab[1]) + float(ab[3]))
        ehx, ehy = 0.5 * (float(cur_box[0]) + float(cur_box[2])), 0.5 * (float(cur_box[1]) + float(cur_box[3]))
        hvx, hvy = ehx - ahx, ehy - ahy
        h_norm = math.hypot(hvx, hvy)
        to_cx, to_cy = 0.5 - ahx, 0.5 - ahy
        to_c_norm = math.hypot(to_cx, to_cy)
        if h_norm < 0.03 or to_c_norm < 0.04:
            return None
        to_center_cos = (hvx * to_cx + hvy * to_cy) / (h_norm * to_c_norm)
        if to_center_cos < 0.25:
            return None
        return ret_px

    def mark_return_block_fp(self, t: float, note: str = "User marked blocked movement (G key)") -> bool:
        """Record a user-marked false-positive return/clutch block (`return_blocked_fp` + negative candidate)."""
        self.record_negative_candidate(
            t,
            source="g_key",
            suspected_action="return_suppressed",
            rejection_reason="return_blocked_fp",
            note=note,
        )
        return self.trigger(t, "return_blocked_fp", note=note)

    def trigger(self, t: float, kind: str, note: str = "") -> bool:
        """Save the window around `t` once `after` seconds have been recorded."""
        if self.pending is not None or t - self.last_trigger < self.cooldown:
            return False
        self.last_trigger = t
        self.pending = {"t": t, "kind": kind, "note": note}
        return True

    def poll(self, t: float, meta: Dict[str, Any]) -> Optional[Path]:
        """Write a pending trigger when its window is complete. Returns the file, if written."""
        p = self.pending
        if p is None or t < p["t"] + self.after:
            return None
        self.pending = None
        window = [s for s in self.samples if p["t"] - self.before <= s["t"] <= p["t"] + self.after]
        rel = [{**x, "t": round(x["t"] - p["t"], 4)} for x in window]
        summary = summarize(rel, p["kind"], meta)
        payload = {
            "schema": 2,
            "kind": p["kind"],
            "summary": summary,
            "note": p["note"],
            "trigger_t": p["t"],
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "meta": meta,
            "frames": rel,  # t relative to trigger
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{p['kind']}.json"
            n = 1
            while path.exists():
                n += 1
                path = path.with_name(f"{time.strftime('%Y%m%d-%H%M%S')}-{p['kind']}-{n}.json")
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self._append_index(path.name, p["kind"], summary)
        except OSError as exc:
            self.last_error = str(exc)
            return None
        self.saved.append(path)
        self.counts[p["kind"]] = self.counts.get(p["kind"], 0) + 1
        self._prune()
        return path

    INDEX_COLUMNS = ("file", "kind", "saved_at", "lost_ms", "recovered", "lost_frames", "speed", "box_size",
                     "edge_gap", "crop_used", "box_in_crop_margin", "raw_hands_during_loss", "score_last",
                     "max_dt_ms", "min_fps", "paths_during_loss", "hints")

    def _append_index(self, name: str, kind: str, summary: Dict[str, Any]) -> None:
        """One line per event in index.csv, so many events can be compared without opening each JSON."""
        row = {"file": name, "kind": kind, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), **summary}

        def cell(v: Any) -> Any:
            if isinstance(v, dict):
                return json.dumps(v, ensure_ascii=False)
            if isinstance(v, (list, tuple)):
                return " | ".join(map(str, v))
            return v

        try:
            index = self.directory / "index.csv"
            new = not index.exists()
            with index.open("a", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(self.INDEX_COLUMNS)
                w.writerow([cell(row.get(c, "")) for c in self.INDEX_COLUMNS])
        except OSError as exc:
            self.last_error = str(exc)

    def _prune(self) -> None:
        try:
            files = sorted(self.directory.glob("*.json"))
        except OSError:
            return
        for old in files[:max(0, len(files) - self.max_files)]:
            try:
                old.unlink()
            except OSError:
                pass

    def record_negative_candidate(
        self,
        t: float,
        *,
        source: str = "f_key",
        suspected_action: Optional[str] = None,
        suspected_gesture: Optional[str] = None,
        calibrated_confidence: Optional[float] = None,
        rejection_reason: str = "",
        note: str = "",
        lookback_sec: float = 2.5,
    ) -> Optional[Path]:
        """Save a verifiable negative-candidate label (`negative_candidates.jsonl`) when the user
        immediately cancels an unintended action or presses the `F` key.

        Crucially, `auto_retrain` is hard-coded to `False` so unreviewed candidates are NEVER
        automatically injected into model retraining.
        """
        if source not in ("f_key", "g_key", "return_block_mark", "user_cancelled", "manual_mark"):
            return None
        recent = [s for s in self.samples if t - float(lookback_sec) <= s["t"] <= t + 0.05]
        inferred_action = suspected_action
        if not inferred_action:
            for s in reversed(recent):
                act = s.get("action") or s.get("act")
                if act and act not in ("none", "move_cursor"):
                    inferred_action = str(act)
                    break
        compact_frames = [
            {
                "dt": round(float(s["t"] - t), 4),
                "action": s.get("action", s.get("act")),
                "hand_score": s.get("score", s.get("hand_score")),
                "clutch_state": s.get("clutch_state"),
                "return_state": s.get("return_state"),
                "hand_shape_mode": s.get("hand_shape_mode"),
                "cursor": s.get("cursor"),
                "landmarks": s.get("lm", s.get("landmarks")),
            }
            for s in recent[-25:]
        ]
        record = {
            "schema": "manux-negative-candidate/1",
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_t": round(float(t), 4),
            "source": str(source),
            "suspected_action": inferred_action or "unknown",
            "suspected_gesture": suspected_gesture or "",
            "candidate_target_label": "none",
            "calibrated_confidence": round(float(calibrated_confidence), 4) if calibrated_confidence is not None else None,
            "rejection_reason": str(rejection_reason or ""),
            "verified": False,
            "auto_retrain": False,
            "note": str(note or ""),
            "frame_count": len(compact_frames),
            "frames": compact_frames,
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            out_path = self.directory / "negative_candidates.jsonl"
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.counts["negative_candidate"] = self.counts.get("negative_candidate", 0) + 1
            return out_path
        except OSError as exc:
            self.last_error = str(exc)
            return None

    def load_negative_candidates(self) -> List[Dict[str, Any]]:
        """Read stored negative candidate records (`auto_retrain=False`) for human verification."""
        out_path = self.directory / "negative_candidates.jsonl"
        if not out_path.is_file():
            return []
        items: List[Dict[str, Any]] = []
        try:
            for line in out_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        except Exception:
            pass
        return items

    def summary_text(self) -> str:
        if not self.counts:
            return "아직 저장된 기록이 없습니다."
        names = {"lost_while_moving": "이동 중 놓침", "lost_mid_frame": "화면 안에서 놓침", "manual": "직접 표시", "negative_candidate": "오작동 부정 후보", "return_drift": "복귀 드리프트", "return_blocked_fp": "이동 차단 오탐", "drag_started": "드래그 시작", "drag_ended": "드래그 끝", "inertia": "관성 스크롤",
                 "motion_enter": "모션 모드 켜짐", "motion_swipe": "모션 스와이프", "motion_exit": "모션 모드 꺼짐", "motion_close_ask": "창 닫기 질문", "close_ask": "창 닫기 질문(손 모양)", "close_close": "창 닫기(손 모양)", "toggle_sign": "켜기/끄기(손 모양)", "desktop": "바탕화면 보기", "click": "클릭"}
        return " · ".join(f"{names.get(k, k)} {v}건" for k, v in self.counts.items())
