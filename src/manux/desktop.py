"""Desktop app composition (S6): the v0.3-style Qt window on the v0.4 layers.

    python -m manux                 # camera (needs the `vision` and `ui` extras)
    python -m manux --replay <rec>  # play a recording instead of a camera (UI check without a webcam)

Threads: a runtime thread runs input -> tracking -> engine (with the real Windows output) and keeps the
newest `ViewFrame`; the Qt thread runs `ui.bridge.Bridge`, which renders that frame and sends
`UiCommand`s. The runtime never touches Qt objects; the bridge never decides gestures.
Like `app.py`, this module is a composition root and may import every layer.
"""
from __future__ import annotations

import argparse
from collections import deque
import copy
import json
import queue
import sys
import threading
import time
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple
from uuid import uuid4

from manux.actions.bindings import Binding
from manux.app import _FEATURES, _camera_config, build_engine, schema, pipeline_parts, read_bindings, training_specs
from manux.capture.camera import LatestFrameCamera, brightness
from manux.core.contracts import FrameInfo, HandObservation, OutputTarget, PracticeEvent, RecordingIdentity, RuntimeRequest, Stage
from manux.data.catalog import BY_KEY, tasks_for
from manux.data.guided import GuidedSession
from manux.data.recorder import SessionRecorder
from manux.data.recording import read, replay
from manux.data.tracking_test import TrackingTest, load_recent_results, result_line, view as tracking_view
from manux.data.training_center import TrainingCenterService
from manux.data.legacy_import import pending_model
from manux.data.pointer_setup import LIMITS as POINTER_SETUP_LIMITS, SETTING_KEY, PointerSetup, read_profiles, write_profiles
from manux.data.pose_studio import ACTION_IDS, PoseSnapshotStudio, STUDIO_ACTIONS, STUDIO_CATEGORIES, normalize_system_action
from manux.engine.commands import RequestMode, SetBaseMode, SetBusy, SetControl
from manux.engine.pipeline import Engine
from manux.features import tcn_gate
from manux.features.spatial_pose import SpatialPoseFeature, SpatialPoseGuard
from manux.features.pinch.preview import PinchPreview
from manux.features.pinch.engine import palm_anchor_point
from manux.ml.card import ModelError
from manux.ml.predictor import load_model
from manux.ml.spatial_mlp import SpatialMlpModel, train_spatial_mlp
from manux.ml.store import active_token, load_active
from manux.perception.local3d import FEATURES, FEATURE_VERSION
from manux.perception.palm_axes import corrected_point
from manux.perception.palm_pitch import palm_pitch
from manux.perception.spatial_features import SPATIAL_FEATURE_VERSION, extract_spatial_features
from manux.settings.paths import AppPaths, resolve
from manux.settings import store as settings_store
from manux.settings.store import Settings, load
from manux.settings.tcn_policy import GesturePolicies
from manux.ui.view import SeenHand, ViewFrame

POINTER_MOVE = "pointer.move"
CURSOR_BASE_KEYS = ("relative_gain_min", "relative_gain_max", "smoothing")


class CameraInput:
    """Camera + MediaPipe. Opened lazily and retried, so a missing webcam shows a message instead of crashing."""

    def __init__(self, settings: Settings, model_path: Path, source: Optional[str] = None) -> None:
        self.settings = settings
        self.model_path = model_path
        self.source = source
        self.camera: Optional[LatestFrameCamera] = None
        self.tracker = None
        self.landmarker = None
        self.error = ""
        self._next_try = 0.0

    def _open(self) -> bool:
        now = time.perf_counter()
        if now < self._next_try:
            return False
        self._next_try = now + 2.0
        try:
            from manux.tracking.landmarker import MediaPipeLandmarker, options_from
            from manux.tracking.tracker import HandTracker
            track = self.settings.section("tracking")
            if self.landmarker is None:
                self.landmarker = MediaPipeLandmarker(self.model_path, **options_from(track))
                self.tracker = HandTracker(self.landmarker, use_crop=track["hand_crop"], min_score=track["min_hand_score"])
            camera = LatestFrameCamera(_camera_config(self.settings, self.source))
            if not camera.open():
                self.error = "카메라를 찾을 수 없습니다. 연결을 확인하세요."
                return False
            self.camera, self.error = camera, ""
            return True
        except Exception as exc:                       # missing extras, model download failure ...
            self.error = f"카메라·손 인식을 시작하지 못했습니다: {exc}"
            return False

    def read(self, control_hand: str, use_crop: bool):
        """(frame, image, hand, hands, detect_ms) or None (no frame this time)."""
        if self.camera is None and not self._open():
            return None
        item = self.camera.read(timeout=0.05)
        if item is None:
            return None
        frame, image = item
        self.tracker.use_crop = use_crop
        result = self.tracker.step(frame, image, control_hand or "Right")
        from manux.tracking.selection import physical_side
        hands = tuple(SeenHand(physical_side(h, frame.mirrored, self.tracker.min_score), h.score) for h in result.hands)
        return frame, image, result.hand, hands, result.detect_ms

    def mode_text(self) -> str:
        mode = self.camera.actual_mode() if self.camera is not None else None
        return f"{mode[0]}×{mode[1]} · {mode[2]:g}fps" if mode else ""

    @property
    def fps(self) -> float:
        return self.camera.fps if self.camera is not None else 0.0

    def close(self) -> None:
        if self.camera is not None:
            self.camera.release()
        if self.landmarker is not None:
            self.landmarker.close()


class ReplayInput:
    """A finished recording played at its own pace (looping). No preview image."""

    def __init__(self, path: Path) -> None:
        rec = read(path)
        if not rec.complete:
            raise ValueError(f"{path.name}: not a complete recording")
        self.frames = list(replay(rec))
        self.error = ""
        self._i = 0
        self._t0 = time.perf_counter()
        self._rec_t0 = self.frames[0].frame.t if self.frames else 0.0

    def read(self, control_hand: str, use_crop: bool):
        if not self.frames:
            return None
        item = self.frames[self._i % len(self.frames)]
        due = self._t0 + (item.frame.t - self._rec_t0)
        wait = due - time.perf_counter()
        if wait > 0:
            time.sleep(min(wait, 0.05))
            return None
        self._i += 1
        if self._i % len(self.frames) == 0:            # loop: restart the clock
            self._t0, self._i = time.perf_counter(), 0
        f = item.frame
        frame = FrameInfo(seq=f.seq, t=time.perf_counter(), width=f.width, height=f.height, mirrored=f.mirrored)
        hand = None
        if item.hand is not None:
            hand = HandObservation(frame, item.hand.landmarks, item.hand.handedness, item.hand.score, item.hand.world)
        hands = (SeenHand(hand.handedness, hand.score),) if hand is not None else ()
        return frame, None, hand, hands, 0.0

    def mode_text(self) -> str:
        return "녹화 재생"

    fps = 30.0

    def close(self) -> None:
        return None


