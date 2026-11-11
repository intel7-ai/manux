"""Typing a gesture's name must not fire the window's one-letter shortcuts (Q quits, F marks a failure...)."""
import os

import pytest


def test_letters_typed_in_a_field_in_a_popup_stay_in_the_field():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    engine = QQmlApplicationEngine()
    engine.loadData(b"""
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            visible: true; width: 400; height: 300
            property int fired: 0
            property string typed: field.text
            Shortcut { sequence: "Q"; onActivated: fired++ }
            Shortcut { sequence: "F"; onActivated: fired++ }
            Popup {
                id: popup; objectName: "popup"; modal: false; closePolicy: Popup.NoAutoClose; focus: true
                onOpened: field.forceActiveFocus()
                contentItem: TextField { id: field; width: 100 }
            }
            function start() { popup.open() }
        }
    """)
    window = engine.rootObjects()[0]
    window.requestActivate()
    window.findChild(QObject, "popup").setProperty("visible", True)
    QTest.qWait(150)
    QTest.keyClick(window, Qt.Key_Q)
    QTest.keyClick(window, Qt.Key_F)
    QTest.qWait(50)
    app.processEvents()
    assert window.property("typed").lower() == "qf"
    assert window.property("fired") == 0
