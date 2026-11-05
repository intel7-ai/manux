"""Running a window-classifier model behind a small interface (docs/contracts.md section 10).

`Predictor` is what features / filters use: one `(window, features)` array in, one probability per
class out. `load_model` reads `<name>.onnx` + `<name>.json`, checks the card and the ONNX shapes,
and runs one zero window as a smoke test. onnxruntime is imported only here, only when a model is
actually loaded (`ml` extra), so the tests and CI run without it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence, runtime_checkable

import numpy as np

from manux.ml.card import ModelCard, ModelError, card_path, load_card


@runtime_checkable
class Predictor(Protocol):
    classes: Sequence[str]

    def __call__(self, window: np.ndarray) -> np.ndarray:
        """(window, features) float32 -> (len(classes),) probabilities summing to 1."""
        ...


def softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max())
    return e / e.sum()


class OnnxPredictor:
    """An onnxruntime session (or anything with the same `get_inputs/get_outputs/run`) as a Predictor.

    The model outputs logits (as `tools/train_tcn.py` exports); the predictor applies softmax.
    """

    def __init__(self, session: Any, card: ModelCard) -> None:
        self.session = session
        self.card = card
        self.classes = card.classes
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if len(inputs) != 1 or len(outputs) < 1:
            raise ModelError("shape", f"expected 1 input and 1+ outputs, got {len(inputs)} / {len(outputs)}")
        shape = list(inputs[0].shape)
        if len(shape) != 3 or not _batch_ok(shape[0]) or shape[1:] != [card.window, card.features]:
            raise ModelError("shape", f"input {shape} != (1, {card.window}, {card.features})")
        out_shape = list(outputs[0].shape)
        if out_shape and isinstance(out_shape[-1], int) and out_shape[-1] != len(card.classes):
            raise ModelError("shape", f"output {out_shape} != {len(card.classes)} classes")
        self.input_name = inputs[0].name
        probe = self(np.zeros((card.window, card.features), dtype=np.float32))   # also catches symbolic sizes
        if probe.shape != (len(card.classes),) or not np.isfinite(probe).all():
            raise ModelError("shape", f"a zero window gave {probe.shape}, expected ({len(card.classes)},)")

    def __call__(self, window: np.ndarray) -> np.ndarray:
        x = np.asarray(window, dtype=np.float32)[None]
        logits = np.asarray(self.session.run(None, {self.input_name: x})[0], dtype=np.float64).reshape(-1)
        return softmax(logits)


def _batch_ok(dim: Any) -> bool:
    return dim == 1 or dim is None or isinstance(dim, str)    # fixed 1 or a symbolic batch


@dataclass(frozen=True)
class LoadedModel:
    path: Path
    card: ModelCard
    predictor: Predictor


def _onnx_session(path: Path) -> Any:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise ModelError("runtime_missing", "pip install onnxruntime") from exc
    try:
        return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as exc:  # onnxruntime raises its own exception types for broken files
        raise ModelError("unreadable", f"{path.name}: {type(exc).__name__}") from exc


def load_model(path: Path, feature_version: str, features: Optional[int] = None,
               session_factory: Optional[Callable[[Path], Any]] = None) -> LoadedModel:
    """Card + ONNX checked and ready, or ModelError (nothing is half-loaded).

    `feature_version` / `features` are what the caller computes (e.g. `perception.local3d`); `ml`
    does not import `perception`. `session_factory` replaces onnxruntime in tests.
    """
    path = Path(path)
    card = load_card(card_path(path), feature_version, features)
    if not path.is_file():
        raise ModelError("missing", path.name)
    session = (session_factory or _onnx_session)(path)
    try:
        predictor = OnnxPredictor(session, card)
    except ModelError:
        raise
    except Exception as exc:
        raise ModelError("shape", f"{type(exc).__name__}: {exc}") from exc
    return LoadedModel(path=path, card=card, predictor=predictor)
