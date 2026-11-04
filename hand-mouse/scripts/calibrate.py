"""
Personal calibration wizard (about 1 minute): measures this user's pinch distances and hand tremor,
then saves matching thresholds as a PROFILE (config/profiles/<name>.json) and makes it the active one.
config/gestures.json is never edited; other people's profiles are untouched.

Run from hand-mouse/:  uv run --locked python scripts/calibrate.py --name 홍길동 [--source 0] [--hand Right] [--no-save]
Close the running Hand-Mouse first (one program at a time can use the webcam).
Keys: Esc = cancel, Y = save the result, N = discard it.
The mouse is NOT moved and no click is sent. Only numbers are saved, never camera images.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.calibration import (CalibrationSession, FINGER_LABELS, OPEN_MIN_INDEX, raw_dump, report_dict)  # noqa: E402
from src import profiles  # noqa: E402
from src.camera import CameraConfig, CameraStream  # noqa: E402
from src.gesture_engine import DEFAULT_CONFIG_PATH, GestureEngine  # noqa: E402
from src.hand_selection import physical_side  # noqa: E402
from src.hand_tracker import HandTracker, tracker_options  # noqa: E402

WINDOW = "Hand-Mouse calibration"
FONT = cv2.FONT_HERSHEY_SIMPLEX


def text(img, s, y, scale=0.7, color=(245, 245, 245)):
    # OpenCV's built-in font has no Hangul: the prompts are also printed to the console
    cv2.putText(img, s, (24, y), FONT, scale, color, 2, cv2.LINE_AA)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0")
    ap.add_argument("--hand", default="Right", choices=["Right", "Left"], help="the hand that controls the mouse")
    ap.add_argument("--name", default="", help="profile name to save under (an existing one is replaced)")
    ap.add_argument("--no-save", action="store_true", help="measure and print, do not save a profile")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not args.no_save and not profiles.valid_name(args.name):
        print("[Error] --name 프로필 이름을 주세요 (한글·영문·숫자·공백·-_. 32자 이하). 저장 없이 측정만 하려면 --no-save",
              file=sys.stderr)
        return 1
    engine = GestureEngine()
    camera = CameraStream(CameraConfig(source=CameraStream.parse_source(args.source), mirror=True))
    if not camera.open():
        print("[Error] camera could not be opened", file=sys.stderr)
        return 1
    tracker = HandTracker(num_hands=2, **tracker_options(engine.config))
    session = CalibrationSession()
    last_step = None
    last_hint = ""
    result = None
    t_start = time.perf_counter()
    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                time.sleep(0.02)
                continue
            now = time.perf_counter() - t_start
            hands = [d for d in tracker.detect(frame) if physical_side(d, True) == args.hand]
            lm = hands[0].landmarks if len(hands) == 1 else None
            view = frame.copy()
            h, w = view.shape[:2]
            if not session.done:
                session.feed(now, lm)
            if session.done and result is None:
                result = session.finish(engine.config)
                print("\n=== 결과 ===")
                for line in result.summary_lines():
                    print(" ", line)
                print("  저장될 값:", result.updates or "(없음)")
                try:  # numbers only, for diagnosing a bad result afterwards
                    import json
                    out = ROOT / "logs" / f"calibration-{time.strftime('%Y%m%d-%H%M%S')}.json"
                    out.parent.mkdir(exist_ok=True)
                    out.write_text(json.dumps({"result": report_dict(result), "raw": raw_dump(session)},
                                              ensure_ascii=False), encoding="utf-8")
                    print(f"  측정 기록: logs/{out.name}")
                except OSError as exc:
                    print("  측정 기록을 쓰지 못했습니다:", exc)
                print("  저장하려면 Y, 버리려면 N (Esc = 취소)")
            if lm is not None:
                for p in lm:
                    cv2.circle(view, (int(p.x * w), int(p.y * h)), 3, (189, 229, 70), -1)
            bar = np.zeros((190, w, 3), np.uint8)
            if not session.done:
                step = session.step
                n, total, frac = session.progress(now)
                if step.key != last_step:
                    last_step = step.key
                    print(f"[{n}/{total}] {step.prompt}")
                label = FINGER_LABELS.get(step.finger, "")
                wait = session.preparing(now)
                state = "show your hand" if wait < 0 else f"get ready {wait:.0f}" if wait > 0 else "GO"
                text(bar, f"[{n}/{total}] {step.key} {label}  {state}", 36)
                if step.finger:
                    text(bar, f"pinch reps: {session.reps_so_far()}/3", 72, 0.7, (189, 229, 70))
                text(bar, "hand: OK" if lm is not None else "hand: NOT VISIBLE (show the " + args.hand + " hand)", 104,
                     0.6, (189, 229, 70) if lm is not None else (80, 80, 255))
                cv2.rectangle(bar, (24, 118), (24 + int((w - 48) * frac), 126), (189, 229, 70), -1)
                if step.finger and session.last_distance is not None:
                    # live gauge of thumb-to-fingertip distance (0 .. 0.12); the line = the opening that counts
                    gx0, gx1, gy = 24, w - 24, 152
                    cv2.rectangle(bar, (gx0, gy - 8), (gx1, gy + 8), (70, 70, 70), 1)
                    cur = int(gx0 + (gx1 - gx0) * min(1.0, session.last_distance / 0.12))
                    cv2.rectangle(bar, (gx0, gy - 6), (cur, gy + 6), (189, 229, 70), -1)
                    if step.finger == "index":
                        mark = int(gx0 + (gx1 - gx0) * OPEN_MIN_INDEX / 0.12)
                        cv2.line(bar, (mark, gy - 14), (mark, gy + 14), (80, 180, 255), 2)
                        text(bar, "open past the line", gy + 34, 0.5, (80, 180, 255))
                hint = session.coach()
                if hint:
                    text(bar, "! " + ("open your fingers wider" if "활짝" in hint else "pinch and release"), 180, 0.6, (80, 180, 255))
                if hint != last_hint:
                    last_hint = hint
                    if hint:
                        print("  ※", hint)
            else:
                text(bar, "Done. Y = save   N = discard   (see the console)", 40)
                text(bar, "saved values: " + (", ".join(f"{k}={v}" for k, v in list(result.updates.items())[:3]) or "none"), 80, 0.55)
            cv2.imshow(WINDOW, np.vstack([view, bar]))
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                print("취소했습니다. 설정은 바뀌지 않았습니다.")
                return 2
            if session.done and key in (ord("y"), ord("Y")):
                if args.no_save or not result.updates:
                    print("저장하지 않았습니다." if args.no_save else "저장할 값이 없습니다.")
                    return 0 if args.no_save else 3
                existed = args.name.strip() in profiles.list_profiles(DEFAULT_CONFIG_PATH.parent)
                path = profiles.save_profile(DEFAULT_CONFIG_PATH.parent, args.name, result.updates, report_dict(result))
                profiles.set_active(DEFAULT_CONFIG_PATH.parent, args.name)
                print(f"프로필 '{args.name.strip()}'을(를) {'덮어써서 ' if existed else ''}저장하고 선택했습니다: "
                      f"config/profiles/{path.name}. 앱을 다시 시작하면 적용됩니다. gestures.json은 바뀌지 않았습니다.")
                return 0
            if session.done and key in (ord("n"), ord("N")):
                print("버렸습니다. 설정은 바뀌지 않았습니다.")
                return 0
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    sys.exit(main())
