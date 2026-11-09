"""
Qt tray icon (port of hand-mouse/src/qt_tray.py; the "트래킹 측정" item is left out until the tracking test is ported): the app can live in the notification area.

The status dot on the MANUX icon shows camera reception (same states as the overlay):
  ok      green   a hand is tracked
  weak    amber   a hand is tracked but the picture is dark
  none    grey    no hand in view
  paused  slate   control is off
The menu starts / pauses control, shows the dashboard, toggles the
overlay and quits. Everything goes through QtDashboard slots, like the window's buttons.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon

COLORS = {"ok": "#2FBF8F", "weak": "#F2B544", "none": "#8A9AA6", "paused": "#526575"}


def state_icon(brand: QIcon, state: str, size: int = 32) -> QIcon:
    pix = brand.pixmap(size, size)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(COLORS.get(state, COLORS["none"])))
    dot = max(4, size // 4)
    p.drawEllipse(size - dot - 1, size - dot - 1, dot, dot)
    p.end()
    return QIcon(pix)


class QtTray(QObject):
    def __init__(self, dashboard) -> None:
        super().__init__()
        self.dashboard = dashboard
        self.icons = {name: state_icon(dashboard.application.windowIcon(), name) for name in COLORS}
        self.tray = QSystemTrayIcon(self.icons["paused"])
        self.tray.setToolTip("MANUX")
        menu = QMenu()
        self.control_action = QAction("제어 시작", menu)
        self.control_action.triggered.connect(lambda: dashboard.command(32))
        show_action = QAction("화면 열기", menu)
        show_action.triggered.connect(dashboard.showDashboard)
        self.overlay_action = QAction("상태 표시", menu)
        self.overlay_action.setCheckable(True)
        self.overlay_action.toggled.connect(dashboard.setOverlayVisible)
        logs_action = QAction("로그 묶어 내보내기", menu)
        logs_action.triggered.connect(dashboard.exportLogs)
        quit_action = QAction("종료", menu)
        quit_action.triggered.connect(dashboard.quitApp)
        for action in (self.control_action, show_action, self.overlay_action, logs_action):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self._last = None
        self._told_hidden = False
        self.notice = QLabel("MANUX · 트레이에서 계속 실행됩니다. 종료는 트레이 메뉴에서 하세요.",
                             None, Qt.WindowType.ToolTip | Qt.WindowType.WindowTransparentForInput)
        self.notice.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.notice.setStyleSheet("background: #112331; color: white; padding: 12px; border-radius: 8px;")
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.notice_timer.setInterval(2500)
        self.notice_timer.timeout.connect(self.notice.hide)
        dashboard.hiddenToTray.connect(self._hidden)
        dashboard.receptionChanged.connect(self.refresh)
        dashboard.liveChanged.connect(self.refresh)
        dashboard.metricsChanged.connect(self.refresh)
        dashboard.overlayChanged.connect(self.refresh)
        self.tray.show()
        self.refresh()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.dashboard.showDashboard()

    def _hidden(self) -> None:
        if not self._told_hidden:
            self._told_hidden = True
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            area = screen.availableGeometry()
            self.notice.adjustSize()
            self.notice.move(max(area.left(), area.right() - self.notice.width() - 16),
                             max(area.top(), area.bottom() - self.notice.height() - 16))
            self.notice.show()
            self.notice_timer.start()

    def refresh(self) -> None:
        d = self.dashboard
        state, text = d.receptionState, d.receptionText
        key = (state, text, d.controlLabel, d.overlay_visible)
        if key == self._last:
            return
        self._last = key
        self.tray.setIcon(self.icons.get(state, self.icons["none"]))
        self.tray.setToolTip(f"MANUX · {text}")
        self.control_action.setText(d.controlLabel)
        self.overlay_action.blockSignals(True)
        self.overlay_action.setChecked(d.overlay_visible)
        self.overlay_action.blockSignals(False)

    def hide(self) -> None:
        self.notice_timer.stop()
        self.notice.hide()
        self.tray.hide()
