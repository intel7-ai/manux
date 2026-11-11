"""Trains the online encoder on stored samples and builds its prototype memory: data/online/encoder.pt, encoder.json, memory.json.
Used only by the online features; the app's models in models/ are not touched. Gestures a person registered are rebuilt from their
stored demonstrations, because prototypes only make sense for the encoder that made them.

프로젝트 폴더에서 (GPU 환경):  ..\\hand-mouse-gpuenv\\Scripts\\python.exe scripts/online_train_base.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)

import numpy as np  # noqa: E402
import torch  # noqa: E402

from scripts.train_tcn import augment  # noqa: E402
from src.online.baseline import train_encoder  # noqa: E402
from src.online.dataset import SKIP_DO_FRAMES, build_dataset, session_folds, target_positions, window_positions  # noqa: E402
from src.online.live import MEMORY_FILE, LiveEncoder, save_encoder  # noqa: E402
from src.online.memory import PrototypeMemory  # noqa: E402
from src.online.models import make_model  # noqa: E402
from src.online.registration import USER_SOURCE  # noqa: E402
from src.online.samples import DEFAULT_ROOT, GUIDED_SCHEDULED, USER_CONFIRMED, SampleStore  # noqa: E402

CLASSES = ["none", "thumb_index", "thumb_ring", "thumb_middle", "thumb_pinky", "palm_side", "v_sign", "thumbs_up"]
KIND, WINDOW, EMBEDDING, EPOCHS = "tcn", 15, 64, 25
MEMORY_MODE = "ncm"     # docs/online-learning-phase3.md: exemplars recognised least, several means added false fires
MAX_PER_CLASS = 4000
MAX_DEMOS = 40


def main() -> int:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    store = SampleStore()
    data = build_dataset(store, store.samples(sources=[GUIDED_SCHEDULED]), CLASSES)
    # one fold of whole sessions never trains the base model: background learning checks its candidates there (src/online/update_job.py)
    validation = sorted(session_folds(data.samples, CLASSES, 3)[0])
    positions = target_positions(data, {s.sample_id for s in data.samples if s.session not in set(validation)})
    print(f"{len(data.samples)} samples, {len(positions)} windows, {device}", flush=True)
    torch.manual_seed(0)
    model = make_model(KIND, len(CLASSES), WINDOW, EMBEDDING)
    t0 = time.perf_counter()
    info = train_encoder(model, data, positions, WINDOW, epochs=EPOCHS, device=device, seed=0, augment=lambda x: augment(x))
    print(f"trained in {time.perf_counter() - t0:.0f}s, loss {info['losses'][0]} -> {info['losses'][-1]}", flush=True)
    model.to("cpu").eval()
    version = save_encoder(model, {"kind": KIND, "window": WINDOW, "embedding": EMBEDDING, "classes": CLASSES, "epochs": EPOCHS,
                                   "trained_on": len(data.samples), "validation_sessions": validation, "created": time.strftime("%Y-%m-%d %H:%M:%S")}, DEFAULT_ROOT)
    memory = PrototypeMemory(MEMORY_MODE, encoder_version=version)
    rng = np.random.default_rng(0)
    frames = torch.from_numpy(data.frames)
    for c, name in enumerate(CLASSES):
        rows = positions[data.labels[positions] == c]
        owners = data.sample_of[rows]
        chosen = rng.permutation(np.unique(owners))[:MAX_DEMOS]
        rows = rows[np.isin(owners, chosen)]
        if len(rows) > MAX_PER_CLASS:
            rows = rng.choice(rows, MAX_PER_CLASS, replace=False)
        with torch.no_grad():
            z = np.concatenate([model.embed(frames[torch.from_numpy(window_positions(data.starts, rows[i:i + 4096], WINDOW))]).numpy()
                                for i in range(0, len(rows), 4096)])
        entry = memory.add_class(name, z, demo_ids=data.sample_of[rows], source="guided", seed=0)
        print(f"  {name}: {len(entry.prototypes)} prototypes, limit {entry.threshold:.3f}", flush=True)
    encoder = LiveEncoder(DEFAULT_ROOT)
    custom = {}
    for sample in store.samples(sources=[USER_CONFIRMED]):
        if (sample.extra or {}).get("reason") == "registration":
            custom.setdefault(sample.label, []).append(sample)
    for label, samples in custom.items():
        zs, ids = [], []
        for i, sample in enumerate(samples):
            z = encoder.embed_frames(store.frames(sample.sample_id), sample.aspect)[int(sample.extra.get("context_frames", 0)) + SKIP_DO_FRAMES:]
            zs.append(z)
            ids.append(np.full(len(z), i))
        memory.add_class(label, np.concatenate(zs), demo_ids=np.concatenate(ids), source=USER_SOURCE)
        print(f"  {label}: rebuilt from {len(samples)} demonstrations", flush=True)
    memory.save(DEFAULT_ROOT / MEMORY_FILE)
    print("saved", DEFAULT_ROOT, "version", version, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
