"""기준값(임계값) 후보 비교. scripts/far_fold_probs.py가 저장한 거리별 확률로, 손 크기에 따라 기준을 낮추는 여러 규칙을 같은 평가로 잰다.

프로젝트 폴더에서:
  uv run --locked --extra qt --extra train python scripts/far_threshold_compare.py <녹화 파일 이름> <npz 경로> [동작 키]

보는 것(동작 = 기본 thumb_index):
  - 거리별 인식: 정답 구간(do)마다 기준 이상이 연속 k프레임이면 인식
  - 요청 없는 구간 반응: 정답 구간에서 0.7초 넘게 떨어진 곳의 반응 횟수(분당)
  - 같이 반응한 엄지+중지: 정답 구간에서 엄지+중지도 연속 k프레임 넘긴 회차 수
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import train_tcn as T  # noqa: E402
from src.gesture_catalog import BY_KEY  # noqa: E402
from src.training_center import filter_selected_recording_paths  # noqa: E402
from src.gesture_gate import DEFAULT_GESTURE_FRAMES, DEFAULT_GESTURE_THRESHOLDS  # noqa: E402
from src.pinch_report import hand_size, read_recording  # noqa: E402


def frame_sizes(frames, raw_frames):
    """Hand size per training frame, from the raw recording by time (a median of the last 8 frames so the rule does not
    flicker); the last known size when a frame has no hand."""
    by_time = {round(float(f["t"]), 4): hand_size(f["lm"]) for f in raw_frames if f.get("lm")}
    raw, last = [], 0.03
    for f in frames:
        last = by_time.get(round(float(f["t"]), 4), last)
        raw.append(last)
    raw = np.asarray(raw)
    return np.array([np.median(raw[max(0, i - 7): i + 1]) for i in range(len(raw))])


def fire_events(prob_col, thr, k, tokens):
    """Frame indexes where `k` frames in a row reached the threshold (the count restarts at every repetition token)."""
    fired, run, prev = [], 0, None
    for i, p in enumerate(prob_col):
        if tokens[i] != prev:
            run, prev = 0, tokens[i]
        run = run + 1 if p >= thr[i] else 0
        if run == k:
            fired.append(i)
    return fired


def held_order_all(data):
    return [int(c) for c in data["held"]]


def prob_for(data, cond, held, ci):
    """One probability column over all frames: each frame takes the model that did not train on its posture."""
    col = np.zeros(len(cond), dtype=np.float32)
    for h in held:
        idx = cond == h
        col[idx] = data[f"prob_{h}"][idx, ci]
    return col


def main(argv):
    target, npz = argv[1], argv[2]
    key = argv[3] if len(argv) > 3 else "thumb_index"
    label = BY_KEY[key].label
    data = np.load(npz, allow_pickle=True)
    classes = [str(c) for c in data["classes"]]
    ci = classes.index(label)
    others = [c for c in ("thumb_middle",) if c in classes and c != label]
    # the frames the model saw (hand lost frames are not in them): the same loader as the training, so they line up with prob
    paths = [Path(p) for p in filter_selected_recording_paths([str(p) for p in sorted((ROOT / "recordings").glob("*.jsonl.gz"))])]
    args = T.parse_args(["--no-export", "--keep-rest", "--device", "cpu"])
    _, eval_sessions, _ = T.load_training_sessions(paths, args)
    session = eval_sessions[[i for i, p in enumerate(paths) if p.name == target][0]]
    frames, cond = session["frames"], np.asarray(session["cond"])
    assert len(cond) == len(data["prob_" + str(int(data["held"][0]))]), "frames and probabilities do not line up"
    _, raw_frames = read_recording(ROOT / "recordings" / target)
    sizes = frame_sizes(frames, raw_frames)
    t = np.array([float(f["t"]) for f in frames])
    labs = [f.get("label") or {} for f in frames]
    tokens = [(lab.get("set"), lab.get("rep"), lab.get("phase") == "do") for lab in labs]
    do_all = np.array([i for i in range(len(frames)) if labs[i].get("task") == key and labs[i].get("phase") == "do"])
    base = float(DEFAULT_GESTURE_THRESHOLDS.get(label, 0.7))
    k = int(DEFAULT_GESTURE_FRAMES.get(label, 3))
    print(f"동작 {label}: 기준 {base}, 연속 {k}프레임. 평가 창 {len(frames)}개")

    # candidate rules: threshold as a function of the (smoothed) hand size
    rules = {"고정 0.62 (지금)": lambda s: np.full_like(s, base)}
    for off in (0.03, 0.05, 0.08):
        rules[f"고정 {base - off:.2f}"] = (lambda o: (lambda s: np.full_like(s, base - o)))(off)
    for ref in (0.04, 0.03):
        for slope in (0.15, 0.25, 0.4):
            rules[f"크기 비례 기준 {ref:.2f} 이하에서 {slope:.2f}배로 낮춤"] = (
                lambda r, sl: (lambda s: base - sl * np.clip((r - s) / r, 0.0, 1.0)))(ref, slope)
    rows = []
    for held in [int(c) for c in data["held"]]:
        prob = data[f"prob_{held}"]
        sel = np.flatnonzero(cond == held)
        do_idx = np.array([i for i in range(len(frames)) if labs[i].get("task") == key and labs[i].get("phase") == "do"])
        reps = {}
        for i in do_idx:
            if cond[i] == held:
                reps.setdefault((labs[i].get("set"), labs[i].get("rep")), []).append(int(i))
        do_times = t[do_idx]
        quiet = np.array([i for i in range(len(frames)) if cond[i] == held and np.min(np.abs(do_times - t[i])) > 0.7])
        quiet_minutes = sum(t[i + 1] - t[i] for i in quiet if i + 1 < len(t) and 0 < t[i + 1] - t[i] < 0.5) / 60
        size_med = float(np.median(sizes[sel]))
        for rule_name, rule in rules.items():
            thr = rule(sizes)
            fired = fire_events(prob[:, ci], thr, k, tokens)
            hit = sum(1 for idx in reps.values() if any(i in set(fired) for i in idx))
            far = [i for i in fired if cond[i] == held and i in set(quiet.tolist())]
            both = 0
            for o in others:
                cj = classes.index(o)
                thr_o = np.asarray(rule(sizes)) + (float(DEFAULT_GESTURE_THRESHOLDS.get(o, 0.7)) - base)
                of = set(fire_events(prob[:, cj], thr_o, int(DEFAULT_GESTURE_FRAMES.get(o, 3)), tokens))
                both = sum(1 for idx in reps.values() if any(i in of for i in idx))
            rows.append((held, size_med, rule_name, hit, len(reps), len(far), quiet_minutes, both))
    # where do the baseline's unasked fires happen? (the walk between distances is not a place the user cares about)
    thr0 = rules["고정 0.62 (지금)"](sizes)
    fired0 = fire_events(prob_for(data, cond, held_order_all(data), ci), thr0, k, tokens)
    print("\n요청 없는 반응이 일어난 구간(기준 0.62, 정답 구간에서 0.7초 넘게 떨어진 곳):")
    by_phase = {}
    minutes_phase = {}
    do_set = set(do_all.tolist())
    do_times_all = t[do_all]
    for i in range(len(frames) - 1):
        if cond[i] in held_order_all(data) and np.min(np.abs(do_times_all - t[i])) > 0.7 and 0 < t[i + 1] - t[i] < 0.5:
            ph = str(labs[i].get("phase"))
            minutes_phase[ph] = minutes_phase.get(ph, 0.0) + (t[i + 1] - t[i]) / 60
    for i in fired0:
        if cond[i] in held_order_all(data) and np.min(np.abs(do_times_all - t[i])) > 0.7:
            ph = str(labs[i].get("phase"))
            by_phase[ph] = by_phase.get(ph, 0) + 1
    for ph in sorted(set(by_phase) | set(minutes_phase)):
        m = minutes_phase.get(ph, 0.0)
        print(f"  {ph:8s} {by_phase.get(ph, 0):3d}회 / {m:4.1f}분 = {by_phase.get(ph, 0) / max(m, 1e-9):5.1f}/분")

    # print per rule across the three distances
    by_rule = {}
    for held, size_med, rule_name, hit, n, far, qmin, both in rows:
        by_rule.setdefault(rule_name, []).append((held, size_med, hit, n, far, qmin, both))
    held_order = [int(c) for c in data["held"]]
    print("\n자세별 손 크기:", {h: round(float(np.median(sizes[cond == h])), 3) for h in held_order})
    header = "규칙".ljust(34) + " | " + " | ".join(f"자세{h} 인식" for h in held_order) + " | 요청 없는 반응(분당) | 엄지+중지 동시"
    print(header)
    for rule_name, vals in by_rule.items():
        recog = " | ".join(f"{v[2]:>2d}/{v[3]:<2d}".center(8) for v in vals)
        far = sum(v[4] for v in vals)
        minutes = sum(v[5] for v in vals)
        both = sum(v[6] for v in vals)
        total = sum(v[3] for v in vals)
        print(f"{rule_name:34s} | {recog} | {far:>3d}회 ({far / max(minutes, 1e-9):4.1f}/분) | {both:>2d}/{total}")


if __name__ == "__main__":
    main(sys.argv)