class DesktopRuntime:
    """Runs the input -> engine loop on its own thread and keeps the newest ViewFrame."""

    def __init__(self, engine: Engine, source, settings: Settings, *, model_store=None, model_token=None,
                 model_status="", policy_store=None, current_model=None) -> None:
        self.engine = engine
        self.source = source
        self.settings = settings
        self.control_hand = settings.get("tracking.control_hand")
        self.use_crop = bool(settings.get("tracking.hand_crop"))
        self.want_image = True
        self.pinch_preview = PinchPreview()
        self._overrides: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._latest = ViewFrame(None)
        self._seq = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._metrics = ("", -1, False)
        self._frames = 0
        self.model_store = Path(model_store) if model_store is not None else None
        self._model_token = model_token
        self._attempted_model_token = model_token
        self._next_model_check = 0.
        self.model_status = model_status
        self.policy_store = policy_store
        self.current_model = current_model
        self.preview_only = False
        self.tcn_enabled = True
        self._configured_filters = tuple(engine.filters)
        self.spatial_feature: Optional[SpatialPoseFeature] = None
        self.spatial_guard: Optional[SpatialPoseGuard] = None
        engine.settings_for = self.settings_for
        # tracking test (S6): owned by this thread; the UI asks with start/close and reads ViewFrame.tracking
        self.log_dir: Optional[Path] = None
        self.run_info: Callable[[], Dict[str, Any]] = dict    # settings written into the result CSV
        self.tracking: Optional[TrackingTest] = None
        self._tracking_request: Optional[str] = None
        self.recent: Tuple[str, ...] = ()
        # dataset recording (training center): owned by this thread; UI requests mode/focus/plan/advance/pause
        self.recordings_dir: Optional[Path] = None
        self._rec_want: Tuple[str, str, Optional[Dict[str, Any]], int, bool] = ("off", "", None, 0, False)
        self._rec_mode = "off"
        self._rec_focus = ""
        self._rec_adv_seen = 0
        self._rec: Optional[SessionRecorder] = None
        self._guided: Optional[GuidedSession] = None
        self._rec_note = ""
        self._rec_note_until = 0.0
        self._rec_marks = 0
        self._rec_marks_seen = 0
        self._rec_identity = RecordingIdentity("local")
        self._rec_identity_active = None
        self._rec_session_id: Optional[str] = None
        self._rec_session_id_active: Optional[str] = None
        self._runtime_requests = queue.SimpleQueue()
        self._spatial_updates = queue.SimpleQueue()
        self.spatial_model_path = None
        self.pose_model = None
        self._pose_tracker = None
        self._pose_failed = False
        self.pose_status = "팔·어깨 기록 꺼짐"
        self.sensitivity_result = (0, True, "")
        self.pointer_setup_dir: Optional[Path] = None
        self._pointer_setup: Optional[PointerSetup] = None
        self._pointer_setup_action = None
        self._pointer_setup_environment = None
        self._pointer_environment_key = None
        self._pointer_config_signature = None
        self.advanced_result = (0, True, "")
        self._control_model = None
        self._practice_events = deque(maxlen=128)
        self._practice_event_seq = 0
        self._practice_epoch = 0
        self._practice_active = False
        self._pose_preview = None
        from manux.output.windows.cursor import CursorMapper
        from manux.output.windows.config import WindowsOutputConfig
        from manux.platform.monitors import ScreenLayout
        practice = self.engine.sinks.get(OutputTarget.PRACTICE)
        if practice is not None:
            practice.config = WindowsOutputConfig(**settings.section("cursor"))
            practice.cursor = CursorMapper(ScreenLayout.single(1920, 1080), practice.config)
        self._configure_cursor_sinks()

    def _configure_cursor_sinks(self):
        from dataclasses import replace
        for target in (OutputTarget.WINDOWS, OutputTarget.PRACTICE):
            sink = self.engine.sinks.get(target)
            if sink is not None and hasattr(sink, "configure"):
                sink.configure(replace(sink.config, **self.settings.section("cursor")))

    def _sync_control_model(self):
        model = (self.spatial_feature.model
                 if self.spatial_feature and self.spatial_guard and self.spatial_guard.enabled
                 else None)
        if model is not None:
            per_class = model.metadata.get("per_class")
            v_samples = per_class.get("v_sign") if isinstance(per_class, dict) else None
            if isinstance(v_samples, dict) and v_samples.get("total") == 0:
                model = None
        # Pipeline reloads may construct a new control feature, even with the same model.
        for feature in self.engine.features:
            if feature.id == "control" and getattr(feature, "model", None) is not model:
                feature.set_model(model)

    # ------------------------------------------------------------------ settings the UI changes
    def settings_for(self, fid: str) -> Mapping[str, object]:
        section = dict(self.settings.section(fid))
        section.update(self._overrides.get(fid, {}))
        return section

    def set_override(self, fid: str, key: str, value: Any) -> None:
        """Values a feature reads that are not plain settings (e.g. pointer calibration axes)."""
        with self._lock:
            self._overrides = {**self._overrides, fid: {**self._overrides.get(fid, {}), key: value}}

    def set_pointer_gain(self, gain: float) -> None:
        """v0.3 pointer sensitivity: the pointer's move binding gain."""
        bindings = dict(self.engine.router.bindings)
        old = bindings.get(POINTER_MOVE)
        if old is not None:
            bindings[POINTER_MOVE] = Binding(old.action, {**old.params, "gain": max(0.1, float(gain))})
            self.engine.router.set_bindings(bindings)

    def set_spatial_model(self, model: Optional[SpatialMlpModel]) -> None:
        """Stage a model; only the engine thread may mutate the live recognizer."""
        self._spatial_updates.put(model)

    def pointer_gain(self) -> float:
        binding = self.engine.router.bindings.get(POINTER_MOVE)
        return float(binding.params.get("gain", 1.0)) if binding is not None else 1.0

    def _pointer_environment(self, frame, monitor, mode):
        owner = "pointer" if mode == "pointer" else "pinch"
        axes = self._overrides.get(owner, {}).get("axes" if owner == "pointer" else "palm_axes")
        source_id = getattr(self.source, "source", None)
        if source_id is None:
            source_id = self.settings.get("camera.source")
        return (type(self.source).__name__, str(source_id),
                self.control_hand, frame.width, frame.height, frame.mirrored, frame.rotation, mode,
                self.settings.get("pointer.anchor") if mode == "pointer" else "palm", axes,
                self.settings.get(f"{owner}.cursor_anchor_ratio"),
                self.pointer_gain() if mode == "pointer" else 1.0,
                monitor.width, monitor.height)

    def _pointer_profile_locked(self):
        return self.settings.is_overridden(SETTING_KEY) or any(
            self.settings.is_overridden("cursor." + name) for name in POINTER_SETUP_LIMITS)

    def _configure_cursor_for_frame(self, frame, sink):
        if sink is None or not hasattr(sink, "set_frame_aspect"):
            return
        raw = self.settings.get(SETTING_KEY)
        if not raw and self._pointer_config_signature is None:
            sink.set_frame_aspect(frame.aspect)
            return
        environment = self._pointer_environment(frame, sink.screens.primary, self.engine.arbiter.base_mode)
        locked = self._pointer_profile_locked()
        base_values = tuple(self.settings.get("cursor." + key) for key in CURSOR_BASE_KEYS)
        signature = (environment, raw, locked, base_values)
        if signature == self._pointer_config_signature:
            return
        self._pointer_environment_key = sha256(repr(environment).encode("utf-8")).hexdigest()
        values = {} if locked else read_profiles(raw).get(self._pointer_environment_key, {})
        config = {"palm_left": 0., "palm_right": 0., "palm_up": 0., "palm_down": 0.,
                  "palm_x_gain": 1., "palm_y_gain": 1., "frame_aspect": frame.aspect}
        config.update(self.settings.section("cursor"))
        config.update(values)
        candidate = replace(sink.config, **config)
        if candidate != sink.config:
            sink.release_all("cursor environment changed")
            sink.configure(candidate)
        self._pointer_config_signature = signature if raw else None

    def _pointer_setup_frame(self, frame, hand, pitch, stage, sink):
        request, self._pointer_setup_action = self._pointer_setup_action, None
        action = request.path if request is not None else None
        if stage != Stage.TUTORIAL or self.pointer_setup_dir is None:
            self._pointer_setup = None
            return None
        if action is None and self._pointer_setup is None:
            return None

        from manux.output.windows.config import WindowsOutputConfig
        from manux.output.windows.cursor import CursorMapper
        from manux.platform.monitors import ScreenLayout

        mode = self.engine.arbiter.base_mode
        if action == "start":
            monitor = (sink.screens if sink is not None else ScreenLayout.detect()).primary
            config = WindowsOutputConfig(**self.settings.section("cursor"), frame_aspect=frame.aspect)
            layout = ScreenLayout([monitor])
            self._pointer_setup = PointerSetup(
                monitor, frame.width, lambda values: CursorMapper(layout, replace(config, **values)))
            self._pointer_setup_environment = self._pointer_environment(frame, monitor, mode)
        setup = self._pointer_setup
        if setup is None:
            return None
        environment = self._pointer_environment(frame, setup.monitor, mode)
        if environment != self._pointer_setup_environment or self._pointer_profile_locked():
            setup.phase = "retry"
            setup.note = ("프로필이 이동 감도를 지정하고 있어요. 기본 프로필에서 다시 시도해 주세요."
                          if self._pointer_profile_locked() else "카메라나 조작 환경이 바뀌었어요. 다시 측정해 주세요.")
            return setup.view()
        point = None
        if hand is not None and pitch is not None:
            cfg = self.settings_for("pinch")
            raw = palm_anchor_point(hand.landmarks, cfg["cursor_anchor_ratio"])
            x, _ = corrected_point(raw, frame.aspect, cfg.get("palm_axes"))
            point = (x, pitch)
        if action == "capture" and point is not None:
            setup.capture()
        elif action == "adjust":
            setup.adjust(request.values)
        elif action == "retest" and setup.phase in ("aim", "review", "preview"):
            setup.begin_aim()
        elif action == "apply" and setup.phase == "review" and setup.can_apply:
            self._save_pointer_setup(environment, setup)
        setup.step(frame.t, point)
        return setup.view()

    def _save_pointer_setup(self, environment, setup):
        key = sha256(repr(environment).encode("utf-8")).hexdigest()
        profiles = read_profiles(self.settings.get(SETTING_KEY))
        profiles.pop(key, None)
        profiles[key] = dict(setup.values)
        serialized = write_profiles(profiles)
        candidate = Settings(self.settings.schema, self.settings.stored())
        candidate.set(SETTING_KEY, serialized)
        try:
            settings_store.save(candidate, self.pointer_setup_dir)
        except OSError:
            setup.note = "설정을 저장하지 못했어요. 적용을 다시 누르거나 건너뛰어 주세요."
            return
        self.settings.set(SETTING_KEY, serialized)
        self.engine.pause_for_settings()
        self._pointer_config_signature = None
        setup.phase = "saved"

    # ------------------------------------------------------------------ tracking test (UI thread calls these)
    def start_tracking_test(self) -> str:
        """Ask for a new test (starts paused). Returns why it cannot start, or "" when asked."""
        if self.engine.sinks.get(OutputTarget.WINDOWS) is None:
            return "Windows 입력이 꺼져 있어 트래킹을 측정할 수 없습니다."
        with self._lock:
            self._tracking_request = "start"
        return ""

    def close_tracking_test(self) -> None:
        with self._lock:
            self._tracking_request = "close"

    def _tracking_requests(self) -> None:
        with self._lock:
            request, self._tracking_request = self._tracking_request, None
        sink = self.engine.sinks.get(OutputTarget.WINDOWS)
        if request == "start" and self.tracking is None and sink is not None:
            sink.release_all("tracking test")
            self.tracking = TrackingTest(sink.screens.primary)
            self.set_override("pointer", "tracking_only", True)     # follow the hand, never click
        elif request == "close" and self.tracking is not None:
            self.tracking = None
            self.set_override("pointer", "tracking_only", False)

    def _tracking_step(self, snap, hand, detect_ms: float) -> Optional[Dict[str, Any]]:
        test = self.tracking
        sink = self.engine.sinks.get(OutputTarget.WINDOWS)
        if test is None or sink is None:
            return None
        pos = sink.cursor_pos
        test.update(snap.t, pos, hand=hand is not None, active=snap.control_on and snap.mode == "pointer",
                    fps=float(self.source.fps), detect_ms=detect_ms)
        if test.done and test.saved is None and not test.result_text:
            self._save_tracking(test)
        return tracking_view(test, pos)

    def _save_tracking(self, test: TrackingTest) -> None:
        monitor = test.monitor
        run = {"monitor_px": f"{monitor.width}x{monitor.height}", "camera_mode": self.source.mode_text(),
               "hand_crop": self.use_crop, "control_hand": self.control_hand, "app": "manux-v0.4",
               "sensitivity_pct": round(100 * self.pointer_gain()),
               **self.run_info()}
        text = test.summary_text()
        if self.log_dir is None:
            test.result_text = text
            return
        try:
            test.saved = test.save_csv(self.log_dir, run)
            text += f" · {test.saved.name}"
        except OSError as exc:
            text += f" · 저장 실패: {exc}"
        test.result_text = text
        self.recent = (result_line({**run, **test.summary()}, time.strftime("%m/%d %H:%M")),) + self.recent[:4]

    # ------------------------------------------------------------------ dataset recording (UI thread calls these)
    def set_recording(self, mode: str, focus="", plan: Optional[Mapping[str, Any]] = None,
                      *, profile="local", split="train", preset="pinch") -> None:
        identity = RecordingIdentity(profile.lower(), preset, split)
        if mode not in ("off", "guided", "free"):
            raise ValueError("invalid recording mode")
        if not isinstance(focus, str):
            focus = tuple(task.key for task in tasks_for(focus))
        with self._lock:
            self._rec_identity = identity
            # One explicit start = one collection bundle, not one frame/set/file.
            self._rec_session_id = str(uuid4()) if mode != "off" else None
            _, _, _, adv, _ = self._rec_want
            self._rec_want = (mode, focus, copy.deepcopy(dict(plan)) if plan is not None else None, adv, False)

    def advance_recording(self) -> None:
        with self._lock:
            mode, focus, plan, adv, paused = self._rec_want
            self._rec_want = (mode, focus, plan, adv + 1, paused)

    def set_recording_paused(self, paused: bool) -> None:
        with self._lock:
            mode, focus, plan, adv, _ = self._rec_want
            self._rec_want = (mode, focus, plan, adv, bool(paused))

    def mark_recording(self) -> None:
        with self._lock:
            self._rec_marks += 1

    def _recording_step(self, now: float, frame: Optional[FrameInfo], hand: Optional[HandObservation]) -> None:
        with self._lock:
            mode, focus, plan, adv, paused = self._rec_want
            identity = self._rec_identity
            session_id = self._rec_session_id
        if (mode, focus, identity, session_id) != (
                self._rec_mode, self._rec_focus, self._rec_identity_active, self._rec_session_id_active):
            self._stop_recording(now)
            self._rec_mode, self._rec_focus = mode, focus
            self._rec_identity_active = identity
            self._rec_session_id_active = session_id
            if mode in ("guided", "free") and self.recordings_dir is not None:
                try:
                    self.engine.commands.put(SetBusy("recording", True))
                    meta: Dict[str, Any] = {
                        "camera": {"mode": [frame.width, frame.height, float(self.source.fps)] if frame else None,
                                   "mirror": frame.mirrored if frame is not None else bool(self.settings.get("camera.mirror"))},
                        "frame_aspect": round(float(frame.aspect), 5) if frame is not None else 0.5625,
                        "control_hand": self.control_hand,
                        "focus": focus,
                        "person": identity.profile, "profile": identity.profile,
                        "preset": identity.preset, "split": identity.split,
                        "session_id": session_id,
                    }
                    if mode == "guided":
                        self._guided = GuidedSession(tasks_for(focus), plan=plan, settle=0.35)
                        meta["guided"] = self._guided.describe()
                        if self._guided.plan:
                            meta["capture_plan"] = self._guided.plan
                    folder = self.recordings_dir / f"profile-{identity.profile}" / identity.preset / identity.split
                    self._rec = SessionRecorder(folder, mode, meta, now=now, alias=identity.profile,
                                                stamp=time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000000:09d}")
                    self._rec_adv_seen = adv
                except (OSError, ValueError):
                    self._rec, self._guided = None, None
                    self._rec_mode = "off"
                    self.engine.commands.put(SetBusy("recording", False))
                    self.engine.commands.put(SetControl(False))
                    with self._lock:
                        self._rec_want = ("off", "", None, adv, False)
                    self._rec_note = "녹화를 시작하지 못했습니다. 저장 공간·권한과 녹화 설정을 확인하세요."
                    self._rec_note_until = now + 8.0
        if self._guided is not None:
            self._guided.paused = paused
            if adv != self._rec_adv_seen:
                self._rec_adv_seen = adv
                self._guided.advance(now)
                if self._guided.current is not None:
                    _, spec = self._guided.current
                    self.engine.commands.put(RequestMode("motion" if spec.direction else None))
            available = bool(hand is not None and hand.world is not None) if self._guided.plan else (frame is not None)
            if self._guided.update(now, available=available) and self._rec is not None:
                self._rec.event(now, "set_done", {"set": self._guided.index - 1})

    def _record_frame(self, now: float, hand: Optional[HandObservation], snap, pose=None) -> None:
        if self._rec is None:
            return
        with self._lock:
            marks = self._rec_marks
        while self._rec_marks_seen < marks:
            self._rec_marks_seen += 1
            self._rec.event(now, "mark")
        label = self._guided.label(now) if self._guided is not None else {"target": "none", "phase": "use"}
        active = [(gid, phase) for gid, phase in (snap.intents if snap is not None else ()) if phase != "end"]
        rule_gid = next((gid for gid, phase in active if phase in ("begin", "tap")), active[0][0] if active else None)
        rule_act = snap.results[0][0] if (snap is not None and snap.results) else "none"
        rule = {"action": rule_act, "gesture": rule_gid}
        sink = self.engine.sinks.get(OutputTarget.WINDOWS)
        cursor = getattr(sink, "cursor_pos", None)
        self._rec.frame(now, hand, label=label, rule=rule, cursor=cursor, pose=pose)
        if self._guided is not None and self._guided.done:
            self._stop_recording(now)
            with self._lock:
                self._rec_want = ("off", "", None, self._rec_adv_seen, False)
            self._rec_mode = "off"

    def _stop_recording(self, now: float) -> None:
        if self._rec is None:
            return
        rec = self._rec
        path = rec.close()
        self._rec, self._guided = None, None
        self.engine.commands.put(SetBusy("recording", False))
        self.engine.commands.put(RequestMode(None))
        kept = ", ".join(f"{k} {v}" for k, v in sorted(rec.counts.items()))
        self._rec_note = f"녹화 저장: {path.name} ({rec.frames}프레임: {kept}, 표시 {rec.marks})"
        self._rec_note_until = now + 8.0

    def _rec_view(self, now: float) -> Optional[Dict[str, Any]]:
        if self._rec is None:
            if now < self._rec_note_until:
                return {"mode": "off", "prompt": "", "sub": self._rec_note, "progress": 0.0, "overall": 0.0,
                        "waiting": False, "frames": 0}
            return None
        if self._guided is not None:
            v = dict(self._guided.view(now))
        else:
            v = {"prompt": "평소처럼 쓰세요 · 이상하면 F", "sub": "", "progress": 0.0, "overall": 0.0, "waiting": False}
        v.update(mode=self._rec_mode, frames=self._rec.frames, file=self._rec.path.name, marks=self._rec.marks)
        v["sub"] = (v.get("sub") or "") + f"  ·  {self._rec.frames}프레임 녹화 중"
        return v

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="manux-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self._stop_recording(time.perf_counter())
        except Exception:
            pass
        self.engine.shutdown()
        if self._pose_preview is not None:
            self._pose_preview.shutdown()
            self._pose_preview = None
        self.source.close()
        if self._thread is None or not self._thread.is_alive():
            self._close_pose()

    def _close_pose(self):
        if self._pose_tracker is not None:
            self._pose_tracker.close()
            self._pose_tracker = None

    def latest(self) -> Tuple[int, ViewFrame]:
        with self._lock:
            return self._seq, self._latest

    def _publish(self, view: ViewFrame) -> None:
        with self._lock:
            self._latest = view
            self._seq += 1

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:                    # never let the runtime thread die silently
                self._publish(ViewFrame(self._latest.snapshot, message=f"실행 오류: {exc!r}", camera_ok=False))
                time.sleep(0.2)
        self._close_pose()

    def step(self) -> None:
        self._drain_runtime_requests()
        self._tracking_requests()
        self.reload_model()
        self._sync_control_model()
        if self._stop.is_set():
            return
        item = self.source.read(self.control_hand, self.use_crop)
        if item is None:
            now = time.perf_counter()
            self._recording_step(now, None, None)
            self.engine.frame_missing(now)
            if self._pose_preview is not None:
                self._pose_preview.frame_missing(now)
            if getattr(self.source, "error", ""):
                self._publish(ViewFrame(self._latest.snapshot, camera_ok=False, message=self.source.error,
                                        model_status=self.model_status, rec=self._rec_view(now)))
                time.sleep(0.05)
            return
        frame, image, hand, hands, detect_ms = item
        self._recording_step(frame.t, frame, hand)
        sink = self.engine.sinks.get(OutputTarget.WINDOWS)
        pitch = palm_pitch(hand.world) if hand is not None else None
        if sink is not None and hasattr(sink, "set_palm_pitch"):
            sink.set_palm_pitch(pitch)
        self._configure_cursor_for_frame(frame, sink)
        practice_sink = self.engine.sinks.get(OutputTarget.PRACTICE)
        if practice_sink is not None and practice_sink.config is not None:
            practice_sink.update_config(replace(practice_sink.config, frame_aspect=frame.aspect))
        snap = self.engine.step(frame, hand)
        pointer_setup = self._pointer_setup_frame(frame, hand, pitch, snap.stage, sink)
        rule_preview = None
        if {"enrolling", "recording"}.intersection(snap.busy):
            if self._pose_preview is None:
                self._pose_preview = build_engine(self.settings, self.engine.arbiter.base_mode)
            self._pose_preview.commands.put(SetBaseMode(self.engine.arbiter.base_mode))
            rule_preview = self._pose_preview.step(frame, hand)
        elif self._pose_preview is not None:
            self._pose_preview.shutdown()
            self._pose_preview = None
        self._record_frame(frame.t, hand, rule_preview or snap, self._pose_step(image, frame.t))
        tracking = self._tracking_step(snap, hand, float(detect_ms))
        self._frames += 1
        if image is not None and self._frames % 10 == 1:
            level = int(brightness(image))
            self._metrics = (self.source.mode_text(), level,
                             level < float(self.settings.get("camera.low_light_threshold")))
        elif image is None:
            self._metrics = (self.source.mode_text(), -1, False)
        actions = tuple(a for a, _phase, reason in snap.results if reason == "ok")
        practice = self.engine.sinks.get(OutputTarget.PRACTICE)
        if snap.output is OutputTarget.PRACTICE:
            if not self._practice_active or "practice:reset" in snap.events:
                self._practice_events.clear()
                self._practice_epoch += 1
            self._practice_active = True
            discrete = tuple(a for a, phase, reason in snap.results if reason == "ok" and phase in ("tap", "begin"))
            mode_event = any(e.startswith("mode:") and "refused:" not in e for e in snap.events)
            if discrete or mode_event:
                self._practice_event_seq += 1
                self._practice_events.append(PracticeEvent(self._practice_event_seq, frame.t, discrete,
                    practice.cursor_position, practice.precision, snap.mode == "motion", snap.has_hand))
        else:
            self._practice_events.clear()
            self._practice_active = False
        guides, anchor, moving = self.pinch_preview.step(
            hand if self.want_image and snap.mode == "pinch" else None, self.settings_for("pinch"))
        self._publish(ViewFrame(
            snapshot=snap, image=image if self.want_image else None, hands=hands,
            landmarks=hand.landmarks if hand is not None else None,
            world=hand.world if hand is not None else None,
            mirrored=frame.mirrored, aspect=frame.aspect,
            camera_mode=self._metrics[0], brightness=self._metrics[1], low_light=self._metrics[2],
            fps=float(self.source.fps), detect_ms=float(detect_ms), actions=actions,
            model_status=self._model_view_status(), tracking=tracking, recent=self.recent,
            rec=self._rec_view(frame.t), pose_status=self.pose_status,
            spatial_guard_enabled=bool(self.spatial_guard and self.spatial_guard.enabled),
            preview_guides=guides, preview_anchor=anchor, preview_moving=moving, pointer_setup=pointer_setup,
            control_hand=hand.handedness if hand is not None else self.control_hand,
            preview_gesture=snap.intents[0][0] if snap.intents else "",
            practice_cursor=practice.cursor_position if snap.output is OutputTarget.PRACTICE else None,
            practice_precision=bool(practice.precision) if snap.output is OutputTarget.PRACTICE else False,
            practice_events=tuple(self._practice_events), practice_epoch=self._practice_epoch,
            rule_preview=rule_preview, mlp_badge=self._mlp_badge(),
            frame_age_ms=max(0.0, (time.perf_counter() - frame.t) * 1000.0)))

    def _pose_step(self, image, now):
        if not self.settings.get("tracking.pose_enabled"):
            self._close_pose()
            self._pose_failed = False
            self.pose_status = "팔·어깨 기록 꺼짐"
            return None
        if self._rec is None:
            self.pose_status = "팔·어깨 기록 켜짐 · 다음 녹화부터 추가 처리"
            return None
        if self._pose_failed or image is None or self._frames % self.settings.get("tracking.pose_every_n_frames"):
            return None
        try:
            if self._pose_tracker is None:
                from manux.tracking.pose import PoseTracker
                self._pose_tracker = PoseTracker(self.pose_model)
            pose = self._pose_tracker.detect(image, now)
            self.pose_status = "팔·어깨 기록 중" if pose else "팔·어깨가 화면에 보이지 않습니다"
            return pose
        except Exception:
            self._pose_failed = True
            self.pose_status = "팔·어깨 모델 사용 불가 · 손 관절 녹화는 계속합니다"
            return None

    def _mlp_badge(self):
        guard = self.spatial_guard
        if guard is None or self.spatial_feature is None or self.spatial_feature.model is None:
            return "MLP 미적용 (모델 없음)"
        if not guard.enabled:
            return "MLP 꺼짐"
        ev = self.spatial_feature.evidence
        now = f"{BY_KEY[ev.action].name if ev.action in BY_KEY else ev.action} {ev.confidence:.0%}" if ev else "대기"
        return f"MLP 적용 중 · {now} · 오클릭 차단 {guard.vetoed_count} · 보강 클릭 {guard.boosted_count}"

    def _model_view_status(self):
        mlp_status = ""
        if self.spatial_guard and self.spatial_guard.enabled and self.spatial_feature and self.spatial_feature.model:
            ev = self.spatial_feature.evidence
            if ev is not None:
                action_name = ev.action
                title = BY_KEY[action_name].name if action_name in BY_KEY else action_name
                veto_info = f" (오작동 차단 {self.spatial_guard.vetoed_count}회)" if self.spatial_guard.vetoed_count > 0 else ""
                mlp_status = f" · MLP 보조: {title} {ev.confidence:.0%}{veto_info}"
            else:
                mlp_status = " · MLP 보조 가동 중"

        for feature in self.engine.features:
            prediction = getattr(feature, "last_prediction", None) if feature.id == "tcn" else None
            if prediction is not None:
                name, confidence = prediction
                gesture = feature.card.gestures.get(name)
                mode = {"rules": "규칙", "shadow": "시험", "apply": "적용"}.get(feature.policies.get(gesture), "중립")
                title = BY_KEY[name].name if name in BY_KEY else name
                return f"{self.model_status} · TCN {mode} 예측: {title} {confidence:.0%}{mlp_status}"
        return f"{self.model_status}{mlp_status}"

    def request(self, command: RuntimeRequest):
        if self._stop.is_set():
            return False
        self._runtime_requests.put(command)
        return True

    def _drain_runtime_requests(self):
        while not self._spatial_updates.empty():
            model = self._spatial_updates.get_nowait()
            if self.spatial_feature is not None:
                self.engine.prepare_model_reload()
                self.spatial_model_path = None
                self.spatial_feature.set_model(model)
                if self.spatial_guard is not None:
                    self.spatial_guard.enabled = bool(model is not None)
                    self.spatial_guard.reset("settings")
                filters = [f for f in self.engine.filters if f.id != "spatial_pose_guard"] + list(self._aux_filters())
                self.engine.replace_pipeline(self.engine.features, filters, self.engine.router, self.engine.gesture_sources)
        while not self._runtime_requests.empty():
            command = self._runtime_requests.get_nowait()
            if command.kind == "pointer_setup":
                if command.path == "skip":
                    self._pointer_setup = self._pointer_setup_action = None
                    self.engine.pause_for_settings()
                else:
                    self._pointer_setup_action = command
            if command.kind == "sensitivity":
                self._apply_sensitivity(command)
            if command.kind == "advanced":
                self._apply_advanced(command)
            if command.kind == "spatial_guard":
                try:
                    self.engine.prepare_model_reload()
                    model = self.spatial_feature.model
                    if command.path == "on" and model is None and self.spatial_model_path:
                        model = SpatialMlpModel.load(self.spatial_model_path)
                    if command.path == "on" and model is None: raise ValueError("no pose model")
                    self.spatial_feature.set_model(model)
                    self.spatial_guard.enabled = command.path == "on"
                    filters = [f for f in self.engine.filters if f.id != "spatial_pose_guard"] + list(self._aux_filters())
                    self.engine.replace_pipeline(self.engine.features, filters, self.engine.router, self.engine.gesture_sources)
                except (ValueError, OSError, KeyError):
                    self.model_status = "보조 차단 모델을 읽지 못했습니다 · 기존 판정 유지"
            if command.kind == "next_monitor":
                sink = self.engine.sinks.get(OutputTarget.WINDOWS)
                if not self.engine.state.busy and self._rec_want[0] == "off" and hasattr(sink, "next_monitor"):
                    sink.next_monitor()
            if command.kind == "tcn_toggle":
                self.tcn_enabled = command.path == "on"
                if self.current_model is None:
                    self.engine.replace_pipeline(self.engine.features,
                        self._configured_filters if self.tcn_enabled else self._aux_filters(), self.engine.router, {})
            if command.kind in ("tcn_policy", "tcn_toggle") and self.current_model is not None:
                parts = pipeline_parts(self.current_model, self.engine.router.bindings,
                                       self._model_modes(self.current_model))
                self.engine.replace_pipeline(parts[0], self._aux_filters(), parts[1], parts[2])
                self.model_status = ("TCN 동작별 설정 갱신됨 · 제어를 다시 켜세요" if self.tcn_enabled
                                     else "TCN 꺼짐 · 기존 규칙 사용")
            if command.kind == "preview_model":
                try:
                    model = load_model(Path(command.path), FEATURE_VERSION, FEATURES)
                    self.engine.prepare_model_reload()
                    modes = {g: "shadow" if self.tcn_enabled else "rules" for g in model.card.gestures.values()}
                    parts = pipeline_parts(model, self.engine.router.bindings, modes)
                    self.engine.replace_pipeline(parts[0], self._aux_filters(), parts[1], parts[2])
                    self.current_model, self.preview_only = model, True
                    self.model_status = "이전 모델 시험 중 · 별도 평가 전에는 마우스 동작에 적용하지 않습니다"
                except (ModelError, OSError, ValueError):
                    self.model_status = "가져온 모델을 읽지 못했습니다 · 기존 판정 유지"
            if command.kind == "activate_model":
                previous = self.model_store, self._model_token, self._attempted_model_token
                self.model_store = Path(command.path)
                self._attempted_model_token = None
                self._model_token = None
                if not self.reload_model(force=True):
                    self.model_store, self._model_token, self._attempted_model_token = previous

    def _apply_sensitivity(self, command):
        from manux.settings.sensitivity import prepare
        revision = self.sensitivity_result[0] + 1
        self.engine.pause_for_settings()
        try:
            values = prepare(self.settings, command.values)
            if self._pointer_environment_key and any(key.startswith("cursor.") for key in values):
                profiles = read_profiles(self.settings.get(SETTING_KEY))
                if profiles.pop(self._pointer_environment_key, None) is not None:
                    values[SETTING_KEY] = write_profiles(profiles)
            candidate = Settings(self.settings.schema, self.settings.stored())
            for key, value in values.items():
                if not candidate.set(key, value):
                    raise ValueError("민감도 값을 저장할 수 없습니다.")
            settings_store.save(candidate, Path(command.path))
        except (OSError, ValueError) as exc:
            self.sensitivity_result = (revision, False, f"저장 실패 · 기존 설정 유지: {exc}")
            return
        for key, value in values.items():
            self.settings.set(key, value)
        self._configure_cursor_sinks()
        self._pointer_config_signature = None
        self.pinch_preview.step(None, self.settings_for("pinch"))
        self.sensitivity_result = (revision, True, "저장·적용 완료 · 제어 시작을 누르면 새 감도로 동작합니다.")

    def _apply_advanced(self, command):
        from manux.settings.advanced import prepare
        from manux.settings.store import save
        revision = self.advanced_result[0] + 1
        self.engine.pause_for_settings()
        try:
            values = prepare(self.settings, command.values)
            candidate = Settings(self.settings.schema, self.settings.stored())
            for key, value in values.items():
                if not candidate.set(key, value):
                    raise ValueError("설정 값을 저장할 수 없습니다.")
            save(candidate, Path(command.path))
        except (OSError, ValueError) as exc:
            self.advanced_result = (revision, False, f"저장 실패 · 기존 설정 유지: {exc}")
            return
        for key, value in values.items():
            self.settings.set(key, value)
        self._configure_cursor_sinks()
        self.engine.config.hand_lost_reset = self.settings.get("app.hand_lost_reset")
        self.engine.arbiter.quiet = self.settings.get("motion.enter_quiet")
        self.pinch_preview.step(None, self.settings_for("pinch"))
        self.advanced_result = (revision, True, "저장·적용 완료 · 제어를 다시 켜세요.")

    def _model_modes(self, model):
        if not self.tcn_enabled:
            return {g: "rules" for g in model.card.gestures.values()}
        if self.preview_only:
            return {g: "shadow" for g in model.card.gestures.values()}
        return self.policy_store.modes(model.card.raw) if self.policy_store else None

    def _aux_filters(self):
        return (self.spatial_guard,) if self.spatial_guard is not None and self.spatial_guard.enabled else ()

    def reload_model(self, *, force=False) -> bool:
        """Runtime thread only, at a frame boundary. A bad update keeps the live recognizer."""
        now = time.perf_counter()
        if self._stop.is_set() or self.model_store is None or not force and now < self._next_model_check:
            return False
        self._next_model_check = now + 1.
        try:
            token = active_token(self.model_store)
            if token == self._model_token or token == self._attempted_model_token:
                return False
            self._attempted_model_token = token
            self.engine.prepare_model_reload()
            model = load_active(self.model_store, FEATURE_VERSION, FEATURES)
            modes = (self.policy_store.modes(model.card.raw) if self.policy_store else None) if self.tcn_enabled else {
                g: "rules" for g in model.card.gestures.values()}
            parts = pipeline_parts(model, self.engine.router.bindings, modes)
            if self._stop.is_set() or active_token(self.model_store) != token:
                return False                   # a newer installation superseded this load
            self.engine.replace_pipeline(parts[0], self._aux_filters(), parts[1], parts[2])
            self._model_token = token
            self.current_model = model
            self.preview_only = False
            self.model_status = "TCN 모델 갱신됨 · 학습센터에서 동작별 시험/적용 확인"
            from manux.ml.backends import describe
            self.model_status += " · " + describe(model.predictor)["actual"]
            return True
        except (ModelError, OSError, ValueError) as error:
            self.model_status = f"모델 갱신 실패 · 기존 모델 유지 ({getattr(error, 'text', '정보 확인 필요')})"
            return False


