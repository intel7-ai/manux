"""
Hand-Mouse: MediaPipe Hand Tracking Mouse Controller
- Snap & Fling: pinch Thumb + Index to catch the cursor, snap the wrist to throw it
  (distance depends on snap speed and direction). With no pinch, the wrist->palm point
  gives fine RELATIVE control with pointer acceleration ([M] switches to ABSOLUTE).
- Gestures:
  - Fist: same as no-pinch movement at 20x DPI (fast move)
  - Thumb tucked across the palm, other four fingers open (hold 0.4 s): show desktop (Win+D)
  - Thumb + Pinky: task switcher (point at a window, thumb+middle = open it, thumb+ring = cancel)
  - Thumb + Index (#4 + #8): Record & Fling (OFF by default, enable in the settings window) — draw a motion while pinched; on release the
    cursor replays that path (fast strokes amplified more), animated so the line is visible.
  - Thumb + Middle (#4 + #12): Left Click
  - Thumb + Ring (#4 + #16): Right Click
  - Thumb + Index + Middle (#4 + #8 + #12): Drag Hold (press & move)
  - Thumb + Pinky (#4 + #20): Drag Toggle
- Supports:
  1. USB Webcams (`--source 0`)
  2. App-less Smartphone Browser Camera (`--phone-web` or `--source phone`)
  3. Smartphone IP Camera Stream (`--source http://<IP>:8080/video`)
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Union

import cv2
import numpy as np

from src import profiles
from src.camera import CameraConfig, CameraStream
from src.gesture_engine import GestureEngine, GestureResult
from src.hand_tracker import HandTracker, pinch_guides, tracker_options
from src.mouse_controller import MouseController
from src.scroll_target import ScrollTargetFinder
from src.window_probe import window_center_at
from src.settings_panel import SettingsPanel
from src.hotkeys import VK_0, VK_F2, VK_NUMPAD0, VK_O, KeyPoller
from src.win_perf import keep_responsive_in_background
from src.perf_log import PerfLog
from src.pipeline import InferencePipeline, Snapshot
from src.status import describe_state, short_label
from src.overlay import CORNERS, StatusOverlay
from src.tray import TrayIcon
from src.ui_magnet import UITargetMagnet
from src.dashboard import Dashboard

WINDOW_NAME = "Hand-Mouse (MediaPipe)"  # must keep the "Hand-Mouse" prefix (hotkeys.py)


def draw_cursor_anchor(
    frame: np.ndarray,
    landmarks,
    anchor: tuple[float, float] | None,
    moving: bool,
) -> None:
    """Draw the wrist -> palm reference line and the cursor anchor point."""
    h, w = frame.shape[:2]
    wrist = (int(landmarks[0].x * w), int(landmarks[0].y * h))
    palm = (int(landmarks[9].x * w), int(landmarks[9].y * h))
    color = (0, 230, 118) if moving else (0, 140, 255)
    cv2.line(frame, wrist, palm, color, 3, cv2.LINE_AA)
    if anchor is not None:
        pt = (int(anchor[0] * w), int(anchor[1] * h))
        cv2.circle(frame, pt, 10, color, 2, cv2.LINE_AA)
        cv2.circle(frame, pt, 4, color, -1, cv2.LINE_AA)


def draw_recording(frame: np.ndarray, path) -> None:
    """Draw the pinch path being recorded (it is replayed by the cursor on release)."""
    if len(path) < 2:
        return
    h, w = frame.shape[:2]
    pts = np.array([[int(x * w), int(y * h)] for _, x, y in path], dtype=np.int32)
    cv2.polylines(frame, [pts], False, (255, 200, 0), 3, cv2.LINE_AA)
    cv2.circle(frame, tuple(pts[0]), 7, (255, 200, 0), 2, cv2.LINE_AA)
    cv2.circle(frame, tuple(pts[-1]), 5, (255, 255, 255), -1, cv2.LINE_AA)


def draw_hud(
    frame: np.ndarray,
    fps: float,
    source: Union[int, str],
    result: GestureResult,
    screen_pos: tuple[int, int],
    engine: GestureEngine,
    paused: bool,
    status_msg: str = "",
    grab_speed: float = 0.0,
    switcher_open: bool = False,
    detect_ms: float = 0.0,
    recent_action: str = "",
) -> None:
    """Draw real-time status overlay, active margin guide box, and keyboard shortcuts."""
    h, w = frame.shape[:2]
    margin = float(engine.config.get("active_margin", 0.15))
    mode = str(engine.config.get("movement_mode", "absolute")).upper()

    # Draw active screen mapping boundary in ABSOLUTE mode
    if mode == "ABSOLUTE":
        x1, y1 = int(margin * w), int(margin * h)
        x2, y2 = int((1.0 - margin) * w), int((1.0 - margin) * h)
        box_color = (0, 230, 118) if result.is_moving else (120, 120, 120)
        cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 1, cv2.LINE_AA)
        cv2.putText(
            frame,
            "Active Screen Area",
            (x1 + 6, y1 + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            box_color,
            1,
            cv2.LINE_AA,
        )

    # Top semi-transparent status bar
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 92), (18, 18, 24), -1)
    cv2.addWeighted(overlay, 0.72, frame, 0.28, 0, frame)

    state_text, state_rgb = describe_state(
        result,
        paused=paused,
        switcher_open=switcher_open,
        recent_action=recent_action,
        grab_speed=grab_speed,
        mode=mode,
    )
    state_color = state_rgb[::-1]  # RGB -> BGR for OpenCV

    cv2.putText(
        frame,
        f"State: {state_text}",
        (16, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        state_color,
        2,
        cv2.LINE_AA,
    )

    pinch_thr = float(engine.config.get("pinch_threshold", 0.055))
    dist_str = f"{result.pinch_distance:.3f}" if result.pinch_distance < 1.0 else "---"
    d = result.distances or {}
    pky = d.get("pinky")
    mid, rng = d.get("middle"), d.get("ring")
    tk = d.get("thumb_tuck")
    pky_str = f"{pky:.3f}/{float(engine.config.get('pinky_click_threshold', 0.065)):.3f}" if pky is not None else "---"
    tk_str = f"{tk:.2f}/{float(engine.config.get('desktop_thumb_tuck', 0.45)):.2f}" if tk is not None else "---"
    info_line = (
        f"FPS: {fps:4.1f} Det: {detect_ms:4.1f}ms | {mode} | Idx: {dist_str}/{pinch_thr:.3f} | "
        f"Mid: {mid if mid is None else f'{mid:.3f}'}/{float(engine.config.get('click_threshold', 0.052)):.3f} "
        f"Rng: {rng if rng is None else f'{rng:.3f}'}/{float(engine.config.get('ring_click_threshold', 0.044)):.3f} | "
        f"Pky: {pky_str} | Tuck: {tk_str}"
    )
    cv2.putText(
        frame,
        info_line,
        (16, 56),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (220, 220, 220),
        1,
        cv2.LINE_AA,
    )

    help_line = (
        "[O/F2] SETTINGS  [SPACE] Pause  [M] Mode  [N] Monitor  [+/-] Pinch  [ [ / ] ] Fling  "
        "[P] Phone  [C] Cam  [R] Reload  [S] Save  [Q] Quit"
    )
    cv2.putText(
        frame,
        help_line,
        (16, 80),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.43,
        (160, 200, 255),
        1,
        cv2.LINE_AA,
    )

    if status_msg:
        cv2.putText(
            frame,
            status_msg,
            (16, h - 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )


def render_snapshot(snap: Snapshot, engine: GestureEngine, status_msg: str = "") -> None:
    """Draw one pipeline snapshot onto its frame (runs on the main thread only)."""
    frame = snap.frame
    if snap.has_hand:
        HandTracker.draw_hand(frame, snap.landmarks, snap.guides)
        draw_cursor_anchor(frame, snap.landmarks, snap.palm_pt, snap.result.is_moving)
        draw_recording(frame, snap.recorded_path)
    if snap.should_detect:
        draw_hud(
            frame=frame,
            fps=snap.fps,
            source=snap.source,
            result=snap.result,
            screen_pos=snap.screen_pos,
            engine=engine,
            paused=snap.paused,
            status_msg=snap.fling_msg or status_msg,
            grab_speed=snap.grab_speed,
            switcher_open=snap.switcher_open,
            detect_ms=snap.detect_ms,
            recent_action=snap.recent_action,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hand-Mouse: Control your OS mouse using Google MediaPipe hand tracking."
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Camera source: USB webcam index, 'phone', or IP URL (default: saved camera_source, then 0)",
    )
    parser.add_argument(
        "--phone-web",
        action="store_true",
        help="Start built-in HTTPS server so your smartphone can stream its camera via browser without any app",
    )
    parser.add_argument(
        "--phone-port",
        type=int,
        default=8443,
        help="Port for the built-in smartphone HTTPS web camera server (default: 8443)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["absolute", "relative"],
        default=None,
        help="Cursor movement mode: 'absolute' (screen mapping) or 'relative' (touchpad clutch mode)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to custom gesture JSON config (default: config/gestures.json)",
    )
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="Disable horizontal camera flip",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run camera & gesture visualization without moving the actual OS mouse cursor",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="No camera window: run in the background with a tray icon and a small status overlay "
             "(open the camera window again from the tray menu)",
    )
    parser.add_argument("--profile", default=None,
                        help="Calibration profile for this run (name, or 'none'); default: the one chosen last "
                             "(scripts/profile.py list|use)")
    parser.add_argument("--no-tray", action="store_true", help="Do not create the system tray icon")
    parser.add_argument("--overlay", action="store_true",
                        help="Show the small click-through status overlay even with the camera window open")
    parser.add_argument("--no-overlay", action="store_true", help="Never show the status overlay")
    parser.add_argument("--overlay-corner", choices=CORNERS, default="top-right",
                        help="Screen corner of the status overlay (default: top-right)")
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Exit automatically after N frames (0 = run indefinitely)",
    )
    parser.add_argument("--ui", choices=("tk", "qt"), default=None,
                        help="Desktop dashboard (tk or qt). Default: camera window + tray icon")
    parser.add_argument("--tcn-app", action="store_true",
                        help="Use the standalone TCN app identity and palm-mode defaults")
    return parser.parse_args()


def _log_to_file_without_console() -> None:
    """pythonw.exe (run_admin.bat) has no console: keep messages and errors in a log file."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = Path(__file__).resolve().parent / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        f = open(log_dir / "app_latest.log", "w", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def run_qt(args, perf, engine, camera, tracker, mouse, settings, keys, perf_log) -> int:
    """`--ui qt`: Qt dashboard on the UI thread, camera -> tracking -> mouse on a runtime thread.

    The UI thread only polls Qt, forwards keys / requests to the runtime and renders its
    newest snapshot (src/qt_runtime.py), so a slow UI frame never delays the cursor.
    """
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    from src.qt_dashboard import QtDashboard
    from src.qt_runtime import QtRuntime

    QApplication.instance() or QApplication([])
    # Same flags as the default mode: tray unless --no-tray, --headless starts in the tray,
    # the reception overlay shows with --headless or --overlay (never with --no-overlay)
    tray_ok = not args.no_tray and QSystemTrayIcon.isSystemTrayAvailable()
    if args.headless and not tray_ok:
        print("[Warn] --headless needs a system tray; showing the window instead.", file=sys.stderr)
    dashboard = QtDashboard(
        dry_run=args.dry_run, hide_to_tray=tray_ok, start_hidden=args.headless and tray_ok,
        overlay=(args.headless or args.overlay) and not args.no_overlay,
        overlay_corner=args.overlay_corner,
        tcn_app=args.tcn_app,
    )
    tray = None
    if tray_ok:
        from src.qt_tray import QtTray
        tray = QtTray(dashboard)
    runtime = QtRuntime(
        camera=camera, tracker=tracker, engine=engine, mouse=mouse, perf_log=perf_log,
        config_path=args.config, dry_run=args.dry_run, max_frames=args.max_frames,
        draw_cursor_anchor=draw_cursor_anchor, draw_recording=draw_recording,
    )
    print("=== Hand-Mouse Started (Qt, runtime thread) ===")
    if perf:
        print(f"  Background performance: {', '.join(perf)}")
    print(f"  Camera Source : {camera.config.source}")
    print(f"  Movement Mode : {engine.config.get('movement_mode')}")
    print("  Space start/pause · Esc pause · N next monitor · F mark a failure · O settings · Q quit")
    if tray is not None:
        print("  Tray icon: start/pause, show window, tracking test, status overlay, quit"
              + (" (window hidden: --headless)" if args.headless else ""))
    print(f"  Monitors: {len(mouse.screens)} (cursor can cross to any of them, [N] = jump to next)")

    close_posted = False
    # Aim assist: slow the cursor near clickable UI (same as the default mode)
    magnet = UITargetMagnet(lambda: engine.config)
    mouse.slowdown_provider = magnet.factor
    magnet.start()
    runtime.ui = dashboard.ui_state()
    runtime.start()
    try:
        while not dashboard.closed and not runtime.finished:
            key = dashboard.poll()
            if dashboard.closed:
                break
            if dashboard.tracking_requested:
                dashboard.tracking_requested = False
                runtime.post("pause")
                runtime.post("tracking_start")
            if dashboard.tracking_close_requested and not close_posted:
                runtime.post("tracking_close")
                close_posted = True
            elif not dashboard.tracking_close_requested:
                close_posted = False
            if dashboard.failure_mark_requested:
                dashboard.failure_mark_requested = False
                runtime.post("failure_mark")
            want_settings = key in (ord("o"), ord("O"), ord("0"))
            if keys.available:
                want_settings = want_settings or keys.pressed([VK_F2]) or keys.pressed(
                    [VK_O, VK_0, VK_NUMPAD0], require_focus=True)
            if want_settings and dashboard.stage_name == "main":
                runtime.post("pause")
                settings.toggle()
            elif key in (ord("q"), ord("Q")):
                break
            elif key not in (-1, ord("o"), ord("O"), ord("0")):
                runtime.post("key", key)
            runtime.ui = dashboard.ui_state()
            snap = runtime.wait_snapshot(.03)
            if snap is not None:
                dashboard.apply_snapshot(snap)
                if snap.message:
                    print(snap.message)
            settings.update()
        if runtime.error is not None:
            print(f"[Error] runtime thread stopped: {runtime.error!r}", file=sys.stderr)
    finally:
        runtime.stop()
        magnet.shutdown()
        if mouse.scroll_target_finder is not None:
            mouse.scroll_target_finder.shutdown()
        if tray is not None:
            tray.hide()
        mouse.close()
        settings.shutdown()
        perf_log.close()
        mouse.release_all()
        tracker.close()
        camera.release()
        dashboard.close()
        cv2.destroyAllWindows()
    return 1 if runtime.error is not None else 0


