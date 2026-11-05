"""Model info file ("model card", `<model>.json`) and its checks (docs/contracts.md section 10).

A model is used only when its card passes `validate_card`. Every failure is a `ModelError` with a
short `code` (shown as a reason on the screen, `REASON_TEXT`) - never a half-loaded model.

Cards written by Dan's `scripts/train_tcn.py` (hand-mouse-gesture-overhaul@7448e4a) have no
`kind` and no `gestures`: they load as `kind="base"` with an empty `gestures` mapping.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from manux.core.contracts import GESTURE_ID_RE

REQUIRED = ("classes", "window", "features", "feature_version", "threshold", "frames_in_a_row",
            "trained", "data", "held_out_results")
KINDS = ("base", "personal")
CLASS_RE = re.compile(r"^[a-z][a-z0-9_]*$")

REASON_TEXT = {
    "missing": "모델 없음",
    "unreadable": "모델 정보를 읽지 못함",
    "invalid": "모델 정보가 잘못됨",
    "feature_version": "특징 버전 다름",
    "runtime_missing": "모델 실행 라이브러리(onnxruntime) 없음",
    "shape": "모델 입력·출력 모양이 정보와 다름",
    "no_gestures": "모델이 확인할 동작이 없음",
}


class ModelError(Exception):
    """A model that must not be used. `code` is a key of REASON_TEXT."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    @property
    def text(self) -> str:
        return REASON_TEXT.get(self.code, self.code)


@dataclass(frozen=True)
class ModelCard:
    classes: Tuple[str, ...]
    window: int
    features: int
    feature_version: str
    threshold: float
    frames_in_a_row: int
    trained: str
    kind: str = "base"
    gestures: Mapping[str, str] = field(default_factory=dict)   # class -> gesture id
    raw: Mapping[str, Any] = field(default_factory=dict)        # the whole file (data, results ...)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gestures", MappingProxyType(dict(self.gestures)))
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))

    def index(self, name: str) -> int:
        return self.classes.index(name)


def _int(card: Mapping[str, Any], key: str, minimum: int = 1) -> int:
    v = card[key]
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise ModelError("invalid", f"{key} must be an integer >= {minimum}, got {v!r}")
    return v


def validate_card(card: Any, feature_version: str, features: Optional[int] = None) -> ModelCard:
    """The checked card, or ModelError. `feature_version` / `features` are what the app computes."""
    if not isinstance(card, Mapping):
        raise ModelError("invalid", "the card is not a JSON object")
    missing = [k for k in REQUIRED if k not in card]
    if missing:
        raise ModelError("invalid", f"missing keys {missing}")
    classes = card["classes"]
    if (not isinstance(classes, list) or len(classes) < 2
            or not all(isinstance(c, str) and CLASS_RE.fullmatch(c) for c in classes) or len(set(classes)) != len(classes)):
        raise ModelError("invalid", f"classes must be 2+ distinct lowercase names, got {classes!r}")
    window = _int(card, "window")
    n_features = _int(card, "features")
    frames = _int(card, "frames_in_a_row")
    thr = card["threshold"]
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not math.isfinite(thr) or not 0.0 < thr <= 1.0:
        raise ModelError("invalid", f"threshold must be within (0, 1], got {thr!r}")
    if card["feature_version"] != feature_version:
        raise ModelError("feature_version", f"model {card['feature_version']!r} != app {feature_version!r}")
    if features is not None and n_features != features:
        raise ModelError("feature_version", f"model has {n_features} features per frame, the app computes {features}")
    if not isinstance(card["data"], list) or not isinstance(card["held_out_results"], list):
        raise ModelError("invalid", "data and held_out_results must be lists")
    kind = card.get("kind", "base")
    if kind not in KINDS:
        raise ModelError("invalid", f"kind must be one of {KINDS}, got {kind!r}")
    gestures = card.get("gestures", {}) or {}
    if not isinstance(gestures, Mapping):
        raise ModelError("invalid", "gestures must map class -> gesture id")
    for cls, gid in gestures.items():
        if cls not in classes:
            raise ModelError("invalid", f"gestures names unknown class {cls!r}")
        if cls == "none":
            raise ModelError("invalid", "the 'none' class stands for no gesture and maps to no gesture id")
        if not isinstance(gid, str) or not GESTURE_ID_RE.match(gid):
            raise ModelError("invalid", f"gestures[{cls!r}] must be a gesture id '<feature>.<name>', got {gid!r}")
    if len(set(gestures.values())) != len(gestures):
        raise ModelError("invalid", "two classes map to the same gesture id")
    return ModelCard(classes=tuple(classes), window=window, features=n_features,
                     feature_version=str(card["feature_version"]), threshold=float(thr), frames_in_a_row=frames,
                     trained=str(card["trained"]), kind=kind, gestures=dict(gestures), raw=dict(card))


def card_path(model_path: Path) -> Path:
    """`gesture_tcn.onnx` -> `gesture_tcn.json` (same folder, same name)."""
    return Path(model_path).with_suffix(".json")


def load_card(path: Path, feature_version: str, features: Optional[int] = None) -> ModelCard:
    path = Path(path)
    if not path.is_file():
        raise ModelError("missing", str(path.name))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelError("unreadable", f"{path.name}: {type(exc).__name__}") from exc
    return validate_card(data, feature_version, features)