def build(settings: Settings, source, *, windows: bool = True, tcn_model=None, tcn_store=None,
          tcn_mode="gate", binding_overrides=None, policy_store=None, shadow_model=None, spatial_pose_model=None) -> DesktopRuntime:
    if shadow_model is not None:
        if tcn_model is not None or tcn_store is not None:
            raise ValueError("a shadow candidate cannot replace an explicitly selected model")
        tcn_model, tcn_mode = shadow_model, "direct"
    if tcn_store is not None and (tcn_model is not None or tcn_mode != "direct"):
        raise ValueError("a model store requires direct mode and cannot be combined with a model path")
    if tcn_mode == "direct" and tcn_model is None and tcn_store is None:
        raise ValueError("direct mode needs a model or model store")
    filters, direct, token, status = (), None, None, ""
    if tcn_mode == "direct":
        token = active_token(tcn_store) if tcn_store is not None else None
        direct = (load_active(tcn_store, FEATURE_VERSION, FEATURES) if tcn_store is not None
                  else load_model(Path(tcn_model), FEATURE_VERSION, FEATURES))
        if tcn_store is not None and active_token(tcn_store) != token:
            raise ValueError("active model changed while starting; retry")
        if direct.card.kind == "personal" and tcn_store is None and windows and shadow_model is None:
            raise ValueError("personal direct Windows input requires a verified --tcn-store; use --no-windows-input for file tests")
        status = "이전 모델 시험 중 · 별도 평가 후 적용 가능" if shadow_model is not None else "TCN 직접 판정"
        from manux.ml.backends import describe
        status += " · " + describe(direct.predictor)["actual"]
    elif tcn_model is not None:
        eligible = {gid for module, _ in _FEATURES for gid in module.DEFAULT_BINDINGS
                    if module.GESTURE_PHASES[gid] & {"tap", "begin"}}
        gate = tcn_gate.create_gate(Path(tcn_model), settings.section("tcn_gate"), eligible=eligible)
        filters = (gate.gate,) if gate.gate is not None else ()
        status = "TCN 확인기" if filters else f"TCN 확인기 꺼짐 · {gate.text}"
        if filters:
            from manux.ml.backends import describe
            status += " · " + describe(gate.gate.predictor)["actual"]
    spatial_pose_feature = SpatialPoseFeature(spatial_pose_model, mode="assist")
    spatial_guard = SpatialPoseGuard(spatial_pose_feature, enabled=bool(spatial_pose_model is not None), settings=settings)
    filters = tuple(filters) + (spatial_guard,)
    engine = build_engine(settings, "pinch", filters, direct_model=direct, binding_overrides=binding_overrides,
                          policies=({g: "shadow" for g in direct.card.gestures.values()} if shadow_model is not None
                                    else policy_store.modes(direct.card.raw) if policy_store and direct else None))
    if windows:
        from manux.output.windows import Win32Backend, WindowsOutputSink
        from manux.output.windows.config import WindowsOutputConfig
        from manux.platform.monitors import ScreenLayout
        engine.sinks[OutputTarget.WINDOWS] = WindowsOutputSink(
            Win32Backend(), ScreenLayout.detect(), WindowsOutputConfig(**settings.section("cursor")))
    runtime = DesktopRuntime(engine, source, settings, model_store=tcn_store, model_token=token, model_status=status,
                             policy_store=policy_store, current_model=direct)
    runtime.spatial_feature = spatial_pose_feature
    runtime.spatial_guard = spatial_guard
    runtime.preview_only = shadow_model is not None
    return runtime


