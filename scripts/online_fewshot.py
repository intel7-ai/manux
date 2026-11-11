"""Phase 3: how well does a gesture the encoder never saw get recognised from a few demonstrations?

For each gesture: train the encoder without it (and without one fold of sessions), then register it from N demonstrations of ONE session
and test on its repetitions in OTHER sessions. Also: do the old gestures suffer, and is it rejected before it is registered?
Saves nothing into models/. Results: logs/online_fewshot.json (finished runs are skipped).

프로젝트 폴더에서 (GPU 환경):  ..\\hand-mouse-gpuenv\\Scripts\\python.exe scripts/online_fewshot.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)

import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402

from scripts.train_tcn import augment  # noqa: E402
from src.online.baseline import train_encoder  # noqa: E402
from src.online.dataset import build_dataset, session_folds, target_positions, window_positions  # noqa: E402
from src.online.memory import PrototypeMemory, normalize  # noqa: E402
from src.online.models import make_model  # noqa: E402
from src.online.samples import GUIDED_SCHEDULED, SampleStore  # noqa: E402

QUICK = "--unknown" in sys.argv      # unknown rejection, conflicts and same-session recall only, one seed
OUT = ROOT / "logs" / ("online_unknown.json" if QUICK else "online_fewshot.json")
CLASSES = ["none", "thumb_index", "thumb_ring", "thumb_middle", "thumb_pinky", "palm_side", "v_sign", "thumbs_up"]
HELD = ["thumb_ring", "thumb_middle", "thumb_pinky", "palm_side", "thumbs_up", "v_sign", "thumb_index"]
WINDOW, EPOCHS, RUN = 15, 15, 4
DRAWS = 6


@torch.no_grad()
def all_outputs(model, data, device):
    """Embedding of every row with a hand."""
    model.eval().to(device)
    frames = torch.from_numpy(data.frames).to(device)
    rows = np.flatnonzero(data.sample_of >= 0)
    out = np.zeros((len(data.frames), model.embedding), np.float32)
    for i in range(0, len(rows), 8192):
        part = rows[i:i + 8192]
        out[part] = model.embed(frames[torch.from_numpy(window_positions(data.starts, part, WINDOW)).to(device)]).cpu().numpy()
    return out


def fires(hit, run=RUN):
    count, n = 0, 0
    for value in hit:
        count = count + 1 if value else 0
        if count == run:
            n += 1
    return n


def score(decide, data, test, new_label, names, none_label="none", run=RUN):
    """`decide(z rows) -> class name per row (or None)`. Repetition = its class `run` frames in a row."""
    def fires(hit):
        count, n = 0, 0
        for value in hit:
            count = count + 1 if value else 0
            if count == run:
                n += 1
        return n
    out = {"reps": {}, "false_fires": 0, "none_minutes": 0.0, "new_false": 0, "other_reps": 0, "new_on_other_reps": 0, "accepted_reps": 0, "all_reps": 0}
    for s in test:
        rows = np.flatnonzero(data.sample_of == s)
        start = int(np.searchsorted(rows, data.do_start[s]))
        rows = rows[start:]
        rows = rows[data.frames[rows, 132] > 0]
        if len(rows) < RUN:
            continue
        said = decide(rows)
        label = data.samples[s].label
        if label == none_label:
            t = data.times[rows]
            out["none_minutes"] += float(t[-1] - t[0]) / 60
            for name in names:
                if name != none_label:
                    n = fires(said == name)
                    out["false_fires"] += n
                    if name == new_label:
                        out["new_false"] += n
            continue
        hit, total = out["reps"].get(label, (0, 0))
        out["reps"][label] = (hit + (fires(said == label) > 0), total + 1)
        out["all_reps"] += 1
        out["accepted_reps"] += any(fires(said == name) > 0 for name in names if name != none_label)   # taken for some known gesture
        if label != new_label:
            out["other_reps"] += 1
            out["new_on_other_reps"] += fires(said == new_label) > 0
    return out


def old_reps(r, new_label):
    rows = [v for k, v in r["reps"].items() if k != new_label]
    return sum(v[0] for v in rows), sum(v[1] for v in rows)


def run_one(data, held, fold_sessions, supcon, seed, device):
    names = [c for c in CLASSES if c != held]
    index = {n: i for i, n in enumerate(names)}
    test_set = set(fold_sessions)
    train_ids = {s.sample_id for s in data.samples if s.session not in test_set and s.label != held}
    positions = target_positions(data, train_ids)
    # the encoder knows the other classes only
    labels_all = data.labels.copy()
    remap = np.full(len(CLASSES) + 1, -1)
    for i, c in enumerate(CLASSES):
        if c in index:
            remap[i] = index[c]
    data.labels = remap[labels_all]
    data.classes, classes_all = names, data.classes
    torch.manual_seed(seed)
    model = make_model("tcn", len(names), WINDOW)
    train_encoder(model, data, positions, WINDOW, epochs=EPOCHS, device=device, seed=seed, supcon_weight=supcon, augment=lambda x: augment(x))
    data.labels, data.classes = labels_all, classes_all
    z = all_outputs(model, data, device)
    with torch.no_grad():
        logits = model.head(torch.from_numpy(z).to(device)).cpu().numpy()
    probs = torch.softmax(torch.from_numpy(logits), dim=1).numpy()
    # base prototypes come from the training windows
    base_rows = {n: positions[data.labels[positions] == CLASSES.index(n)] for n in names}
    # demonstrations and tests of the held gesture
    by_session = {}
    for i, sample in enumerate(data.samples):
        if sample.label == held:
            by_session.setdefault(sample.session, []).append(i)
    sessions = sorted(by_session, key=lambda s: -len(by_session[s]))
    test_old = [i for i, s in enumerate(data.samples) if s.session in test_set and s.label != held]
    results = []
    rng = np.random.default_rng(seed)

    def head_decide(rows):
        best = probs[rows].argmax(axis=1)
        conf = probs[rows].max(axis=1)
        return np.array([names[b] if c >= .7 else "" for b, c in zip(best, conf)], dtype=object)

    baseline = score(head_decide, data, test_old, None, names)
    # the held gesture before it is registered: how often does the head claim it is something?
    unseen_all = [i for ids in by_session.values() for i in ids]
    head_on_unseen = score(head_decide, data, unseen_all, None, names)

    def rows_of(sample_index):
        rows = np.flatnonzero(data.sample_of == sample_index)
        return rows[data.labels[rows] >= 0]

    def memory_for(mode, factor):
        memory = PrototypeMemory(mode, k=3, threshold_factor=factor)
        for n in names:
            pick = base_rows[n]
            if len(pick) > 4000:
                pick = rng.choice(pick, 4000, replace=False)
            if mode == "exemplar":
                # one "demonstration" per sample, at most 40 of them
                owners = data.sample_of[pick]
                chosen = rng.permutation(np.unique(owners))[:40]
                keep = np.isin(owners, chosen)
                memory.add_class(n, z[pick[keep]], demo_ids=owners[keep], seed=seed)
            else:
                memory.add_class(n, z[pick], seed=seed)
        return memory

    def memory_decide(memory):
        labels = np.array(memory.labels + [""], dtype=object)
        return lambda rows: labels[memory.classify_arrays(z[rows])[0]]

    # rejection of the unregistered gesture by the memory alone
    for mode in ("ncm", "multi", "exemplar"):
        for factor in (1.0, 1.25, 1.5):
            memory = memory_for(mode, factor)
            before = score(memory_decide(memory), data, test_old, None, names)
            unseen = score(memory_decide(memory), data, unseen_all, None, names)
            results.append({"kind": "unseen", "mode": mode, "factor": factor, "before": before, "unseen": unseen})
    results.append({"kind": "head", "before": baseline, "unseen": head_on_unseen})

    if QUICK:
        # Several signals together: the classifier's confidence must agree with the nearest prototype, with a margin and a longer run.
        # A new class is accepted only when the classifier is not sure of an old one.
        def hybrid(memory, conf, margin_min):
            labels = np.array(memory.labels + [""], dtype=object)
            known = set(names)

            def decide(rows):
                index, _, margin = memory.classify_arrays(z[rows])
                said = labels[index]
                best = np.array(names, dtype=object)[probs[rows].argmax(axis=1)]
                top = probs[rows].max(axis=1)
                keep = np.zeros(len(rows), bool)
                for i, name in enumerate(said):
                    if not name or margin[i] < margin_min:
                        continue
                    keep[i] = (best[i] == name and top[i] >= conf) if name in known else top[i] < conf
                return np.where(keep, said, "")
            return decide
        reg = sessions[0]
        pool = [int(i) for i in by_session[reg]]
        if len(pool) >= 5:
            demo_rows = np.concatenate([rows_of(int(i)) for i in rng.choice(pool, 5, replace=False)])
        else:
            demo_rows = None
        elsewhere = [i for s in sessions[1:] for i in by_session[s]]
        for conf in (.7, .9):
            for margin_min in (0.0, .02, .05):
                for run in (4, 8):
                    base = memory_for("ncm", 1.25)
                    row = {"kind": "hybrid", "conf": conf, "margin": margin_min, "run": run,
                           "before": score(hybrid(base, conf, margin_min), data, test_old, None, names, run=run),
                           "unseen": score(hybrid(base, conf, margin_min), data, unseen_all, None, names, run=run)}
                    if demo_rows is not None and elsewhere:
                        added = memory_for("ncm", 1.25)
                        added.add_class(held, z[demo_rows], demo_ids=data.sample_of[demo_rows], seed=seed)
                        row["new"] = score(hybrid(added, conf, margin_min), data, elsewhere, held, added.labels, run=run)
                        row["old_with_new"] = score(hybrid(added, conf, margin_min), data, test_old, held, added.labels, run=run)
                    results.append(row)
        # the same session too: closest to using a gesture right after registering it (and the optimistic side)
        for draw in range(DRAWS):
            reg = sessions[draw % len(sessions)]
            pool = [int(i) for i in by_session[reg]]
            if len(pool) < 7:
                continue
            chosen = [int(i) for i in rng.choice(pool, 5, replace=False)]
            demo_rows = np.concatenate([rows_of(i) for i in chosen])
            memory = memory_for("ncm", 1.25)
            memory.add_class(held, z[demo_rows], demo_ids=data.sample_of[demo_rows], seed=seed)
            decide = memory_decide(memory)
            rest = [i for i in pool if i not in chosen]
            elsewhere = [i for s in sessions if s != reg for i in by_session[s]]
            results.append({"kind": "same_session", "same": score(decide, data, rest, held, memory.labels),
                            "other": score(decide, data, elsewhere, held, memory.labels) if elsewhere else None,
                            "conflicts": memory.conflicts(held)})
        return results, {"names": names}
    if len(sessions) < 2:
        return results, {"names": names}
    for draw in range(DRAWS):
        reg = sessions[draw % len(sessions)]
        tests = [i for s in sessions if s != reg for i in by_session[s]]
        pool = by_session[reg]
        for shots in (1, 3, 5, 10):
            if len(pool) < shots:
                continue
            demos = [int(i) for i in rng.choice(pool, shots, replace=False)]
            demo_rows = np.concatenate([rows_of(i) for i in demos])
            demo_ids = data.sample_of[demo_rows]
            for mode in (("ncm", "multi", "exemplar") if shots == 5 else ("ncm",)):
                factor = 1.25
                memory = memory_for(mode, factor)
                memory.add_class(held, z[demo_rows], demo_ids=demo_ids, seed=seed)
                decide = memory_decide(memory)
                new = score(decide, data, tests, held, memory.labels)
                old = score(decide, data, test_old, held, memory.labels)
                conflicts = memory.conflicts(held)
                results.append({"kind": "memory", "mode": mode, "shots": shots, "draw": draw, "new": new, "old": old,
                                "conflicts": conflicts[:2]})
            if shots == 5:
                # classifier head expansion on frozen embeddings: 5 demonstrations plus a replay of the old classes
                head = nn.Linear(model.embedding, len(names) + 1).to(device)
                with torch.no_grad():
                    head.weight[:len(names)] = model.head.weight
                    head.bias[:len(names)] = model.head.bias
                opt = torch.optim.Adam(head.parameters(), lr=1e-2)
                replay = np.concatenate([rng.choice(base_rows[n], min(300, len(base_rows[n])), replace=False) for n in names])
                x = torch.from_numpy(np.concatenate([z[replay], z[demo_rows]])).to(device)
                y = torch.tensor(np.concatenate([[names.index(CLASSES[c]) for c in data.labels[replay]], np.full(len(demo_rows), len(names))]),
                                 dtype=torch.long, device=device)
                weight = torch.ones(len(names) + 1, device=device)
                weight[-1] = 5.0
                for _ in range(200):
                    opt.zero_grad()
                    nn.functional.cross_entropy(head(x), y, weight=weight).backward()
                    opt.step()
                with torch.no_grad():
                    expanded = torch.softmax(head(torch.from_numpy(z).to(device)), dim=1).cpu().numpy()
                wide = names + [held]

                def wide_decide(rows):
                    best = expanded[rows].argmax(axis=1)
                    conf = expanded[rows].max(axis=1)
                    return np.array([wide[b] if c >= .7 else "" for b, c in zip(best, conf)], dtype=object)

                results.append({"kind": "memory", "mode": "head_expand", "shots": 5, "draw": draw,
                                "new": score(wide_decide, data, tests, held, wide),
                                "old": score(wide_decide, data, test_old, held, wide), "conflicts": []})
    return results, {"names": names}


def main() -> int:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    store = SampleStore()
    data = build_dataset(store, store.samples(sources=[GUIDED_SCHEDULED]), CLASSES)
    folds = session_folds(data.samples, CLASSES, 3)
    done = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}
    OUT.parent.mkdir(exist_ok=True)
    for seed in ((0,) if QUICK else (0, 1)):
        for supcon in ((0.0,) if QUICK else (0.0, .2)):
            for i, held in enumerate(HELD):
                key = f"{held}|s{supcon}|seed{seed}"
                if key in done:
                    continue
                results, info = run_one(data, held, folds[(i + seed) % len(folds)], supcon, seed, device)
                done[key] = {"held": held, "supcon": supcon, "seed": seed, "results": results, **info}
                OUT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                if QUICK:
                    print(key, "done", flush=True)
                    continue
                mem = [r for r in results if r["kind"] == "memory" and r["shots"] == 5 and r["mode"] == "ncm"]
                hit = sum(r["new"]["reps"].get(held, (0, 0))[0] for r in mem)
                tot = sum(r["new"]["reps"].get(held, (0, 0))[1] for r in mem)
                print(f"{key}: ncm 5 shots {hit}/{tot}", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