def run_dashboard(args, perf, engine, camera, tracker, mouse, settings, keys, perf_log) -> int:
    """`--ui tk`: Tk dashboard (Dan's palm engine). `--ui qt` uses run_qt (runtime thread).

    Runs camera -> gestures -> mouse inline on the main thread, because the dashboard UI
    must be polled there. Tray, headless mode, status overlay and UI aim assist belong to
    the default pipeline loop in main() and are not wired into this mode yet.
    """
    dashboard = Dashboard(dry_run=args.dry_run)
    paused = True
    status_msg = ""
    status_expire = 0.0
    frame_count = 0
    last_fling_count = 0
    recent_action, recent_action_until = "", 0.0

    print("=== Hand-Mouse Started ===")
    if perf:
        print(f"  Background performance: {', '.join(perf)}")
    if camera.is_phone_web and camera.phone_server is not None:
        print(f"  [Smartphone Browser Mode] Open this URL on your phone (Chrome/Safari):")
        print(f"  >>> {camera.phone_server.url} <<<")
    else:
        print(f"  Camera Source : {camera.config.source}")
    print(f"  Movement Mode : {engine.config.get('movement_mode')}")
    print("  Fine Control: move your hand with NO pinch (relative + acceleration, [M] = absolute).")
    print("  Make a FIST to move the same way at 20x DPI.")
    print("  Tuck the THUMB, open the other four fingers, point the fingers sideways in the image and hold 0.4 s = show desktop (Win+D).")
    print("  THUMB+PINKY opens the task switcher: point at a window, THUMB+MIDDLE opens it, THUMB+RING cancels.")
    if engine.config.get("record_fling_enabled", False):
        print("  Record & Fling ON: pinch THUMB+INDEX, draw a motion, release to replay it.")
    else:
        print("  THUMB+INDEX pinch = grab the page and scroll it like a touchscreen.")
    print("  Thumb+Middle = Left Click, Thumb+Ring = Right Click.")
    print("  Press 'O' (or '0', or F2 from any window) to open the SETTINGS window.")
    print("  Press 'P' in window to switch to Smartphone Browser mode, 'C' for USB Webcam, 'Q' to quit.")
    print(f"  Monitors: {len(mouse.screens)} (cursor can cross to any of them, [N] = jump to next)")

    try:
        while not dashboard.closed:
            key = dashboard.poll()
            if dashboard.closed:
                break
            if key == 27:
                paused = True
                mouse.close_switcher(confirm=False)
                mouse.release_all()
                engine.reset()
                recent_action_until = 0.0
                key = -1
            ok, frame = camera.read()
            loop_t0 = time.perf_counter()
            if not ok or frame is None:
                time.sleep(0.03)
                continue

            # Only run MediaPipe detection when we have an actual camera feed (not on the QR waiting screen)
            should_detect = not camera.is_phone_web or (
                camera.phone_server is not None and camera.phone_server.has_client
            )

            if should_detect:
                detections = tracker.detect(frame)
            else:
                detections = []

            fh, fw = frame.shape[:2]
            mouse.frame_aspect = fh / max(1, fw)
            engine.frame_aspect = mouse.frame_aspect
            if detections:
                hand = detections[0]
                result = engine.process(hand.landmarks)
                if not paused:
                    mouse.apply(result, engine.config)
                if not paused and result.action in ("left_click", "right_click", "drag_toggle", "alt_tab",
                                     "show_desktop", "task_switcher"):
                    recent_action, recent_action_until = result.action, time.perf_counter() + 0.8

                HandTracker.draw_hand(
                    frame,
                    hand.landmarks,
                    pinch_guides(
                        result.distances,
                        engine.config,
                        engine.pinched_fingers,
                        action=result.action,
                        gesture=result.active_gesture,
                    ),
                )
                palm_pt = engine.palm_anchor_point(hand.landmarks)
                draw_cursor_anchor(frame, hand.landmarks, palm_pt, result.is_moving)
                draw_recording(frame, mouse.recorded_path)
                if mouse.fling_count != last_fling_count:
                    last_fling_count = mouse.fling_count
                    status_msg = f"FLING! ~{mouse.last_fling_distance:.0f}px"
                    status_expire = time.perf_counter() + 0.8
            else:
                result = engine.process(None)
                if not paused:
                    mouse.hand_lost(engine.config)

            now = time.perf_counter()
            if now > status_expire:
                status_msg = ""
            action = recent_action if now < recent_action_until else result.action
            dashboard.show(frame, bool(detections), paused, action, camera.fps,
                           tracker.last_detect_ms, status_msg)
            frame_count += 1
            if args.max_frames > 0 and frame_count >= args.max_frames:
                break

            settings.update()
            perf_log.frame(tracker.last_detect_ms if should_detect else None,
                           (time.perf_counter() - loop_t0) * 1000.0)
            perf_log.tick(camera.fps, camera.dropped_frames)

            # Settings window: physical-key polling works in Korean IME mode too.
            # O / 0 while a Hand-Mouse window is focused, F2 from anywhere.
            toggle_settings = key in (ord("o"), ord("O"), ord("0"))
            if keys.available:
                toggle_settings = toggle_settings or keys.pressed([VK_F2]) or keys.pressed(
                    [VK_O, VK_0, VK_NUMPAD0], require_focus=True
                )
            elif key in (ord("o"), ord("O"), ord("0")):
                toggle_settings = True
            if toggle_settings:
                paused = True
                mouse.close_switcher(confirm=False)
                mouse.release_all()
                engine.reset()
                opened = settings.toggle()
                status_msg = (
                    "Settings window OPEN (sliders apply live, [S] to save)" if opened else "Settings window closed"
                )
                status_expire = time.perf_counter() + 2.0
            if key in (ord("q"), ord("Q")):
                break
            elif key in (27, ord(" ")):
                paused = key == 27 or not paused
                mouse.close_switcher(confirm=False)
                mouse.release_all()
                engine.reset()
                recent_action_until = 0.0
                status_msg = "Mouse Control PAUSED" if paused else "Mouse Control RESUMED"
                status_expire = now + 2.0
            elif key in (ord("m"), ord("M")):
                cur_mode = str(engine.config.get("movement_mode", "relative")).lower()
                new_mode = "relative" if cur_mode == "absolute" else "absolute"
                engine.set_movement_mode(new_mode)
                mouse.release_all()
                status_msg = f"Switched Movement Mode -> {new_mode.upper()}"
                status_expire = now + 2.0
            elif key in (ord("n"), ord("N")):
                mouse.release_all()
                target = mouse.cycle_monitor()
                status_msg = f"Monitor {mouse.screens.monitors.index(target) + 1}/{len(mouse.screens)}"
                status_expire = now + 2.0
            elif key in (ord("+"), ord("=")):
                thr = min(0.12, float(engine.config.get("pinch_threshold", 0.055)) + 0.005)
                engine.config["pinch_threshold"] = round(thr, 4)
                engine.config["release_threshold"] = round(thr + 0.02, 4)
                status_msg = f"Pinch Threshold -> {thr:.3f} (More sensitive)"
                status_expire = now + 2.0
            elif key in (ord("-"), ord("_")):
                thr = max(0.02, float(engine.config.get("pinch_threshold", 0.055)) - 0.005)
                engine.config["pinch_threshold"] = round(thr, 4)
                engine.config["release_threshold"] = round(thr + 0.02, 4)
                status_msg = f"Pinch Threshold -> {thr:.3f} (Stricter)"
                status_expire = now + 2.0
            elif key in (ord("["), ord("]")):
                # Fling distance: ']' farther, '[' shorter (x1.2 per press, saved with 'S')
                cur = float(engine.config.get("fling_strength", 1.0))
                new = cur * 1.2 if key == ord("]") else cur / 1.2
                new = round(max(0.1, min(5.0, new)), 3)
                engine.config["fling_strength"] = new
                status_msg = f"Fling Distance -> {new:.2f} ({'farther' if key == ord(']') else 'shorter'}, [S] to save)"
                status_expire = now + 2.0
            elif key in (ord("r"), ord("R")):
                engine.load_config()
                status_msg = "Reloaded config/gestures.json"
                status_expire = now + 2.0
            elif key in (ord("s"), ord("S")):
                saved_path = engine.save_config()
                status_msg = f"Saved config to {saved_path.name}"
                status_expire = now + 2.0
            elif key in (ord("p"), ord("P")):
                camera.switch_source("phone")
                status_msg = "Switched to Smartphone Browser Camera Mode"
                status_expire = now + 2.5
            elif key in (ord("c"), ord("C")):
                if camera.is_phone_web:
                    camera.switch_source(0)
                    status_msg = "Switched to USB Webcam 0"
                elif isinstance(camera.config.source, int):
                    next_idx = (camera.config.source + 1) % 3
                    if camera.switch_source(next_idx):
                        status_msg = f"Switched to Camera Index {next_idx}"
                    else:
                        camera.switch_source(0)
                        status_msg = "Reverted to Camera Index 0"
                status_expire = now + 2.0
    finally:
        mouse.close()
        settings.shutdown()
        perf_log.close()
        mouse.release_all()
        tracker.close()
        camera.release()
        dashboard.close()
        cv2.destroyAllWindows()

    return 0