def make_pose_studio(data_dir: Path,
                     on_model_updated: Optional[Callable[[Optional[SpatialMlpModel]], None]] = None,
                     snapshots_dir: Optional[Path] = None,
                     models_dir: Optional[Path] = None) -> PoseSnapshotStudio:
    """Create a PoseSnapshotStudio wired to the 96-dim SpatialFeatureExtractor and 4-second SpatialMlpModel."""
    import numpy as np

    active_mlp: List[Optional[SpatialMlpModel]] = [None]

    def update_model(model: Optional[SpatialMlpModel]) -> None:
        active_mlp[0] = model
        if on_model_updated is not None:
            on_model_updated(model)

    model_stamp = [None]

    def predict_fn(landmarks: Any, _world: Any) -> Optional[Dict[str, Any]]:
        model_file = studio.models_dir / (studio.active_model_id or "spatial_pose_mlp.json")
        stamp = (str(model_file), model_file.stat().st_mtime_ns) if model_file.is_file() else None
        if stamp != model_stamp[0]:
            try: active_mlp[0] = SpatialMlpModel.load(model_file) if stamp else None
            except (ValueError, OSError, KeyError): active_mlp[0] = None
            model_stamp[0] = stamp
        mlp = active_mlp[0]
        if mlp is None:
            return None
        from manux.perception.pose_angles import pose_angles
        feats = pose_angles(_world) if mlp.feature_version == "pose-angles-v1" else extract_spatial_features(landmarks)
        if feats is None:
            return None
        return mlp.predict(feats)

    def train_fn(samples: List[Dict[str, Any]], out_path: Path) -> Dict[str, Any]:
        class_to_idx = {c: i for i, c in enumerate(ACTION_IDS)}
        xs: List[np.ndarray] = []
        ys: List[int] = []
        ws: List[float] = []
        for s in samples:
            raw_lm = s.get("rawLandmarks")
            gt = s.get("groundTruthAction") or s.get("action")
            if not raw_lm or gt not in class_to_idx:
                continue
            feats = extract_spatial_features(raw_lm)
            if feats is None:
                continue
            xs.append(feats)
            ys.append(class_to_idx[gt])
            # Emphasize discrepancy (failure boundary) samples so the MLP rapidly corrects blind spots!
            ws.append(1.8 if s.get("isFailureSample") else 1.0)
        if len(xs) < 2:
            raise ValueError("유효한 3D 관절 표본이 2개 미만입니다.")
        from manux.data.pose_studio import pose_training_partition
        tx, ty, tw, validation = pose_training_partition(np.stack(xs), np.array(ys, dtype=np.int64), np.array(ws, dtype=np.float64))
        model, metrics = train_spatial_mlp(
            tx, ty, tw, validation_data=validation,
            classes=ACTION_IDS,
            feature_version=SPATIAL_FEATURE_VERSION,
        )
        from manux.data.pose_studio import summarize_pose_fit
        metrics = summarize_pose_fit(model, metrics, np.stack(xs), np.array(ys))
        model.save(out_path)
        update_model(model)
        return {
            "train_accuracy": metrics.train_accuracy,
            "test_accuracy": metrics.test_accuracy,
            "total_samples": len(xs),
            "train_samples": metrics.train_samples,
            "test_samples": metrics.test_samples,
            "elapsed_ms": metrics.elapsed_ms,
            "per_class": dict(metrics.per_class),
            "trained_at": metrics.trained_at,
        }

    studio = PoseSnapshotStudio(
        data_dir,
        train_fn=train_fn,
        predict_fn=predict_fn,
        load_model_fn=SpatialMlpModel.load,
        on_model_updated=update_model,
        snapshots_dir=snapshots_dir,
        models_dir=models_dir,
    )
    if studio.active_model is not None:
        update_model(studio.active_model)
    return studio


