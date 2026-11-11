"""Replay the live engine readings of the distance-course recordings through the real GestureGate.
  python scripts/gate_replay.py deployed          the models in models/ (never saw these recordings)
  python scripts/gate_replay.py folds 0 1         distance-held-out models, with and without near_thumb recordings
Nothing is written to models/. Results: gate_replay_<mode>.json in logs/."""
import collections
import json
import statistics as st
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "logs"
HERE.mkdir(exist_ok=True)
sys.path = [str(ROOT)] + [p for p in sys.path[1:] if Path(p).resolve() != ROOT / "scripts"]

import src.gesture_gate as GG  # noqa: E402
from src.pinch_report import hand_size, read_recording  # noqa: E402

TARGETS = ["20261109-162920-0bead271-home-pc-guided.jsonl.gz", "20261109-171723-e992bdbc-home-pc-guided.jsonl.gz"]
HELD_TO_SET = {7: 0, 8: 1, 21: 2}
DIST = {0: "1m", 1: "2m", 2: "3m"}
SMALL = 0.03
ASPECT = 720 / 1280
CFG = json.loads((ROOT / "config" / "gestures.json").read_text(encoding="utf-8"))
ACTIONS = {"scroll_grab", "left_click", "right_click", "task_switcher", "show_desktop", "drag_hold"}
_real_features, _real_windows = GG.frame_features, GG.windows


def load(name):
    _, frames = read_recording(ROOT / "recordings" / name)
    return [f for f in frames if "label" in f or "lm" in f]


def to_hand(f):
    if not f.get("lm") or not f.get("world"):
        return None
    return NS(landmarks=[NS(x=p[0], y=p[1], z=p[2]) for p in f["lm"]], world=f["world"], score=(f.get("hand") or [None, 1.0])[1])


def slice_set(frames, k):
    idx = [i for i, f in enumerate(frames) if (f.get("label") or {}).get("set") == k]
    return frames[max(0, idx[0] - 30): idx[-1] + 1]


def run_variant(frames, variant, predict, info, sp, cache):
    """Returns per-frame output action. `cache` holds TCN probabilities per frame so only the first variant pays for them."""
    cfg = dict(CFG)
    if variant == "tcn":
        cfg["spatial_mlp_enabled"] = False
    i_box = [0]
    if cache is not None and len(cache) == len(frames):
        GG.frame_features, GG.windows = (lambda *_: None), (lambda *_: [None])
        pred = lambda _: cache[i_box[0]]
    else:
        GG.frame_features, GG.windows = _real_features, _real_windows
        def pred(x):
            p = np.asarray(predict(x), dtype=np.float64).reshape(-1)
            cache.append(p)
            return p
    model = None if variant == "tcn" else sp
    gate = GG.GestureGate(pred, dict(info, spatial_mlp_path=None, spatial_mlp=None), cfg, spatial_model=model)
    base = (gate.spatial_alpha, gate.spatial_veto_threshold, gate.ood_gate_enabled)
    recent = collections.deque(maxlen=8)
    out = []
    for i, f in enumerate(frames):
        i_box[0] = i
        now = float(f["t"])
        hand = to_hand(f)
        if f.get("lm"):
            recent.append(hand_size(f["lm"]))
        small = bool(recent) and float(np.median(recent)) < SMALL
        if variant == "off_small":
            gate.spatial_model = None if small else sp
        elif variant == "light_small":
            gate.spatial_alpha, gate.spatial_veto_threshold, gate.ood_gate_enabled = (0.15, 2.0, False) if small else base
        gate.step(now, hand, ASPECT)
        r = f.get("rule") or {}
        res = NS(action=r.get("engine_action") or "none", active_gesture=r.get("engine_gesture") or "", is_moving=False)
        out.append(gate.filter(now, res).action)
    GG.frame_features, GG.windows = _real_features, _real_windows
    return out, dict(passed=gate.passed, blocked=gate.blocked, spatial_vetoed=gate.spatial_vetoed, ood_vetoed=gate.ood_vetoed, quality_withheld=gate.quality_withheld)


def score(frames, acts, k):
    t = np.array([float(f["t"]) for f in frames])
    labs = [f.get("label") or {} for f in frames]
    do = [i for i, l in enumerate(labs) if l.get("task") == "thumb_index" and l.get("phase") == "do" and l.get("set") == k]
    reps, latency = {}, []
    for i in do:
        reps.setdefault(labs[i].get("rep"), []).append(i)
    hit = 0
    for idx in reps.values():
        h = [i for i in idx if acts[i] == "scroll_grab"]
        if h:
            hit += 1
            latency.append(t[h[0]] - t[idx[0]])
    do_t = t[do]
    in_set = [i for i, l in enumerate(labs) if l.get("set") == k]
    quiet = {i for i in in_set if np.min(np.abs(do_t - t[i])) > 0.7}
    do_set = set(do)
    minutes = sum(t[i + 1] - t[i] for i in quiet if i + 1 < len(t) and 0 < t[i + 1] - t[i] < 0.5) / 60
    false, wrong = collections.Counter(), collections.Counter()
    prev = None
    for i, a in enumerate(acts):
        if a in ACTIONS and a != prev:
            if i in quiet:
                false[a] += 1
            elif i in do_set and a != "scroll_grab":
                wrong[a] += 1
        prev = a
    return {"hit": hit, "reps": len(reps), "false": sum(false.values()), "false_by": dict(false), "wrong": sum(wrong.values()),
            "wrong_by": dict(wrong), "minutes": round(float(minutes), 3), "latency": round(float(st.median(latency)), 3) if latency else None}


