"""Teaching a hand shape by showing it (2026-11-11, "말·글로 연결 (AI)" window).

After [손 모양·움직임 보여주기] the user sees the picture of their hand and what the app told it as. When that is
wrong (or right and they want it surer) they say what it really is; the joints of that look are kept as an example
(`data/posture_examples/`, joints only, no picture). [이 손 모양 학습] then fits a small MLP in a few seconds:

  input   the 96 spatial features of src/spatial_features.py (palm-frame geometry: the same for a hand turned upside
          down) + the hand's direction on the image (wrist -> middle knuckle), so an upside-down V is not a V
  classes "other" + every taught shape
  others  frames of the recordings (recordings/*.jsonl.gz) whose rule shape is none of the taught ones: the hand in
          normal use and the other signs. Read once and kept in data/posture_examples/.negatives.npz (reading all
          recordings takes about a minute; later trainings take seconds).

The result is checked before it can be used: each example is left out in turn (recognition on a look the model did
not learn from), and a quarter of the recordings is kept out (how often the normal hand is told as a taught shape).
The user applies it ([적용] -> models/posture_teach.json) only when that misfire rate is low enough (MAX_FALSE).

At run time the rules still decide first. The model only replaces a rule answer that the rules gave to that shape's
examples (for the upside-down V of log 20261111-091241: "", "v_sign_down", "open_hand"), and only when it is sure
(THRESHOLD). See `override`.
"""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT, USER_RECORDINGS

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = DATA_ROOT
EXAMPLES_DIR = ROOT / "data" / "posture_examples"
MODEL_PATH = ROOT / "models" / "posture_teach.json"
CANDIDATE_PATH = ROOT / "models" / "posture_teach.candidate.json"
REC_DIR = USER_RECORDINGS  # host recordings use the same hand-mouse-recording/1 format; read in place

THRESHOLD = .85          # probability the model needs to replace a rule answer
MAX_FALSE = .01          # share of the held-out recording frames told as a taught shape that still allows [적용]
NEG_EVERY = 15           # every n-th frame of a recording is an "other" example (~2000 per 17-minute recording)
HIDDEN = 48
EPOCHS = 40
OTHER = "other"
ASPECT = 720 / 1280      # frame height / width (the recordings and the app's camera)


# ------------------------------------------------------------------ features
def _xyz(p: Any) -> Tuple[float, float, float]:
    if hasattr(p, "x"):
        return float(p.x), float(p.y), float(getattr(p, "z", 0.0) or 0.0)
    return float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0


def features(landmarks: Any, aspect: float = ASPECT) -> Optional[np.ndarray]:
    """96 palm-frame features + 2 for the hand's direction on the image (unit vector wrist -> middle knuckle, y up)."""
    from manux.compat.hand_mouse.spatial_features import extract_spatial_features
    if landmarks is None or len(landmarks) < 21:
        return None
    pts = [_xyz(p) for p in landmarks]
    base = extract_spatial_features(pts)
    if base is None:
        return None
    dx, dy = pts[9][0] - pts[0][0], -(pts[9][1] - pts[0][1]) * aspect
    n = math.hypot(dx, dy) or 1e-6
    return np.concatenate([base.astype(np.float32), np.array([dx / n, dy / n], np.float32)])


