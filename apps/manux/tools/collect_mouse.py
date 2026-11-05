"""Collect a local, labeled mouse dataset from a webcam, with Windows output disabled.

Tasks come from the one catalog (`manux.data.catalog`), conditions and timing from a capture plan
(`manux.data.capture_plan`, `--plan basic|positions|diverse|saved|<file>`). One completed set
(one task in one plan condition) = one recording in the user's data folder, never the repository.

    uv run --python 3.12 --extra vision python tools/collect_mouse.py --list --plan positions
    uv run --python 3.12 --extra vision python tools/collect_mouse.py --profile tester01 --focus thumb_ring
"""
from __future__ import annotations

import argparse
import re
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter, strftime
from uuid import uuid4

import numpy as np

from manux.app import build_engine, camera_session, schema
from manux.data import capture_plan
from manux.data.catalog import BY_KEY, MODES, NONE, tasks_for
from manux.data.guided import GuidedSession
from manux.data.recorder import SessionRecorder
from manux.engine.commands import RequestMode
from manux.perception.local3d import hand_frame
from manux.settings.paths import resolve
from manux.settings.store import load


def preset_tasks(mode):
    """The catalog's tasks for one hand-control mode: neutral first, then the mode's own, then the shared ones."""
    pool = tasks_for(mode=mode)
    return (BY_KEY[NONE], *(t for t in pool if t.modes), *(t for t in pool if not t.modes and t.key != NONE))


PRESETS = {mode: preset_tasks(mode) for mode in MODES}
SETTLE = 0.35   # s of each rest still labelled transition (the hand on its way back)
TITLE = "MANUX Dataset"
MIN_FRAMES = 3


def tracked(hand):
    return hand is not None and hand.world is not None


class Take:
    """Keep a single take open; only usable completed takes become training input.

    One take = one task in one plan condition. The session spans the whole plan so `condition_index`
    stays the row's index in the plan; `condition_key` names the physical condition."""

    def __init__(self, task, plan, condition, folder, meta, now):
        self.task = task
        self.session = GuidedSession([task], plan=plan, settle=SETTLE)
        self.session.index = condition
        self.session.advance(now)
        self.valid = Counter()
        stamp = f"{strftime('%Y%m%d-%H%M%S')}-{task.key}-{uuid4().hex}"
        self.recorder = SessionRecorder(folder, "guided", {**meta, "guided": self.session.describe()},
                                        now=now, stamp=stamp)

    def add(self, frame, hand, intents):
        label = self.session.label(frame.t)
        if label["target"] == self.task.label and tracked(hand):
            if hand_frame(np.round(hand.world, 5)) is not None:
                self.valid[label["rep"]] += 1
        active = [(gesture, phase) for gesture, phase in intents if phase != "end"]
        rule = next((gid for gid, phase in active if phase in ("begin", "tap")), active[0][0] if active else None)
        self.recorder.frame(frame.t, hand, label=label, rule={"gesture": rule} if rule else None)

    def finish(self, now):
        reps = range(1, self.session.reps + 1) if not self.task.free else (0,)
        missing = [rep for rep in reps if self.valid[rep] < (15 if self.task.free else MIN_FRAMES)]
        if missing:
            self.abort()
            return None, f"Retry: {len(missing)} attempt(s) lacked usable hand tracking."
        self.recorder.event(now, "set_done")
        path = self.recorder.close()
        return path, f"Saved {self.task.key} ({sum(self.valid.values())} tracked frames)."

    def abort(self):
        if not self.recorder.closed:
            self.recorder.abort()


def draw(cv2, image, hand, lines, progress):
    h, w = image.shape[:2]
    for x, y, _ in hand.landmarks if hand is not None else ():
        cv2.circle(image, (int(x * w), int(y * h)), 4, (50, 240, 120), -1)
    preview = cv2.resize(image, (800, max(1, round(800 * h / w))))
    bar_y = len(lines) * 26 + 12
    panel = np.full((bar_y + 16, 800, 3), 24, dtype=np.uint8)
    for i, line in enumerate(lines):
        cv2.putText(panel, line, (15, 26 + i * 26), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (240, 240, 240), 1,
                    cv2.LINE_AA)
    cv2.rectangle(panel, (15, bar_y), (785, bar_y + 6), (80, 80, 80), -1)
    cv2.rectangle(panel, (15, bar_y), (15 + int(770 * progress), bar_y + 6), (50, 210, 120), -1)
    cv2.imshow(TITLE, np.vstack((panel, preview)))


