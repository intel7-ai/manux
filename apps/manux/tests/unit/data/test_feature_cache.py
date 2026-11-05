import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import feature_cache


def test_cache_is_bound_to_recording_version_group_and_aspect(tmp_path, monkeypatch):
    calls = []
    def compute(frames, aspect):
        calls.append((len(frames), aspect))
        return np.full((len(frames), 133), len(calls), dtype=np.float32)
    monkeypatch.setattr(feature_cache, "frame_features", compute)
    cache = feature_cache.FeatureCache(tmp_path)
    frames = [{"t": 0.}, {"t": .03}]
    first = cache.get(frames, .75, "a" * 64, 0)
    assert np.array_equal(first, cache.get(frames, .75, "a" * 64, 0))
    assert len(calls) == 1
    cache.get(frames, .75, "b" * 64, 0)
    cache.get(frames, .75, "a" * 64, 2)
    cache.get(frames, .5, "a" * 64, 0)
    feature_cache.FeatureCache(tmp_path, version="local3d-v2").get(frames, .75, "a" * 64, 0)
    assert len(calls) == 5


def test_corrupt_cache_is_recomputed_without_pickle(tmp_path):
    cache = feature_cache.FeatureCache(tmp_path)
    expected = cache.get([{"t": 0.}], .75, "a" * 64, 0)
    path = next(tmp_path.glob("*.npz"))
    path.write_bytes(b"broken cache")
    assert np.array_equal(expected, cache.get([{"t": 0.}], .75, "a" * 64, 0))
    with np.load(path, allow_pickle=False) as stored:
        assert stored["features"].shape == (1, 133)