# ------------------------------------------------------------------ examples
def save_example(label: str, frames: Sequence[Sequence[Sequence[float]]], rule_said: Sequence[str] = (),
                 folder: Optional[Path] = None, *, aspect: float = ASPECT, split: str = 'train',
                 profile: str = '', trial: str = '') -> Path:
    """One look (its frames' joints) as an example of `label`. `rule_said`: the rule shape of each frame."""
    d = Path(folder or EXAMPLES_DIR)
    d.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y%m%d-%H%M%S") + f"-{label}"
    path, n = d / f"{stem}.json", 1
    while path.exists():                     # two looks saved within a second
        n += 1
        path = d / f"{stem}-{n}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"label": label, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "aspect": aspect,
                               "split": split, "profile": profile, "trial": trial,
                               "rule_said": list(rule_said), "frames": [list(map(list, f)) for f in frames]},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_examples(folder: Optional[Path] = None) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(Path(folder or EXAMPLES_DIR).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("label") and d.get("frames"):
            d["file"] = p.name
            out.append(d)
    return out


def example_counts(folder: Optional[Path] = None) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for d in load_examples(folder):
        if d.get('split') == 'eval' or d['label'] == OTHER:
            continue
        counts[d["label"]] = counts.get(d["label"], 0) + 1
    return counts


def delete_examples(label: str, folder: Optional[Path] = None) -> int:
    n = 0
    for d in load_examples(folder):
        if d["label"] == label:
            (Path(folder or EXAMPLES_DIR) / d["file"]).unlink(missing_ok=True)
            n += 1
    return n


# ------------------------------------------------------------------ the "other" frames from the recordings
def negatives(rec_dir: Optional[Path] = None, cache: Optional[Path] = None,
              progress: Optional[Callable[[str], None]] = None) -> Dict[str, np.ndarray]:
    """{"x": features, "rule": rule shape per frame, "rec": recording index per frame}. Cached by the file list."""
    from manux.compat.hand_mouse.pose_probe import hand_posture
    from manux.compat.hand_mouse.recorder import finished_recordings, read_session
    rec_dir = Path(rec_dir or REC_DIR)
    cache = Path(cache or (EXAMPLES_DIR / ".negatives.npz"))
    files, _ = finished_recordings(rec_dir)
    files = [f for f in files if ".cleaned." not in f.name and ".aug" not in f.name]
    key = json.dumps([[f.name, f.stat().st_size] for f in files] + [NEG_EVERY])
    if cache.is_file():
        try:
            z = np.load(cache, allow_pickle=False)
            if str(z["key"]) == key:
                return {"x": z["x"], "rule": z["rule"], "rec": z["rec"]}
        except Exception:
            pass
    xs, rules, idx = [], [], []
    from types import SimpleNamespace
    for i, f in enumerate(files):
        if progress:
            progress(f"녹화에서 '아님' 예시 모으는 중 {i + 1}/{len(files)} (처음 한 번만)")
        try:
            meta, frames, _ = read_session(f)
        except Exception:
            continue
        aspect = float(meta.get("frame_aspect") or ASPECT)
        for fr in frames[::NEG_EVERY]:
            lm = fr.get("lm")
            if not lm or len(lm) < 21:
                continue
            v = features(lm, aspect)
            if v is None:
                continue
            pts = [SimpleNamespace(x=p[0], y=p[1], z=p[2] if len(p) > 2 else 0.0) for p in lm]
            xs.append(v)
            rules.append(hand_posture(pts, aspect, taught=False))
            idx.append(i)
    out = {"x": np.array(xs, np.float32).reshape(-1, 98), "rule": np.array(rules, dtype="<U24"),
           "rec": np.array(idx, np.int32)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(cache.stem + ".tmp.npz")
    np.savez_compressed(tmp, key=np.array(key), **out)
    os.replace(tmp, cache)
    return out


# ------------------------------------------------------------------ the model
class TaughtModel:
    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data
        self.classes: List[str] = list(data["classes"])
        self.mean = np.array(data["mean"], np.float32)
        self.std = np.array(data["std"], np.float32)
        self.w1 = np.array(data["w1"], np.float32)
        self.b1 = np.array(data["b1"], np.float32)
        self.w2 = np.array(data["w2"], np.float32)
        self.b2 = np.array(data["b2"], np.float32)
        self.threshold = float(data.get("threshold", THRESHOLD))
        self.replaces: Dict[str, set] = {k: set(v) for k, v in (data.get("replaces") or {}).items()}
        # each shape's taught thumb (thumb_stats); None = a model trained before 2026-11-11 (read from the examples)
        self.thumb: Optional[Dict[str, Dict[str, float]]] = data.get("thumb") if isinstance(data.get("thumb"), dict) else None
        # each shape's taught fingers (finger_stats); None = a model trained before 2026-11-12 (read from the examples)
        self.fingers: Optional[Dict[str, Dict[str, List[float]]]] = (data.get("fingers") if isinstance(data.get("fingers"), dict)
                                                                     else None)

    def probs(self, x: np.ndarray) -> np.ndarray:
        h = np.maximum(0.0, ((x - self.mean) / self.std) @ self.w1.T + self.b1)
        z = h @ self.w2.T + self.b2
        z = z - z.max(axis=-1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=-1, keepdims=True)

    def predict(self, landmarks: Any, aspect: float = ASPECT) -> Tuple[str, float]:
        v = features(landmarks, aspect)
        if v is None:
            return OTHER, 0.0
        p = self.probs(v[None, :])[0]
        k = int(p.argmax())
        return self.classes[k], float(p[k])

    def override(self, landmarks: Any, rule: str, aspect: float = ASPECT) -> str:
        """The taught shape instead of the rule answer, when sure and the rules told that shape's examples so."""
        return self.decision(landmarks, rule, aspect)['posture']

    def decision(self, landmarks: Any, rule: str, aspect: float = ASPECT) -> Dict[str, Any]:
        """Same runtime decision plus its raw prediction and rejection reason for diagnostics."""
        label, p = self.predict(landmarks, aspect)
        posture, reason = rule, 'accepted'
        if label == OTHER:
            reason = 'other'
        elif p < self.threshold:
            reason = 'confidence'
        elif label == rule:
            reason = 'same'
        elif rule not in self.replaces.get(label, ()):
            reason = 'rule_scope'
        elif not plausible(label, landmarks, aspect, (self.thumb or {}).get(label), (self.fingers or {}).get(label)):
            reason = 'shape_check'
        else:
            posture = label
        return dict(prediction=label, confidence=p, posture=posture, reason=reason, threshold=self.threshold)


# 2026-11-11 report ("뒤집은 주먹(손등이 바닥을 향하는 상태)을 오른쪽으로 따봉을 하는 것으로 인식한다. 엄지를 펴 올리지도
# 않았음. 엄지를 충분히 폈을 때 우따봉으로 판정해야 함"): the model's features are relative to the palm, so a fist turned
# over looks to it much like the fist of a taught thumb-out shape, and nothing asked whether the thumb was out. A shape
# that was taught WITH the thumb out (its examples' thumbs: straight, long, the tip away from the fingers) is now told
# only when the hand's thumb is out about as far. No recording of the reported hand exists on this PC: the numbers are
# starting points (pose_probe calls a thumb "stuck out" at straight .9 / length .65).
THUMB_OUT = {"straight": .85, "len": .55, "reach": .4}   # a shape's examples at or over these: taught with the thumb out
THUMB_KEEP = .75         # share of the taught thumb's length and reach the hand must show
THUMB_STRAIGHT_MIN = .8  # ... with the thumb about straight


def thumb_measure(landmarks: Any) -> Dict[str, float]:
    """How far the thumb is out: straight (1 = stretched), len (tip from its base, in palm lengths) and reach (the
    tip's distance to the nearest joint of the four fingers, in palm lengths: small when it lies on a fist)."""
    from manux.compat.hand_mouse.motion_mode import hand_features
    pts = [_xyz(p) for p in landmarks[:21]]
    f = hand_features(pts)
    palm = max(1e-6, math.hypot(pts[9][0] - pts[0][0], pts[9][1] - pts[0][1]))
    reach = min(math.hypot(pts[4][0] - pts[i][0], pts[4][1] - pts[i][1]) for i in range(5, 21)) / palm
    return {"straight": float(f["thumb_straight"]), "len": float(f["thumb_len"]), "reach": float(reach)}


def thumb_stats(examples: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Per taught shape: the median thumb of its example frames ({} for a shape without usable frames)."""
    rows: Dict[str, List[Dict[str, float]]] = {}
    for ex in examples:
        for frame in ex.get("frames") or ():
            if len(frame) >= 21:
                try:
                    rows.setdefault(str(ex["label"]), []).append(thumb_measure(frame))
                except Exception:
                    pass
    return {label: {k: round(float(np.median([m[k] for m in ms])), 3) for k in ("straight", "len", "reach")}
            for label, ms in rows.items()}


def thumb_ok(taught: Optional[Dict[str, float]], landmarks: Any) -> bool:
    """False when the shape was taught with the thumb out and this hand's thumb is not (see THUMB_OUT)."""
    if not taught or any(float(taught.get(k, 0.0)) < v for k, v in THUMB_OUT.items()):
        return True                          # taught with the thumb in (or nothing known): the thumb decides nothing
    try:
        now = thumb_measure(landmarks)
    except Exception:
        return True
    return (now["straight"] >= THUMB_STRAIGHT_MIN and now["len"] >= THUMB_KEEP * float(taught["len"])
            and now["reach"] >= THUMB_KEEP * float(taught["reach"]))


# 2026-11-12 report ("커스텀 등록한 검지만 펴기 동작이 창닫기 동작이랑 겹쳐서 작동하는데, 엄연히 다른 동작"): one of the three
# examples of the taught "index only" had the little finger half out (1.19-1.46; the rules read 43 of its 54 frames as
# the rock sign), so the model was allowed to replace the rock sign and then told a full rock sign (little finger
# 1.7-1.8) as "index only": the close-window sign and the taught shape's command ran on the same hand. As with the
# thumb, each of the four fingers is now asked: a finger the examples never showed out must not be clearly out, and
# one they always showed out must not be clearly folded. Measured in 3D (pose_probe.finger_ratios_3d): in the picture
# the half-out little finger of that example read 1.72-2.78 (the hand leaned towards the camera, which shortens the
# palm and blows the ratio up), in 3D 1.2-1.4, and the rock signs that ran both commands 1.7-1.8. The limits are
# pattern_posture's 3D ones; the margin over the examples is a starting point, not measured.
FINGER_OUT_3D = 1.5          # a finger at or over this: clearly straight
FINGER_FOLD_3D = 1.3         # at or under: clearly folded
FINGER_MARGIN = .15          # ... and this far past what the examples showed


def finger_measure(landmarks: Any, aspect: float = ASPECT) -> List[float]:
    """Tip-to-wrist over knuckle-to-wrist in 3D for index, middle, ring, little finger (about 1.9 straight)."""
    from types import SimpleNamespace
    from manux.compat.hand_mouse.pose_probe import finger_ratios_3d
    return [float(v) for v in finger_ratios_3d([SimpleNamespace(x=q[0], y=q[1], z=q[2]) for q in map(_xyz, landmarks[:21])], aspect)]


def finger_stats(examples: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, List[float]]]:
    """Per taught shape: how far in ("low", 10th percentile) and out ("high", 90th) each finger was in its examples."""
    rows: Dict[str, List[List[float]]] = {}
    for ex in examples:
        for frame in ex.get("frames") or ():
            if len(frame) >= 21:
                try:
                    rows.setdefault(str(ex["label"]), []).append(finger_measure(frame, float(ex.get("aspect") or ASPECT)))
                except Exception:
                    pass
    return {label: {"low": [round(float(v), 3) for v in np.percentile(np.array(ms), 10, axis=0)],
                    "high": [round(float(v), 3) for v in np.percentile(np.array(ms), 90, axis=0)]}
            for label, ms in rows.items()}


def fingers_ok(taught: Optional[Dict[str, List[float]]], landmarks: Any, aspect: float = ASPECT) -> bool:
    """False when a finger is clearly out that the shape's examples kept in, or clearly folded that they kept out."""
    if not taught or len(taught.get("low") or ()) != 4 or len(taught.get("high") or ()) != 4:
        return True
    try:
        now = finger_measure(landmarks, aspect)
    except Exception:
        return True
    for v, low, high in zip(now, taught["low"], taught["high"]):
        if high < FINGER_OUT_3D and v >= max(FINGER_OUT_3D, high + FINGER_MARGIN):
            return False                     # taught folded (or half out at most), now straight
        if low >= FINGER_OUT_3D and v <= min(FINGER_FOLD_3D, low - FINGER_MARGIN):
            return False                     # taught straight, now folded
    return True


def plausible(label: str, landmarks: Any, aspect: float = ASPECT, thumb: Optional[Dict[str, float]] = None,
              fingers: Optional[Dict[str, List[float]]] = None) -> bool:
    """A taught shape must still look like it in the roughest way. A LISTED V (upright or upside down) has index and
    middle straight in 3D, an upside-down one has them pointing down: the model saw a few looks of each taught shape,
    and a hand it never saw (another taught shape at a new angle) must not be told as a V on its say alone
    (2026-11-11: "옆따봉을 했는데 거꾸로 V로 등록한 명령이 나오거나"). Any shape taught with the thumb out needs the thumb
    out (`thumb`: that shape's taught thumb, see thumb_ok), and every shape its fingers about as taught (`fingers`,
    see fingers_ok)."""
    if not thumb_ok(thumb, landmarks) or not fingers_ok(fingers, landmarks, aspect):
        return False
    if label not in ("v_sign", "v_sign_down"):
        return True
    from types import SimpleNamespace
    from manux.compat.hand_mouse.pose_probe import V_3D, finger_ratios_3d, fingers_down
    pts = [p if hasattr(p, "x") else SimpleNamespace(x=p[0], y=p[1], z=p[2] if len(p) > 2 else 0.0) for p in landmarks]
    r = finger_ratios_3d(pts, aspect)
    return min(r[0], r[1]) >= V_3D and fingers_down(pts) == (label == "v_sign_down")


def _fit(x: np.ndarray, y: np.ndarray, n_classes: int, seed: int = 0) -> Dict[str, np.ndarray]:
    """2-layer ReLU MLP, softmax, classes weighted equally, Adam. A few seconds for ~100k frames."""
    rng = np.random.default_rng(seed)
    mean = x.mean(axis=0)
    std = x.std(axis=0) + 1e-4
    xs = ((x - mean) / std).astype(np.float32)
    d = xs.shape[1]
    params = {"w1": rng.standard_normal((HIDDEN, d)).astype(np.float32) * math.sqrt(2.0 / d),
              "b1": np.zeros(HIDDEN, np.float32),
              "w2": rng.standard_normal((n_classes, HIDDEN)).astype(np.float32) * math.sqrt(2.0 / HIDDEN),
              "b2": np.zeros(n_classes, np.float32)}
    counts = np.bincount(y, minlength=n_classes).astype(np.float32)
    cw = (len(y) / (n_classes * np.maximum(counts, 1))).astype(np.float32)
    m = {k: np.zeros_like(v) for k, v in params.items()}
    s = {k: np.zeros_like(v) for k, v in params.items()}
    lr, b1, b2, step, l2 = 3e-3, .9, .999, 0, 1e-4
    batch = 512
    for _ in range(EPOCHS):
        order = rng.permutation(len(y))
        for i in range(0, len(order), batch):
            j = order[i:i + batch]
            xb, yb = xs[j], y[j]
            # light noise: the examples are few looks of a hand held still
            xb = xb + rng.standard_normal(xb.shape).astype(np.float32) * .05
            h_pre = xb @ params["w1"].T + params["b1"]
            h = np.maximum(0.0, h_pre)
            z = h @ params["w2"].T + params["b2"]
            z -= z.max(axis=1, keepdims=True)
            p = np.exp(z)
            p /= p.sum(axis=1, keepdims=True)
            wb = cw[yb]
            g = p.copy()
            g[np.arange(len(yb)), yb] -= 1.0
            g *= (wb / wb.sum())[:, None]
            grads = {"w2": g.T @ h + l2 * params["w2"], "b2": g.sum(axis=0)}
            gh = (g @ params["w2"]) * (h_pre > 0)
            grads["w1"] = gh.T @ xb + l2 * params["w1"]
            grads["b1"] = gh.sum(axis=0)
            step += 1
            for k in params:
                m[k] = b1 * m[k] + (1 - b1) * grads[k]
                s[k] = b2 * s[k] + (1 - b2) * grads[k] ** 2
                params[k] -= lr * (m[k] / (1 - b1 ** step)) / (np.sqrt(s[k] / (1 - b2 ** step)) + 1e-8)
    return {"mean": mean, "std": std, **params}


def _data(examples: List[Dict[str, Any]], neg: Dict[str, np.ndarray], classes: List[str]):
    xs, ys, groups = [], [], []
    for gi, ex in enumerate(examples):
        for lm in ex["frames"]:
            v = features(lm, float(ex.get("aspect") or ASPECT))
            if v is not None:
                xs.append(v)
                ys.append(classes.index(ex["label"]))
                groups.append(gi)
    keep = ~np.isin(neg["rule"], classes[1:])          # a recorded frame the rules call a taught shape is not "other"
    nx = neg["x"][keep]
    x = np.concatenate([np.array(xs, np.float32).reshape(-1, nx.shape[1] if len(nx) else 98), nx])
    y = np.concatenate([np.array(ys, np.int64), np.zeros(len(nx), np.int64)])
    g = np.concatenate([np.array(groups, np.int64), -1 - neg["rec"][keep].astype(np.int64)])
    return x, y, g


def _rule_answers(ex: Dict[str, Any]) -> set:
    """What the rules call an example's frames in use, where the rule engine has a say too: with no engine gesture and
    with its "fist" (the composite detector then asks fist or fingers bent). 2026-11-11 (옆따봉, logs 104444 / 104507):
    the examples only carried the shape probe's answers ("", fist, open_hand); in use the engine said fist, the
    detector said "fingers_bent", which the model was not allowed to replace - the gesture never fired."""
    from types import SimpleNamespace
    from manux.compat.hand_mouse.action_bindings import CompositeGestureDetector
    out = set()
    for lm in ex["frames"][::5]:
        try:
            pts = [SimpleNamespace(x=p[0], y=p[1], z=p[2] if len(p) > 2 else 0.0) for p in lm]
            for gesture in ("", "fist"):
                out.add(CompositeGestureDetector._detect_base_posture_rules(pts, gesture, set()))
        except Exception:
            continue
    return out


def train(examples: Optional[List[Dict[str, Any]]] = None, neg: Optional[Dict[str, np.ndarray]] = None,
          progress: Optional[Callable[[str], None]] = None, out: Optional[Path] = None) -> Dict[str, Any]:
    """Fit, check (examples left out in turn, a quarter of the recordings kept out), save the candidate.
    Returns the report (also inside the saved file)."""
    say = progress or (lambda _m: None)
    all_examples = load_examples() if examples is None else examples
    examples = [e for e in all_examples if e.get('split') != 'eval']
    positives = [e for e in examples if e['label'] != OTHER]
    if not positives:
        raise ValueError("가르친 손 모양 견본이 없어요. [보여주기] 뒤 이름을 골라 저장해 주세요.")
    neg = negatives(progress=say) if neg is None else neg
    classes = [OTHER] + sorted({e["label"] for e in positives})
    x, y, g = _data(examples, neg, classes)
    t0 = time.time()

    # the check: fold k leaves out the k-th example of every shape and every 4th recording
    by_label: Dict[str, List[List[int]]] = {}
    # A continuous capture is one trial even if aspect/save boundaries split its files.
    trial_groups = {}
    for gi, ex in enumerate(examples):
        identity = (ex['label'], ex.get('trial') or ex.get('file') or str(gi))
        trial_groups.setdefault(identity, []).append(gi)
    for (label, _trial), indices in trial_groups.items():
        by_label.setdefault(label, []).append(indices)
    folds = max(2, min(4, max(len(v) for v in by_label.values())))
    hits = {c: [0, 0] for c in classes[1:]}
    false = [0, 0]
    for k in range(folds):
        say(f"확인 학습 {k + 1}/{folds}…")
        out_ex = {gi for v in by_label.values() if len(v) > 1 and k < len(v) for gi in v[k]}
        test = np.isin(g, list(out_ex)) | ((g < 0) & (((-1 - g) % folds) == k))
        if not test.any() or test.all():
            continue
        fit = _fit(x[~test], y[~test], len(classes), seed=k)
        model = TaughtModel({"classes": classes, **{a: b.tolist() for a, b in fit.items()}})
        p = model.probs(x[test])
        pred = p.argmax(axis=1)
        sure = p.max(axis=1) >= THRESHOLD
        yt = y[test]
        for ci, c in enumerate(classes[1:], start=1):
            mask = yt == ci
            hits[c][0] += int(((pred == ci) & sure)[mask].sum())
            hits[c][1] += int(mask.sum())
        mask = yt == 0
        false[0] += int(((pred != 0) & sure)[mask].sum())
        false[1] += int(mask.sum())

    say("최종 학습…")
    fit = _fit(x, y, len(classes), seed=99)
    replaces = {}
    for ex in positives:
        replaces.setdefault(ex["label"], set()).update(set(ex.get("rule_said") or []) | {""} | _rule_answers(ex))
    report = {
        "classes": classes,
        "examples": {c: len(v) for c, v in by_label.items() if c != OTHER},
        "negative_examples": len(by_label.get(OTHER, [])),
        "frames": {c: sum(len(e['frames']) for e in positives if e['label'] == c) for c in classes[1:]},
        "evaluation_frames_excluded": sum(len(e['frames']) for e in all_examples if e.get('split') == 'eval'),
        "recognition": {c: (round(h / n, 3) if n else None) for c, (h, n) in hits.items()},
        "false_rate": round(false[0] / false[1], 4) if false[1] else None,
        "false_frames": false,
        "others": int((y == 0).sum()),
        "seconds": round(time.time() - t0, 1),
        "trained": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    report["can_apply"] = report["false_rate"] is not None and report["false_rate"] <= MAX_FALSE
    data = {"version": "posture-teach-v1", "classes": classes, "threshold": THRESHOLD,
            "replaces": {k: sorted(v) for k, v in replaces.items()}, "report": report,
            "thumb": thumb_stats(positives),
            "fingers": finger_stats(positives),
            **{a: b.tolist() for a, b in fit.items()}}
    out = Path(out or CANDIDATE_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, out)
    return report


def apply_candidate() -> bool:
    if not CANDIDATE_PATH.is_file():
        return False
    os.replace(CANDIDATE_PATH, MODEL_PATH)
    return True


def discard_candidate() -> None:
    CANDIDATE_PATH.unlink(missing_ok=True)


def turn_off() -> None:
    MODEL_PATH.unlink(missing_ok=True)


def forget(label: str) -> None:
    """A shape taken out of the applied model and the waiting one without training again: it is no longer allowed to
    replace any rule answer (see TaughtModel.override), so it is never told. The other shapes stay as they are."""
    for path in (MODEL_PATH, CANDIDATE_PATH):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if label not in (data.get("replaces") or {}) and label not in ((data.get("report") or {}).get("examples") or {}):
            continue
        (data.get("replaces") or {}).pop(label, None)
        for part in ("examples", "recognition"):
            ((data.get("report") or {}).get(part) or {}).pop(label, None)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    reset_cache()


def read_report(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("report")
    except (OSError, ValueError, AttributeError):
        return None


# ------------------------------------------------------------------ the guide on the screen
ENOUGH = 3               # examples the guide asks for (2 are needed to measure recognition at all)
AUTO_TRAIN = 2           # examples of the shape being taught from which saving one starts a training


def guide(focus: str, name: str, counts: Dict[str, int], state: str = "", report: Optional[Dict[str, Any]] = None,
          applied: Optional[Dict[str, Any]] = None, text: str = "") -> Dict[str, str]:
    """{"steps", "text"} for the shape being taught NOW (`focus`), and only that one (2026-11-11 request: "옆따봉 하고
    추가 학습 시키면 정확도가 더 높아진다는 흐름으로 … 해당 동작 이야기 외에는 안하는게 좋을듯"; the screen listed every
    taught shape's numbers). `state`: "" | "training" | "ready" (`report` = that training's) | "applied" | "error".
    `applied`: the report of the model in use. `text`: the trainer's progress line or an error."""
    if not focus:
        return {"steps": "", "text": text}
    n = int(counts.get(focus, 0))
    in_use = focus in ((applied or {}).get("examples") or {})
    trained = state in ("ready", "applied") or in_use
    done = state == "applied" or (in_use and state != "ready")
    mark = lambda ok: "✓" if ok else "…"
    steps = (f"① 보여주기 ✓   ② 이름 ✓   ③ 견본 {n}개 {mark(n >= ENOUGH)}   "
             f"④ 학습 {mark(trained)}   ⑤ 적용 {mark(done)}")
    more = "각도·거리를 조금 바꿔 한 번 더 보여 주고 다시 학습하면 더 정확해져요"
    if state == "training":
        return {"steps": steps, "text": f"'{name}' 학습 중… {text}".rstrip()}
    if state == "error":
        return {"steps": steps, "text": text}
    rep = report if state == "ready" else (applied if in_use else None)
    rate = ((rep or {}).get("recognition") or {}).get(focus)
    false = (rep or {}).get("false_rate")
    numbers = ""
    if rep is not None:
        numbers = (f"'{name}' 인식 {round(rate * 100)}%" if rate is not None
                   else f"'{name}' 견본이 {n}개라 인식률은 아직 못 재요")
        if false is not None:
            numbers += f" · 평소 손에서 잘못 걸림 {false * 100:.1f}%"
    if state == "ready":
        if not (report or {}).get("can_apply"):
            return {"steps": steps, "text": f"{numbers} → 잘못 걸림이 {MAX_FALSE * 100:.0f}%를 넘어 아직 쓸 수 없어요 "
                                            f"(이 모양에 연결한 명령도 실행되지 않아요). [한 번 더 보여주기]로 견본을 늘려 주세요."}
        tail = "" if (rate is not None and rate >= .95 and n >= ENOUGH) else f" · {more} (눌러서 보여 주면 저절로 추가돼요)"
        return {"steps": steps, "text": f"{numbers} → [적용]을 누르면 바로 쓸 수 있어요{tail}"}
    if state == "applied" or in_use:
        head = f"'{name}' 적용됨 · 이제 앱이 이 모양을 알아봐요"
        return {"steps": steps, "text": f"{head} ({numbers}) · {more}" if numbers else f"{head} · {more}"}
    if n < AUTO_TRAIN:
        return {"steps": steps, "text": f"'{name}' 견본 {n}개 저장됨 → 각도를 조금 바꿔 [한 번 더 보여주기] "
                                        f"(견본이 {AUTO_TRAIN}개가 되면 바로 학습해요)"}
    return {"steps": steps, "text": f"'{name}' 견본 {n}개 → [학습]을 눌러 주세요 · 더 보여 줄수록 정확해져요"}


# ------------------------------------------------------------------ run time
_lock = threading.Lock()
_cache: Dict[str, Any] = {"checked": 0.0, "mtime": None, "model": None}


def active_model() -> Optional[TaughtModel]:
    """The applied model, reloaded when the file changes (checked at most once a second)."""
    now = time.monotonic()
    with _lock:
        if now - _cache["checked"] < 1.0:
            return _cache["model"]
        _cache["checked"] = now
        try:
            mtime = MODEL_PATH.stat().st_mtime_ns
        except OSError:
            _cache["mtime"], _cache["model"] = None, None
            return None
        if mtime != _cache["mtime"]:
            _cache["mtime"] = mtime
            try:
                _cache["model"] = TaughtModel(json.loads(MODEL_PATH.read_text(encoding="utf-8")))
                if _cache["model"].thumb is None:      # trained before the thumb was kept: from the saved examples
                    _cache["model"].thumb = thumb_stats(load_examples())
                if _cache["model"].fingers is None:    # ... or before the fingers were (2026-11-12)
                    _cache["model"].fingers = finger_stats(load_examples())
            except Exception:
                _cache["model"] = None
        return _cache["model"]


def reset_cache() -> None:
    with _lock:
        _cache.update(checked=0.0, mtime=None, model=None)


def override(landmarks: Any, rule: str, aspect: float = ASPECT) -> str:
    model = active_model()
    if model is None or landmarks is None or len(landmarks) < 21:
        return rule
    try:
        return model.override(landmarks, rule, aspect)
    except Exception:
        return rule