def default_hand_model(paths: AppPaths) -> Path:
    return paths.hand_model


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MANUX v0.4 desktop app")
    parser.add_argument("--replay", type=Path, help="play a recording instead of the camera")
    parser.add_argument("--source", help="camera index or stream URL")
    parser.add_argument("--hand-model", type=Path)
    parser.add_argument("--tcn-model", type=Path)
    parser.add_argument("--tcn-store", type=Path, help="verified personal model store (hot reload)")
    parser.add_argument("--tcn-mode", choices=("gate", "direct"), default="gate")
    parser.add_argument("--bindings", type=Path, help="gesture -> action bindings JSON")
    parser.add_argument("--no-windows-input", action="store_true", help="never send real input (UI check)")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--ui", choices=("studio", "standard"), help="UI layout; remembered for the next launch")
    parser.add_argument("--screenshot", type=Path, help="save the window to this PNG after a few seconds, then quit")
    parser.add_argument("--self-check", type=Path, metavar="OUT.json",
                        help="check the bundled model, MediaPipe, OpenCV and Qt, write the result, then quit")
    parser.add_argument("--train-device", choices=("auto","cpu","cuda","xpu"))
    parser.add_argument("--inference-device", choices=("auto","cpu","cuda","openvino-cpu","openvino-gpu","openvino-npu"))
    args = parser.parse_args(argv)
    paths = resolve()
    from manux.platform.updater import Updater, read_build_info
    info = read_build_info(paths.app_dir)
    if args.self_check is not None:
        return self_check(paths, args.hand_model or default_hand_model(paths), info, args.self_check)
    updater = Updater(info, paths.frozen)
    updater.start()
    settings = load(schema(), paths.config_dir)
    from manux.app import configure_acceleration
    try: configure_acceleration(args, settings)
    except ValueError as error: parser.error(str(error))
    policy_store = GesturePolicies(paths.config_dir / "tcn-policies.json")
    default_store = paths.data_dir / "models" / "personal"
    if args.tcn_model is None and args.tcn_store is None and (default_store / "active.json").is_file():
        args.tcn_store, args.tcn_mode = default_store, "direct"
    preview = pending_model(paths.data_dir) if args.tcn_model is None and args.tcn_store is None else None
    try:
        source = ReplayInput(args.replay) if args.replay else CameraInput(
            settings, args.hand_model or default_hand_model(paths), args.source)
    except (OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    models_dir = paths.spatial_models_dir
    bundled_models_dir = paths.app_dir / "models" / "spatial_mlp"
    active_cfg = models_dir / "active_model.json"
    if not active_cfg.is_file() and (bundled_models_dir / "active_model.json").is_file():
        active_cfg = bundled_models_dir / "active_model.json"
    active_model_file = "spatial_pose_mlp.json"
    if active_cfg.is_file():
        try:
            active_model_file = json.loads(active_cfg.read_text("utf-8")).get("active_model_id", active_model_file)
        except Exception:
            pass
    spatial_model_path = models_dir / active_model_file
    if not spatial_model_path.is_file():
        spatial_model_path = bundled_models_dir / active_model_file
    if not spatial_model_path.is_file():
        spatial_model_path = models_dir / "spatial_pose_mlp.json"
    if not spatial_model_path.is_file():
        spatial_model_path = bundled_models_dir / "spatial_pose_mlp.json"
    if not spatial_model_path.is_file():
        for candidate_dir in (models_dir, bundled_models_dir):
            if candidate_dir.is_dir():
                candidates = sorted(f for f in candidate_dir.glob("*.json") if f.name != "active_model.json")
                if candidates:
                    spatial_model_path = candidates[0]
                    break

    init_spatial_model: Optional[SpatialMlpModel] = None
    if spatial_model_path.is_file():
        try:
            init_spatial_model = SpatialMlpModel.load(spatial_model_path)
        except Exception:
            init_spatial_model = None

    try:
        runtime = build(settings, source, windows=not args.no_windows_input and sys.platform == "win32",
                        tcn_model=args.tcn_model, tcn_store=args.tcn_store, tcn_mode=args.tcn_mode,
                        binding_overrides=read_bindings(args.bindings), policy_store=policy_store, shadow_model=preview,
                        spatial_pose_model=init_spatial_model)
    except (ModelError, OSError, ValueError) as error:
        if preview is None:
            source.close()
            print(error, file=sys.stderr)
            return 1
        runtime = build(settings, source, windows=not args.no_windows_input and sys.platform == "win32",
                        binding_overrides=read_bindings(args.bindings), policy_store=policy_store,
                        spatial_pose_model=init_spatial_model)
        runtime.model_status = "가져온 모델 읽기 실패 · 학습센터에서 복구 상태를 확인하세요"
    runtime.pose_model = paths.app_dir / "models" / "pose_landmarker_lite.task"
    runtime.pointer_setup_dir = paths.config_dir
    runtime.log_dir = paths.log_dir
    runtime.spatial_model_path = spatial_model_path
    runtime.recordings_dir = paths.recordings_dir
    runtime.recent = tuple(load_recent_results(paths.log_dir))
    runtime.run_info = lambda: {"anchor": settings.get("pointer.anchor"), "version": info.label,
                                "tracker_min_detection_confidence": settings.get("tracking.min_detection_confidence"),
                                "tracker_min_tracking_confidence": settings.get("tracking.min_tracking_confidence")}
    from manux.features.pointer.workspace import pointer_axes
    from manux.platform import hotkeys
    from manux.platform.tray import QtTray
    from manux.ui.bridge import run
    tc = TrainingCenterService(paths.config_dir, paths.data_dir, paths.recordings_dir, model_path=args.tcn_model,
                               store_dir=args.tcn_store, policy_store=policy_store, on_policy=lambda: runtime.request(RuntimeRequest("tcn_policy")),
                               env_files=(Path(sys.executable).parent / ".env",) if paths.frozen else (),
                               specs=training_specs(), on_ready=lambda event: runtime.request(
                                   RuntimeRequest("activate_model", event["store"])) if event.get("store") else
                                   runtime.request(RuntimeRequest("preview_model", event["candidate"])) if event.get("candidate") else None,
                               pose_snapshots_dir=paths.pose_snapshots_dir,
                               pose_models_dir=paths.spatial_models_dir)
    tc.simulation_bindings = read_bindings(args.bindings)
    tc.simulation_config = {"simulation_mode": args.tcn_mode,
                            "simulation_model": str(args.tcn_model) if args.tcn_model else None}
    studio = make_pose_studio(paths.pose_data_dir, runtime.set_spatial_model,
                              snapshots_dir=paths.pose_snapshots_dir,
                              models_dir=paths.spatial_models_dir)
    from manux.perception.palm_axes import PalmAxesCalibration, valid_axes, palm_point
    return run(runtime, settings, paths, tray=not args.no_tray, screenshot=args.screenshot, layout=args.ui,
               services={
                   "palm_calibration": PalmAxesCalibration,
                   "valid_palm_axes": valid_axes,
                   "palm_point": palm_point,
                   "pointer_axes": pointer_axes,
                   "export_logs": lambda metrics: export_logs(paths, metrics, info.label),
                   "training_center": tc,
                   "pose_studio": studio,
                   "studio_categories": lambda: list(STUDIO_CATEGORIES),
                   "studio_actions": lambda: list(STUDIO_ACTIONS),
                   "studio_categories": lambda: list(STUDIO_CATEGORIES),
                   "studio_system_action": normalize_system_action,
               },
               hotkeys=hotkeys.GlobalHotkeys({}), hotkey_defaults=hotkeys.DEFAULTS,
               normalize_combo=hotkeys.normalize, combo_label=hotkeys.label, make_tray=QtTray)


def self_check(paths: AppPaths, model: Path, info, out: Path) -> int:
    """What a packaged build needs to run, checked without a webcam or a window (installer CI)."""
    import json
    checks: Dict[str, str] = {}

    def attempt(name: str, fn) -> None:
        try:
            checks[name] = str(fn() or "ok")
        except Exception as exc:                       # report every failure, not only the first
            checks[name] = f"FAIL: {type(exc).__name__}: {exc}"

    def landmarker():
        if not model.is_file():                        # never fall back to downloading: check the bundle
            raise FileNotFoundError(model)
        from manux.tracking.landmarker import MediaPipeLandmarker
        MediaPipeLandmarker(model).close()

    def qml():
        from PySide6 import QtQml  # noqa: F401
        from manux.ui.bridge import QML
        if not QML.exists():
            raise FileNotFoundError(QML)
        return QML.name

    attempt("hand_model", lambda: f"{model.name} {model.stat().st_size} bytes")
    attempt("opencv", lambda: __import__("cv2").__version__)
    attempt("mediapipe", landmarker)
    attempt("qt", qml)
    ok = not any(v.startswith("FAIL") for v in checks.values())
    result = {"ok": ok, "version": info.version, "pack_id": info.pack_id, "updates": info.updates,
              "frozen": paths.frozen, "app_dir": str(paths.app_dir), "checks": checks}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


def export_logs(paths: AppPaths, metrics, version: str = "0.4.0.dev0") -> Path:
    """Zip the user's logs and settings (no recordings, no images) into Downloads; returns the file."""
    import json
    import zipfile
    out_dir = Path.home() / "Downloads"
    out_dir = out_dir if out_dir.is_dir() else Path.home()
    target = out_dir / f"manux-logs-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, prefix in ((paths.log_dir, "logs"), (paths.config_dir, "config")):
            if folder.is_dir():
                for f in sorted(folder.rglob("*")):
                    if f.is_file() and f.suffix in (".json", ".jsonl", ".txt", ".log", ".csv") and "profiles" not in f.parts:
                        z.write(f, f"{prefix}/{f.relative_to(folder)}")
        z.writestr("info.json", json.dumps({"version": version, "camera": metrics[0],
                                            "brightness": metrics[1]}, ensure_ascii=False, indent=2))
    if sys.platform == "win32":
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(target)])
    return target


if __name__ == "__main__":
    raise SystemExit(main())
