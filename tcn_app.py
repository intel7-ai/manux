"""Dedicated launcher for the TCN-enabled MANUX build, based on overhaul 7448e4a."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check_model(model_path=None):
    """Run the shipped ONNX model before opening the camera; never silently skip TCN."""
    import numpy as np
    from src.gesture_gate import DEFAULT_MODEL, load_predictor
    from src.tcn_features import FEATURES

    path = Path(model_path) if model_path is not None else DEFAULT_MODEL
    predict, info = load_predictor(path)
    from src.gesture_catalog import CLASSES
    classes = info.get("classes", [])
    if ("none" not in classes or len(classes) < 2 or len(classes) != len(set(classes))
            or any(c not in CLASSES for c in classes)):
        raise ValueError("TCN model has unsupported or duplicate gesture classes")
    if info.get("features") != FEATURES or int(info.get("window", 0)) < 1:
        raise ValueError("TCN model input dimensions do not match this app")
    probs = predict(np.zeros((1, int(info["window"]), FEATURES), dtype=np.float32))
    if (probs.shape != (len(classes),) or not np.isfinite(probs).all()
            or (probs < 0).any() or not np.isclose(probs.sum(), 1.0)):
        raise ValueError("TCN model returned invalid probabilities")
    return {
        "model": path.name,
        "trained": info.get("trained", "unknown"),
        "classes": info["classes"],
        "input": [1, int(info["window"]), FEATURES],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "inference": "passed",
    }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    try:
        result = check_model()
        import PySide6  # noqa: F401 -- fail before opening the camera if the UI is unavailable
    except Exception as exc:
        print(f"[MANUX TCN] 시작 검사 실패: {exc}", file=sys.stderr)
        print("setup-tcn.cmd 실행 후 다시 시도하세요.", file=sys.stderr)
        return 1
    if "--check" in args:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"[MANUX TCN] 모델 검사 통과 · 학습 {result['trained']}")
    print(f"손바닥 모드 · TCN {len(result['classes']) - 1}종 동작 확인 켬 · 제어는 일시정지 상태로 시작합니다.")
    from main import main as run_app
    original = sys.argv
    try:
        sys.argv = [str(ROOT / "main.py"), *args, "--ui", "qt", "--no-tray", "--tcn-app"]
        return run_app()
    finally:
        sys.argv = original


if __name__ == "__main__":
    raise SystemExit(main())
