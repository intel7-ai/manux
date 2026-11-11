"""Click-through transparent border glow overlay (platform layer).

Renders a soft directional vignette glow at the screen edges when the control hand
approaches the camera boundaries while control is active.
Uses frameless transparent click-through window attributes so it never intercepts
mouse clicks, keyboard input, or focus.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QLinearGradient,
    QPaintEvent,
    QPainter,
)
from PySide6.QtWidgets import QApplication, QWidget

from manux.core.boundary import EdgeProximity


class EdgeGlowOverlay(QWidget):
    """Transparent click-through border glow window for webcam FOV boundary warning."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._left_intensity: float = 0.0
        self._right_intensity: float = 0.0
        self._top_intensity: float = 0.0
        self._bottom_intensity: float = 0.0

        # Visual configuration (Option A: soft vignette glow 35-50px)
        self.base_glow_depth: int = 45

        self.sync_screen_geometry()

    def sync_screen_geometry(self) -> None:
        """Position the overlay to cover the primary screen."""
        app = QGuiApplication.instance()
        if app is None:
            return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.geometry()
            self.setGeometry(geo)

    def set_proximities(self, proximity: EdgeProximity) -> None:
        """Update directional edge intensities [0.0..1.0]."""
        self._left_intensity = max(0.0, min(1.0, float(proximity.left)))
        self._right_intensity = max(0.0, min(1.0, float(proximity.right)))
        self._top_intensity = max(0.0, min(1.0, float(proximity.top)))
        self._bottom_intensity = max(0.0, min(1.0, float(proximity.bottom)))

        if proximity.active:
            if not self.isVisible():
                self.sync_screen_geometry()
                self.show()
            self.update()
        else:
            if self.isVisible():
                self.hide()

    def clear(self) -> None:
        """Immediately hide and clear all glows (e.g. when control turns off or hand is lost)."""
        self._left_intensity = 0.0
        self._right_intensity = 0.0
        self._top_intensity = 0.0
        self._bottom_intensity = 0.0
        if self.isVisible():
            self.hide()

    @staticmethod
    def _interpolate_color(intensity: float) -> tuple[int, int, int, int]:
        """Interpolate from Amber/Orange (#FFA500) to Crimson Red (#FF1E28) with dynamic alpha."""
        t = max(0.0, min(1.0, intensity))
        # RGB interpolation:
        # t=0.0 -> (255, 165, 0)
        # t=1.0 -> (255, 30, 40)
        r = 255
        g = int(165 * (1.0 - t) + 30 * t)
        b = int(0 * (1.0 - t) + 40 * t)
        # Alpha interpolation: up to ~215 (84% opacity) at the extreme boundary
        alpha = int(215 * t)
        return r, g, b, alpha

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 1. Left border glow
        if self._left_intensity > 0.0:
            r, g, b, alpha = self._interpolate_color(self._left_intensity)
            depth = int(self.base_glow_depth * (0.8 + 0.4 * self._left_intensity))
            grad = QLinearGradient(0, 0, depth, 0)
            grad.setColorAt(0.0, QColor(r, g, b, alpha))
            grad.setColorAt(0.35, QColor(r, g, b, int(alpha * 0.45)))
            grad.setColorAt(0.75, QColor(r, g, b, int(alpha * 0.15)))
            grad.setColorAt(1.0, QColor(r, g, b, 0))
            painter.fillRect(QRect(0, 0, depth, h), grad)

        # 2. Right border glow
        if self._right_intensity > 0.0:
            r, g, b, alpha = self._interpolate_color(self._right_intensity)
            depth = int(self.base_glow_depth * (0.8 + 0.4 * self._right_intensity))
            grad = QLinearGradient(w, 0, w - depth, 0)
            grad.setColorAt(0.0, QColor(r, g, b, alpha))
            grad.setColorAt(0.35, QColor(r, g, b, int(alpha * 0.45)))
            grad.setColorAt(0.75, QColor(r, g, b, int(alpha * 0.15)))
            grad.setColorAt(1.0, QColor(r, g, b, 0))
            painter.fillRect(QRect(w - depth, 0, depth, h), grad)

        # 3. Top border glow
        if self._top_intensity > 0.0:
            r, g, b, alpha = self._interpolate_color(self._top_intensity)
            depth = int(self.base_glow_depth * (0.8 + 0.4 * self._top_intensity))
            grad = QLinearGradient(0, 0, 0, depth)
            grad.setColorAt(0.0, QColor(r, g, b, alpha))
            grad.setColorAt(0.35, QColor(r, g, b, int(alpha * 0.45)))
            grad.setColorAt(0.75, QColor(r, g, b, int(alpha * 0.15)))
            grad.setColorAt(1.0, QColor(r, g, b, 0))
            painter.fillRect(QRect(0, 0, w, depth), grad)

        # 4. Bottom border glow
        if self._bottom_intensity > 0.0:
            r, g, b, alpha = self._interpolate_color(self._bottom_intensity)
            depth = int(self.base_glow_depth * (0.8 + 0.4 * self._bottom_intensity))
            grad = QLinearGradient(0, h, 0, h - depth)
            grad.setColorAt(0.0, QColor(r, g, b, alpha))
            grad.setColorAt(0.35, QColor(r, g, b, int(alpha * 0.45)))
            grad.setColorAt(0.75, QColor(r, g, b, int(alpha * 0.15)))
            grad.setColorAt(1.0, QColor(r, g, b, 0))
            painter.fillRect(QRect(0, h - depth, w, depth), grad)

        painter.end()
