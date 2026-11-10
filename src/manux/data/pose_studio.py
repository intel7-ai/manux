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
import math
import os
import re
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
        "label": "4. 양쪽 클릭 (엄지+중지+약지)",
        "short": "양쪽 클릭 (엄지+중지+약지)",
        "desc": "엄지 끝에 중지와 약지를 동시에 맞대어 좌클릭·우클릭을 독립적으로 함께 판정하는 자세",
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
    "pinch.right_drag": "thumb_ring",
    "pointer.right_click": "thumb_ring",
    "pinch.scroll": "thumb_index",
    "pinch.thumb_pinky": "thumb_pinky",
    "pinch.thumb_tucked_open": "palm_side",
    "motion.enter": "thumbs_up",
    "motion.exit": "thumbs_up",
    "v_sign": "v_sign",
    "control.v_sign": "v_sign",
    "control.toggle": "v_sign",
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
        # Left (thumb+middle) and right (thumb+ring) are judged independently; both live -> "both click".
        live = {gid for gid, phase in getattr(snapshot, "intents", ()) if phase != "end"}
        live |= {act for act, phase, _r in getattr(snapshot, "results", ()) if phase != "end"}
        left = bool(live & {"pinch.thumb_middle", "pinch.drag", "mouse.left_click", "pointer.left_click", "pointer.drag"})
        right = bool(live & {"pinch.thumb_ring", "pinch.right_drag", "mouse.right_click", "pointer.right_click"})
        if left and right:
            return "thumb_middle_ring"
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
    for event in getattr(snapshot, "events", ()):
        if event.startswith("mode:") and event.endswith(":intent") and "motion" in event:
            return "thumbs_up"
    return "none"


