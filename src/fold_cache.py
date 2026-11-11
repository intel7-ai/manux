"""Finished evaluation folds of scripts/train_tcn.py, kept on disk so a stopped training can go on (docs/training-resume.md).

One fold = one posture held out, trained and scored (~40 s on the GPU, ~80 s on the CPU). A fold's result is only reused
when everything that could change it is the same: the recordings (name, size, modification time), the options, and
the code of the training. Anything else starts again, so old numbers never mix with new ones.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
CODE_FILES = ("scripts/train_tcn.py", "scripts/train_selected.py", "src/spatial_mlp.py", "src/tcn_features.py", "src/capture_dataset.py",
              "src/gesture_catalog.py", "src/evaluation.py",
              # these change the frames a fold trains on (cleaning, augmenting, finger reconstruction, the gate's rule)
              "src/data_quality.py", "src/offline_augment.py", "src/finger_kinematics.py", "src/gesture_gate.py")


def code_hash(files: Iterable[str] = CODE_FILES, root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for rel in files:
        try:
            h.update((root / rel).read_bytes())
        except OSError:
            h.update(b"missing:" + rel.encode())
    return h.hexdigest()[:16]


def recordings_key(paths: Iterable[Path]) -> list:
    out = []
    for p in sorted(Path(x) for x in paths):
        st = p.stat()
        out.append([p.name, st.st_size, st.st_mtime_ns])
    return out


def derived_key(paths: Iterable[Path]) -> list:
    """What is made from the recordings and trained on instead of / next to them: the cleaned copies, the augmented
    copies and the augmentation settings. Without it a recording cleaned or augmented again kept the same fingerprint
    and every old fold was reused next to a new final model."""
    out: list = []
    folders = sorted({Path(x).parent for x in paths})
    for folder in folders:
        for sub in ("cleaned", "augmented"):
            try:
                files = sorted(f for f in (folder / sub).iterdir() if f.is_file())
            except OSError:
                continue
            for f in files:
                st = f.stat()
                out.append([sub, f.name, st.st_size, st.st_mtime_ns])
        for name in ("training_selection.json", ".aug_config.json"):
            try:
                data = json.loads((folder / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                out.append([name, data.get("augmentation", data if name == ".aug_config.json" else None)])
    return out


class FoldCache:
    def __init__(self, root: Path, key_parts: Dict[str, Any], enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = bool(enabled)
        text = json.dumps(key_parts, sort_keys=True, default=str, ensure_ascii=False)
        self.fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        self.dir = self.root / self.fingerprint

    def _path(self, fold: int) -> Path:
        return self.dir / f"fold_{int(fold)}.pkl"

    def load(self, fold: int) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            with open(self._path(fold), "rb") as fh:
                data = pickle.load(fh)  # our own file on this PC
            return data if isinstance(data, dict) and data.get("fingerprint") == self.fingerprint else None
        except Exception:
            return None

    def save(self, fold: int, payload: dict) -> None:
        if not self.enabled:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(fold).with_suffix(".tmp")
            with open(tmp, "wb") as fh:
                pickle.dump({**payload, "fingerprint": self.fingerprint}, fh)
                fh.flush()
                os.fsync(fh.fileno())  # a power cut right after a fold must not lose it
            os.replace(tmp, self._path(fold))  # a stop in the middle never leaves a half file
        except Exception:
            pass

    def prune_others(self) -> None:
        """Results of other recordings/options can never be reused again."""
        try:
            for child in self.root.iterdir():
                if child.is_dir() and child.name != self.fingerprint:
                    shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass

    def prune_except(self, fingerprints: Iterable[str]) -> None:
        """For a run with several keys at once (one per gesture): keep all of them, drop every other key."""
        keep = set(fingerprints)
        try:
            for child in self.root.iterdir():
                if child.is_dir() and child.name not in keep:
                    shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass

    def clear(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- pause request: the app writes this file, the training process stops after the fold it is on -------------------
PAUSE_EXIT_CODE = 75   # EX_TEMPFAIL: "paused, run me again"
CACHE_ROOT = ROOT / "logs" / "fold_cache"


def pause_file(root: Optional[Path] = None) -> Path:
    return Path(root or CACHE_ROOT) / "PAUSE"


def request_pause(root: Optional[Path] = None) -> None:
    pause_file(root).parent.mkdir(parents=True, exist_ok=True)
    pause_file(root).write_text("pause", encoding="utf-8")


def pause_requested(root: Optional[Path] = None) -> bool:
    return pause_file(root).exists()


def clear_pause(root: Optional[Path] = None) -> None:
    try:
        pause_file(root).unlink()
    except OSError:
        pass


def saved_fold_count(root: Optional[Path] = None) -> int:
    """Folds saved by the newest key (older keys are pruned when a run starts)."""
    try:
        dirs = [d for d in Path(root or CACHE_ROOT).iterdir() if d.is_dir()]
        if not dirs:
            return 0
        newest = max(dirs, key=lambda d: d.stat().st_mtime)
        return len(list(newest.glob("fold_*.pkl")))
    except OSError:
        return 0
