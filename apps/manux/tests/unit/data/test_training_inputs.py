from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from smoke_worker import recording
from tcn_data import load
from training_inputs import sample_data


def test_class_and_session_balance_with_other_gestures_as_negatives(tmp_path):
    path = tmp_path / "synthetic.jsonl.gz"
    recording(path, "train")
    segment = load([path])[0]
    # Two very different length sessions must get equal total sampling weight per class.
    from dataclasses import replace
    short = replace(segment, frames=segment.frames[:80], features=segment.features[:80], recording_id="other")
    x, y, weights = sample_data([segment, short], ["none", "thumb_ring"], 15)
    assert np.all(x[:, :, -1] == 1)
    assert weights[y == 0].sum() == pytest.approx(.5)
    assert weights[y == 1].sum() == pytest.approx(.5)
    split = len(segment.frames) - 14
    assert weights[:split].sum() == pytest.approx(.5)
    assert weights[split:].sum() == pytest.approx(.5)
    _, negative, _ = sample_data([segment], ["none", "other"], 15, other_as_none=True)
    assert set(negative) == {0}
    # Separate condition segments purge history instead of borrowing the other fold's frames.
    assert len(x) == len(segment.frames) + len(short.frames) - 2 * 14


def test_augmentation_keeps_padding_geometry_and_motion_sign_coherent(tmp_path):
    torch = pytest.importorskip("torch")
    from tcn_augmentation import augment
    path = tmp_path / "synthetic.jsonl.gz"
    recording(path, "train")
    x, _, _ = sample_data(load([path]), ["none", "thumb_ring"], 15)
    x = torch.from_numpy(x[:8].copy())
    x[:, :3] = 0
    x[:, 3:, 130] = -.2
    torch.manual_seed(15)
    y = augment(x)
    assert torch.isfinite(y).all() and (y[:, :, 130] <= 0).all()
    assert torch.count_nonzero(y[y[:, :, -1] == 0]) == 0
    points = y[:, :, :63].reshape(8, 15, 21, 3)
    distances = torch.linalg.vector_norm(points[:, :, [8, 12, 16, 20]] - points[:, :, 4:5], dim=-1)
    torch.testing.assert_close(y[:, :, 126:130], distances)
    valid = (y[:, 1:, -1] == 1) & (y[:, :-1, -1] == 1)
    delta = (points[:, 1:] - points[:, :-1]).reshape(8, 14, 63)
    torch.testing.assert_close(y[:, 1:, 63:126][valid], delta[valid])
