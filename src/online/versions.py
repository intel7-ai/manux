"""Which online model is in use. A candidate must pass its checks to become production; the one before it stays for rollback.

data/online/versions/ holds production.pt, previous.pt, candidate.pt and versions.json. A failed candidate changes nothing.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import torch

from src.online.samples import DEFAULT_ROOT


@dataclass
class ValidationLimits:
    macro_f1_drop: float = .02            # against production
    class_recall_drop: float = .05        # for each old gesture
    false_fire_ratio: float = 1.1         # false fires per minute, against production
    false_fire_margin: float = .5         # per minute, so a production near zero can still vary a little
    wrong_rep_rise: float = .03           # another gesture firing during a repetition (clicks, drags at the wrong moment)
    new_class_recall: float = .5          # for a class being added
    max_inference_ms: float = 8.0


def finite_weights(state: Dict[str, torch.Tensor]) -> bool:
    return all(bool(torch.isfinite(v).all()) for v in state.values() if torch.is_floating_point(v))


def _rate(report: Dict[str, Any]) -> float:
    return report["false_fires"] / report["none_minutes"] if report.get("none_minutes") else 0.0


def _recalls(report: Dict[str, Any]) -> Dict[str, float]:
    return {k: v[0] / v[1] for k, v in report.get("reps", {}).items() if v[1]}


def check_candidate(candidate: Dict[str, Any], production: Optional[Dict[str, Any]], *, state: Optional[Dict[str, torch.Tensor]] = None,
                    new_classes: Optional[List[str]] = None, inference_ms: Optional[float] = None, limits: Optional[ValidationLimits] = None) -> List[str]:
    """Reasons to refuse a candidate (evaluate_samples reports on the same held-out sessions). An empty list means it passes."""
    limits = limits or ValidationLimits()
    problems: List[str] = []
    if state is not None and not finite_weights(state):
        problems.append("non_finite_weights")
    if not math.isfinite(candidate.get("macro_f1", float("nan"))):
        problems.append("non_finite_metrics")
    if inference_ms is not None and inference_ms > limits.max_inference_ms:
        problems.append(f"too_slow:{inference_ms:.1f}ms")
    recalls = _recalls(candidate)
    for name in new_classes or []:
        if recalls.get(name, 0.0) < limits.new_class_recall:
            problems.append(f"new_class_recall:{name}")
    if production is None:
        return problems
    if candidate["macro_f1"] < production["macro_f1"] - limits.macro_f1_drop:
        problems.append("macro_f1_dropped")
    before = _recalls(production)
    for name, value in before.items():
        if name not in (new_classes or []) and recalls.get(name, 0.0) < value - limits.class_recall_drop:
            problems.append(f"forgot:{name}")
    if _rate(candidate) > _rate(production) * limits.false_fire_ratio + limits.false_fire_margin:
        problems.append("more_false_fires")
    total_new, total_old = candidate.get("reps_total", 0), production.get("reps_total", 0)
    if total_new and total_old and candidate.get("wrong_reps", 0) / total_new > production.get("wrong_reps", 0) / total_old + limits.wrong_rep_rise:
        problems.append("more_wrong_gestures")
    return problems


class ModelManager:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root) / "versions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "versions.json"
        self.history: List[Dict[str, Any]] = json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path.is_file() else []

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.pt"

    def has(self, name: str) -> bool:
        return self._path(name).is_file()

    def _save_index(self) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.history, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.index_path)

    def _save(self, name: str, state: Dict[str, torch.Tensor], info: Dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        os.close(fd)
        torch.save({"state": {k: v.detach().cpu() for k, v in state.items()}, "info": info}, tmp)
        os.replace(tmp, self._path(name))

    def load(self, name: str = "production") -> Dict[str, Any]:
        return torch.load(self._path(name), map_location="cpu", weights_only=True)

    def initialize(self, state: Dict[str, torch.Tensor], info: Dict[str, Any]) -> None:
        """The first production model, when there is none."""
        if not self.has("production"):
            self._save("production", state, {**info, "created": time.time()})
            self._log("initialized", info)

    def _log(self, event: str, info: Dict[str, Any], reasons: Optional[List[str]] = None) -> None:
        self.history.append({"event": event, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "reasons": reasons or [],
                             **{k: v for k, v in info.items() if k in ("metrics", "training", "data_snapshot", "classes")}})
        self._save_index()

    def submit(self, state: Dict[str, torch.Tensor], info: Dict[str, Any], problems: List[str]) -> bool:
        """Saves the candidate. It becomes production only with no problems; otherwise production stays exactly as it was."""
        self._save("candidate", state, {**info, "created": time.time(), "problems": problems})
        if problems:
            self._log("rejected", info, problems)
            return False
        if self.has("production"):
            shutil.copy2(self._path("production"), self._path("previous"))
        os.replace(self._path("candidate"), self._path("production"))
        self._log("promoted", info)
        return True

    def rollback(self) -> bool:
        if not self.has("previous"):
            return False
        os.replace(self._path("previous"), self._path("production"))
        self._log("rolled_back", {})
        return True
