"""Unit tests for EdgeGlowOverlay (platform layer)."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPaintEvent
from PySide6.QtWidgets import QApplication

from manux.core.boundary import EdgeProximity
from manux.platform.overlay import EdgeGlowOverlay


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_edge_glow_overlay_window_flags_and_attributes(qapp):
    overlay = EdgeGlowOverlay()
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    overlay.deleteLater()


def test_edge_glow_color_interpolation():
    # Intensity 0.0 -> amber
    r0, g0, b0, a0 = EdgeGlowOverlay._interpolate_color(0.0)
    assert r0 == 255
    assert g0 == 165
    assert b0 == 0
    assert a0 == 0

    # Intensity 1.0 -> crimson red with high alpha
    r1, g1, b1, a1 = EdgeGlowOverlay._interpolate_color(1.0)
    assert r1 == 255
    assert g1 == 30
    assert b1 == 40
    assert a1 == 215

    # Intensity 0.5 -> intermediate
    r_mid, g_mid, b_mid, a_mid = EdgeGlowOverlay._interpolate_color(0.5)
    assert r_mid == 255
    assert 30 < g_mid < 165
    assert 0 < b_mid < 40
    assert 0 < a_mid < 215


def test_edge_glow_overlay_proximity_update_and_clear(qapp):
    overlay = EdgeGlowOverlay()
    overlay.resize(800, 600)

    # Initially inactive
    overlay.set_proximities(EdgeProximity(0.0, 0.0, 0.0, 0.0))
    assert not overlay.isVisible()

    # Active right edge
    overlay.set_proximities(EdgeProximity(left=0.0, right=0.75, top=0.0, bottom=0.0))
    assert overlay.isVisible()
    assert overlay._right_intensity == 0.75

    # Paint event triggers without error
    event = QPaintEvent(QRect(0, 0, 800, 600))
    overlay.paintEvent(event)

    # Active corner (top and left)
    overlay.set_proximities(EdgeProximity(left=0.5, right=0.0, top=0.8, bottom=0.0))
    assert overlay.isVisible()
    overlay.paintEvent(event)

    # Clear hides overlay
    overlay.clear()
    assert not overlay.isVisible()
    assert overlay._left_intensity == 0.0
    assert overlay._top_intensity == 0.0

    overlay.deleteLater()
