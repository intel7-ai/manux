"""Screen geometry, error statistics, the measurement protocol and the validation script."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gaze.estimator import GazeEstimator  # noqa: E402
from src.gaze.metrics import ScreenSpec, summarize_errors  # noqa: E402
from src.gaze.protocol import CALIBRATION_POINTS, VALIDATION_POINTS, run_session  # noqa: E402
from src.gaze.synthetic import SyntheticSource  # noqa: E402

SCREEN = ScreenSpec()  # 1920x1080, 53.1x29.9 cm, 60 cm


def test_one_degree_is_distance_times_tan_one_degree():
    assert SCREEN.cm_per_degree == pytest.approx(60.0 * math.tan(math.radians(1.0)))
    assert SCREEN.cm_per_degree == pytest.approx(1.0472, abs=1e-3)
    # horizontal shift of exactly 1 degree worth of cm is a 1 degree error
    shift = SCREEN.cm_per_degree / SCREEN.width_cm
    assert SCREEN.error_deg((0.5 + shift, 0.5), (0.5, 0.5)) == pytest.approx(1.0, abs=1e-9)
    assert SCREEN.px_per_degree == pytest.approx(37.8, abs=0.3)  # the "1 degree ~ 38 px" in the research notes
    assert SCREEN.error_px((0.5 + shift, 0.5), (0.5, 0.5)) == pytest.approx(shift * 1920)


def test_screen_spec_rejects_nonsense_at_configuration_time():
    for bad in ({"distance_cm": 0}, {"width_cm": -1}, {"width_px": float("nan")}, {"height_px": "1080"}):
        with pytest.raises(ValueError):
            ScreenSpec(**bad)


def test_summarize_errors():
    s = summarize_errors([1.0, 2.0, 3.0, 4.0, float("nan"), None], total_frames=8)
    assert s["n"] == 4 and s["valid_rate"] == 0.5
    assert s["median"] == 2.5 and s["mean"] == 2.5 and s["max"] == 4.0
    assert s["p90"] == pytest.approx(3.7)
    empty = summarize_errors([], total_frames=10)
    assert empty["n"] == 0 and empty["valid_rate"] == 0.0 and empty["median"] is None
    assert summarize_errors([], total_frames=0)["valid_rate"] == 0.0


def test_validation_points_are_not_calibration_points():
    assert len(CALIBRATION_POINTS) == 9 and len(set(CALIBRATION_POINTS)) == 9
    assert not set(CALIBRATION_POINTS) & set(VALIDATION_POINTS)


def test_session_on_a_perfect_synthetic_person_has_near_zero_error():
    report = run_session(SyntheticSource(noise=0.0), SCREEN, GazeEstimator(alpha=1e-8, smooth=False))
    assert report["calibration"]["ok"] and report["calibration"]["n_targets"] == 9
    err = report["validation"]["error_deg"]
    assert err["median"] < 0.05 and err["valid_rate"] == 1.0
    assert len(report["validation"]["per_point"]) == 5
    assert report["drift"] == []


def test_session_with_noise_stays_in_a_sane_range_and_reports_valid_rate():
    src = SyntheticSource(noise=0.005, head_wobble_deg=1.0, dropout_rate=0.2, blink_rate=0.05, seed=7)
    report = run_session(src, SCREEN, GazeEstimator(), frames_per_point=25, settle_frames=10)
    assert report["calibration"]["ok"]
    err = report["validation"]["error_deg"]
    assert err["median"] < 2.0
    assert 0.5 < err["valid_rate"] < 0.95  # dropped and blink frames reduce the valid rate, honestly


def test_drift_mode_measures_growing_error():
    src = SyntheticSource(noise=0.0, drift_h_per_min=0.01)
    report = run_session(src, SCREEN, GazeEstimator(alpha=1e-8, smooth=False), drift_minutes=5, drift_interval_s=60)
    drift = report["drift"]
    assert len(drift) >= 6 and drift[0]["t_s"] == 0.0
    errs = [d["median_error_deg"] for d in drift]
    assert errs[-1] > errs[0] + 3.0
    assert report["drift_slope_deg_per_min"] > 0.5


def test_failed_calibration_returns_a_report_not_an_exception():
    report = run_session(SyntheticSource(dropout_rate=1.0), SCREEN, GazeEstimator())
    assert report["calibration"]["ok"] is False and report["validation"] is None
    assert report["calibration"]["rejected_frames"]["no-face"] > 0


def test_report_is_json_and_holds_no_per_frame_data():
    report = run_session(SyntheticSource(noise=0.003), SCREEN, GazeEstimator(), drift_minutes=2)
    json.dumps(report)  # serializable

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield str(k)
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    # no key may carry landmarks, images or per-frame gaze points; lists stay short (per point/time only)
    assert not [k for k in keys(report) if any(w in k.lower() for w in ("landmark", "image", "frame_point", "raw"))]
    assert not any(isinstance(v, list) and len(v) > 50 for v in report["validation"].values())
    assert set(report["validation"]["per_point"][0]) == {
        "target", "frames", "valid_rate", "median_error_deg", "median_error_px"}


def test_session_is_deterministic_for_a_seed():
    a = run_session(SyntheticSource(noise=0.004, seed=11), SCREEN, GazeEstimator())
    b = run_session(SyntheticSource(noise=0.004, seed=11), SCREEN, GazeEstimator())
    assert a == b


def test_validate_script_synthetic_mode_end_to_end(tmp_path):
    script = PROJECT_ROOT / "scripts" / "gaze_validate.py"
    out = tmp_path / "gaze"
    proc = subprocess.run([sys.executable, str(script), "--synthetic", "--out-dir", str(out),
                           "--frames-per-point", "15", "--settle-frames", "5"],
                          capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT))
    assert proc.returncode == 0, proc.stderr
    files = list(out.glob("gaze-synthetic-*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["meta"]["mode"] == "synthetic" and "code" in data["meta"]["interpretation"]
    assert data["calibration"]["ok"] and data["validation"]["error_deg"]["median"] is not None


def test_validate_script_real_mode_refuses_without_consent_and_without_model(tmp_path):
    script = PROJECT_ROOT / "scripts" / "gaze_validate.py"
    no_consent = subprocess.run([sys.executable, str(script), "--out-dir", str(tmp_path)],
                                capture_output=True, text=True, timeout=60, cwd=str(PROJECT_ROOT))
    assert no_consent.returncode == 2 and "--consent" in no_consent.stderr
    no_model = subprocess.run([sys.executable, str(script), "--consent", "--model", str(tmp_path / "absent.task"),
                               "--out-dir", str(tmp_path)],
                              capture_output=True, text=True, timeout=60, cwd=str(PROJECT_ROOT))
    assert no_model.returncode == 2 and "fetch_face_model" in no_model.stderr
    assert not list(tmp_path.glob("*.json"))  # nothing was started, nothing saved


def test_fetch_script_pins_a_hash_and_verifies_files(tmp_path):
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    try:
        import fetch_face_model as fm
    finally:
        sys.path.pop(0)
    assert len(fm.EXPECTED_SHA256) == 64 and fm.EXPECTED_SIZE > 1_000_000
    fake = tmp_path / "m.task"
    fake.write_bytes(b"not the model")
    assert fm.verify(fake) is False and fm.verify(tmp_path / "missing.task") is False
