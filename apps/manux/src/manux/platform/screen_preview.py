"""Qt GUI-thread capture of the whole monitor containing the current OS cursor."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QPolygonF


def capture_monitor():
    point = QCursor.pos()
    screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        return None
    image = pixmap.toImage()
    image.setDevicePixelRatio(1.)
    image = image.scaled(1280, 720, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    # Desktop grabs omit the OS cursor. Show its actual position on the same monitor,
    # mapping logical monitor coordinates to the final (possibly high-DPI) image.
    rect = screen.geometry()
    x = (point.x() - rect.x()) * image.width() / rect.width()
    y = (point.y() - rect.y()) * image.height() / rect.height()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#111827"), 1.5))
    painter.setBrush(QColor("white"))
    painter.drawPolygon(QPolygonF([QPointF(x + dx, y + dy) for dx, dy in
                                  ((0, 0), (0, 18), (5, 13), (9, 21), (12, 19), (8, 12), (15, 12))]))
    painter.end()
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.WriteOnly)
    ok = image.save(buffer, "JPEG", 65)
    buffer.close()
    return bytes(encoded) if ok and 0 < encoded.size() <= 512 * 1024 else None


class ScreenPreview(QObject):
    def __init__(self, parent, *, take, complete, capture=capture_monitor):
        super().__init__(parent)
        self.take, self.complete, self.capture = take, complete, capture
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(25)

    def poll(self):
        ticket = self.take()
        if ticket is None:
            return
        try:
            jpeg = self.capture()
        except Exception:
            jpeg = None  # locked/unavailable desktop must not interrupt hand capture
        self.complete(ticket, jpeg)

    def close(self):
        self.timer.stop()
