"""Immutable personal model versions, published through one atomic active pointer."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid
from contextlib import contextmanager

from manux.ml.card import ModelError, card_path, load_card
from manux.ml.predictor import load_model

SCHEMA = "manux-active-model/1"
REPORT_SCHEMA = "manux-model-validation/1"


@contextmanager
def _install_lock(folder):
    lock = folder / ".install.lock"
    with lock.open("x"):
        pass
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass  # never report a committed pointer as a failed installation due to lock cleanup


def active_token(folder: Path) -> bytes | None:
    try:
        with (Path(folder) / "active.json").open("rb") as file:
            token = file.read(65537)
        if len(token) > 65536:
            raise ValueError("active model pointer is too large")
        return token
    except FileNotFoundError:
        return None


def load_active(folder: Path, feature_version: str, features: int, session_factory=None):
    folder = Path(folder)
    try:
        pointer = json.loads(active_token(folder) or b"null")
        if (not isinstance(pointer, dict) or pointer.get("schema") != SCHEMA
                or not re.fullmatch(r"[0-9a-f]{32}", str(pointer.get("version", "")))):
            raise ValueError("invalid active model pointer")
        version = folder / "versions" / pointer["version"]
        # Do not permit a symlink/junction in the version path to escape this store.
        if not version.resolve().is_relative_to(folder.resolve()):
            raise ValueError("model version leaves the store")
        if hashlib.sha256((version / "model.json").read_bytes()).hexdigest() != pointer.get("card_sha256"):
            raise ModelError("checksum", "active card")
        model = load_model(version / "model.onnx", feature_version, features, session_factory)
        if model.card.raw.get("model_sha256") != pointer.get("model_sha256"):
            raise ModelError("checksum", "active model")
        report_bytes = (version / "validation.json").read_bytes()
        if hashlib.sha256(report_bytes).hexdigest() != pointer.get("validation_sha256"):
            raise ModelError("checksum", "validation report")
        if model.card.kind != "personal" or not model.card.decisions:
            raise ValueError("active direct store requires a personal model")
        validate_report(json.loads(report_bytes), model.card, pointer["model_sha256"], pointer["card_sha256"])
        return model
    except ModelError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise ModelError("unreadable", "active model") from error


def validate_report(report, card, model_digest, card_digest):
    """Reject incomplete/failed/regressing evidence before any active-file change."""
    if (not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA
            or report.get("model_sha256") != model_digest or report.get("card_sha256") != card_digest):
        raise ValueError("validation report is not bound to this model and card")
    metrics, baseline = report.get("candidate"), report.get("baseline")
    if not isinstance(metrics, dict) or not isinstance(baseline, dict):
        raise ValueError("missing candidate or baseline evidence")
    expected = set(card.gestures)
    rows, previous = metrics.get("per_gesture", {}), baseline.get("per_gesture", {})
    if (not isinstance(rows, dict) or not isinstance(previous, dict) or set(rows) != expected
            or not expected or not set(previous) <= expected
            or not all(isinstance(row, dict) for row in [*rows.values(), *previous.values()])):
        raise ValueError("validation must cover every candidate and existing gesture class")
    for cls, row in rows.items():
        attempts, hits = row.get("attempts"), row.get("tcn_hits")
        if (type(attempts) is not int or type(hits) is not int or not 0 < hits <= attempts):
            raise ValueError(f"no successful independent evaluation attempt for {cls}")
        if cls in previous:
            old = previous[cls]
            if (old.get("attempts") != attempts or type(old.get("tcn_hits")) is not int
                    or not 0 <= old["tcn_hits"] <= attempts or hits < old["tcn_hits"]):
                raise ValueError(f"existing gesture regressed: {cls}")
    rates = []
    for value in (metrics, baseline):
        seconds, rate = value.get("neutral_seconds"), value.get("tcn_misfires_per_minute")
        if (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0
                or type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
            raise ValueError("neutral exposure and measured misfire rates are required")
        rates.append(rate)
    if rates[0] > rates[1]:
        raise ValueError("neutral misfires regressed")
    if not math.isclose(metrics["neutral_seconds"], baseline["neutral_seconds"]):
        raise ValueError("candidate and baseline must use the same neutral exposure")
    wrong, old_wrong = metrics.get("wrong_events"), baseline.get("wrong_events")
    if type(wrong) is not int or type(old_wrong) is not int or not 0 <= wrong <= old_wrong:
        raise ValueError("wrong-gesture events regressed or were not measured")
    hashes = report.get("evaluation_sha256")
    if not isinstance(hashes, list) or not hashes or not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
        raise ValueError("independent evaluation recording hashes are required")
    sources = card.raw["data"]
    if not sources or not all(isinstance(row, dict) and isinstance(row.get("sha256"), str)
                              and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) for row in sources):
        raise ValueError("training recording hashes are required")
    training = {row["sha256"] for row in sources}
    for row in sources:
        values = row.get("capture_sha256", [row["sha256"]])
        if not isinstance(values, list) or not values or row["sha256"] not in values or any(not re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in values):
            raise ValueError("invalid training capture lineage")
        training.update(values)
    if training.intersection(hashes):
        raise ValueError("evaluation recordings overlap training")


def install(folder: Path, candidate, report: dict, expected_active: bytes | None, *, cancelled=None) -> Path:
    """Called after held-out evaluation. A failed install leaves the old pointer and files intact."""
    folder = Path(folder)
    if cancelled is not None and cancelled():
        raise ValueError("작업을 취소했습니다. 사용 중 모델은 유지됩니다.")
    model_bytes, card_bytes = candidate.path.read_bytes(), card_path(candidate.path).read_bytes()
    model_digest, card_digest = hashlib.sha256(model_bytes).hexdigest(), hashlib.sha256(card_bytes).hexdigest()
    if candidate.card.kind != "personal" or not candidate.card.decisions:
        raise ValueError("only a direct personal model can be installed here")
    if candidate.card.raw.get("model_sha256") != model_digest:
        raise ValueError("candidate changed since evaluation or has no model checksum")
    validate_report(report, candidate.card, model_digest, card_digest)
    folder.mkdir(parents=True, exist_ok=True)
    with _install_lock(folder):
        temporary = None
        try:
            if active_token(folder) != expected_active:
                raise ValueError("active model changed during evaluation; evaluate again")
            if expected_active is not None:
                previous = json.loads(expected_active)
                previous_card = load_card(folder / "versions" / previous["version"] / "model.json",
                                          candidate.card.feature_version, candidate.card.features)
                if (set(report["baseline"]["per_gesture"]) != set(previous_card.gestures)
                        or any(candidate.card.gestures.get(c) != g for c, g in previous_card.gestures.items())
                        or any(candidate.card.decisions[c].kind != d.kind for c, d in previous_card.decisions.items())):
                    raise ValueError("validation omitted or remapped an existing gesture")
                old_training = {row.get("sha256") for row in previous_card.raw["data"] if isinstance(row, dict)}
                for row in previous_card.raw["data"]:
                    old_training.update(row.get("capture_sha256", [row["sha256"]]))
                if old_training.intersection(report["evaluation_sha256"]):
                    raise ValueError("evaluation overlaps the active model's training")
            elif set(report["baseline"]["per_gesture"]) != set(candidate.card.gestures):
                raise ValueError("initial rule baseline must cover all candidate gestures")
            version_id = uuid.uuid4().hex
            version = folder / "versions" / version_id
            if not version.resolve().is_relative_to(folder.resolve()):
                raise ValueError("model version leaves the store")
            version.mkdir(parents=True)
            report_bytes = json.dumps(report, indent=2).encode()
            for name, data in (("model.onnx", model_bytes), ("model.json", card_bytes),
                               ("validation.json", report_bytes)):
                with (version / name).open("xb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
            pointer = {"schema": SCHEMA, "version": version_id,
                       "model_sha256": model_digest, "card_sha256": card_digest,
                       "validation_sha256": hashlib.sha256(report_bytes).hexdigest()}
            temporary = folder / f".active-{version_id}.tmp"
            with temporary.open("x", encoding="utf-8") as file:
                json.dump(pointer, file)
                file.flush()
                os.fsync(file.fileno())
            if cancelled is not None and cancelled():
                raise ValueError("작업을 취소했습니다. 사용 중 모델은 유지됩니다.")
            os.replace(temporary, folder / "active.json")
            return version / "model.onnx"
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
