"""Local-only derived features. Content/version/group keyed, never pickle or raw recordings."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from zipfile import BadZipFile

import numpy as np

from manux.perception.local3d import FEATURES, FEATURE_VERSION, frame_features


class FeatureCache:
    def __init__(self, folder: Path, version: str = FEATURE_VERSION):
        self.folder, self.version = Path(folder), version

    def get(self, frames, aspect: float, recording_hash: str, offset: int):
        signature = json.dumps([self.version, FEATURES, recording_hash, offset, len(frames), aspect])
        key = hashlib.sha256(signature.encode()).hexdigest()
        path = self.folder / f"{key}.npz"
        try:
            with np.load(path, allow_pickle=False) as cached:
                features = cached["features"]
                if (str(cached["signature"].item()) == signature and features.shape == (len(frames), FEATURES)
                        and features.dtype == np.float32 and np.isfinite(features).all()):
                    return features
        except (OSError, ValueError, KeyError, EOFError, BadZipFile):
            pass
        features = frame_features(frames, aspect)
        temporary = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.folder, suffix=".npz.tmp", delete=False) as file:
                temporary = Path(file.name)
                np.savez_compressed(file, signature=np.array(signature), features=features)
            os.replace(temporary, path)
        except OSError:
            pass  # a cache is optional; inability to cache must not change the training data
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return features
