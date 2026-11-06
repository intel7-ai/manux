"""Pose Snapshot Studio for MANUX v0.4 (`data` layer).

Ports the high-speed `PoseSnapshotStudio` workflow from `park` branch directly into `preview`:
- Supports 8 official `preview` posture classes:
  1. `none` (이동·중립: 반주먹/열린손 이동 및 휴식)
  2. `thumb_middle` (좌클릭·드래그: 엄지+중지 맞대기)
  3. `thumb_ring` (우클릭: 엄지+약지 맞대기)
  4. `thumb_index` (스크롤: 엄지+검지 맞대기)
  5. `thumb_pinky` (창 전환: 엄지+새끼 맞대기)
  6. `palm_side` (바탕화면: 엄지 접고 네 손가락 펴기)
  7. `thumbs_up` (모션 모드: 엄지 위로 세우기)
  8. `v_sign` (제어 토글: 검지·중지 V자 펴기)
- Capture Modes:
  - `snapshot` (단일 캡처): 1-click / Spacebar single-frame capture
  - `stream` (영상 연속 수집): 50ms throttled continuous stream up to `max_samples` (default 1,000)
- Discrepancy Filter (`discrepancy_only`):
  - Automatically captures ONLY frames where the current Rule/AI judgment disagrees with User Ground Truth!
- Saves and loads JSON snapshot datasets in `data_dir / "pose_snapshots"` so multiple 1,000-sample sessions
  accumulate and can be trained into a Spatial MLP in < 3 seconds.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

STUDIO_CATEGORIES: Tuple[Dict[str, str], ...] = (
    {
        "id": "click",
        "name": "마우스·클릭",
        "icon": "🖱️",
        "desc": "좌·우클릭, 스크롤, 동시클릭",
    },
    {
        "id": "window",
        "name": "창·시스템 제어",
        "icon": "🪟",
        "desc": "창 전환, 바탕화면, 토글, 닫기",
    },
    {
        "id": "guard",
        "name": "이동·방어 가드",
        "icon": "🛡️",
        "desc": "커서 이동, 휴식 자세, 헛클릭 방어",
    },
)

STUDIO_ACTIONS: Tuple[Dict[str, str], ...] = (
    # --- [guard] 이동·방어 가드 ---
    {
        "id": "none",
        "category": "guard",
        "key": "1",
        "icon": "✊",
        "label": "1. 이동·휴식 가드 (none)",
        "short": "이동·휴식",
        "desc": "아무 동작 아님 · 반주먹이나 편 손으로 커서를 이동하거나 쉬는 자세 (헛클릭 방어)",
    },
    # --- [click] 마우스·클릭 ---
    {
        "id": "thumb_middle",
        "category": "click",
        "key": "1",
        "icon": "👆",
        "label": "1. 좌클릭 (엄지+중지)",
        "short": "좌클릭 (엄지+중지)",
        "desc": "엄지 끝과 중지 끝을 맞대어 좌클릭하는 자세",
    },
    {
        "id": "thumb_ring",
        "category": "click",
        "key": "2",
        "icon": "💍",
        "label": "2. 우클릭 (엄지+약지)",
        "short": "우클릭 (엄지+약지)",
        "desc": "엄지 끝과 약지 끝을 맞대어 우클릭하는 자세",
    },
    {
        "id": "thumb_index",
        "category": "click",
        "key": "3",
        "icon": "📜",
        "label": "3. 스크롤 진입 (엄지+검지)",
        "short": "스크롤 (엄지+검지)",
        "desc": "엄지 끝과 검지 끝을 맞대어 상하 스크롤 모드로 진입하는 자세",
    },
    {
        "id": "thumb_middle_ring",
        "category": "click",
        "key": "4",
        "icon": "🤞",
        "label": "4. 동시·휠클릭 (엄지+중지+약지)",
        "short": "동시클릭 (엄지+중지+약지)",
        "desc": "엄지 끝에 중지와 약지를 동시에 맞대어 좌우 동시 클릭 및 휠 클릭하는 자세",
    },
    # --- [window] 창·시스템 제어 ---
    {
        "id": "thumb_pinky",
        "category": "window",
        "key": "1",
        "icon": "🤙",
        "label": "1. 창 전환 (엄지+새끼)",
        "short": "창 전환 (엄지+새끼)",
        "desc": "엄지 끝과 새끼손가락 끝을 맞대어 창 전환(Alt+Tab) 창을 띄우는 자세",
    },
    {
        "id": "palm_side",
        "category": "window",
        "key": "2",
        "icon": "🖐️",
        "label": "2. 바탕화면 (엄지 접고 손 가로)",
        "short": "바탕화면 (손가로)",
        "desc": "엄지만 손바닥 안으로 접고 네 손가락을 펴 손을 가로로 세워 모든 창을 최소화하는 자세",
    },
    {
        "id": "v_sign",
        "category": "window",
        "key": "3",
        "icon": "✌️",
        "label": "3. 제어 켜기·끄기 (V 손 모양)",
        "short": "제어 토글 (V자)",
        "desc": "검지와 중지만 V자로 펴고 나머지는 접어 마우스 제어를 토글하는 자세",
    },
    {
        "id": "rock_sign",
        "category": "window",
        "key": "4",
        "icon": "🤘",
        "label": "4. 창 닫기 (검지·새끼 펴기)",
        "short": "창 닫기 (Rock)",
        "desc": "검지와 새끼만 펴고 중지·약지를 접어 현재 창을 닫는(Alt+F4) 자세",
    },
    {
        "id": "thumbs_up",
        "category": "window",
        "key": "5",
        "icon": "👍",
        "label": "5. 모션 모드 (엄지 위로)",
        "short": "모션 모드 (엄지척)",
        "desc": "네 손가락을 접고 엄지만 위로 세워 모션 제어 모드를 전환하는 자세",
    },
)

ACTION_IDS: Tuple[str, ...] = tuple(a["id"] for a in STUDIO_ACTIONS)
ACTION_BY_ID: Dict[str, Dict[str, str]] = {a["id"]: a for a in STUDIO_ACTIONS}
ACTION_LABELS: Dict[str, str] = {a["id"]: a["short"] for a in STUDIO_ACTIONS}

# Map engine gesture IDs / routed action IDs to Studio class IDs
GESTURE_TO_STUDIO_CLASS: Dict[str, str] = {
    "pinch.move": "none",
    "pinch.move_fist": "none",
    "pointer.move": "none",
    "pinch.thumb_middle": "thumb_middle",
    "pinch.drag": "thumb_middle",
    "pointer.left_click": "thumb_middle",
    "pointer.drag": "thumb_middle",
    "pinch.thumb_ring": "thumb_ring",
    "pointer.right_click": "thumb_ring",
    "pinch.scroll": "thumb_index",
    "pinch.thumb_pinky": "thumb_pinky",
    "pinch.thumb_tucked_open": "palm_side",
    "motion.enter": "thumbs_up",
    "motion.exit": "thumbs_up",
    "v_sign": "v_sign",
    "rock_sign": "rock_sign",
    "window.close": "rock_sign",
    "mouse.move": "none",
    "mouse.left_click": "thumb_middle",
    "mouse.drag": "thumb_middle",
    "mouse.right_click": "thumb_ring",
    "mouse.scroll": "thumb_index",
    "window.task_switcher": "thumb_pinky",
    "window.show_desktop": "palm_side",
    "pinch.thumb_middle_ring": "thumb_middle_ring",
    "mouse.middle_click": "thumb_middle_ring",
    "mouse.both_click": "thumb_middle_ring",
}


def normalize_system_action(snapshot: Any, actions: Sequence[str] = ()) -> str:
    """Extract the current rule-engine class (`STUDIO_ACTIONS` id) from an engine Snapshot."""
    if snapshot is not None:
        for gid, phase in getattr(snapshot, "intents", ()):
            if phase != "end" and gid in GESTURE_TO_STUDIO_CLASS:
                cls = GESTURE_TO_STUDIO_CLASS[gid]
                if cls != "none":
                    return cls
        for act, phase, _reason in getattr(snapshot, "results", ()):
            if phase != "end" and act in GESTURE_TO_STUDIO_CLASS:
                cls = GESTURE_TO_STUDIO_CLASS[act]
                if cls != "none":
                    return cls
    for act in actions:
        if act in GESTURE_TO_STUDIO_CLASS:
            cls = GESTURE_TO_STUDIO_CLASS[act]
            if cls != "none":
                return cls
    return "none"


class PoseSnapshotStudio:
    """Manages live posture snapshot/stream collection, dataset files, and discrepancy statistics."""

    def __init__(
        self,
        data_dir: Path,
        *,
        max_samples: int = 1000,
        stream_throttle_ms: float = 50.0,
        train_fn: Optional[Callable[[List[Dict[str, Any]], Path], Dict[str, Any]]] = None,
        predict_fn: Optional[Callable[[Any, Any], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.snapshots_dir = self.data_dir / "pose_snapshots"
        self.models_dir = self.data_dir / "models" / "spatial_mlp"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.max_samples = max(10, int(max_samples))
        self.stream_throttle_ms = max(0.0, float(stream_throttle_ms))
        self.samples: List[Dict[str, Any]] = []
        self.selected_ground_truth: str = "none"
        self.capture_mode: str = "stream"  # Default to 'stream' as user loved continuous video frame collection!
        self.is_streaming: bool = False
        self.discrepancy_only: bool = False
        self.last_stream_time_ms: float = 0.0
        self.status_note: str = "숫자키(1~8)로 동작을 고르고 Space 또는 수집 버튼으로 프레임을 모으세요."
        self.last_capture_status: str = ""

        self._train_fn = train_fn
        self._predict_fn = predict_fn

        # Live frame state updated every frame from runtime/bridge
        self.hand_tracked: bool = False
        self.live_landmarks: Optional[Sequence[Tuple[float, float, float]]] = None
        self.live_world: Optional[Sequence[Tuple[float, float, float]]] = None
        self.live_system_action: str = "none"
        self.live_ai_action: str = "none"
        self.live_ai_confidence: float = 0.0
        self.model_summary: Dict[str, Any] = self._load_saved_model_summary()
        self._saved_counts_cache: Optional[Dict[str, int]] = None
        self._saved_files_count: int = 0
        self._summary_cache: Optional[Dict[str, Any]] = None
        self._summary_cache_time: float = 0.0
        self.saved_dataset_counts(force=True)

    def _load_saved_model_summary(self) -> Dict[str, Any]:
        model_file = self.models_dir / "spatial_pose_mlp.json"
        if model_file.is_file():
            try:
                doc = json.loads(model_file.read_text(encoding="utf-8"))
                meta = doc.get("metadata") or {}
                return {
                    "loaded": True,
                    "path": str(model_file),
                    "test_accuracy": meta.get("test_accuracy", 0.0),
                    "train_accuracy": meta.get("train_accuracy", 0.0),
                    "total_samples": meta.get("total_samples", 0),
                    "elapsed_ms": meta.get("elapsed_ms", 0.0),
                    "trained_at": meta.get("trained_at", ""),
                    "per_class": meta.get("per_class", {}),
                }
            except Exception:
                pass
        return {"loaded": False}

    # ------------------------------------------------------------------ configuration
    def set_capture_mode(self, mode: str) -> str:
        if mode not in ("snapshot", "stream"):
            raise ValueError(f"Invalid capture mode: {mode}")
        self.capture_mode = mode
        if mode == "snapshot" and self.is_streaming:
            self.is_streaming = False
        self.status_note = (
            "연속 스트림 모드: Space를 누르면 50ms 간격으로 최대 1,000프레임을 자동 수집합니다."
            if mode == "stream"
            else "단일 스냅샷 모드: Space 또는 캡처 버튼을 누를 때마다 1프레임씩 정밀 캡처합니다."
        )
        return self.capture_mode

    def set_ground_truth(self, action_id: str) -> bool:
        if action_id not in ACTION_BY_ID:
            return False
        self.selected_ground_truth = action_id
        info = ACTION_BY_ID[action_id]
        self.status_note = f"[{info['label']}] 선택됨 — {info['desc']}"
        return True

    def set_discrepancy_only(self, enabled: bool) -> bool:
        self.discrepancy_only = bool(enabled)
        self.status_note = (
            "불일치 자동 선별 ON: 현재 모델/규칙 판정이 지정 정답과 다른 사각지대 프레임만 골라서 수집합니다."
            if self.discrepancy_only
            else "전체 프레임 수집 모드: 일치 여부와 상관없이 모든 각도의 자세 프레임을 수집합니다."
        )
        return self.discrepancy_only

    def start_stream(self) -> bool:
        self.capture_mode = "stream"
        self.is_streaming = True
        self.last_stream_time_ms = 0.0
        info = ACTION_BY_ID[self.selected_ground_truth]
        self.status_note = f"🎥 [{info['short']}] 연속 프레임 수집 중... (Space로 정지)"
        return True

    def stop_stream(self) -> bool:
        self.is_streaming = False
        self.status_note = f"⏹️ 연속 수집 정지됨 (현재 세션 표본 {len(self.samples)}개)"
        return False

    def toggle_stream_or_snapshot(self) -> Dict[str, Any]:
        if self.capture_mode == "stream":
            if self.is_streaming:
                self.stop_stream()
                return {"mode": "stream", "streaming": False}
            self.start_stream()
            return {"mode": "stream", "streaming": True}
        return self.capture_snapshot()

    # ------------------------------------------------------------------ live frame & capture
    def observe_frame(
        self,
        landmarks: Optional[Sequence[Tuple[float, float, float]]],
        world: Optional[Sequence[Tuple[float, float, float]]],
        system_action: str,
        now_ms: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Called on every incoming frame when Studio is active."""
        now_ms = time.perf_counter() * 1000.0 if now_ms is None else float(now_ms)
        self.hand_tracked = landmarks is not None and len(landmarks) == 21
        self.live_landmarks = landmarks if self.hand_tracked else None
        self.live_world = world if (world is not None and len(world) == 21) else None
        self.live_system_action = system_action if system_action in ACTION_BY_ID else "none"

        if self.hand_tracked and self._predict_fn is not None:
            pred = self._predict_fn(self.live_landmarks, self.live_world)
            if pred is not None:
                self.live_ai_action = str(pred.get("action", "none"))
                self.live_ai_confidence = float(pred.get("confidence", 0.0))
            else:
                self.live_ai_action = "none"
                self.live_ai_confidence = 0.0
        else:
            self.live_ai_action = "none"
            self.live_ai_confidence = 0.0

        if self.is_streaming and self.capture_mode == "stream":
            return self.process_stream_frame(now_ms=now_ms)
        return None

    def effective_predicted_action(self) -> str:
        """If a trained Spatial MLP is loaded, compare against AI prediction; otherwise Rule Engine."""
        if self.model_summary.get("loaded") and self.live_ai_confidence > 0.0:
            return self.live_ai_action
        return self.live_system_action

    def is_live_match(self) -> bool:
        if not self.hand_tracked:
            return False
        return self.effective_predicted_action() == self.selected_ground_truth

    def capture_snapshot(
        self,
        *,
        ground_truth: Optional[str] = None,
        landmarks: Optional[Sequence[Tuple[float, float, float]]] = None,
        world: Optional[Sequence[Tuple[float, float, float]]] = None,
        now_ms: Optional[float] = None,
        source: str = "snapshot",
    ) -> Dict[str, Any]:
        gt = ground_truth or self.selected_ground_truth
        lm = landmarks if landmarks is not None else self.live_landmarks
        wd = world if world is not None else self.live_world
        now_ms = time.perf_counter() * 1000.0 if now_ms is None else float(now_ms)

        if lm is None or len(lm) != 21:
            self.status_note = "⚠️ 손이 감지되지 않아 캡처하지 않았습니다. 카메라에 손을 보여주세요."
            return {"success": False, "reason": "no_hand_tracked"}
        if gt not in ACTION_BY_ID:
            return {"success": False, "reason": "unknown_action"}

        raw_landmarks = [
            {"x": round(float(pt[0]), 6), "y": round(float(pt[1]), 6), "z": round(float(pt[2]), 6)}
            for pt in lm
        ]
        raw_world = (
            [{"x": round(float(pt[0]), 6), "y": round(float(pt[1]), 6), "z": round(float(pt[2]), 6)} for pt in wd]
            if wd is not None and len(wd) == 21
            else None
        )

        pred_action = self.effective_predicted_action()
        is_match = pred_action == gt
        sample_id = f"snap_{gt}_{int(time.time() * 1000)}_{uuid4().hex[:4]}"

        sample = {
            "sampleId": sample_id,
            "timestamp": round(now_ms, 2),
            "isoTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source,
            "trackingValid": True,
            "predictedAction": pred_action,
            "systemAction": self.live_system_action,
            "aiAction": self.live_ai_action,
            "aiConfidence": round(self.live_ai_confidence, 4),
            "groundTruthAction": gt,
            "isMatch": is_match,
            "isFailureSample": not is_match,
            "rawLandmarks": raw_landmarks,
            "rawWorld": raw_world,
        }

        if len(self.samples) >= self.max_samples:
            self.samples.pop(0)
        self.samples.append(sample)

        tag = "✅ 일치" if is_match else "🚨 불일치(사각지대)"
        self.last_capture_status = f"{ACTION_LABELS[gt]} 캡처 ({tag}) · 총 {len(self.samples)}개"
        if source == "snapshot":
            self.status_note = f"📸 {self.last_capture_status}"
        return {"success": True, "sample": sample, "totalSamples": len(self.samples)}

    def process_stream_frame(self, *, now_ms: Optional[float] = None) -> Dict[str, Any]:
        if not self.is_streaming:
            return {"success": False, "reason": "stream_not_active"}
        now_ms = time.perf_counter() * 1000.0 if now_ms is None else float(now_ms)
        if self.last_stream_time_ms > 0.0 and (now_ms - self.last_stream_time_ms) < self.stream_throttle_ms:
            return {"success": False, "reason": "throttled"}
        if not self.hand_tracked or self.live_landmarks is None:
            return {"success": False, "reason": "no_hand_tracked"}

        pred_action = self.effective_predicted_action()
        is_match = pred_action == self.selected_ground_truth
        if self.discrepancy_only and is_match:
            return {"success": False, "reason": "match_skipped_discrepancy_only"}

        res = self.capture_snapshot(now_ms=now_ms, source="stream")
        if res.get("success"):
            self.last_stream_time_ms = now_ms
            if len(self.samples) >= self.max_samples:
                self.stop_stream()
                self.status_note = f"🎉 목표 상한({self.max_samples}개) 수집 완료! [데이터셋 저장] 또는 [즉시 학습]을 누르세요."
        return res

    # ------------------------------------------------------------------ management & persistence
    def undo_last(self) -> bool:
        if not self.samples:
            self.status_note = "취소할 표본이 없습니다."
            return False
        removed = self.samples.pop()
        gt = removed.get("groundTruthAction", "none")
        self.status_note = f"↩️ 직전 표본({ACTION_LABELS.get(gt, gt)})을 취소했습니다. (남은 표본: {len(self.samples)}개)"
        return True

    def clear_samples(self) -> None:
        self.is_streaming = False
        self.samples.clear()
        self.status_note = "🗑️ 현재 세션의 수집 버퍼를 비웠습니다."
        self.invalidate_saved_cache()

    def invalidate_saved_cache(self) -> None:
        self._saved_counts_cache = None
        self._summary_cache = None

    def summary(self) -> Dict[str, Any]:
        now = time.perf_counter()
        if self._summary_cache is not None and (now - self._summary_cache_time) < 0.20:
            return self._summary_cache

        by_action = {k: 0 for k in ACTION_IDS}
        matches = 0
        discrepancies = 0
        for s in self.samples:
            gt = s.get("groundTruthAction", "none")
            if gt in by_action:
                by_action[gt] += 1
            if s.get("isMatch"):
                matches += 1
            else:
                discrepancies += 1
        saved_counts = self.saved_dataset_counts()
        res = {
            "total": len(self.samples),
            "maxSamples": self.max_samples,
            "matches": matches,
            "discrepancies": discrepancies,
            "byAction": by_action,
            "savedTotal": sum(saved_counts.values()),
            "savedByAction": saved_counts,
            "savedFiles": self._saved_files_count,
        }
        self._summary_cache = res
        self._summary_cache_time = now
        return res

    def saved_dataset_counts(self, force: bool = False) -> Dict[str, int]:
        if self._saved_counts_cache is not None and not force:
            return dict(self._saved_counts_cache)
        counts = {k: 0 for k in ACTION_IDS}
        files = 0
        if self.snapshots_dir.is_dir():
            for fpath in sorted(self.snapshots_dir.glob("*.json")):
                files += 1
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                    items = doc.get("samples") if isinstance(doc, dict) else (doc if isinstance(doc, list) else [])
                    for s in items or []:
                        gt = s.get("groundTruthAction") or s.get("action")
                        if gt in counts:
                            counts[gt] += 1
                except Exception:
                    continue
        self._saved_counts_cache = counts
        self._saved_files_count = files
        return dict(counts)

    def save_dataset(self) -> Optional[Path]:
        if not self.samples:
            self.status_note = "⚠️ 저장할 표본이 없습니다. 먼저 프레임을 캡처하거나 연속 수집하세요."
            return None
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        filename = f"manux_pose_snapshots_preview_{stamp}.json"
        out_path = self.snapshots_dir / filename
        stat = self.summary()
        payload = {
            "schema": "manux-pose-snapshots/2",
            "branch": "preview",
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "totalSamples": len(self.samples),
            "matches": stat["matches"],
            "discrepancies": stat["discrepancies"],
            "byAction": stat["byAction"],
            "samples": self.samples,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.invalidate_saved_cache()
        self.saved_dataset_counts(force=True)
        self.status_note = f"💾 데이터셋 저장 완료: {filename} ({len(self.samples)}개 표본)"
        return out_path

    def load_all_samples(self) -> List[Dict[str, Any]]:
        """Combine all saved JSON datasets in `snapshots_dir` plus any unsaved in-memory samples."""
        combined: List[Dict[str, Any]] = []
        seen_ids = set()
        if self.snapshots_dir.is_dir():
            for fpath in sorted(self.snapshots_dir.glob("*.json")):
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                    items = doc.get("samples") if isinstance(doc, dict) else (doc if isinstance(doc, list) else [])
                    for s in items or []:
                        sid = s.get("sampleId")
                        if sid and sid in seen_ids:
                            continue
                        if sid:
                            seen_ids.add(sid)
                        combined.append(s)
                except Exception:
                    continue
        for s in self.samples:
            sid = s.get("sampleId")
            if sid and sid in seen_ids:
                continue
            if sid:
                seen_ids.add(sid)
            combined.append(s)
        return combined

    def train_now(self) -> Dict[str, Any]:
        """Save current buffer (if non-empty) and run the 4-second Spatial MLP trainer over all samples."""
        if self.is_streaming:
            self.stop_stream()
        if self.samples:
            self.save_dataset()
        all_samples = self.load_all_samples()
        if len(all_samples) < 2:
            self.status_note = "⚠️ 학습할 표본이 부족합니다 (최소 2개 이상 수집 후 실행하세요)."
            return {"success": False, "reason": "not_enough_samples"}
        if self._train_fn is None:
            self.status_note = "⚠️ 학습 엔진이 연결되지 않았습니다."
            return {"success": False, "reason": "no_trainer"}
        try:
            res = self._train_fn(all_samples, self.models_dir / "spatial_pose_mlp.json")
            self.model_summary = {
                "loaded": True,
                "path": str(self.models_dir / "spatial_pose_mlp.json"),
                **res,
            }
            self.status_note = (
                f"⚡ Spatial MLP 학습 완료! ({res.get('elapsed_ms', 0):.0f}ms 소요) · "
                f"표본 {res.get('total_samples', 0)}개 · 검증 정확도 {res.get('test_accuracy', 0):.1f}% "
                f"(학습 {res.get('train_accuracy', 0):.1f}%)"
            )
            return {"success": True, **res}
        except Exception as exc:
            self.status_note = f"❌ 학습 실패: {exc}"
            return {"success": False, "reason": str(exc)}

