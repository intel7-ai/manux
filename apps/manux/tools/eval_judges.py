"""동일한 독립 평가 녹화로 규칙·MLP·TCN 판정 후보 다섯 개를 비교한다.

실행 예와 지표 정의: docs/eval-judges.md. 카메라·Windows 입력·모델 설치 없음.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from manux.app import build_engine, schema
from manux.data.capture_plan import label_condition_key
from manux.data.catalog import NONE, RESERVED
from manux.data.recording import replay
from manux.data.rule_replay import RuleIntents, TAP, rule_action
from manux.ml.card import ModelError, validate_card
from manux.ml.spatial_mlp import train_spatial_mlp
from manux.perception.local3d import FEATURES, FEATURE_VERSION, frame_features, windows
from manux.perception.pose_angles import frame_pose_angles
from manux.perception.spatial_features import extract_spatial_features
from manux.settings.store import Settings

from compare_tcn import WINDOW, train_multi
from pose_data import check_files_disjoint
from replay import read, summary  # replay CLI와 동일한 reader/요약 경로
from tcn_data import LEGACY_RULES, Segment, class_mapping, predicted_events

JUDGES = ("rules", "mlp", "tcn", "rules_mlp_veto", "mlp_shape_tcn_motion")
MOTION_PREFIX = "motion."
APP = Path(__file__).resolve().parents[1]


@dataclass
class Dataset:
    path: Path
    digest: str
    content: str
    recording: object
    segments: list[Segment]
    session: str
    person: str
    conditions: frozenset[str]

    def keys(self, axis):
        if axis == "condition":
            return self.conditions
        return frozenset({getattr(self, axis)}) - {""}


def load_dataset(path: Path, role: str) -> Dataset:
    path = path.resolve()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    rec = read(path)
    info = summary(rec)
    if not info["complete"] or info["bad_lines"] or not rec.frames:
        raise ValueError("완료되고 손상되지 않은 녹화가 필요합니다")
    if rec.meta.get("split") not in (None, role):
        raise ValueError(f"{role} 파일에 다른 split이 지정되어 있습니다")
    times = [f.get("t") for f in rec.frames]
    if (any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in times)
            or any(b <= a for a, b in zip(times, times[1:]))):
        raise ValueError("프레임 t는 유한하고 엄격히 증가해야 합니다")
    # gzip 타임스탬프/파일명/meta 변경으로 복제 파일 검사를 우회하지 않는다.
    content = hashlib.sha256(json.dumps(rec.frames, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if before != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError("읽는 중 녹화가 변경되었습니다")
    person = rec.meta.get("person") or rec.meta.get("pc")
    person = hashlib.sha256(str(person).encode()).hexdigest()[:12] if person else ""
    session = rec.meta.get("session_id") or rec.meta.get("session") or content
    if not isinstance(session, str):
        raise ValueError("session/session_id는 문자열이어야 합니다")
    session = hashlib.sha256(session.encode()).hexdigest()[:16]
    keys = frozenset(label_condition_key(f.get("label") or {}) for f in rec.frames) - {"unassigned"}
    width, height, _ = rec.frame_size()
    # 평가 시에는 파일 전체를 연속 재생한다. 정답/과제 경계에서 모델을 reset하지 않는다.
    segment = Segment(person, "", rec.frames, frame_features(rec.frames, height / width), "", height / width)
    return Dataset(path, before, content, rec, [segment], session, person, keys)


def folds(train: list[Dataset], evaluation: list[Dataset], axis: str):
    if axis not in ("session", "condition", "person"):
        raise ValueError("split은 session|condition|person만 가능합니다")
    if {x.path for x in train} & {x.path for x in evaluation}:
        raise ValueError("학습/평가 파일 경로가 겹칩니다")
    check_files_disjoint([x.digest for x in train], [x.digest for x in evaluation])
    check_files_disjoint([x.content for x in train], [x.content for x in evaluation])
    for pool in (train, evaluation):
        if len({x.content for x in pool}) != len(pool):
            raise ValueError("같은 내용의 녹화가 중복 지정되었습니다")
    if any(not x.keys(axis) for x in train + evaluation):
        raise ValueError(f"{axis} 그룹 정보가 없는 녹화입니다")
    if axis == "condition" and any(
            label_condition_key(f.get("label") or {}) == "unassigned"
            for d in train + evaluation for f in d.recording.frames
            if (f.get("label") or {}).get("target") not in RESERVED):
        raise ValueError("조건이 지정되지 않은 평가/학습 프레임이 있습니다")
    for key in sorted(set().union(*(x.keys(axis) for x in evaluation))):
        held = [x for x in evaluation if key in x.keys(axis)]
        # 조건이 여러 개인 파일도 일부 프레임만 잘라 학습하지 않는다. held 파일의
        # 모든 그룹을 제외해야 파일 경계와 그룹 경계가 동시에 보존된다.
        excluded = set().union(*(x.keys(axis) for x in held))
        training = [x for x in train if not (x.keys(axis) & excluded)
                    and x.session not in {e.session for e in held}]
        if not training:
            raise ValueError(f"{axis} holdout 뒤 학습 파일이 없습니다")
        yield key, training, held


def intervals(frames):
    """현재 프레임에서 다음 프레임까지의 실제 노출. 마지막/0.3초 초과 공백은 0."""
    ts = [float(f["t"]) for f in frames]
    return [b - a if 0 < b - a <= .3 else 0. for a, b in zip(ts, ts[1:])] + [0.]


def budget_segments(datasets, seconds, classes):
    used = Counter()
    selected = []
    for data in sorted(datasets, key=lambda d: d.content):
        for seg in data.segments:
            duration = intervals(seg.frames)
            targets = seg.targets()  # 기존 스와이프 방향 검사도 재사용
            batch = []

            def flush():
                if batch:
                    selected.append(replace(seg, frames=list(batch), features=frame_features(batch, seg.aspect)))
                    batch.clear()

            for i, frame in enumerate(seg.frames):
                name = targets[i]
                dt = duration[i]
                keep = (name in classes and seg.features[i, -1] == 1 and dt > 0
                        and used[name] + dt <= seconds + 1e-9)
                if keep:
                    if batch and float(frame["t"]) - float(batch[-1]["t"]) > .3:
                        flush()
                    batch.append(frame)
                    used[name] += dt
                else:
                    flush()
            flush()
    return selected, {c: used[c] for c in classes}


def spatial_rows(frames, feature):
    if feature == "angles33":
        return frame_pose_angles(frames)
    rows = np.zeros((len(frames), 96), dtype=np.float32)
    valid = np.zeros(len(frames), dtype=bool)
    for i, frame in enumerate(frames):
        row = extract_spatial_features(frame.get("lm"))
        if row is not None:
            rows[i] = row
            valid[i] = True
    return rows, valid


def mlp_samples(segments, classes, feature):
    xs, ys = [], []
    for seg in segments:
        rows, valid = spatial_rows(seg.frames, feature)
        targets = seg.targets()
        keep = valid & np.array([n in classes for n in targets])
        xs.extend(rows[keep])
        ys.extend(classes.index(n) for n, k in zip(targets, keep) if k)
    y = np.asarray(ys, dtype=np.int64)
    if not len(y) or set(y) != set(range(len(classes))):
        raise ValueError("선택한 학습량/특징에서 일부 클래스의 유효 학습 표본이 없습니다")
    return np.asarray(xs, dtype=np.float32), y


def confirmed_starts(scores, classes, frames, valid, threshold, count):
    """공통 오프라인 정책: 연속 확정 + 중립 재무장. 정답 라벨은 읽지 않는다.

    tcn_data의 run 검출을 재사용한다. 손 소실/공백 뒤 재무장이 필요하고,
    tap/span 모두 시작 하나만 반환한다. 실제 OS/중재/바인딩 지연은 측정하지 않는다.
    """
    scores = np.asarray(scores).copy()
    scores[~valid] = 0
    # 공백에서 run도 끊는다. 첫 프레임은 reset을 위한 무효 표본으로 취급한다.
    reset = {i for i in range(1, len(frames)) if frames[i]["t"] - frames[i-1]["t"] > .3}
    scores[list(reset)] = 0
    candidates = dict(predicted_events(scores, classes, threshold, count))
    neutral = classes.index(NONE)
    armed, run = False, 0
    events = []
    for i, p in enumerate(scores):
        if not valid[i] or i in reset:
            armed, run = False, 0
            continue
        is_neutral = int(p.argmax()) == neutral and p[neutral] >= threshold
        run = run + 1 if is_neutral else 0
        if run >= count:
            armed = True
        if armed and i in candidates:
            events.append((i, candidates[i]))
            armed = False
    return events


def rule_starts(data, mode, recorded=False):
    events, elapsed = [], []
    if recorded:
        conv = RuleIntents({a: (g, TAP) for a, g in LEGACY_RULES.items()})
        for i, frame in enumerate(data.recording.frames):
            start = time.perf_counter()
            rule = frame.get("rule")
            if not isinstance(rule, dict) or not ({"action", "gesture"} & rule.keys()):
                raise ValueError("recorded 비교에는 모든 프레임의 규칙 기록이 필요합니다")
            gesture = rule.get("gesture")
            action = gesture if isinstance(gesture, str) and "." in gesture else rule_action(frame)
            if isinstance(action, str) and "." in action:
                conv.rules.setdefault(action, (action, TAP))
            events.extend((i, it.gesture) for it in conv.step(frame["t"], action) if it.phase in ("tap", "begin"))
            elapsed.append((time.perf_counter() - start) * 1000)
        return events, elapsed
    engine = build_engine(Settings(schema()), mode)
    try:
        for i, item in enumerate(replay(data.recording)):
            start = time.perf_counter()
            snapshot = engine.step(item.frame, item.hand)
            if snapshot.errors:
                raise ValueError(f"규칙 재생 실패: {snapshot.errors}")
            events.extend((i, g) for g, phase in snapshot.intents if phase in ("tap", "begin"))
            elapsed.append((time.perf_counter() - start) * 1000)
    finally:
        engine.shutdown()
    return events, elapsed


def attempt_key(frame):
    label = frame.get("label") or {}
    return tuple(label.get(k) for k in ("target", "task", "set", "rep", "condition_key", "condition_index"))


def score_events(frames, events, classes):
    """하나의 시도당 recall 적중 1회. 모든 명령 시작은 혼동표와 떨림에 남긴다."""
    rows = {c: {"attempts": 0, "hits": 0, "latencies_ms": []} for c in classes if c != NONE}
    confusion = {c: Counter() for c in classes}
    starts = defaultdict(list)
    for index, name in events:
        if not 0 <= index < len(frames):
            raise ValueError("이벤트 프레임 번호가 범위를 벗어났습니다")
        starts[index].append(name)
    latencies, jitter, false = [], 0, 0
    durations = intervals(frames)
    negative_seconds = sum(dt for f, dt in zip(frames, durations) if (f.get("label") or {}).get("target") == NONE)
    ignored = sum(len(starts[i]) for i, f in enumerate(frames)
                  if (f.get("label") or {}).get("target") not in classes)
    i = 0
    while i < len(frames):
        j = i + 1
        while (j < len(frames) and attempt_key(frames[j]) == attempt_key(frames[i])
               and 0 < frames[j]["t"] - frames[j-1]["t"] <= .3):
            j += 1
        truth = (frames[i].get("label") or {}).get("target")
        emitted = [(k, name) for k in range(i, j) for name in starts[k]]
        if truth in classes:
            for _, name in emitted:
                confusion[truth][name] += 1
            if truth == NONE:
                false += len(emitted)
            else:
                rows[truth]["attempts"] += 1
                hits = [k for k, name in emitted if name == truth]
                rows[truth]["hits"] += bool(hits)
                if hits:
                    latency = (frames[hits[0]]["t"] - frames[i]["t"]) * 1000
                    latencies.append(latency)
                    rows[truth]["latencies_ms"].append(latency)
                else:
                    confusion[truth]["miss"] += 1
                jitter += max(0, len(emitted) - 1)
        i = j
    return {"per_gesture": rows, "confusion": {c: dict(v) for c, v in confusion.items()},
            "latencies_ms": latencies, "negative_seconds": negative_seconds,
            "false_starts": false, "repeated_starts": jitter, "ignored_starts": ignored}


def combine_metrics(parts, classes):
    per = {c: {"attempts": 0, "hits": 0, "latencies_ms": []} for c in classes if c != NONE}
    confusion = {c: Counter() for c in classes}
    latencies = []
    for part in parts:
        for c, row in part["per_gesture"].items():
            for key in ("attempts", "hits"):
                per[c][key] += row[key]
            per[c]["latencies_ms"].extend(row["latencies_ms"])
        for c, row in part["confusion"].items():
            confusion[c].update(row)
        latencies.extend(part["latencies_ms"])
    for row in per.values():
        row["recall"] = row["hits"] / row["attempts"] if row["attempts"] else None
        observed = row.pop("latencies_ms")
        row["latency_median_ms"] = float(np.median(observed)) if observed else None
        row["latency_p95_ms"] = float(np.percentile(observed, 95)) if observed else None
    seconds = sum(p["negative_seconds"] for p in parts)
    false = sum(p["false_starts"] for p in parts)
    return {"per_gesture": per, "confusion": {c: dict(v) for c, v in confusion.items()},
            "negative_seconds": seconds, "false_starts": false,
            "false_starts_per_negative_minute": false * 60 / seconds if seconds else None,
            "latency_median_ms": float(np.median(latencies)) if latencies else None,
            "latency_p95_ms": float(np.percentile(latencies, 95)) if latencies else None,
            "latency_samples": len(latencies), "repeated_starts": sum(p["repeated_starts"] for p in parts),
            "ignored_starts": sum(p["ignored_starts"] for p in parts)}


def infer_frames(infer, inputs):
    scores, elapsed = [], []
    for row in inputs:
        start = time.perf_counter()
        scores.append(infer(row))
        elapsed.append((time.perf_counter() - start) * 1000)
    return np.asarray(scores), elapsed


def evaluate_fold(training, evaluation, seconds, args):
    all_segments = [s for d in training for s in d.segments]
    classes = [NONE] + sorted({n for s in all_segments for n in s.labels() if n and n != NONE and n not in RESERVED})
    if len(classes) < 2 or not any(NONE in s.labels() for s in all_segments):
        raise ValueError("학습에 none과 최소 한 동작 클래스가 필요합니다")
    unknown = {n for d in evaluation for s in d.segments for n in s.labels()
               if n and n not in RESERVED and n not in classes}
    if unknown:
        raise ValueError(f"평가에만 있는 학습 미등록 클래스: {sorted(unknown)}")
    mapping = class_mapping(all_segments + [s for d in evaluation for s in d.segments], classes)
    if any(g.endswith((".enter", ".exit")) for g in mapping.values()):
        raise ValueError("모드 진입/종료 클래스는 명령 비교 대상에서 제외해 주세요")
    segments, used = budget_segments(training, seconds, classes)
    x, y = mlp_samples(segments, classes, args.features)
    # TCN도 같은 선택 프레임으로 학습하며 기존 학습기/증강/가중치를 사용한다.
    tcn_frames = sum(1 for s in segments for n, valid in zip(s.targets(), s.features[:, -1])
                     if n in classes and valid == 1)
    feature_version = "pose-angles-v1" if args.features == "angles33" else "spatial-mlp-v1"
    dimension = 33 if args.features == "angles33" else 96
    cards = {}
    for name, win, dim, version in (("mlp", 1, dimension, feature_version), ("tcn", WINDOW, FEATURES, FEATURE_VERSION)):
        cards[name] = validate_card({"classes": classes, "window": win, "features": dim,
                                    "feature_version": version, "threshold": args.threshold,
                                    "frames_in_a_row": args.count, "trained": "offline-evaluation",
                                    "data": [{"sha256": d.digest} for d in training], "held_out_results": [],
                                    "gestures": mapping}, version, dim)
    started = time.perf_counter()
    weights = np.bincount(y, minlength=len(classes))[y].astype(float) ** -.5
    mlp, _ = train_spatial_mlp(x, y, weights, classes=classes, epochs=args.mlp_epochs,
                             seed=args.seed, feature_version=feature_version)
    mlp_seconds = time.perf_counter() - started
    started = time.perf_counter()
    # train_multi delegates to compare_tcn.fit, whose fixed seed is recorded below.
    tcn = train_multi(segments, classes, args.tcn_epochs)
    tcn_seconds = time.perf_counter() - started
    parts = {j: [] for j in JUDGES}
    timings = {j: [] for j in JUDGES}
    by_gesture = {g: c for c, g in mapping.items()}
    motion = {c for c, g in mapping.items() if g.startswith(MOTION_PREFIX)}
    preprocessing = []
    for data in evaluation:
        seg = data.segments[0]
        frames = seg.frames
        started = time.perf_counter()
        mx, mv = spatial_rows(frames, args.features)
        tw = windows(seg.features, WINDOW)
        preprocessing.append((time.perf_counter() - started) * 1000 / len(frames))
        # Same usable-world criterion and no warm-up/label resets hidden from scoring.
        valid = mv & (seg.features[:, -1] == 1)
        mp, mt = infer_frames(mlp.predict_proba, mx)
        tp, tt = infer_frames(lambda w: tcn(w[None, ...])[0], tw)
        tv = np.zeros(len(frames), dtype=bool)
        run = 0
        for i, f in enumerate(frames):
            run = run + 1 if seg.features[i, -1] == 1 and (not i or f["t"] - frames[i-1]["t"] <= .3) else 0
            tv[i] = run >= WINDOW
        raw, rt = rule_starts(data, args.mode, args.rules == "recorded")
        rules = [(i, by_gesture.get(g, "unmodelled:" + g)) for i, g in raw]
        mlp_events = confirmed_starts(mp, classes, frames, valid, args.threshold, args.count)
        tcn_events = confirmed_starts(tp, classes, frames, tv, args.threshold, args.count)
        # A veto only removes existing starts, never generates a replacement command.
        veto = [(i, c) for i, c in rules if valid[i] and not
                (classes[int(mp[i].argmax())] != c and mp[i].max() >= args.veto_threshold)]
        # Motion wins when above threshold; static poses cannot hijack a rejected motion.
        hybrid = mp.copy()
        hybrid_valid = valid.copy()
        for i, scores in enumerate(tp):
            top = classes[int(scores.argmax())]
            if top in motion and scores.max() >= args.threshold:
                hybrid[i] = scores
                hybrid_valid[i] = tv[i]
            elif classes[int(mp[i].argmax())] in motion:
                hybrid[i] = 0  # no static model fallback for movement classes
        events = dict(zip(JUDGES, (rules, mlp_events, tcn_events, veto,
                      confirmed_starts(hybrid, classes, frames, hybrid_valid, args.threshold, args.count))))
        for judge in JUDGES:
            parts[judge].append(score_events(frames, events[judge], classes))
        for name, values in (("rules", rt), ("mlp", mt), ("tcn", tt),
                             ("rules_mlp_veto", np.asarray(rt) + mt),
                             ("mlp_shape_tcn_motion", np.asarray(mt) + tt)):
            timings[name].extend(values)
    training_time = {"rules": 0., "mlp": mlp_seconds, "tcn": tcn_seconds,
                     "rules_mlp_veto": mlp_seconds, "mlp_shape_tcn_motion": mlp_seconds + tcn_seconds}
    result = {}
    for judge in JUDGES:
        result[judge] = combine_metrics(parts[judge], classes)
        result[judge].update(training_seconds=training_time[judge],
                             inference_ms_per_frame=float(np.mean(timings[judge])),
                             inference_p95_ms=float(np.percentile(timings[judge], 95)))
    return {"requested_seconds_per_class": seconds, "used_seconds_per_class": used,
            "classes": classes, "gestures": mapping, "motion_classes": sorted(motion),
            "model_cards": {n: dict(c.raw) for n, c in cards.items()},
            "spatial_preprocessing_ms_per_frame_by_file": preprocessing,
            "training_frames": {"mlp": len(y), "tcn": tcn_frames}, "judges": result}


def outside_repo(path: Path) -> Path:
    path = path.expanduser().resolve()
    roots = {APP.parents[1]}
    # A worktree's .git is a file. The shared repository and every worktree are private-data boundaries.
    common = subprocess.check_output(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                     cwd=APP, text=True).strip()
    roots.add(Path(common).resolve().parent)
    worktrees = subprocess.check_output(["git", "worktree", "list", "--porcelain"], cwd=APP, text=True)
    roots.update(Path(line[9:]).resolve() for line in worktrees.splitlines() if line.startswith("worktree "))
    targets = (path, (path / "results.json").resolve(), (path / "results.md").resolve())
    if any(target == root or target.is_relative_to(root) for root in roots for target in targets):
        raise ValueError("--out은 저장소와 모든 worktree 밖 폴더여야 합니다")
    return path


def markdown(report):
    lines = ["# 판정기 비교", "", "실제 정확도는 이 입력 녹화의 독립 시도에 한정됩니다. 합성 시험은 제품 정확도가 아닙니다.", "",
             "판정 지연은 녹화 시간 기준이며 카메라·OS 출력 지연을 포함하지 않습니다.", ""]
    def fmt(v):
        return "—" if v is None else f"{v:.3f}"
    for fold in report["folds"]:
        for curve in fold["curves"]:
            lines += [f"## {fold['key']} / 동작당 {curve['requested_seconds_per_class']:g}초", "",
                      "| 판정기 | 재현율 (적중/시도) | 부정 구간 오작동/분 | 지연 중앙/p95 ms | 반복 시작 | 학습 s | 추론 ms/프레임 |",
                      "|---|---|---:|---:|---:|---:|---:|"]
            for name, row in curve["judges"].items():
                recall = "; ".join(f"{c}: {r['hits']}/{r['attempts']} ({fmt(r['recall'])})" for c, r in row["per_gesture"].items())
                lines.append(f"| {name} | {recall} | {fmt(row['false_starts_per_negative_minute'])} | "
                             f"{fmt(row['latency_median_ms'])}/{fmt(row['latency_p95_ms'])} | {row['repeated_starts']} | "
                             f"{fmt(row['training_seconds'])} | {fmt(row['inference_ms_per_frame'])} |")
            lines += ["", "실제 사용 학습 초: " + json.dumps(curve["used_seconds_per_class"], ensure_ascii=False), ""]
            for name, row in curve["judges"].items():
                columns = sorted(set(curve["classes"]) | {"miss"} | {c for r in row["confusion"].values() for c in r})
                lines += [f"### {name} 혼동표 (행=정답, 열=명령 시작; miss=적중 없는 시도)", "",
                          "| 정답 | " + " | ".join(columns) + " |", "|---|" + "---:|" * len(columns)]
                for truth, values in row["confusion"].items():
                    lines.append("| " + truth + " | " + " | ".join(str(values.get(c, 0)) for c in columns) + " |")
                lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, nargs="+", required=True)
    parser.add_argument("--eval", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--features", choices=("angles33", "spatial96"), default="angles33")
    parser.add_argument("--split", choices=("session", "condition", "person"), default="session")
    parser.add_argument("--train-seconds", default="10,30,60")
    parser.add_argument("--mlp-epochs", type=int, default=45)
    parser.add_argument("--tcn-epochs", type=int, default=20)
    parser.add_argument("--threshold", type=float, default=.7)
    parser.add_argument("--veto-threshold", type=float, default=.9)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mode", choices=("pointer", "pinch"), default="pinch")
    parser.add_argument("--rules", choices=("current", "recorded"), default="current")
    args = parser.parse_args(argv)
    try:
        out = outside_repo(args.out)  # before reading personal data/training/creating any output
        budgets = [float(x) for x in args.train_seconds.split(",")]
        if (not budgets or any(not math.isfinite(x) or x <= 0 for x in budgets)
                or min(args.count, args.mlp_epochs, args.tcn_epochs) < 1
                or not 0 < args.threshold <= 1 or not 0 < args.veto_threshold <= 1):
            raise ValueError("시간/epoch/연속 수는 양수, 임계값은 (0,1]이어야 합니다")
        train = [load_dataset(p, "train") for p in args.train]
        evaluation = [load_dataset(p, "eval") for p in args.eval]
        if len({d.recording.meta.get("preset") for d in train + evaluation} - {None}) > 1:
            raise ValueError("서로 다른 preset을 함께 평가할 수 없습니다")
        planned = list(folds(train, evaluation, args.split))
        report = {"schema": "manux-judge-evaluation/1", "features": args.features, "split": args.split,
                  "configuration": {k: v for k, v in vars(args).items() if k not in ("train", "eval", "out")},
                  "tcn_seed": 0, "python": platform.python_version(), "numpy": np.__version__,
                  "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=APP, text=True).strip(),
                  "policy": "offline-neutral-rearm-v1", "folds": []}
        for key, training, held in planned:
            report["folds"].append({"key": key, "training_sha256": [d.digest for d in training],
                                    "evaluation_sha256": [d.digest for d in held],
                                    "curves": [evaluate_fold(training, held, n, args) for n in budgets]})
        # Input modification invalidates the whole result; no partial metrics are published.
        if any(hashlib.sha256(d.path.read_bytes()).hexdigest() != d.digest for d in train + evaluation):
            raise ValueError("평가 중 입력 녹화가 변경되었습니다")
        outside_repo(out)  # 출력 파일 자체의 링크도 쓰기 직전에 재확인
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        (out / "results.md").write_text(markdown(report), encoding="utf-8")
    except (OSError, ValueError, ImportError, ModelError) as exc:
        parser.exit(1, f"평가 실패: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
