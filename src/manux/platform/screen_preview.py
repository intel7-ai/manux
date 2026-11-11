"""GUI-thread monitor grab with a single background JPEG encoder."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import sys

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QPolygonF

from manux.platform.monitors import windows_monitors

SCREEN_PROFILES = {"detail": (1280, 720, 65), "balanced": (960, 540, 55), "compact": (640, 360, 45)}


class WindowsMonitorGrabber:
    """Own the native capture resources on the single screen worker, not Qt's GUI thread."""
    def __init__(self):
        self._capture = None

    def __call__(self):
        import ctypes
        from ctypes import wintypes
        from mss import MSS

        if self._capture is None:
            self._capture = MSS()
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        monitors = windows_monitors()
        if not monitors:
            return None
        monitor = min(monitors, key=lambda m: m.distance(point.x, point.y))
        shot = self._capture.grab(dict(left=monitor.left, top=monitor.top,
                                      width=monitor.width, height=monitor.height))
        # Copy out of the reusable native buffer; the worker owns this QImage.
        image = QImage(shot.bgra, shot.width, shot.height, shot.width * 4, QImage.Format_RGB32).copy()
        return image, ((point.x-monitor.left)/monitor.width, (point.y-monitor.top)/monitor.height)

    def close(self):
        if self._capture is not None:
            self._capture.close()
            self._capture = None


def grab_monitor():
    """Only screen access stays on the GUI thread; hand off a private QImage."""
    point = QCursor.pos()
    screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        return None
    image = pixmap.toImage()
    image.setDevicePixelRatio(1.)
    rect = screen.geometry()
    cursor = ((point.x() - rect.x()) / rect.width(), (point.y() - rect.y()) / rect.height())
    return image, cursor


def encode_monitor(grab, profile="detail"):
    if grab is None:
        return None
    image, cursor = grab
    width, height, quality = SCREEN_PROFILES[profile]
    image = image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    # Desktop grabs omit the OS cursor. Show its actual position on the same monitor,
    # mapping logical monitor coordinates to the final (possibly high-DPI) image.
    x, y = cursor[0] * image.width(), cursor[1] * image.height()
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
    ok = image.save(buffer, "JPEG", quality)
    buffer.close()
    return bytes(encoded) if ok and 0 < encoded.size() <= 512 * 1024 else None


def capture_monitor():
    """Synchronous composition helper; the live preview uses the worker below."""
    return encode_monitor(grab_monitor())


def _encode_request(ticket, grab, encode, complete, closed, capture=None):
    try:
        if closed.is_set():
            return
        if capture is not None:
            grab = capture()
        jpeg = encode(grab, ticket.profile)
    except Exception:
        jpeg = None
    if not closed.is_set():
        complete(ticket, jpeg)  # ScreenRequests.complete is thread-safe and checks the ticket.


class ScreenPreview(QObject):
    def __init__(self, parent, *, take, complete, capture=None, encode=encode_monitor, background_capture=None):
        super().__init__(parent)
        self._native = WindowsMonitorGrabber() if capture is None and sys.platform == "win32" else None
        if background_capture is None:
            background_capture = self._native is not None
        capture = capture or self._native or grab_monitor
        self.take, self.complete, self.capture = take, complete, capture
        self.background_capture = background_capture
        self.encode = encode
        self._closed = threading.Event()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="screen-jpeg")
        self._encoding = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.start(8)

    def poll(self):
        # A timed-out HTTP request must never build an encoder queue.
        if self._closed.is_set() or self._encoding is not None and not self._encoding.done():
            return
        ticket = self.take()
        if ticket is None:
            return
        if self.background_capture:
            self._encoding = self._worker.submit(
                _encode_request, ticket, None, self.encode, self.complete, self._closed, self.capture)
            return
        try:
            grab = self.capture()
        except Exception:
            grab = None  # locked/unavailable desktop must not interrupt hand capture
        if grab is None:
            self.complete(ticket, None)
        else:
            self._encoding = self._worker.submit(
                _encode_request, ticket, grab, self.encode, self.complete, self._closed)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self.timer.stop()
        if self._native is not None:
            self._worker.submit(self._native.close)
        self._worker.shutdown(wait=False)