VARIANTS = ["blend", "tcn", "off_small", "light_small"]


def evaluate_all(frames, k, predict, info, sp):
    rows = {"rules": score(frames, [(f.get("rule") or {}).get("engine_action") or "none" for f in frames], k)}
    cache = []
    for v in VARIANTS:
        acts, stats = run_variant(frames, v, predict, info, sp, cache)
        rows[v] = {**score(frames, acts, k), **stats}
    return rows


def brief(rows):
    return {v: (r["hit"], r["reps"], r["false"], r["wrong"]) for v, r in rows.items()}


def deployed():
    predict, info = GG.load_predictor(GG.DEFAULT_MODEL)
    sp = GG.load_spatial_mlp(ROOT / "models" / "spatial_mlp.json", compose_specialists=True)
    print("classes", info["classes"], "mlp", sp.classes if sp else None, flush=True)
    out = []
    for name in TARGETS:
        frames = load(name)
        for k in (0, 1, 2):
            t0 = time.perf_counter()
            part = slice_set(frames, k)
            rows = evaluate_all(part, k, predict, info, sp)
            out.append({"rec": name[9:15], "set": k, "rows": rows})
            print(name[9:15], DIST[k], len(part), "frames", f"{time.perf_counter() - t0:.0f}s", brief(rows), flush=True)
            (HERE / "gate_replay_deployed.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("DONE", flush=True)


def folds(seeds):
    from scripts import train_tcn as T
    from src.spatial_mlp import pick_spatial_from_sessions, train_spatial_mlp
    from src.training_center import filter_selected_recording_paths
    outp = HERE / "gate_replay_folds.json"
    done = json.loads(outp.read_text(encoding="utf-8")) if outp.is_file() else []
    seen = {(r["data"], r["seed"], r["held"]) for r in done}
    paths = [Path(p) for p in filter_selected_recording_paths([str(p) for p in sorted((ROOT / "recordings").glob("*.jsonl.gz"))])]
    args = T.parse_args(["--no-export", "--keep-rest", "--device", "gpu", "--mode", "fast", "--seed", "0"])
    sessions, eval_sessions, opts = T.load_training_sessions(paths, args)
    present = {int(y) for s in sessions for y in s["y"] if y >= 0}
    classes = [n for i, n in enumerate(T.CLASSES) if i in present]
    for s in sessions:
        original = s["y"].copy()
        for c, n in enumerate(classes):
            s["y"][original == T.CLASSES.index(n)] = c
        s["classes"] = classes
    eval_sessions = [{**s, "y": np.where(s["rest_dropped"], -1, s["y"])} for s in eval_sessions]
    conds = sorted({int(c) for s in eval_sessions for c, y in zip(s["cond"], s["y"]) if c >= 0 and y >= 0})
    no_near = [s for s in sessions if not str((s.get("meta") or {}).get("focus") or "").startswith("near_thumb")]
    raw = {n: load(n) for n in TARGETS}
    info = {"classes": classes, "window": 15, "threshold": 0.7, "frames_in_a_row": 3}
    print("classes", classes, flush=True)
    for seed in seeds:
        for data_name, data in (("with_near", sessions), ("no_near", no_near)):
            for held, k in HELD_TO_SET.items():
                if (data_name, seed, held) in seen:
                    continue
                t0 = time.perf_counter()
                train_conds = [c for c in conds if c != held]
                x, y, w = T.pick_with_weights(data, train_conds)
                model = T.train(x, y, opts["epochs"], seed=seed, stage=f"far{held}", classes=classes,
                                window_stride=opts["window_stride"], device=opts["effective_device"], sample_weights=w)
                xs, ys, gs, ws = pick_spatial_from_sessions(data, train_conds, return_groups=True, return_weights=True)
                sp, _ = train_spatial_mlp(xs, ys, weights_all=ws, classes=classes, device=opts["effective_device"], groups=gs,
                                          compute_random_comparison=False, seed=42 + seed)
                t1 = time.perf_counter()
                predict = lambda arr, m=model: T.predict(m, np.asarray(arr, dtype=np.float32))[0]
                res = {}
                for n in TARGETS:
                    res[n[9:15]] = evaluate_all(slice_set(raw[n], k), k, predict, info, sp)
                done.append({"data": data_name, "seed": seed, "held": held, "set": k, "results": res})
                outp.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                print(f"seed {seed} {data_name} {DIST[k]} train {t1 - t0:.0f}s replay {time.perf_counter() - t1:.0f}s",
                      {n: brief(r) for n, r in res.items()}, flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "deployed":
        deployed()
    else:
        folds([int(x) for x in sys.argv[2:]])
