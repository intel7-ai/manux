"""gaze_validate.py and fetch_face_model.py: argument errors, resource cleanup, timeout download."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", PROJECT_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gv = _load("gaze_validate")
fm = _load("fetch_face_model")


# ---------- argument errors: one line, exit code 2, no traceback --------------------------

@pytest.mark.parametrize("bad", [
    ["--screen-px", "1920"], ["--screen-px", "axb"], ["--screen-px", "1920x1080x3"], ["--screen-cm", "53.1x"],
    ["--screen-cm", "0x10"], ["--screen-cm=-5x10"], ["--distance-cm", "0"], ["--distance-cm", "nan"],
    ["--frames-per-point", "0"], ["--settle-frames", "-1"], ["--drift-minutes", "-2"],
])
def test_bad_arguments_give_one_line_error_and_exit_2(bad, capsys, tmp_path):
    rc = gv.main(["--synthetic", "--out-dir", str(tmp_path), *bad])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("error: invalid argument:") and len(err.strip().splitlines()) == 1
    assert "Traceback" not in err
    assert not list(tmp_path.glob("*.json"))  # nothing was run or saved


def test_pair_parser_messages():
    assert gv._pair("1920x1080", int) == (1920, 1080)
    assert gv._pair("53.1X29.9", float) == (53.1, 29.9)
    for text in ("1920", "", "axb", "1x2x3"):
        with pytest.raises(ValueError):
            gv._pair(text, int)


def test_real_mode_without_consent_or_model_is_a_clean_error(capsys, tmp_path):
    assert gv.main(["--out-dir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "--consent" in err and "self-declaration" in err
    assert gv.main(["--consent", "--model", str(tmp_path / "none.task"), "--out-dir", str(tmp_path)]) == 2
    assert "model not found" in capsys.readouterr().err


# ---------- CameraSource cleanup, with fake cv2 / mediapipe (no camera, no model) ---------

class FakeCap:
    def __init__(self, opened=True):
        self._opened, self.released = opened, 0

    def isOpened(self):
        return self._opened

    def release(self):
        self.released += 1


class FakeLandmarker:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    state = types.SimpleNamespace(cap=None, landmarker=FakeLandmarker(), destroyed=0, windows=0,
                                  cap_opened=True, window_error=None)

    def video_capture(_src):
        state.cap = FakeCap(state.cap_opened)
        return state.cap

    def named_window(*_a):
        if state.window_error:
            raise state.window_error
        state.windows += 1

    cv2 = types.SimpleNamespace(VideoCapture=video_capture, namedWindow=named_window,
                                setWindowProperty=lambda *a: None, destroyAllWindows=lambda: setattr(
                                    state, "destroyed", state.destroyed + 1),
                                WINDOW_NORMAL=0, WND_PROP_FULLSCREEN=0, WINDOW_FULLSCREEN=0)
    vision = types.SimpleNamespace(
        FaceLandmarkerOptions=lambda **kw: kw, RunningMode=types.SimpleNamespace(VIDEO=1),
        FaceLandmarker=types.SimpleNamespace(create_from_options=lambda _o: state.landmarker))
    mp = types.SimpleNamespace(tasks=types.SimpleNamespace(BaseOptions=lambda **kw: kw, vision=vision))
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    monkeypatch.setitem(sys.modules, "mediapipe", mp)
    model = tmp_path / "m.task"
    model.write_bytes(b"x")
    state.model = model
    return state


def test_close_releases_camera_window_and_landmarker_and_is_idempotent(fakes):
    src = gv.CameraSource(gv.ScreenSpec(), "0", fakes.model)
    src.close()
    src.close()
    assert fakes.cap.released == 1 and fakes.destroyed == 1 and fakes.landmarker.closed == 1


def test_failed_camera_open_still_closes_the_landmarker(fakes):
    fakes.cap_opened = False
    with pytest.raises(RuntimeError, match="camera"):
        gv.CameraSource(gv.ScreenSpec(), "0", fakes.model)
    assert fakes.landmarker.closed == 1 and fakes.cap.released == 1
    assert fakes.destroyed == 0  # no window was ever opened


def test_failure_after_the_window_opened_cleans_up_everything(fakes, monkeypatch):
    cv2 = sys.modules["cv2"]
    monkeypatch.setattr(cv2, "setWindowProperty", lambda *a: (_ for _ in ()).throw(OSError("no fullscreen")))
    with pytest.raises(OSError):
        gv.CameraSource(gv.ScreenSpec(), "0", fakes.model)
    assert fakes.landmarker.closed == 1 and fakes.cap.released == 1 and fakes.destroyed == 1


def test_unreadable_model_closes_nothing_twice_and_reports(fakes, tmp_path):
    with pytest.raises(OSError):
        gv.CameraSource(gv.ScreenSpec(), "0", tmp_path / "missing.task")
    assert fakes.landmarker.closed == 0 and fakes.cap is None


def test_main_reports_a_camera_failure_as_one_line_and_closes(fakes, capsys, tmp_path):
    fakes.cap_opened = False
    rc = gv.main(["--consent", "--model", str(fakes.model), "--out-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2 and err.startswith("error: RuntimeError:") and "Traceback" not in err
    assert fakes.landmarker.closed == 1
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("raised,expected_rc", [(KeyboardInterrupt(), 1), (RuntimeError("boom"), 2)])
def test_main_closes_camera_window_and_landmarker_when_the_session_stops(fakes, monkeypatch, capsys, tmp_path,
                                                                         raised, expected_rc):
    def stop(*_a, **_k):
        raise raised

    monkeypatch.setattr(gv, "run_session", stop)
    rc = gv.main(["--consent", "--model", str(fakes.model), "--out-dir", str(tmp_path)])
    assert rc == expected_rc
    assert fakes.cap.released == 1 and fakes.destroyed == 1 and fakes.landmarker.closed == 1
    assert not list(tmp_path.glob("*.json"))  # an aborted or failed session saves nothing
    assert "Traceback" not in capsys.readouterr().err


# ---------- fetch_face_model: timeout download, hash flow unchanged ------------------------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _opener(data, seen):
    def open_(url, timeout=None):
        seen.append((url, timeout))
        return FakeResponse(data)
    return open_


def test_download_uses_a_timeout_and_keeps_a_verified_file(tmp_path):
    data = b"pretend model bytes" * 100
    sha = hashlib.sha256(data).hexdigest()
    seen = []
    dest = tmp_path / "models" / "f.task"
    rc = fm.fetch(dest, "https://example.invalid/f.task", sha, len(data), _opener(data, seen))
    assert rc == 0 and dest.read_bytes() == data and not dest.with_suffix(".task.part").exists()
    assert seen == [("https://example.invalid/f.task", fm.TIMEOUT_S)] and fm.TIMEOUT_S == 30
    # second run: already verified, no network call
    assert fm.fetch(dest, "https://example.invalid/f.task", sha, len(data), _opener(b"", seen)) == 0
    assert len(seen) == 1


def test_hash_mismatch_removes_the_file_and_returns_3(tmp_path, capsys):
    data = b"tampered" * 50
    dest = tmp_path / "f.task"
    rc = fm.fetch(dest, "u", "0" * 64, len(data), _opener(data, []))
    assert rc == 3 and not dest.exists() and not dest.with_suffix(".task.part").exists()
    assert "mismatch" in capsys.readouterr().err


def test_network_errors_and_oversized_downloads_return_2(tmp_path, capsys):
    def boom(url, timeout=None):
        raise TimeoutError("timed out")

    dest = tmp_path / "f.task"
    assert fm.fetch(dest, "u", "0" * 64, 10, boom) == 2 and not dest.exists()
    assert "TimeoutError" in capsys.readouterr().err
    big = b"a" * (3 * (1 << 20))
    assert fm.fetch(dest, "u", hashlib.sha256(b"a").hexdigest(), 10, _opener(big, [])) == 2
    assert not dest.exists() and not dest.with_suffix(".task.part").exists()
    assert "larger than" in capsys.readouterr().err


def test_pinned_values_are_unchanged():
    assert fm.EXPECTED_SHA256 == "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
    assert fm.EXPECTED_SIZE == 3758596
    assert fm.URL.startswith("https://storage.googleapis.com/mediapipe-models/")
