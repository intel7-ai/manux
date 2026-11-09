"""Whole-monitor composition with synthetic Qt pixels; never captures this PC."""
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication
import manux.platform.screen_preview as module


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