def _safe_console() -> None:
    """Never crash on print(): Korean status text on a console that cannot encode it
    (an English Windows console is cp1252) becomes '?' instead of UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def main() -> int:
    _log_to_file_without_console()
    _safe_console()
    args = parse_args()
    perf = keep_responsive_in_background()

    if args.profile is not None:
        profiles.RUN_OVERRIDE = "" if args.profile.strip().lower() in ("none", "없음", "") else args.profile.strip()
    engine = GestureEngine(config_path=args.config)
    print(f"  Profile       : {engine.profile_name or '(none)'}"
          + ("" if not args.profile or engine.profile_name else f"  [Warn] profile '{args.profile}' not found"))
    if args.mode:
        engine.set_movement_mode(args.mode)
    # Setting "start_in_tray": same as passing --headless (the flag always wins when given)
    if engine.config.get("start_in_tray", False):
        args.headless = True

    chosen_source = "phone" if args.phone_web else (
        args.source if args.source is not None else engine.config.get("camera_source", 0)
    )
    def cfg_int(key: str, default: int) -> int:
        try:
            value = int(engine.config.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    cam_cfg = CameraConfig(
        source=CameraStream.parse_source(chosen_source),
        mirror=not args.no_mirror,
        phone_port=args.phone_port,
        # What we ask the webcam for (config keys); actual_mode() shows what it gave
        width=cfg_int("camera_width", 1280),
        height=cfg_int("camera_height", 720),
        fps=cfg_int("camera_fps", 60),
        backend=str(engine.config.get("camera_backend", "auto")),
    )
    camera = CameraStream(cam_cfg)
    if not camera.open():
        print(
            f"[Error] Could not open camera source '{chosen_source}'.\n"
            "  - For USB webcam: python main.py --source 0\n"
            "  - For App-less Smartphone Browser: python main.py --phone-web",
            file=sys.stderr,
        )
        return 1

    tracker_cfg = tracker_options(engine.config)
    tracker = HandTracker(num_hands=2 if args.ui == "qt" else 1, **tracker_cfg)
    mode = camera.actual_mode()
    print(f"  Camera opened : {mode[0]}x{mode[1]} @ {mode[2]} fps (requested "
          f"{cam_cfg.width}x{cam_cfg.height} @ {cam_cfg.fps})" if mode else "  Camera opened : (mode unknown)")
    print("  Tracker       : detection {min_detection_confidence} / presence {min_presence_confidence} / "
          "tracking {min_tracking_confidence}, detect width {detect_max_width}".format(**tracker_cfg))
    mouse = MouseController(dry_run=args.dry_run)
    mouse.window_probe = window_center_at       # page scrolling keeps the cursor in the middle of its window
    mouse.scroll_target_finder = ScrollTargetFinder(lambda: engine.config)    # ...of the scroll region under the cursor
    mouse.scroll_target_finder.start()
    settings = SettingsPanel(engine)
    keys = KeyPoller()
    perf_log = PerfLog(Path(__file__).resolve().parent / "logs" / "perf_latest.csv")
    if args.ui == "qt":
        return run_qt(args, perf, engine, camera, tracker, mouse, settings, keys, perf_log)
    if args.ui:
        return run_dashboard(args, perf, engine, camera, tracker, mouse, settings, keys, perf_log)
    # Aim assist: slow the cursor near clickable UI (reads engine.config live, [Settings] toggles it)
    magnet = UITargetMagnet(lambda: engine.config)
    mouse.slowdown_provider = magnet.factor
    magnet.start()
    pipeline = InferencePipeline(camera, tracker, engine, mouse, perf_log, max_frames=args.max_frames)

    hud_visible = not args.headless
    pipeline.publish_frames = hud_visible

    overlay: StatusOverlay | None = None
    overlay_wanted = (args.headless or args.overlay) and not args.no_overlay
    if overlay_wanted:
        overlay = StatusOverlay(corner=args.overlay_corner)
        overlay.start()

    tray: TrayIcon | None = None
    if not args.no_tray:
        tray = TrayIcon(
            is_paused=lambda: pipeline.paused,
            is_hud=lambda: hud_visible,
            is_overlay=lambda: overlay is not None and overlay.visible,
        )
        if not tray.start():
            tray = None
    if args.headless and tray is None:
        print("[Warn] --headless without a tray icon: stop the app from Task Manager.", file=sys.stderr)

    status_msg = ""
    status_expire = 0.0
    window_open = False

    print("=== Hand-Mouse Started ===")
    if perf:
        print(f"  Background performance: {', '.join(perf)}")
    if camera.is_phone_web and camera.phone_server is not None:
        print(f"  [Smartphone Browser Mode] Open this URL on your phone (Chrome/Safari):")
        print(f"  >>> {camera.phone_server.url} <<<")
    else:
        print(f"  Camera Source : {camera.config.source}")
    print(f"  Movement Mode : {engine.config.get('movement_mode')}")
    print("  Fine Control: move your hand with NO pinch (relative + acceleration, [M] = absolute).")
    print("  Make a FIST to move the same way at 20x DPI.")
    print("  Tuck the THUMB, open the other four fingers, point the fingers sideways in the image and hold 0.4 s = show desktop (Win+D).")
    print("  THUMB+PINKY opens the task switcher: point at a window, THUMB+MIDDLE opens it, THUMB+RING cancels.")
    if engine.config.get("record_fling_enabled", False):
        print("  Record & Fling ON: pinch THUMB+INDEX, draw a motion, release to replay it.")
    else:
        print("  THUMB+INDEX pinch = grab the page and scroll it like a touchscreen.")
    print("  Thumb+Middle = Left Click, Thumb+Ring = Right Click.")
    print("  Press 'O' (or '0', or F2 from any window) to open the SETTINGS window.")
    print("  Press 'P' in window to switch to Smartphone Browser mode, 'C' for USB Webcam, 'Q' to quit.")
    print(f"  Monitors: {len(mouse.screens)} (cursor can cross to any of them, [N] = jump to next)")
    if tray is not None:
        print("  System tray icon: pause / camera window / status overlay / settings / quit.")
    if args.headless:
        print("  Headless: no camera window (open it from the tray menu).")

    def say(text: str, seconds: float = 2.0) -> None:
        nonlocal status_msg, status_expire
        status_msg, status_expire = text, time.perf_counter() + seconds

    def set_hud(visible: bool) -> None:
        nonlocal hud_visible
        hud_visible = visible
        pipeline.publish_frames = visible  # hidden => the pipeline skips the frame hand-off entirely

    def set_paused(value: bool) -> None:
        pipeline.set_paused(value)
        say("Mouse Control PAUSED" if value else "Mouse Control RESUMED")
        if tray is not None:
            tray.refresh()

    def toggle_settings() -> None:
        opened = settings.toggle()
        say("Settings window OPEN (sliders apply live, [S] to save)" if opened else "Settings window closed")

    def next_monitor() -> None:
        def task() -> str:
            mouse.release_all()
            target = mouse.cycle_monitor()
            return f"Monitor {mouse.screens.monitors.index(target) + 1}/{len(mouse.screens)}"
        pipeline.post(task)

    def switch_mode() -> None:
        def task() -> str:
            cur = str(engine.config.get("movement_mode", "relative")).lower()
            new = "relative" if cur == "absolute" else "absolute"
            engine.set_movement_mode(new)
            mouse.release_all()
            return f"Switched Movement Mode -> {new.upper()}"
        pipeline.post(task)

    def switch_camera(target: str) -> None:
        def task() -> str:
            if target == "phone":
                camera.switch_source("phone")
                return "Switched to Smartphone Browser Camera Mode"
            if camera.is_phone_web:
                camera.switch_source(0)
                return "Switched to USB Webcam 0"
            if isinstance(camera.config.source, int):
                next_idx = (camera.config.source + 1) % 3
                if camera.switch_source(next_idx):
                    return f"Switched to Camera Index {next_idx}"
                camera.switch_source(0)
                return "Reverted to Camera Index 0"
            return ""
        pipeline.post(task)

    quit_requested = False
    pipeline.start()
    try:
        while not pipeline.finished and not quit_requested:
            # --- tray menu commands (executed here, on the main thread)
            while tray is not None and not tray.commands.empty():
                cmd = tray.commands.get_nowait()
                if cmd == "pause":
                    set_paused(not pipeline.paused)
                elif cmd == "hud":
                    set_hud(not hud_visible)
                    tray.refresh()
                elif cmd == "overlay":
                    if overlay is None:
                        overlay = StatusOverlay(corner=args.overlay_corner)
                        overlay.start()
                    else:
                        overlay.set_visible(not overlay.visible)
                    tray.refresh()
                elif cmd == "settings":
                    toggle_settings()
                elif cmd == "quit":
                    quit_requested = True
            while not pipeline.messages.empty():
                say(pipeline.messages.get_nowait())

            key = -1
            if hud_visible:
                snap = pipeline.wait_snapshot(0.05)
                if snap is not None and snap.frame is not None:
                    if time.perf_counter() > status_expire:
                        status_msg = ""
                    render_snapshot(snap, engine, status_msg)
                    cv2.imshow(WINDOW_NAME, snap.frame)
                    window_open = True
                key = cv2.waitKey(1) & 0xFF
                if window_open and cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    # closed with the window's X button: hide to the tray, or quit without one
                    if tray is not None:
                        set_hud(False)
                        tray.refresh()
                    else:
                        quit_requested = True
            else:
                if window_open:
                    cv2.destroyAllWindows()
                    window_open = False
                time.sleep(0.04)  # no rendering at all: the main thread is (almost) idle

            if overlay is not None and overlay.visible:
                snap_now = pipeline.latest()
                if snap_now is not None:
                    overlay.set_state(*short_label(
                        snap_now.result, has_hand=snap_now.has_hand, paused=snap_now.paused,
                        switcher_open=snap_now.switcher_open, recent_action=snap_now.recent_action,
                    ))

            # Settings window: physical-key polling works in Korean IME mode too.
            # O / 0 while a Hand-Mouse window is focused, F2 from anywhere.
            want_settings = False
            if keys.available:
                want_settings = keys.pressed([VK_F2]) or keys.pressed(
                    [VK_O, VK_0, VK_NUMPAD0], require_focus=True
                )
            elif key in (ord("o"), ord("O"), ord("0")):
                want_settings = True
            if want_settings:
                toggle_settings()

            if key in (27, ord("q"), ord("Q")):
                break
            elif key == ord(" "):
                set_paused(not pipeline.paused)
            elif key in (ord("m"), ord("M")):
                switch_mode()
            elif key in (ord("n"), ord("N")):
                next_monitor()
            elif key in (ord("+"), ord("=")):
                thr = min(0.12, float(engine.config.get("pinch_threshold", 0.055)) + 0.005)
                engine.config["pinch_threshold"] = round(thr, 4)
                engine.config["release_threshold"] = round(thr + 0.02, 4)
                say(f"Pinch Threshold -> {thr:.3f} (More sensitive)")
            elif key in (ord("-"), ord("_")):
                thr = max(0.02, float(engine.config.get("pinch_threshold", 0.055)) - 0.005)
                engine.config["pinch_threshold"] = round(thr, 4)
                engine.config["release_threshold"] = round(thr + 0.02, 4)
                say(f"Pinch Threshold -> {thr:.3f} (Stricter)")
            elif key in (ord("["), ord("]")):
                # Fling distance: ']' farther, '[' shorter (x1.2 per press, saved with 'S')
                cur = float(engine.config.get("fling_strength", 1.0))
                new = cur * 1.2 if key == ord("]") else cur / 1.2
                new = round(max(0.1, min(5.0, new)), 3)
                engine.config["fling_strength"] = new
                say(f"Fling Distance -> {new:.2f} ({'farther' if key == ord(']') else 'shorter'}, [S] to save)")
            elif key in (ord("r"), ord("R")):
                pipeline.post(lambda: (engine.load_config(), "Reloaded config/gestures.json")[1])
            elif key in (ord("s"), ord("S")):
                say(f"Saved config to {engine.save_config().name}")
            elif key in (ord("p"), ord("P")):
                switch_camera("phone")
            elif key in (ord("c"), ord("C")):
                switch_camera("next")
        if pipeline.error is not None:
            print(f"[Error] inference thread stopped: {pipeline.error!r}", file=sys.stderr)
    finally:
        pipeline.stop()
        magnet.shutdown()
        if mouse.scroll_target_finder is not None:
            mouse.scroll_target_finder.shutdown()
        if tray is not None:
            tray.stop()
        if overlay is not None:
            overlay.shutdown()
        mouse.close()
        settings.shutdown()
        perf_log.close()
        mouse.release_all()
        tracker.close()
        camera.release()
        cv2.destroyAllWindows()

    return 1 if pipeline.error is not None else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        # Ctrl+C in the terminal: main()'s finally blocks already released the mouse,
        # camera and windows; no traceback for a normal way to quit.
        print("[Info] Ctrl+C: closed.", file=sys.stderr)
        raise SystemExit(130)
    except BaseException:
        import traceback

        _log_to_file_without_console()
        traceback.print_exc()
        raise
