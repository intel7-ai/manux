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
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

from manux.actions.bindings import Binding
from manux.app import _FEATURES, _camera_config, build_engine, schema, pipeline_parts, read_bindings
from manux.capture.camera import LatestFrameCamera, brightness
from manux.core.contracts import FrameInfo, HandObservation, OutputTarget
from manux.data.recording import read, replay
from manux.engine.pipeline import Engine
from manux.features import tcn_gate
from manux.ml.card import ModelError
from manux.ml.predictor import load_model
from manux.ml.store import active_token, load_active
from manux.perception.local3d import FEATURES, FEATURE_VERSION
from manux.settings.paths import AppPaths, resolve
from manux.settings.store import Settings, load
from manux.ui.view import SeenHand, ViewFrame

POINTER_MOVE = "pointer.move"


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
                 model_status="") -> None:
        self.engine = engine
        self.source = source
        self.settings = settings
        self.control_hand = settings.get("tracking.control_hand")
        self.use_crop = bool(settings.get("tracking.hand_crop"))
        self.want_image = True
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
        engine.settings_for = self.settings_for

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

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="manux-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.engine.shutdown()
        self.source.close()

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

    def step(self) -> None:
        self.reload_model()
        if self._stop.is_set():
            return
        item = self.source.read(self.control_hand, self.use_crop)
        if item is None:
            self.engine.frame_missing(time.perf_counter())
            if getattr(self.source, "error", ""):
                self._publish(ViewFrame(self._latest.snapshot, camera_ok=False, message=self.source.error,
                                        model_status=self.model_status))
                time.sleep(0.05)
            return
        frame, image, hand, hands, detect_ms = item
        sink = self.engine.sinks.get(OutputTarget.WINDOWS)
        if sink is not None and hasattr(sink, "set_frame_aspect"):
            sink.set_frame_aspect(frame.aspect)
        snap = self.engine.step(frame, hand)
        self._frames += 1
        if image is not None and self._frames % 10 == 1:
            level = int(brightness(image))
            self._metrics = (self.source.mode_text(), level,
                             level < float(self.settings.get("camera.low_light_threshold")))
        elif image is None:
            self._metrics = (self.source.mode_text(), -1, False)
        actions = tuple(a for a, _phase, _reason in snap.results)
        self._publish(ViewFrame(
            snapshot=snap, image=image if self.want_image else None, hands=hands,
            landmarks=hand.landmarks if hand is not None else None, mirrored=frame.mirrored, aspect=frame.aspect,
            camera_mode=self._metrics[0], brightness=self._metrics[1], low_light=self._metrics[2],
            fps=float(self.source.fps), detect_ms=float(detect_ms), actions=actions,
            model_status=self.model_status))

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
            parts = pipeline_parts(model, self.engine.router.bindings)
            if self._stop.is_set() or active_token(self.model_store) != token:
                return False                   # a newer installation superseded this load
            self.engine.replace_pipeline(parts[0], (), parts[1], parts[2])
            self._model_token = token
            self.model_status = "TCN 직접 판정 · 모델 갱신됨"
            return True
        except (ModelError, OSError, ValueError) as error:
            self.model_status = f"모델 갱신 실패 · 기존 모델 유지 ({getattr(error, 'text', '정보 확인 필요')})"
            return False


def build(settings: Settings, source, *, windows: bool = True, tcn_model=None, tcn_store=None,
          tcn_mode="gate", binding_overrides=None) -> DesktopRuntime:
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
        if direct.card.kind == "personal" and tcn_store is None and windows:
            raise ValueError("personal direct Windows input requires a verified --tcn-store; use --no-windows-input for file tests")
        status = "TCN 직접 판정"
    elif tcn_model is not None:
        eligible = {gid for module, _ in _FEATURES for gid in module.DEFAULT_BINDINGS
                    if module.GESTURE_PHASES[gid] & {"tap", "begin"}}
        gate = tcn_gate.create_gate(Path(tcn_model), settings.section("tcn_gate"), eligible=eligible)
        filters = (gate.gate,) if gate.gate is not None else ()
        status = "TCN 확인기" if filters else f"TCN 확인기 꺼짐 · {gate.text}"
    engine = build_engine(settings, "pinch", filters, direct_model=direct, binding_overrides=binding_overrides)
    if windows:
        from manux.output.windows import Win32Backend, WindowsOutputSink
        from manux.platform.monitors import ScreenLayout
        engine.sinks[OutputTarget.WINDOWS] = WindowsOutputSink(Win32Backend(), ScreenLayout.detect())
    return DesktopRuntime(engine, source, settings, model_store=tcn_store, model_token=token, model_status=status)


def default_hand_model(paths: AppPaths) -> Path:
    bundled = paths.app_dir / "models" / "hand_landmarker.task"
    return bundled if bundled.exists() else paths.data_dir / "models" / "hand_landmarker.task"


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
    parser.add_argument("--screenshot", type=Path, help="save the window to this PNG after a few seconds, then quit")
    args = parser.parse_args(argv)
    paths = resolve()
    settings = load(schema(), paths.config_dir)
    try:
        source = ReplayInput(args.replay) if args.replay else CameraInput(
            settings, args.hand_model or default_hand_model(paths), args.source)
    except (OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        runtime = build(settings, source, windows=not args.no_windows_input and sys.platform == "win32",
                        tcn_model=args.tcn_model, tcn_store=args.tcn_store, tcn_mode=args.tcn_mode,
                        binding_overrides=read_bindings(args.bindings))
    except (ModelError, OSError, ValueError) as error:
        source.close()
        print(error, file=sys.stderr)
        return 1
    from manux.features.pointer.workspace import pointer_axes
    from manux.platform import hotkeys
    from manux.platform.tray import QtTray
    from manux.ui.bridge import run
    return run(runtime, settings, paths, tray=not args.no_tray, screenshot=args.screenshot,
               services={"pointer_axes": pointer_axes, "export_logs": lambda metrics: export_logs(paths, metrics)},
               hotkeys=hotkeys.GlobalHotkeys({}), hotkey_defaults=hotkeys.DEFAULTS,
               normalize_combo=hotkeys.normalize, combo_label=hotkeys.label, make_tray=QtTray)


def export_logs(paths: AppPaths, metrics) -> Path:
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
        z.writestr("info.json", json.dumps({"version": "0.4.0.dev0", "camera": metrics[0],
                                            "brightness": metrics[1]}, ensure_ascii=False, indent=2))
    if sys.platform == "win32":
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(target)])
    return target


if __name__ == "__main__":
    raise SystemExit(main())
