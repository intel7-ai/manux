"""Replay the same eval recordings through the engine under three conditions and score them the same way.

    cd apps/manux
    uv run --python 3.12 python tools/pose_compare.py <eval.jsonl.gz ...> \\
        --tcn-model <tcn.onnx> --pose-model <pose.onnx> [--tcn-mode direct|gate] [--json out.json] [--markdown out.md]

Conditions
  A  rules alone (the v0.4 rule features, no model)
  B  rules + the TCN alone (direct decisions, or the confirmation gate with --tcn-mode gate)
  C  B + the pose assist (veto / rest gate; needs --pose-model)

The recording's labels are the ground truth: `label.target` per frame, `label.gesture` for the gesture id a
class stands for. The bound action of a gesture is the engine's own binding. Frame-level attribution: an
action is judged by the label of the frame it was sent on (no grace period), so a click that lands a few
frames after a segment ends counts against the next segment.

Metrics (all per condition)
  false_right_clicks        right-click actions sent on labelled frames whose class is not the right-click
                            class, with a per-minute rate over those frames' time
  idle                      frames labelled with an idle label (default `idle`; --idle-labels) with the cursor
                            moved (mouse.move) and with any tap/begin action sent
  click_success / latency   per labelled class whose gesture has a bound action: the share of its segments in
                            which that action was sent, and frames from the segment start to the first one
  vetoes (C only)           actions the pose assist dropped (events `pose:veto:...`, `pose:idle:...`), split by
                            the label of that frame: justified (the frame was not an attempt of that gesture),
                            unjustified (it was), unlabeled
  pose_silent               share of frames with a hand on which the pose assist gave no evidence (C only)

Safety: only recordings marked `split=eval` are used; a recording that a model's card lists among its training
data (sha256) is refused; output has no names or paths (recordings and models appear as short hashes).

This tool has been checked on synthetic recordings and fake models only. Nothing here is a measurement of real
users until real eval recordings and real models are run through it (docs/pose-assist.md, "측정 절차").
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from manux.app import _FEATURES, build_engine, schema
from manux.core.contracts import OutputResult, OutputTarget, Stage
from manux.data.recording import read, replay
from manux.engine.commands import SetControl, SetStage
from manux.features import pose, tcn_gate
from manux.ml.card import ModelError, card_path
from manux.ml.predictor import load_model
from manux.perception.local3d import FEATURE_VERSION, FEATURES
from manux.settings.store import Settings
import pose_data

CONDITIONS = ("A", "B", "C")
RIGHT_CLICK_GESTURE = "pinch.thumb_ring"
MOVE_ACTION = "mouse.move"
HIT_PHASES = ("tap", "begin")
GAP = 0.5                       # seconds: a larger time step starts a new labelled segment
MAX_DT = 0.5                    # seconds counted for one frame when summing durations


class Sink:
    """Takes every command. The replay measures what the engine decides, not what an OS does."""

    def apply(self, cmd):
        return OutputResult(True)

    def release_all(self, reason):
        pass

    def close(self):
        pass


@dataclass
class FrameLog:
    t: float
    target: Optional[str]                         # label.target (None = unlabelled)
    has_hand: bool
    results: Tuple[Tuple[str, str], ...]          # (action, phase) sent on this frame
    events: Tuple[str, ...]
    silent: Optional[bool] = None                 # pose evidence missing (condition C only)


@dataclass
class Recorded:
    sha: str
    rec: Any                                      # the read Recording
    class_gesture: Dict[str, str]
    log: Dict[str, List[FrameLog]] = field(default_factory=dict)


# ----------------------------------------------------------------------------- running

def _engine(condition: str, settings: Settings, tcn_model, tcn_mode: str, pose_assist):
    direct, filters = None, ()
    if condition != "A" and tcn_model is not None:
        model = tcn_model()
        if tcn_mode == "direct":
            direct = model
        else:
            eligible = {gid for module, _ in _FEATURES for gid in module.DEFAULT_BINDINGS
                        if module.GESTURE_PHASES[gid] & {"tap", "begin"}}
            gate = tcn_gate.create_gate(model.path, settings.section("tcn_gate"), eligible=eligible)
            filters = (gate.gate,) if gate.gate is not None else ()
    engine = build_engine(settings, "pinch", filters, direct_model=direct,
                          pose_assist=pose_assist if condition == "C" else None)
    engine.sinks[OutputTarget.WINDOWS] = Sink()
    engine.commands.put(SetStage(Stage.MAIN))
    engine.commands.put(SetControl(True))
    return engine


def run_condition(condition: str, rec, settings: Settings, *, tcn_model=None, tcn_mode="direct",
                  pose_factory: Optional[Callable[[], Any]] = None) -> List[FrameLog]:
    assist = pose_factory() if condition == "C" and pose_factory is not None else None
    engine = _engine(condition, settings, tcn_model, tcn_mode, assist)
    logs: List[FrameLog] = []
    try:
        for item in replay(rec):
            snap = engine.step(item.frame, item.hand)
            label = item.record.get("label") or {}
            silent = None
            if assist is not None and item.hand is not None:
                ev = assist.feature.evidence
                silent = ev is None or ev.t != item.frame.t
            logs.append(FrameLog(item.frame.t, label.get("target") or None, item.hand is not None,
                                 tuple((a, p) for a, p, _ in snap.results), tuple(snap.events), silent))
    finally:
        engine.shutdown()
    return logs


# ----------------------------------------------------------------------------- scoring

def _seconds(logs: Sequence[FrameLog], mask: Sequence[bool]) -> float:
    total = 0.0
    for i, keep in enumerate(mask):
        if keep and i + 1 < len(logs):
            total += min(max(logs[i + 1].t - logs[i].t, 0.0), MAX_DT)
    return total


def _segments(logs: Sequence[FrameLog]) -> List[Tuple[str, int, int]]:
    """(target, first index, last index) of consecutive frames of one label (a gap in time starts a new one)."""
    out: List[Tuple[str, int, int]] = []
    for i, f in enumerate(logs):
        if f.target is None:
            continue
        if out and out[-1][0] == f.target and out[-1][2] == i - 1 and f.t - logs[i - 1].t <= GAP:
            out[-1] = (f.target, out[-1][1], i)
        else:
            out.append((f.target, i, i))
    return out


def score(per_recording: Sequence[Tuple[Sequence[FrameLog], Mapping[str, str]]], bindings: Mapping[str, str], *,
          right_click_gesture: str = RIGHT_CLICK_GESTURE, idle_labels: Sequence[str] = ("idle",)) -> Dict[str, Any]:
    """The metrics for one condition over several recordings. `bindings` maps gesture id -> action id."""
    right_action = bindings.get(right_click_gesture)
    false_clicks, other_seconds = 0, 0.0
    idle_frames = idle_moves = idle_clicks = 0
    classes: Dict[str, Dict[str, Any]] = {}
    veto = {"justified": 0, "unjustified": 0, "unlabeled": 0}
    idle_gate = {"justified": 0, "unjustified": 0, "unlabeled": 0}
    hand_frames = silent_frames = 0
    frames_total = 0
    has_pose = False
    for logs, class_gesture in per_recording:
        frames_total += len(logs)
        right_class = {c for c, g in class_gesture.items() if g == right_click_gesture}
        other = [f.target is not None and f.target not in right_class and f.target not in idle_labels for f in logs]
        other_seconds += _seconds(logs, other)
        for i, f in enumerate(logs):
            hits = [(a, p) for a, p in f.results if p in HIT_PHASES]
            if other[i] and right_action is not None:
                false_clicks += sum(1 for a, _ in hits if a == right_action)
            if f.target in idle_labels:
                idle_frames += 1
                idle_moves += any(a == MOVE_ACTION for a, _ in f.results)
                idle_clicks += len(hits)
            for note in f.events:
                kind = note.split(":", 2)[1] if note.startswith("pose:") else None
                if kind not in ("veto", "idle"):
                    continue
                gesture = note.split(":")[2]
                if f.target is None:
                    bucket = "unlabeled"
                elif class_gesture.get(f.target) == gesture:
                    bucket = "unjustified"
                else:
                    bucket = "justified"
                (veto if kind == "veto" else idle_gate)[bucket] += 1
            if f.silent is not None:
                has_pose = True
                hand_frames += 1
                silent_frames += bool(f.silent)
        for target, first, last in _segments(logs):
            gesture = class_gesture.get(target)
            action = bindings.get(gesture) if gesture else None
            if action is None:
                continue
            row = classes.setdefault(target, {"gesture": gesture, "action": action, "segments": 0, "hit": 0, "latency_frames": []})
            row["segments"] += 1
            for i in range(first, last + 1):
                if any(a == action and p in HIT_PHASES for a, p in logs[i].results):
                    row["hit"] += 1
                    row["latency_frames"].append(i - first)
                    break
    per_class = {}
    for name, row in sorted(classes.items()):
        lat = row["latency_frames"]
        per_class[name] = {"gesture": row["gesture"], "action": row["action"], "segments": row["segments"], "hit": row["hit"],
                           "success_rate": round(row["hit"] / row["segments"], 4) if row["segments"] else None,
                           "latency_frames_median": median(lat) if lat else None}
    total_segments = sum(r["segments"] for r in classes.values())
    total_hit = sum(r["hit"] for r in classes.values())
    out: Dict[str, Any] = {
        "frames": frames_total,
        "false_right_clicks": {"count": false_clicks, "labelled_other_seconds": round(other_seconds, 2),
                               "per_minute": round(false_clicks * 60 / other_seconds, 3) if other_seconds else None},
        "idle": {"frames": idle_frames, "cursor_move_frames": idle_moves, "click_events": idle_clicks},
        "click_success": {"segments": total_segments, "hit": total_hit,
                          "rate": round(total_hit / total_segments, 4) if total_segments else None, "per_class": per_class},
    }
    if has_pose:
        out["vetoes"] = {"veto": dict(veto), "rest": dict(idle_gate)}
        out["pose_silent"] = {"hand_frames": hand_frames, "silent_frames": silent_frames,
                              "share": round(silent_frames / hand_frames, 4) if hand_frames else None}
    return out


def compare(recordings: Sequence[Recorded], *, tcn_model=None, tcn_mode="direct", pose_factory=None,
            settings: Optional[Settings] = None, conditions: Sequence[str] = CONDITIONS,
            idle_labels: Sequence[str] = ("idle",)) -> Dict[str, Any]:
    """Run every condition that has what it needs and score them. Conditions without their model are left out."""
    settings = settings or Settings(schema())
    wanted = [c for c in conditions if c == "A" or (c == "B" and tcn_model is not None)
              or (c == "C" and tcn_model is not None and pose_factory is not None)]
    probe = build_engine(settings, "pinch")
    try:
        bindings = {g: b.action for g, b in probe.router.bindings.items() if b is not None}
    finally:
        probe.shutdown()
    result: Dict[str, Any] = {}
    for condition in wanted:
        rows = []
        for rec in recordings:
            logs = run_condition(condition, rec.rec, settings, tcn_model=tcn_model, tcn_mode=tcn_mode,
                                 pose_factory=pose_factory)
            rows.append((logs, rec.class_gesture))
        result[condition] = score(rows, bindings, idle_labels=idle_labels)
    return result


# ----------------------------------------------------------------------------- inputs and output

def _hashes(model_path: Path) -> Set[str]:
    raw = json.loads(card_path(model_path).read_text(encoding="utf-8"))
    out = set()
    for row in raw.get("data") or []:
        digest = row.get("sha256") if isinstance(row, dict) else None
        if not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
            raise ValueError("a model card must identify every training recording by SHA256")
        out.add(digest)
    if not out:
        raise ValueError("a model card without training recordings cannot be checked against the eval recordings")
    return out


def load_eval(paths: Sequence[Path], model_paths: Sequence[Path]):
    """Read eval recordings and refuse any that a model was trained on. Returns objects with .sha, .frames_rec."""
    seen, loaded = set(), []
    for path in paths:
        digest = pose_data.file_sha256(path)
        if digest in seen:
            continue
        seen.add(digest)
        rec = read(Path(path))
        if not rec.complete or rec.bad_lines:
            raise ValueError("an eval recording is incomplete or corrupt")
        if rec.meta.get("split") != "eval":
            raise ValueError("only recordings marked split=eval can be compared")
        gestures: Dict[str, str] = {}
        for frame in rec.frames:
            label = frame.get("label") or {}
            if label.get("target") and label.get("gesture"):
                if gestures.setdefault(label["target"], label["gesture"]) != label["gesture"]:
                    raise ValueError("conflicting gesture IDs in an eval recording")
        loaded.append(Recorded(digest, rec, gestures))
    for model_path in model_paths:
        pose_data.check_files_disjoint(_hashes(Path(model_path)), seen)
    return loaded


def markdown(report: Mapping[str, Any]) -> str:
    cond = report["conditions"]
    names = {"A": "A 규칙", "B": "B 규칙+TCN", "C": "C TCN+자세 보조"}
    cols = [c for c in CONDITIONS if c in cond]

    def row(label, pick):
        return "| " + label + " | " + " | ".join(str(pick(cond[c])) for c in cols) + " |"

    def num(value):
        return "-" if value is None else value
    lines = ["| 지표 | " + " | ".join(names[c] for c in cols) + " |", "| --- |" + " --- |" * len(cols),
             row("오인식 우클릭 횟수", lambda m: m["false_right_clicks"]["count"]),
             row("오인식 우클릭 /분", lambda m: num(m["false_right_clicks"]["per_minute"])),
             row("휴식 프레임 수", lambda m: m["idle"]["frames"]),
             row("휴식 중 커서 이동 프레임", lambda m: m["idle"]["cursor_move_frames"]),
             row("휴식 중 클릭 발생", lambda m: m["idle"]["click_events"]),
             row("클릭 성공률", lambda m: num(m["click_success"]["rate"])),
             row("클릭 성공 구간/전체", lambda m: f"{m['click_success']['hit']}/{m['click_success']['segments']}"),
             row("거부권 취소(정당/부당/미라벨)", lambda m: "-" if "vetoes" not in m else "/".join(
                 str(m["vetoes"]["veto"][k]) for k in ("justified", "unjustified", "unlabeled"))),
             row("휴식 게이트 취소(정당/부당/미라벨)", lambda m: "-" if "vetoes" not in m else "/".join(
                 str(m["vetoes"]["rest"][k]) for k in ("justified", "unjustified", "unlabeled"))),
             row("자세 보조 침묵 프레임 비율", lambda m: "-" if "pose_silent" not in m else num(m["pose_silent"]["share"]))]
    return "\n".join(lines) + "\n"


def build_report(recordings, results: Mapping[str, Any], model_hashes: Mapping[str, str]) -> Dict[str, Any]:
    return {"tool": "pose_compare", "level": "frame", "measured_on": "eval recordings given on the command line",
            "recordings": [r.sha[:12] for r in recordings], "models": dict(model_hashes), "conditions": dict(results)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+", type=Path, help="eval recordings (split=eval, with world joints)")
    parser.add_argument("--tcn-model", type=Path, help="candidate / active TCN .onnx (+ card); needed for B and C")
    parser.add_argument("--pose-model", type=Path, help="pose .onnx (+ card); needed for C")
    parser.add_argument("--tcn-mode", choices=("direct", "gate"), default="direct")
    parser.add_argument("--idle-labels", nargs="*", default=["idle"])
    parser.add_argument("--tau-veto", type=float)
    parser.add_argument("--tau-idle", type=float)
    parser.add_argument("--k-frames", type=int)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    try:
        models = [m for m in (args.tcn_model, args.pose_model) if m is not None]
        recordings = load_eval(args.recordings, models)
        settings = Settings(schema())
        for key, value in (("tau_veto", args.tau_veto), ("tau_idle", args.tau_idle), ("k_frames", args.k_frames)):
            if value is not None and not settings.set(f"pose_assist.{key}", value):
                raise ValueError(f"{key} is outside its allowed range")
        tcn = None
        if args.tcn_model is not None:
            loaded = load_model(args.tcn_model, FEATURE_VERSION, FEATURES)
            tcn = lambda: loaded                                       # noqa: E731
        factory = None
        if args.pose_model is not None:
            def factory():
                made = pose.create_pose_assist(args.pose_model, settings.section(pose.ID))
                if made.assist is None:
                    raise ValueError(f"pose model: {made.text}")
                return made.assist
            factory()                                                  # fail early, before any replay
        results = compare(recordings, tcn_model=tcn, tcn_mode=args.tcn_mode, pose_factory=factory, settings=settings,
                          idle_labels=args.idle_labels)
        ids = {name: sha256(Path(p).read_bytes()).hexdigest()[:12] for name, p in
               (("tcn", args.tcn_model), ("pose", args.pose_model)) if p is not None}
        report = build_report(recordings, results, ids)
        text = markdown(report)
        if args.json:
            args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.markdown:
            args.markdown.write_text(text, encoding="utf-8")
        print(text)
    except (ValueError, OSError, ModelError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