def capture(cv2, camera, tracker, engine, hand_side, sets, args, folder, settings, plan):
    """Record `sets` = [(plan condition index, task), ...] one take at a time."""
    index, take, start = 0, None, False
    message = ""
    last_frame = perf_counter()
    try:
        cv2.namedWindow(TITLE, cv2.WINDOW_NORMAL)
        cv2.imshow(TITLE, np.zeros((320, 800, 3), dtype=np.uint8))
        rows = capture_plan.enabled(plan)
        shown = None
        while index < len(sets):
            cond, task = sets[index]
            if shown != index:
                shown = index
                print(f"[{index + 1}/{len(sets)}] {rows[cond]['name']} ({capture_plan.description(rows[cond])})"
                      f" - {task.name}: {task.do}", flush=True)
            item = camera.read(timeout=0.05)
            if item is None and perf_counter() - last_frame > 5:
                raise OSError("No camera frames for 5 seconds. Completed sets are saved.")
            if item is not None:
                last_frame = perf_counter()
                frame, image = item
                hand = tracker.step(frame, image, hand_side).hand
                if start:
                    mode = camera.actual_mode()
                    meta = {
                        "person": args.profile, "preset": args.preset, "split": args.split,
                        "control_hand": hand_side,
                        "camera": {"mode": [frame.width, frame.height, mode[2] if mode else camera.fps],
                                   "mirror": frame.mirrored, "rotation": frame.rotation},
                        "thresholds": {owner: {k: v for k, v in settings.section(owner).items()
                                               if isinstance(v, (bool, int, float))}
                                       for owner in ("pinch", "pointer", "motion", "tracking")},
                    }
                    take = Take(task, plan, cond, folder, meta, frame.t)
                    engine.commands.put(RequestMode("motion" if task.direction else None))
                    start, message = False, ""
                snapshot = engine.step(frame, hand)
                progress = 0.0
                if take is None:
                    prompt = "Press SPACE to start this set."
                elif take.session.update(frame.t, available=tracked(hand)):
                    saved, message = take.finish(frame.t)
                    take = None
                    print(message if saved is None else f"{message} {saved.name}", flush=True)
                    if saved is not None:
                        index += 1
                        continue
                    prompt = "Press SPACE to retry this set."
                else:
                    take.add(frame, hand, snapshot.intents)
                    label = take.session.label(frame.t)
                    progress = take.session.view(frame.t)["progress"]
                    phase = label["phase"]
                    prompt = {"prep": "GET READY", "rest": "REST - relax your hand", "free": "GO", "do": "GO",
                              "paused": "PAUSED - show your hand (timer stopped)"}[phase]
                    if label["rep"]:
                        prompt += f"  {label['rep']}/{take.session.reps}"
                status = ("Hand tracked" if hand is not None and hand.world is not None
                          else f"Show your {hand_side.lower()} hand to the camera")
                # OpenCV cannot draw Korean: the window shows ids, the console the Korean prompt.
                lines = [f"{index + 1}/{len(sets)}  {task.key} (class {task.label})  |  condition {cond + 1}",
                         prompt, message, status,
                         "SPACE Start   R Retry   N Skip (idle)   ESC Exit"]
                draw(cv2, image, hand, [line for line in lines if line], progress)
            key = cv2.waitKey(1) & 0xFF
            if key == 27 or cv2.getWindowProperty(TITLE, cv2.WND_PROP_VISIBLE) < 1:
                return
            if key in (ord("r"), ord("R")) and take is not None:
                take.abort()
                take, message = None, "Current set discarded. Press SPACE to retry."
            if take is None and key in (ord("n"), ord("N")):
                start = False
                index += 1
            if take is None and key == 32:
                start = True
    finally:
        if take is not None:
            take.abort()
        cv2.destroyAllWindows()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="list the tasks and plan conditions, then exit")
    parser.add_argument("--profile", help="anonymous tester alias (1-32 letters, digits, _ or -)")
    parser.add_argument("--preset", choices=PRESETS, default="pinch", help="hand-control mode")
    parser.add_argument("--focus", nargs="+", help="catalog task keys to collect, or omit for the preset's tasks")
    parser.add_argument("--plan", default="basic",
                        help="capture plan: basic | positions | diverse | saved (your stored plan) | <plan.json>")
    parser.add_argument("--condition", nargs="+",
                        help="only these plan conditions (names or 1-based numbers); default: every enabled one")
    parser.add_argument("--split", choices=("train", "eval"), default="train")
    parser.add_argument("--reps", type=int, help="override the plan's repetitions per set (1-30)")
    parser.add_argument("--free-seconds", type=int, help="override the plan's seconds per free task (10-900)")
    parser.add_argument("--source", help="camera index or stream URL (defaults to configured camera)")
    parser.add_argument("--hand-model", type=Path)
    parser.add_argument("--control-hand", choices=("Left", "Right"))
    return parser, parser.parse_args(argv)


