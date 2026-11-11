"""crop_shadow: the shadow CNN reader brought over from hand-mouse-1109 (not connected to the app)."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.crop_shadow import CropShadow, ShadowModel, centre_crop

ROOT = Path(__file__).resolve().parents[1]


class FakeSession:
    def __init__(self, logits):
        self.logits = np.asarray(logits, dtype=np.float32)
        self.inputs = []

    def get_inputs(self):
        return [SimpleNamespace(name="x")]

    def run(self, _outputs, feed):
        self.inputs.append(feed["x"])
        return [self.logits[None]]


def _model(frames=3, step=2, center=0, logits=(0.0, 2.0, 1.0)):
    info = {"classes": ["none", "thumb_index", "thumb_ring"], "frames": frames, "step": step, "center": center}
    return ShadowModel(FakeSession(logits), info, "test")


def test_centre_crop_keeps_the_middle():
    pics = np.arange(2 * 6 * 6).reshape(2, 6, 6)
    out = centre_crop(pics, 2)
    assert out.shape == (2, 2, 2)
    assert (out == pics[:, 2:4, 2:4]).all()
    assert centre_crop(pics, 0) is pics


def test_predict_stacks_oldest_first_and_drops_none():
    m = _model(frames=3, step=2, center=4)
    history = [np.full((8, 8), i, np.uint8) for i in range(5)]
    probs = m.predict(history)
    x = m.session.inputs[-1]
    assert x.shape == (1, 3, 4, 4) and x.dtype == np.float32
    assert [round(float(x[0, k, 0, 0]) * 255) for k in range(3)] == [0, 2, 4]
    assert set(probs) == {"thumb_index", "thumb_ring"}
    assert probs["thumb_index"] > probs["thumb_ring"]


def test_short_history_repeats_the_first_picture():
    m = _model(frames=3, step=2)
    m.predict([np.full((8, 8), 7, np.uint8)])
    assert m.session.inputs[-1].shape == (1, 3, 8, 8)


def test_update_without_a_hand_counts_misses_and_resets():
    shadow = CropShadow([_model()])
    shadow.history.append(np.zeros((48, 48), np.uint8))
    for _ in range(9):
        assert shadow.update(None, None) is None
    assert len(shadow.history) == 1
    shadow.update(None, None)
    assert len(shadow.history) == 0


def test_from_config_is_off_by_default_and_survives_missing_models(tmp_path):
    assert CropShadow.from_config({}, tmp_path) is None
    assert CropShadow.from_config({"crop_shadow": True, "crop_shadow_models": ["models/missing.json"]}, tmp_path) is None


def test_importing_it_does_not_load_torch():
    code = "import sys; import src.crop_shadow; print('torch' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
