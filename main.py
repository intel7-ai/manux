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
import math
from typing import Union

import cv2
import numpy as np

from src.camera import CameraConfig, CameraStream
from src.gesture_engine import GestureEngine, GestureResult
from src.hand_tracker import HandTracker, pinch_guides
from src.mouse_controller import MouseController
from src.settings_panel import SettingsPanel
from src.hotkeys import VK_0, VK_F2, VK_NUMPAD0, VK_O, KeyPoller
from src.win_perf import keep_responsive_in_background
from src.perf_log import PerfLog
from src.dashboard import Dashboard
from src.pointer_workspace import (
    FINGERTIPS, IndexTipGuard, PointerPose, is_pointing_pose, thumb_contacts, thumb_distances,
)


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

    state_color = (0, 255, 128) if result.is_moving else (200, 200, 200)
    if paused:
        state_text = "PAUSED (Press SPACE to resume)"
        state_color = (0, 180, 255)
    elif switcher_open:
        state_text = "TASK SWITCHER: point at a window | thumb+middle = open | thumb+ring = cancel"
        state_color = (255, 120, 255)
    elif result.action == "move_cursor" and result.active_gesture == "fist":
        state_text = f"FAST MOVE x{result.dpi_multiplier:.1f} (Fist / {mode})"
        state_color = (0, 200, 255)
    elif result.action == "move_cursor":
        state_text = f"FINE MOVE (No Pinch / {mode})"
    elif result.action == "scroll_grab":
        state_text = "SCROLLING - page grabbed (Thumb + Index), move the hand to scroll"
        state_color = (255, 220, 0)
    elif result.action == "grab":
        state_text = f"RECORDING - draw, release to fling  [speed {grab_speed:.2f}]"
        state_color = (255, 200, 0)
    elif result.action == "drag_hold":
        state_text = "DRAGGING (left button held) - release the pinch to drop"
        state_color = (80, 160, 255)
    elif recent_action:
        # shown for a moment after it fires (a single frame was unreadable)
        state_text = f"ACTION: {recent_action.upper()}"
        state_color = (0, 120, 255)
    elif result.active_gesture in ("thumb_middle_pinch", "thumb_ring_pinch", "thumb_pinky_pinch"):
        finger = result.active_gesture.split("_")[1].upper()
        state_text = f"PINCH HELD: {finger} (open the fingers to click again)"
        state_color = (0, 230, 180)
    elif result.distances:
        near = min(("index", "middle", "ring", "pinky"), key=lambda f: result.distances.get(f, 9.9))
        state_text = f"AIMING: {near.upper()} near the thumb (cursor held for a precise click)"
    else:
        state_text = "NO HAND"

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
        f"Mid: {mid if mid is None else f'{mid:.3f}'} Rng: {rng if rng is None else f'{rng:.3f}'}"
        f"/{float(engine.config.get('click_threshold', 0.052)):.3f} | "
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
        "[O/F2] SETTINGS  [SPACE] Pause  [M] Mode  [+/-] Pinch  [ [ / ] ] Fling  "
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hand-Mouse: Control your OS mouse using Google MediaPipe hand tracking."
    )
    parser.add_argument(
        "--source",
        type=str,
        default="0",
        help="Camera source: USB webcam index ('0'), 'phone' (app-less smartphone browser), or IP URL",
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
        "--max-frames",
        type=int,
        default=0,
        help="Exit automatically after N frames (0 = run indefinitely)",
    )
    parser.add_argument("--ui", choices=("tk", "qt"), default="tk", help="Desktop UI toolkit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    perf = keep_responsive_in_background()

    engine = GestureEngine(config_path=args.config)
    if args.mode:
        engine.set_movement_mode(args.mode)

    chosen_source = "phone" if args.phone_web else args.source
    cam_cfg = CameraConfig(
        source=CameraStream.parse_source(chosen_source),
        mirror=not args.no_mirror,
        phone_port=args.phone_port,
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

    tracker = HandTracker(num_hands=2 if args.ui == "qt" else 1)
    mouse = MouseController(dry_run=args.dry_run)
    settings = SettingsPanel(engine)
    keys = KeyPoller()
    perf_log = PerfLog(Path(__file__).resolve().parent / "logs" / "perf_latest.csv")

    if args.ui == "qt":
        from src.qt_dashboard import QtDashboard
        dashboard = QtDashboard(dry_run=args.dry_run)
    else:
        dashboard = Dashboard(dry_run=args.dry_run)
    paused = True
    had_selected_hand = False
    pointer_engine = GestureEngine(config_path=args.config) if args.ui == "qt" else None
    index_tip_guard = IndexTipGuard()
    pointer_pose = PointerPose()
    pointer_gestures = {
        "thumb_middle_pinch": {"enabled": True, "action": "left_click", "cooldown": .35},
        "thumb_ring_pinch": {"enabled": True, "action": "right_click", "cooldown": .45},
    }
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
    print("  Tuck the THUMB, open the other four fingers and hold 0.4 s = show desktop (Win+D).")
    print("  THUMB+PINKY opens the task switcher: point at a window, THUMB+MIDDLE opens it, THUMB+RING cancels.")
    if engine.config.get("record_fling_enabled", False):
        print("  Record & Fling ON: pinch THUMB+INDEX, draw a motion, release to replay it.")
    else:
        print("  THUMB+INDEX pinch = grab the page and scroll it like a touchscreen.")
    print("  Thumb+Middle = Left Click, Thumb+Ring = Right Click.")
    print("  Press 'O' (or '0', or F2 from any window) to open the SETTINGS window.")
    print("  Press 'P' in window to switch to Smartphone Browser mode, 'C' for USB Webcam, 'Q' to quit.")

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
                index_tip_guard.reset()
                pointer_pose.reset()
                if pointer_engine is not None:
                    pointer_engine.reset()
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

            if args.ui == "qt":
                selected = dashboard.select_hand(detections, camera.config.mirror)
                detections = [selected] if selected is not None else []
                if dashboard.stage_name != "main" and not paused:
                    paused = True
                    mouse.close_switcher(confirm=False)
                    mouse.release_all()
                    engine.reset()
                if had_selected_hand and not detections:
                    mouse.close_switcher(confirm=False)
                    mouse.release_all()
                    engine.reset()
                had_selected_hand = bool(detections)

            fh, fw = frame.shape[:2]
            mouse.frame_aspect = fh / max(1, fw)

            index_mode = args.ui == "qt" and dashboard.use_index_pointer
            contacts = ()
            if not index_mode or not detections or paused or dashboard.calibrating:
                index_tip_guard.reset()
                pointer_pose.reset()
            if pointer_engine is not None:
                if not index_mode or paused or dashboard.calibrating or not should_detect:
                    pointer_engine.reset()
                elif not detections:
                    pointer_engine.process(None, timestamp=loop_t0)
            if index_mode:
                tip = detections[0].landmarks[GestureEngine.INDEX_TIP] if detections else None
                dashboard.update_pointer((tip.x, tip.y) if tip else None, camera.config.source,
                                         camera.config.mirror, mouse.frame_aspect)
            if detections and index_mode:
                points = detections[0].landmarks
                tip = points[GestureEngine.INDEX_TIP]
                ratios = thumb_distances(points, mouse.frame_aspect)
                contacts = thumb_contacts(ratios)
                result = GestureResult()
                preview_point = (tip.x, tip.y)
                if not paused and not dashboard.calibrating:
                    pointer_engine.config.update(engine.config)
                    pointer_engine.config.update(
                        gestures=pointer_gestures, record_fling_enabled=False,
                        grab_scroll_enabled=False, long_press_drag=False,
                    )
                    gesture = pointer_engine.process(points, timestamp=loop_t0)
                    released = False
                    if pointer_pose.pending:
                        finger_tip = 12 if pointer_pose.pending == "left_click" else 16
                        thumb = points[4]
                        release_distance = (
                            float(pointer_engine.config.get("click_threshold", .052))
                            + float(pointer_engine.config.get("click_release_margin", .02))
                        )
                        released = all(
                            math.hypot(thumb.x - p.x, thumb.y - p.y) > release_distance
                            for p in (points[finger_tip], points[finger_tip - 1])
                        )
                    curls = pointer_engine.finger_curl_ratios(points)
                    action = pointer_pose.update(
                        loop_t0, pointing=is_pointing_pose(points, curls, mouse.frame_aspect),
                        click=gesture.action, released=released,
                        pinching=gesture.active_gesture in pointer_gestures,
                    )
                    if action == "move_cursor":
                        if not index_tip_guard.accept(points, mouse.frame_aspect):
                            action = "pointer_wait"
                    else:
                        index_tip_guard.reset()
                    point = dashboard.pointer_position(preview_point)
                    moving = action == "move_cursor"
                    result = GestureResult(action=action, cursor_point=point if moving else None,
                                           is_moving=moving)
                    mouse.apply(result, engine.config, index_tip=point,
                                index_sensitivity=dashboard.pointer_sensitivity / 100)
                    if action in ("left_click", "right_click"):
                        recent_action, recent_action_until = action, time.perf_counter() + .5
                x, y = preview_point
                if not dashboard.show_fingertips and 0 <= x <= 1 and 0 <= y <= 1:
                    cv2.circle(frame, (int(x * fw), int(y * fh)), 6,
                               (189, 229, 70), -1, cv2.LINE_AA)
                numbered_tips = FINGERTIPS if dashboard.show_fingertips else ()
                for number, landmark_id in enumerate(numbered_tips):
                    point = points[landmark_id]
                    if not (0 <= point.x <= 1 and 0 <= point.y <= 1):
                        continue
                    x, y = int(point.x * fw), int(point.y * fh)
                    color = (189, 229, 70) if number == 1 or number in contacts else (245, 245, 245)
                    cv2.circle(frame, (x, y), 10, (49, 35, 17), -1, cv2.LINE_AA)
                    cv2.putText(frame, str(number), (x - 5, y + 5), cv2.FONT_HERSHEY_SIMPLEX,
                                .46, color, 1, cv2.LINE_AA)
            elif detections:
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
                result = GestureResult() if index_mode else engine.process(None)
                if not paused and args.ui != "qt":
                    mouse.hand_lost(engine.config)

            now = time.perf_counter()
            if now > status_expire:
                status_msg = ""
            action = recent_action if now < recent_action_until else result.action
            if args.ui == "qt":
                dashboard.show(frame, bool(detections), paused, action, camera.fps,
                               tracker.last_detect_ms, status_msg, contacts=contacts)
            else:
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
            if toggle_settings and (args.ui != "qt" or dashboard.stage_name == "main"):
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
                paused = key == 27 or not paused or (args.ui == "qt" and dashboard.stage_name != "main")
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


if __name__ == "__main__":
    raise SystemExit(main())
