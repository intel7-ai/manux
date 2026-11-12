"""Whole-monitor composition with synthetic Qt pixels; never captures this PC."""
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication
import manux.platform.screen_preview as module
from manux.core.contracts import ScreenCaptureRequest


@pytest.mark.parametrize("size,dpr", [((1920, 1080), 1.), ((1080, 1920), 1.), ((3840, 2160), 2.)])
def test_entire_current_monitor_ratio_corners_and_high_dpi(monkeypatch, size, dpr):
    app = QApplication.instance() or QApplication([])
    image = QImage(*size, QImage.Format_RGB32)
    image.fill(QColor("#c83232"))
    # Different colors at opposite sides expose cropping or accidental mirroring.
    from PySide6.QtGui import QPainter
    painter = QPainter(image)
    painter.fillRect(0, 0, size[0] // 4, size[1], QColor("#32c832"))
    painter.fillRect(size[0] * 3 // 4, 0, size[0] // 4, size[1], QColor("#3232c8"))
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    selected = []
    point = QPoint(-100, 50)
    rect = QRect(-int(size[0] / dpr), 0, int(size[0] / dpr), int(size[1] / dpr))
    screen = SimpleNamespace(grabWindow=lambda window: pixmap, geometry=lambda: rect)
    monkeypatch.setattr(module, "QCursor", SimpleNamespace(pos=lambda: point))
    monkeypatch.setattr(module, "QGuiApplication", SimpleNamespace(
        screenAt=lambda p: selected.append(p) or screen, primaryScreen=lambda: None))
    jpeg = module.capture_monitor()
    result = QImage.fromData(jpeg, "JPEG")
    assert selected == [point] and not result.isNull()
    assert result.width() <= 1280 and result.height() <= 720
    assert result.width() / result.height() == pytest.approx(size[0] / size[1], abs=.002)
    left, right = result.pixelColor(5, 5), result.pixelColor(result.width() - 6, 5)
    assert left.green() > 150 and right.blue() > 150
    assert 4 < len(jpeg) <= 512 * 1024


def test_no_capture_without_request_and_failed_capture_isolated():
    app = QApplication.instance() or QApplication([])
    requests, captures, completed = [], [], []
    def capture():
        captures.append(True)
        raise OSError("desktop unavailable")
    preview = module.ScreenPreview(None, take=lambda: requests.pop(0) if requests else None,
                                   complete=lambda ticket, data: completed.append((ticket, data)), capture=capture)
    preview.timer.stop()
    try:
        preview.poll()
        assert not captures
        ticket = object()
        requests.append(ticket)
        preview.poll()
        assert completed == [(ticket, None)]
    finally:
        preview.close()


@pytest.mark.parametrize("close_during_encode", [False, True])
@pytest.mark.parametrize("background_capture", [False, True])
def test_slow_screen_encoding_does_not_block_gui_or_queue_more_grabs(close_during_encode, background_capture):
    import threading
    app = QApplication.instance() or QApplication([])
    gui_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    tickets = [ScreenCaptureRequest("balanced"), ScreenCaptureRequest("compact")]
    captured, completed, encoded_on = [], [], []
    def capture():
        assert (threading.get_ident() != gui_thread) == background_capture
        captured.append(True)
        return object()
    def encode(grab, profile):
        assert profile == "balanced"
        encoded_on.append(threading.get_ident())
        entered.set()
        assert release.wait(2)
        return b"synthetic JPEG"
    preview = module.ScreenPreview(None, take=lambda: tickets.pop(0) if tickets else None,
                                   complete=lambda ticket, data: completed.append((ticket, data)),
                                   capture=capture, encode=encode, background_capture=background_capture)
    preview.timer.stop()
    try:
        first = tickets[0]
        preview.poll()
        assert entered.wait(1)  # GUI returned while the encoder is deliberately blocked.
        preview.poll()
        assert len(captured) == 1 and len(tickets) == 1
        assert encoded_on == [encoded_on[0]] and encoded_on[0] != gui_thread
        if close_during_encode:
            preview.close()
        release.set()
        preview._encoding.result(timeout=1)
        assert completed == ([] if close_during_encode else [(first, b"synthetic JPEG")])
    finally:
        release.set()
        preview.close()


@pytest.mark.parametrize("profile,size", [("detail", (1280, 720)), ("balanced", (960, 540)),
                                         ("compact", (640, 360)), ("internet_hd", (1280, 720)),
                                         ("internet_detail", (1920, 1080))])
def test_screen_profiles_reduce_bytes_without_cropping_monitor(profile, size):
    image = QImage(1920, 1080, QImage.Format_RGB32)
    image.fill(QColor("#123456"))
    from PySide6.QtGui import QPainter
    painter = QPainter(image)
    painter.fillRect(0, 0, 480, 1080, QColor("#ff0000"))
    painter.fillRect(1440, 0, 480, 1080, QColor("#0000ff"))
    painter.end()
    encoded = module.encode_monitor((image, (.5, .5)), profile)
    result = QImage.fromData(encoded, "JPEG")
    assert (result.width(), result.height()) == size
    assert result.pixelColor(5, 5).red() > 200
    assert result.pixelColor(size[0]-6, 5).blue() > 200


def test_internet_detail_busy_frame_falls_back_inside_existing_byte_limit():
    import numpy as np
    pixels = np.random.default_rng(42).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
    image = QImage(pixels.data, 1920, 1080, 1920 * 3, QImage.Format_RGB888).copy()
    encoded = module.encode_monitor((image, (.5, .5)), "internet_detail")
    assert encoded is not None and 4 < len(encoded) <= 512 * 1024
    result = QImage.fromData(encoded, "JPEG")
    assert (result.width(), result.height()) == (1280, 720)


def test_internet_profile_keeps_existing_transport_profiles_unchanged():
    assert {name: module.SCREEN_PROFILES[name] for name in ("detail", "balanced", "compact")} == {
        "detail": (1280, 720, 65), "balanced": (960, 540, 55), "compact": (640, 360, 45)}


@pytest.mark.parametrize("profile,size", [("internet_detail", (1080, 1920)), ("internet_hd", (720, 1280))])
def test_internet_profiles_preserve_portrait_monitor_resolution(profile, size):
    image = QImage(1080, 1920, QImage.Format_RGB32)
    image.fill(QColor("#123456"))
    encoded = module.encode_monitor((image, (.5, .5)), profile)
    result = QImage.fromData(encoded, "JPEG")
    assert (result.width(), result.height()) == size


def test_native_capture_and_cleanup_run_on_the_same_worker(monkeypatch):
    import threading
    app = QApplication.instance() or QApplication([])
    gui_thread = threading.get_ident()
    calls, requests = [], [ScreenCaptureRequest("balanced")]
    closed = threading.Event()
    class Native:
        def __call__(self):
            calls.append(("grab", threading.get_ident()))
            return object()
        def close(self):
            calls.append(("close", threading.get_ident()))
            closed.set()
    monkeypatch.setattr(module, "WindowsMonitorGrabber", Native)
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    preview = module.ScreenPreview(None, take=lambda: requests.pop(0) if requests else None,
                                   complete=lambda *_: None, encode=lambda *_: b"synthetic JPEG")
    preview.timer.stop()
    try:
        preview.poll()
        preview._encoding.result(timeout=1)
    finally:
        preview.close()
    assert closed.wait(1)
    preview.close()
    preview.poll()
    assert [name for name, _ in calls] == ["grab", "close"]
    assert calls[0][1] == calls[1][1] != gui_thread
