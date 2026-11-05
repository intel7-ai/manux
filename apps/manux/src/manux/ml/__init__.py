"""ONNX model loading and model-info validation (docs/contracts.md section 10).

`card.py`: the model info file and its checks. `predictor.py`: the `Predictor` interface and the
onnxruntime-backed implementation (onnxruntime imported lazily, `ml` extra).
See apps/manux/docs/architecture.md section 3-4 for what belongs here and what it may import."""
from manux.ml.card import KINDS, REASON_TEXT, ModelCard, ModelError, card_path, load_card, validate_card
from manux.ml.predictor import LoadedModel, OnnxPredictor, Predictor, load_model, softmax

__all__ = ["KINDS", "REASON_TEXT", "ModelCard", "ModelError", "card_path", "load_card", "validate_card",
           "LoadedModel", "OnnxPredictor", "Predictor", "load_model", "softmax"]