def resolve_tasks(parser, args):
    if not args.focus:
        return PRESETS[args.preset]
    allowed = {task.key for task in PRESETS[args.preset]}
    unknown = [key for key in args.focus if key not in allowed]
    if unknown:
        parser.error(f"task(s) not in the {args.preset} preset: {', '.join(unknown)}; see --list")
    return tasks_for(args.focus)


def resolve_plan(parser, args, config_dir):
    """The plan (CLI overrides applied) and the indices of its enabled rows to record."""
    focus = args.focus[0] if args.focus and len(args.focus) == 1 else ""
    try:
        if args.reps is not None and not 1 <= args.reps <= 30:
            raise ValueError("--reps must be between 1 and 30")
        plan = capture_plan.plan_from(args.plan, focus, config_dir)
        for row in plan["conditions"]:
            if args.reps is not None:
                row["reps"] = args.reps
            if args.free_seconds is not None:
                row["free_seconds"] = args.free_seconds
        plan = capture_plan.normalise(plan)
    except ValueError as error:
        parser.error(str(error))
    rows = capture_plan.enabled(plan)
    if not args.condition:
        return plan, list(range(len(rows)))
    picked = []
    for choice in args.condition:
        index = (int(choice) - 1 if choice.isdigit()
                 else next((i for i, row in enumerate(rows) if row["name"] == choice), -1))
        if not 0 <= index < len(rows):
            parser.error(f"unknown plan condition {choice!r}; see --list")
        picked.append(index)
    return plan, list(dict.fromkeys(picked))


def list_plan(tasks, plan, conditions):
    for task in tasks:
        gesture = task.gesture or (f"({task.related})" if task.related else "capture-only")
        print(f"{task.key:22} class={task.label:18} {gesture:28} {task.name}")
    rows = capture_plan.enabled(plan)
    for i in conditions:
        print(f"condition {i + 1}: {rows[i]['name']} [{capture_plan.condition_key(rows[i])}] "
              f"{capture_plan.description(rows[i])}")
    seconds = capture_plan.duration({"conditions": [rows[i] for i in conditions]}, tasks)
    print(f"about {seconds / 60:.0f} min of recording, without the waits between sets")


def main(argv=None):
    parser, args = parse_args(argv)
    paths = resolve()
    tasks = resolve_tasks(parser, args)
    plan, conditions = resolve_plan(parser, args, paths.config_dir)
    if args.list:
        list_plan(tasks, plan, conditions)
        return 0
    if not args.profile or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", args.profile):
        parser.error("--profile requires an anonymous alias of 1-32 letters, digits, _ or -")
    sets = [(cond, task) for cond in conditions for task in tasks]

    args.profile = args.profile.lower()
    folder = paths.recordings_dir / f"profile-{args.profile}" / args.preset / args.split
    settings = load(schema(), paths.config_dir)
    if args.preset != "pinch":
        settings.apply_profile({"pointer.click_style": "trigger" if args.preset == "trigger" else "pinch"})
    hand_side = args.control_hand or settings.get("tracking.control_hand") or "Right"
    bundled = Path(__file__).resolve().parents[3] / "hand-mouse" / "models" / "hand_landmarker.task"
    user_model = paths.data_dir / "models" / bundled.name
    model = args.hand_model or (user_model if user_model.is_file() else bundled)
    print(f"Recording folder: {folder}", flush=True)
    try:
        import cv2
    except ImportError:
        parser.exit(1, "Camera support requires uv run --extra vision.\n")
    try:
        with ExitStack() as resources:
            camera, tracker = resources.enter_context(camera_session(settings, args.source, model))
            engine = build_engine(settings, "pinch" if args.preset == "pinch" else "pointer", recording=True)
            resources.callback(engine.shutdown)
            capture(cv2, camera, tracker, engine, hand_side, sets, args, folder, settings, plan)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, ImportError, cv2.error) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