def is_full_joints_tracked(
    landmarks: Optional[Sequence[Tuple[float, float, float]]],
    world: Optional[Sequence[Tuple[float, float, float]]] = None,
    *,
    margin: float = 0.005,
    min_span: float = 0.035,
) -> Tuple[bool, str]:
    """Check if all 21 hand landmarks are fully recognized, valid, and unclipped."""
    if landmarks is None or len(landmarks) != 21:
        return False, "no_hand"
    for i, pt in enumerate(landmarks):
        if not (math.isfinite(pt[0]) and math.isfinite(pt[1]) and math.isfinite(pt[2])):
            return False, f"invalid_coord_{i}"
        if pt[0] < -margin or pt[0] > 1.0 + margin or pt[1] < -margin or pt[1] > 1.0 + margin:
            return False, f"clipped_edge_{i}"
    span = math.hypot(landmarks[9][0] - landmarks[0][0], landmarks[9][1] - landmarks[0][1])
    if span < min_span:
        return False, "collapsed_or_too_small"
    if world is not None and len(world) == 21:
        for i, pt in enumerate(world):
            if not (math.isfinite(pt[0]) and math.isfinite(pt[1]) and math.isfinite(pt[2])):
                return False, f"invalid_world_{i}"
    return True, "ok"


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
        load_model_fn: Optional[Callable[[Path], Any]] = None,
        on_model_updated: Optional[Callable[[Any], None]] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.snapshots_dir = self.data_dir / "pose_snapshots"
        self.models_dir = self.data_dir / "models" / "spatial_mlp"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.max_samples = max(10, int(max_samples))
        self.stream_throttle_ms = max(0.0, float(stream_throttle_ms))
        self.samples: List[Dict[str, Any]] = []
        self.profile, self.split = "", "train"
        self.session_id = uuid4().hex
        self.selected_ground_truth: str = "none"
        self.capture_mode: str = "stream"  # Default to 'stream' as user loved continuous video frame collection!
        self.is_streaming: bool = False
        self.discrepancy_only: bool = False
        self.require_full_joints: bool = True
        self.last_stream_time_ms: float = 0.0
        self.status_note: str = "숫자키(1~8)로 동작을 고르고 Space 또는 수집 버튼으로 프레임을 모으세요."
        self.last_capture_status: str = ""

        self._load_model_fn = load_model_fn
        self._train_fn = train_fn
        self._predict_fn = predict_fn
        self._on_model_updated = on_model_updated

        # Live frame state updated every frame from runtime/bridge
        self.hand_tracked: bool = False
        self.full_joints_tracked: bool = False
        self.joints_status_reason: str = "no_hand"
        self.live_landmarks: Optional[Sequence[Tuple[float, float, float]]] = None
        self.live_world: Optional[Sequence[Tuple[float, float, float]]] = None
        self.live_system_action: str = "none"
        self.live_ai_action: str = "none"
        self.live_ai_confidence: float = 0.0
        self.live_ai_probabilities: Dict[str, float] = {}
        self.live_hybrid_action: str = "none"
        self.live_hybrid_confidence: float = 0.0

        # Multi-model management
        self.active_model: Optional[Any] = None
        self.active_model_id: str = self._resolve_initial_active_model()
        self.model_summary: Dict[str, Any] = self._load_model_summary(self.active_model_id)
        self._load_active_model_instance()

        self._saved_counts_cache: Optional[Dict[str, int]] = None
        self._saved_files_count: int = 0
        self._summary_cache: Optional[Dict[str, Any]] = None
        self._summary_cache_time: float = 0.0
        self.saved_dataset_counts(force=True)

    def _resolve_initial_active_model(self) -> str:
        cfg = self.models_dir / "active_model.json"
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                active_id = data.get("active_model_id")
                if active_id and (self.models_dir / active_id).is_file():
                    return str(active_id)
            except Exception:
                pass
        if (self.models_dir / "spatial_pose_mlp.json").is_file():
            return "spatial_pose_mlp.json"
        existing = sorted(f.name for f in self.models_dir.glob("*.json") if f.name != "active_model.json")
        if existing:
            return existing[0]
        return "spatial_pose_mlp.json"

    def _save_active_model_config(self) -> None:
        cfg = self.models_dir / "active_model.json"
        try:
            cfg.write_text(json.dumps({"active_model_id": self.active_model_id}, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_model_summary(self, model_id: str) -> Dict[str, Any]:
        model_file = self.models_dir / model_id
        default_name = "기본 모델 (Default)" if model_id == "spatial_pose_mlp.json" else Path(model_id).stem
        if model_file.is_file():
            try:
                doc = json.loads(model_file.read_text(encoding="utf-8"))
                meta = doc.get("metadata") or {}
                is_trained = doc.get("schema") == "manux-spatial-mlp/1" and "weights" in doc
                return {
                    "loaded": is_trained,
                    "model_id": model_id,
                    "name": meta.get("name") or default_name,
                    "path": str(model_file),
                    "test_accuracy": float(meta.get("test_accuracy", 0.0)),
                    "train_accuracy": float(meta.get("train_accuracy", 0.0)),
                    "total_samples": int(meta.get("total_samples", 0)),
                    "elapsed_ms": float(meta.get("elapsed_ms", 0.0)),
                    "trained_at": str(meta.get("trained_at", "")),
                    "per_class": meta.get("per_class", {}),
                }
            except Exception:
                pass
        return {"loaded": False, "model_id": model_id, "name": default_name}

    def _load_active_model_instance(self) -> None:
        model_file = self.models_dir / self.active_model_id
        if model_file.is_file() and self._load_model_fn is not None:
            try:
                doc = json.loads(model_file.read_text(encoding="utf-8"))
                if doc.get("schema") == "manux-spatial-mlp/1" and "weights" in doc:
                    self.active_model = self._load_model_fn(model_file)
                    if self._on_model_updated is not None:
                        self._on_model_updated(self.active_model)
                else:
                    self.active_model = None
            except Exception:
                self.active_model = None

    def _load_saved_model_summary(self) -> Dict[str, Any]:
        return self._load_model_summary(self.active_model_id)

    # ------------------------------------------------------------------ configuration
    def set_require_full_joints(self, enabled: bool) -> bool:
        self.require_full_joints = bool(enabled)
        self.status_note = (
            "관절 완전 인식 필터 ON: 21개 관절이 카메라 화면에 완전히 들어왔을 때만 수집합니다 (잘림·가림 방지)."
            if self.require_full_joints
            else "전체 프레임 수집 모드: 손 일부가 잘리더라도 감지되면 수집합니다."
        )
        return self.require_full_joints

    def list_models(self) -> List[Dict[str, Any]]:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        res = []
        files = sorted(self.models_dir.glob("*.json"))
        has_default = any(f.name == "spatial_pose_mlp.json" for f in files)
        if not has_default:
            res.append({
                "id": "spatial_pose_mlp.json",
                "name": "기본 모델 (Default)",
                "active": self.active_model_id == "spatial_pose_mlp.json",
                "exists": False,
                "test_accuracy": 0.0,
                "train_accuracy": 0.0,
                "total_samples": 0,
                "trained_at": "미학습",
                "file_size_kb": 0.0,
            })
        for f in files:
            if f.name == "active_model.json":
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                meta = doc.get("metadata") or {}
                is_trained = doc.get("schema") == "manux-spatial-mlp/1" and "weights" in doc
                is_active = (f.name == self.active_model_id)
                name = meta.get("name") or (
                    "기본 모델 (Default)" if f.name == "spatial_pose_mlp.json" else f.stem
                )
                res.append({
                    "id": f.name,
                    "name": name,
                    "active": is_active,
                    "exists": is_trained,
                    "test_accuracy": float(meta.get("test_accuracy", 0.0)),
                    "train_accuracy": float(meta.get("train_accuracy", 0.0)),
                    "total_samples": int(meta.get("total_samples", 0)),
                    "trained_at": str(meta.get("trained_at", "미학습" if not is_trained else "")),
                    "file_size_kb": round(f.stat().st_size / 1024, 1) if is_trained else 0.0,
                })
            except Exception:
                continue
        return res

    def create_model(self, name: str) -> Dict[str, Any]:
        raw_name = (name or "").strip()
        clean = re.sub(r"[^\w\-\uac00-\ud7a3]", "_", raw_name).strip("_")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        if not clean:
            existing_count = len([f for f in self.models_dir.glob("*.json") if f.name != "active_model.json"])
            clean = f"새_모델_{existing_count + 1}"
            raw_name = clean
        filename = f"{clean}.json"
        target_file = self.models_dir / filename
        if not target_file.is_file():
            slot_payload = {
                "schema": "manux-spatial-mlp-slot/1",
                "metadata": {
                    "name": raw_name or clean,
                    "trained_at": "미학습",
                    "test_accuracy": 0.0,
                    "train_accuracy": 0.0,
                    "total_samples": 0,
                },
            }
            target_file.write_text(json.dumps(slot_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.active_model_id = filename
        self.active_model = None
        self.samples.clear()
        self.invalidate_saved_cache()
        self.saved_dataset_counts(force=True)
        if self._on_model_updated is not None:
            self._on_model_updated(None)
        self._save_active_model_config()
        self.model_summary = self._load_model_summary(filename)
        self.status_note = f"✨ 새 모델 슬롯 생성: [{raw_name or clean}] 선택됨 (누적 0개). [⚡ 즉시 학습]을 누르면 이 모델에 저장됩니다."
        return {"success": True, "id": filename, "name": raw_name or clean}

    def activate_model(self, model_id: str) -> bool:
        model_file = self.models_dir / model_id
        is_trained = False
        if model_file.is_file():
            try:
                doc = json.loads(model_file.read_text(encoding="utf-8"))
                is_trained = doc.get("schema") == "manux-spatial-mlp/1" and "weights" in doc
            except Exception:
                is_trained = False

        if is_trained:
            try:
                model = self._load_model_fn(model_file) if self._load_model_fn is not None else None
                self.active_model = model
                self.active_model_id = model_id
                self.samples.clear()
                self.invalidate_saved_cache()
                self.saved_dataset_counts(force=True)
                self._save_active_model_config()
                self.model_summary = self._load_model_summary(model_id)
                if self._on_model_updated is not None:
                    self._on_model_updated(model)
                display_name = self.model_summary.get("name") or model_id
                self.status_note = f"✅ [{display_name}] 모델이 활성화되어 실시간 제어 엔진에 즉시 적용되었습니다!"
                return True
            except Exception as exc:
                self.status_note = f"❌ 모델 활성화 실패: {exc}"
                return False
        else:
            if model_id != "spatial_pose_mlp.json" and not model_file.is_file():
                self.status_note = f"⚠️ 모델 파일을 찾을 수 없습니다: {model_id}"
                return False
            self.active_model = None
            self.active_model_id = model_id
            self.samples.clear()
            self.invalidate_saved_cache()
            self.saved_dataset_counts(force=True)
            self._save_active_model_config()
            self.model_summary = self._load_model_summary(model_id)
            if self._on_model_updated is not None:
                self._on_model_updated(None)
            display_name = self.model_summary.get("name") or Path(model_id).stem
            self.status_note = f"📌 [{display_name}] 슬롯 선택됨 (미학습 상태 — 표본 수집 후 [⚡ 즉시 학습]을 누르세요)"
            return True

    def delete_model(self, model_id: str) -> bool:
        model_file = self.models_dir / model_id
        if model_file.is_file():
            try:
                model_file.unlink()
            except Exception as exc:
                self.status_note = f"❌ 모델 삭제 실패: {exc}"
                return False
        if self.snapshots_dir.is_dir():
            for fpath in list(self.snapshots_dir.glob("*.json")):
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                    doc_model = doc.get("modelId", "spatial_pose_mlp.json") if isinstance(doc, dict) else "spatial_pose_mlp.json"
                    if doc_model == model_id:
                        fpath.unlink()
                except Exception:
                    continue
        was_active = (self.active_model_id == model_id)
        if was_active:
            self.active_model = None
            self.samples.clear()
            if self._on_model_updated is not None:
                self._on_model_updated(None)
            remaining = [f.name for f in sorted(self.models_dir.glob("*.json")) if f.name != "active_model.json"]
            if remaining:
                self.activate_model(remaining[0])
            else:
                self.active_model_id = "spatial_pose_mlp.json"
                self._save_active_model_config()
                self.model_summary = {"loaded": False}
        self.invalidate_saved_cache()
        self.saved_dataset_counts(force=True)
        self.status_note = f"🗑️ [{model_id}] 모델이 삭제되었습니다."
        return True

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
        self.full_joints_tracked, self.joints_status_reason = (
            is_full_joints_tracked(landmarks, world) if self.hand_tracked else (False, "no_hand")
        )
        self.live_landmarks = landmarks if self.hand_tracked else None
        self.live_world = world if (world is not None and len(world) == 21) else None
        self.live_system_action = system_action if system_action in ACTION_BY_ID else "none"

        if self.hand_tracked and self._predict_fn is not None:
            pred = self._predict_fn(self.live_landmarks, self.live_world)
            if pred is not None:
                self.live_ai_action = str(pred.get("action", "none"))
                self.live_ai_confidence = float(pred.get("confidence", 0.0))
                self.live_ai_probabilities = dict(pred.get("probabilities", {}))
            else:
                self.live_ai_action = "none"
                self.live_ai_confidence = 0.0
                self.live_ai_probabilities = {}
        else:
            self.live_ai_action = "none"
            self.live_ai_confidence = 0.0
            self.live_ai_probabilities = {}

        self.live_hybrid_action, self.live_hybrid_confidence = self._compute_hybrid_action()

        if self.is_streaming and self.capture_mode == "stream":
            return self.process_stream_frame(now_ms=now_ms)
        return None

    def _compute_hybrid_action(self) -> Tuple[str, float]:
        """Return the active Spatial MLP prediction when loaded, or fall back to the rule engine."""
        if not self.hand_tracked:
            return "none", 0.0
        if not self.model_summary.get("loaded"):
            return self.live_system_action, 1.0 if self.live_system_action != "none" else 0.0
        return self.live_ai_action, self.live_ai_confidence

    def effective_predicted_action(self) -> str:
        """If a trained Spatial MLP is loaded, compare against hybrid prediction; otherwise Rule Engine."""
        if self.model_summary.get("loaded") and self.live_hybrid_confidence > 0.0:
            return self.live_hybrid_action
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
        if self.require_full_joints:
            ok, reason = is_full_joints_tracked(lm, wd)
            if not ok:
                self.status_note = "⚠️ 21개 관절이 완전히 인식되지 않아 캡처하지 않았습니다 (화면 경계 잘림·가림 방지)."
                return {"success": False, "reason": reason}
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
        if self.require_full_joints and not self.full_joints_tracked:
            return {"success": False, "reason": f"partial_joints_{self.joints_status_reason}"}

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
        self.session_id = uuid4().hex
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

    def _doc_matches_active_model(self, doc: Any) -> bool:
        target = self.active_model_id or "spatial_pose_mlp.json"
        if isinstance(doc, dict):
            doc_model = doc.get("modelId") or "spatial_pose_mlp.json"
            return doc_model == target
        return target == "spatial_pose_mlp.json"

    def saved_dataset_counts(self, force: bool = False) -> Dict[str, int]:
        if self._saved_counts_cache is not None and not force:
            return dict(self._saved_counts_cache)
        counts = {k: 0 for k in ACTION_IDS}
        files = 0
        if self.snapshots_dir.is_dir():
            for fpath in sorted(self.snapshots_dir.glob("*.json")):
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                    if not self._doc_matches_active_model(doc):
                        continue
                    files += 1
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
        model_id = self.active_model_id or "spatial_pose_mlp.json"
        model_stem = Path(model_id).stem
        filename = f"manux_pose_snapshots_{model_stem}_{self.split}_{stamp}_{uuid4().hex}.json"
        out_path = self.snapshots_dir / filename
        stat = self.summary()
        saved_count = len(self.samples)
        payload = {
            "schema": "manux-pose-snapshots/2",
            "branch": "preview",
            "person": self.profile, "split": self.split, "session_id": self.session_id,
            "modelId": model_id,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "totalSamples": saved_count,
            "matches": stat["matches"],
            "discrepancies": stat["discrepancies"],
            "byAction": stat["byAction"],
            "samples": list(self.samples),
        }
        # Publish a complete new file without ever replacing another capture. A UUID avoids
        # same-millisecond saves colliding; exclusive publication also protects existing
        # files if a name does collide. Readers glob only *.json, never this staging file.
        part = out_path.with_name(f".{filename}.part")
        created = False
        try:
            with part.open("x", encoding="utf-8") as file:
                created = True
                json.dump(payload, file, ensure_ascii=False, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            if os.name == "nt":
                os.rename(part, out_path)  # Windows rename refuses an existing destination.
            else:
                os.link(part, out_path)  # POSIX rename replaces; linking is exclusive instead.
        finally:
            if created:
                part.unlink(missing_ok=True)
        self.samples.clear()
        self.invalidate_saved_cache()
        self.saved_dataset_counts(force=True)
        self.status_note = f"💾 [{model_stem}] 데이터셋 저장 완료: {filename} ({saved_count}개 표본 누적 반영)"
        return out_path

    def load_all_samples(self) -> List[Dict[str, Any]]:
        """Combine saved JSON datasets for the active model in `snapshots_dir` plus any unsaved in-memory samples."""
        combined: List[Dict[str, Any]] = []
        seen_ids = set()
        if self.snapshots_dir.is_dir():
            for fpath in sorted(self.snapshots_dir.glob("*.json")):
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(doc, dict) and doc.get("split", "train") != "train":
                        continue
                    if not self._doc_matches_active_model(doc):
                        continue
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
        for s in self.samples if self.split == "train" else ():
            sid = s.get("sampleId")
            if sid and sid in seen_ids:
                continue
            if sid:
                seen_ids.add(sid)
            combined.append(s)
        return combined

    def train_now(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Save current buffer (if non-empty) and run the Spatial MLP trainer into target model."""
        if self.is_streaming:
            self.stop_stream()
        target_id = model_name or self.active_model_id or "spatial_pose_mlp.json"
        self.active_model_id = target_id
        if self.samples:
            self.save_dataset()
        all_samples = self.load_all_samples()
        if len(all_samples) < 2:
            self.status_note = "⚠️ 학습할 표본이 부족합니다 (최소 2개 이상 수집 후 실행하세요)."
            return {"success": False, "reason": "not_enough_samples"}
        if self._train_fn is None:
            self.status_note = "⚠️ 학습 엔진이 연결되지 않았습니다."
            return {"success": False, "reason": "no_trainer"}

        target_path = self.models_dir / target_id
        try:
            res = self._train_fn(all_samples, target_path)
            self.active_model_id = target_id
            self._save_active_model_config()

            if self._load_model_fn is not None and target_path.is_file():
                self.active_model = self._load_model_fn(target_path)
                if self._on_model_updated is not None:
                    self._on_model_updated(self.active_model)

            self.model_summary = {
                "loaded": True,
                "model_id": target_id,
                "name": target_path.stem,
                "path": str(target_path),
                **res,
            }
            display_name = target_path.stem
            self.status_note = (
                f"⚡ [{display_name}] Spatial MLP 학습 완료! ({res.get('elapsed_ms', 0):.0f}ms 소요) · "
                f"표본 {res.get('total_samples', 0)}개 · 학습 데이터 내부 확인(실제 정확도 아님) {res.get('test_accuracy', 0):.1f}% "
                f"(학습 {res.get('train_accuracy', 0):.1f}%)"
            )
            return {"success": True, **res}
        except Exception as exc:
            self.status_note = f"❌ 학습 실패: {exc}"
            return {"success": False, "reason": str(exc)}



def pose_training_partition(x, y, weights=None, *, seed=42):
    """기존 Pose 화면 내부 확인: 무작위 80/20, 10개 미만은 학습 데이터 재확인.

    독립 세션 평가가 아니다. MLP 학습기와 eval_judges에서는 분할하지 않는다.
    """
    import numpy as np
    order = np.random.default_rng(seed).permutation(len(y))
    split = max(1, min(len(y) - 1, int(len(y) * .8))) if len(y) >= 10 else len(y)
    train, check = order[:split], order[split:] if len(y) >= 10 else order
    return x[train], y[train], None if weights is None else weights[train], (x[check], y[check])


def summarize_pose_fit(model, metrics, x, y):
    """원래 Studio의 전체 표본 수·클래스별 내부 확인 집계를 보존한다."""
    from dataclasses import replace
    import numpy as np
    predicted = np.argmax(model.predict_proba(x), axis=1)
    per_class = {}
    for index, name in enumerate(model.classes):
        mask = y == index
        total = int(mask.sum())
        correct = int((predicted[mask] == index).sum())
        per_class[name] = {"total": total, "correct": correct,
                           "accuracy": round(correct / total * 100, 1) if total else None}
    metrics = replace(metrics, total_samples=len(y), per_class=per_class)
    model.metadata.update(total_samples=metrics.total_samples, per_class=per_class)
    return metrics
