"""One background learning round (docs/online-learning-phase4.md): the person's confirmed and corrected samples teach a candidate copy
of the production model, with a class-balanced replay of the guided recordings so old gestures are kept. The candidate replaces production
only if it passes `check_candidate` on sessions neither model trained on; otherwise production stays exactly as it was.

Runs in its own process (scripts/online_update.py) so the camera loop never waits for it.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import torch

from src.online.baseline import evaluate_samples, inference_ms
from src.online.dataset import build_dataset, target_positions, window_positions
from src.online.live import ENCODER_FILE, ENCODER_INFO
from src.online.models import make_model
from src.online.replay import BalancedBuffer, ReservoirBuffer
from src.online.samples import DEFAULT_ROOT, GUIDED_SCHEDULED, SUPERVISED_SOURCES, UNKNOWN, SampleStore
from src.online.trainer import OnlineLearner
from src.online.versions import ModelManager, check_candidate
from src.tcn_features import FEATURES

# docs/online-learning-phase4.md, tuning: ER + class-balanced memory + several passes + lr 1e-3
BUFFER = 5000
PER_CLASS_FILL = 1500
LR = 3e-4
ENCODER_LR = 0.0          # measured on this data: training the encoder on a few samples made old gestures fall (phase4 doc)
PASSES = 3
MIN_UPDATES = 60
BATCH = 16
REPLAY = 32
HOLD_OUT_MIN = 4          # a label needs this many samples before one is kept aside to test it


def initialize_production(root: Path, manager: ModelManager) -> Dict[str, Any]:
    """The first production model is the base encoder (scripts/online_train_base.py)."""
    import json
    info = json.loads((root / ENCODER_INFO).read_text(encoding="utf-8"))
    state = torch.load(root / ENCODER_FILE, map_location="cpu", weights_only=True)
    manager.initialize(state, {k: info[k] for k in ("kind", "window", "embedding", "classes", "validation_sessions", "version") if k in info})
    return manager.load()


def _model(info: Dict[str, Any], classes: int, state: Optional[Dict[str, torch.Tensor]] = None):
    model = make_model(info["kind"], classes, info["window"], info["embedding"])
    if state is not None:
        model.load_state_dict(state)
    return model


def run_update(root: Path | str = DEFAULT_ROOT, *, device: Optional[str] = None, emit: Callable[..., None] = lambda *a, **k: None,
               passes: int = PASSES, min_updates: int = MIN_UPDATES, buffer_size: int = BUFFER, seed: int = 0, augment: Any = None,
               lr: float = None, encoder_lr: float = None, balanced: bool = False) -> Dict[str, Any]:
    root = Path(root)
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.perf_counter()
    store, manager = SampleStore(root), ModelManager(root)
    production = manager.load() if manager.has("production") else initialize_production(root, manager)
    info = dict(production["info"])
    classes: List[str] = list(info["classes"])
    window = int(info["window"])
    validation = set(info.get("validation_sessions") or [])

    pruned = store.prune()                     # duplicates and hand-mostly-missing unchecked samples (spec 11)
    supervised = [s for s in store.samples(sources=list(SUPERVISED_SOURCES)) if s.label and s.label != UNKNOWN]
    if not supervised:
        return {"status": "nothing", "message": "학습할 정정·확인 데이터가 없어요"}
    new_labels = sorted({s.label for s in supervised} - set(classes))
    wide = classes + new_labels

    # one sample of each well-filled label stays aside, to test what was learned from the person
    held: List[str] = []
    by_label: Dict[str, list] = {}
    for sample in supervised:
        by_label.setdefault(sample.label, []).append(sample)
    for label, items in by_label.items():
        if len(items) >= HOLD_OUT_MIN:
            held.append(max(items, key=lambda s: s.created).sample_id)
    guided = store.samples(sources=[GUIDED_SCHEDULED])
    emit("loading", samples=len(guided) + len(supervised))
    data = build_dataset(store, guided + supervised, wide)
    in_validation = np.array([s.session in validation for s in data.samples])
    slipped = auto_rollback(manager, data, in_validation, window)
    if slipped:
        emit("rolled_back", problems=slipped)
        return {"status": "rolled_back", "problems": slipped, "pruned": pruned,
                "message": "지금 모델이 이전 모델보다 나빠져서 이전 모델로 되돌렸어요", "seconds": round(time.perf_counter() - t0, 1)}
    is_user = np.array([s.label_source in SUPERVISED_SOURCES for s in data.samples])
    is_held = np.array([s.sample_id in set(held) for s in data.samples])

    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    model = _model(info, len(classes), production["state"])
    lr = LR if lr is None else lr
    encoder_lr = ENCODER_LR if encoder_lr is None else encoder_lr
    learner = OnlineLearner(model, method="er", buffer=(BalancedBuffer if balanced else ReservoirBuffer)(buffer_size, seed=seed), lr=lr, encoder_lr=encoder_lr, device=device, seed=seed,
                            replay_batch=REPLAY, augment=augment, train_encoder=encoder_lr > 0)
    if new_labels:
        learner.add_class(len(wide))
    # replay memory: guided windows of every class (never the validation sessions)
    replay_ids = {s.sample_id for i, s in enumerate(data.samples) if not is_user[i] and not in_validation[i]}
    positions = target_positions(data, replay_ids)[::2]
    fill = []
    for c in range(len(wide)):
        rows = positions[data.labels[positions] == c]
        cap = PER_CLASS_FILL if balanced else len(rows)   # natural shares: the model keeps the prior it was trained with
        if len(rows) > cap:
            rows = rng.choice(rows, cap, replace=False)
        fill.append(rows)
    fill = rng.permutation(np.concatenate(fill)) if fill else np.zeros(0, dtype=np.int64)
    for i in range(0, len(fill), 512):
        part = fill[i:i + 512]
        learner.buffer.add(data.frames[window_positions(data.starts, part, window)], data.labels[part])
    # the person's windows
    train_ids = {s.sample_id for i, s in enumerate(data.samples) if is_user[i] and not is_held[i]}
    new_rows = target_positions(data, train_ids)
    if not len(new_rows):
        return {"status": "nothing", "message": "학습할 정정·확인 데이터가 없어요"}
    batches = math.ceil(len(new_rows) / BATCH)
    rounds = max(passes, math.ceil(min_updates / batches))
    total, done = rounds * batches, 0
    for _ in range(rounds):
        stream = rng.permutation(new_rows)
        for i in range(0, len(stream), BATCH):
            part = stream[i:i + BATCH]
            learner.update(data.frames[window_positions(data.starts, part, window)], data.labels[part])
            done += 1
            if done % 25 == 0 or done == total:
                emit("update", done=done, total=total)

    emit("checking")
    candidate = learner.candidate.to("cpu").eval()
    old = _model(info, len(classes), production["state"]).eval()
    data.classes = classes
    old_indexes = [i for i, s in enumerate(data.samples) if in_validation[i] and s.label in classes]
    before = evaluate_samples(old, data, old_indexes, window, "cpu") if old_indexes else None
    data.classes = wide
    after = evaluate_samples(candidate, data, old_indexes, window, "cpu") if old_indexes else None
    user_indexes = [i for i in range(len(data.samples)) if is_held[i]]
    learned = evaluate_samples(candidate, data, user_indexes, window, "cpu") if user_indexes else None
    problems: List[str] = []
    if after is not None:
        problems += check_candidate(after, before, state=candidate.state_dict(), inference_ms=inference_ms(candidate, window, FEATURES))
    else:
        problems.append("no_validation_sessions")
    tested_new = [label for label in new_labels if learned and label in learned.get("reps", {})]
    if learned is not None:
        problems += [p for p in check_candidate(learned, None, new_classes=tested_new) if p.startswith("new_class_recall")]
    snapshot = f"{len(supervised)}:{max(s.created for s in supervised):.0f}"
    metrics = {"before": _brief(before), "after": _brief(after), "learned": _brief(learned)}
    new_info = {**info, "classes": wide, "metrics": metrics, "data_snapshot": snapshot,
                "training": {"method": "er", "buffer": "balanced" if balanced else "reservoir", "buffer_size": buffer_size, "lr": lr, "encoder_lr": encoder_lr, "rounds": rounds, "updates": learner.updates,
                             "events": learner.events, "user_samples": len(supervised), "held_out": len(held), "new_classes": new_labels,
                             "untested_new_classes": [label for label in new_labels if label not in tested_new], "device": device}}
    promoted = manager.submit(candidate.state_dict(), new_info, problems)
    return {"status": "promoted" if promoted else "rejected", "problems": problems, "metrics": metrics, "new_classes": new_labels,
            "untested_new_classes": new_info["training"]["untested_new_classes"], "updates": learner.updates, "events": learner.events,
            "seconds": round(time.perf_counter() - t0, 1)}


def auto_rollback(manager: ModelManager, data, in_validation, window: int) -> List[str]:
    """Spec 9.3: after a swap, before learning more. The production model is checked against the one it replaced (previous) on the
    validation sessions, with the same limits a candidate must pass. When it fell behind, the previous model comes back. Returns the
    problems ([] = kept). One step only: after a rollback there is no previous to go back to."""
    if not (manager.has("previous") and manager.has("production")):
        return []
    prod, prev = manager.load("production"), manager.load("previous")
    prev_classes, prod_classes = list(prev["info"]["classes"]), list(prod["info"]["classes"])
    if prod_classes[:len(prev_classes)] != prev_classes:
        return []                      # not the same line of models: nothing to compare
    indexes = [i for i, s in enumerate(data.samples) if in_validation[i] and s.label in prev_classes]
    if not indexes:
        return []
    saved = data.classes
    try:
        data.classes = prev_classes
        before = evaluate_samples(_model(prev["info"], len(prev_classes), prev["state"]).eval(), data, indexes, window, "cpu")
        data.classes = prod_classes
        after = evaluate_samples(_model(prod["info"], len(prod_classes), prod["state"]).eval(), data, indexes, window, "cpu")
    finally:
        data.classes = saved
    problems = check_candidate(after, before)
    if problems and manager.rollback():
        return problems
    return []


def _brief(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if report is None:
        return None
    return {k: report[k] for k in ("macro_f1", "reps", "reps_hit", "reps_total", "wrong_reps", "false_fires", "none_minutes")}


REASONS_KO = {
    "macro_f1_dropped": "전체 성능이 떨어짐", "more_false_fires": "오작동이 늘어남", "more_wrong_gestures": "다른 동작이 함께 잡히는 일이 늘어남",
    "non_finite_weights": "학습이 깨짐", "non_finite_metrics": "학습이 깨짐", "no_validation_sessions": "검사할 데이터가 없음",
}


def reason_ko(problem: str, names: Optional[Dict[str, str]] = None) -> str:
    names = names or {}
    if problem.startswith("forgot:"):
        label = problem.split(":", 1)[1]
        return f"'{names.get(label, label)}'을(를) 잊음"
    if problem.startswith("new_class_recall:"):
        label = problem.split(":", 1)[1]
        return f"'{names.get(label, label)}'을(를) 아직 잘 못 알아봄"
    if problem.startswith("too_slow"):
        return "너무 느림"
    return REASONS_KO.get(problem, problem)
