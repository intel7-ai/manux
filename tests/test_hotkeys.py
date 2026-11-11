"""The physical settings keys must not fire while the user types O or 0 into a text field."""
import os

import pytest

from src.hotkeys import VK_0, VK_F2, VK_O, settings_key_pressed


class FakeKeys:
    def __init__(self, *down):
        self.down = set(down)
        self.read = []

    def pressed(self, vks, require_focus=False):
        self.read.extend(vks)
        return any(vk in self.down for vk in vks)


@pytest.mark.parametrize("vk", [VK_O, VK_0])
def test_o_and_0_open_the_settings_only_outside_text_fields(vk):
    assert settings_key_pressed(FakeKeys(vk), typing=False) is True
    assert settings_key_pressed(FakeKeys(vk), typing=True) is False


def test_f2_opens_the_settings_even_while_typing():
    assert settings_key_pressed(FakeKeys(VK_F2), typing=True) is True


def test_keys_are_read_while_typing_so_no_press_is_left_for_later():
    keys = FakeKeys(VK_O)
    settings_key_pressed(keys, typing=True)
    assert {VK_F2, VK_O, VK_0} <= set(keys.read)


def test_o_is_read_even_when_f2_is_down():
    keys = FakeKeys(VK_F2, VK_O)
    assert settings_key_pressed(keys, typing=False) is True
    assert VK_O in keys.read


def test_focus_check_sees_fields_in_the_window_and_in_popups_but_not_buttons():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from src.qt_dashboard import text_input_has_focus

    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    engine = QQmlApplicationEngine()
    engine.loadData(b"""
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            visible: true; width: 400; height: 300
            TextField { id: field }
            Button { id: button; y: 50; text: "x" }
            SpinBox { id: spin; y: 100; editable: true }
            Popup { id: popup; contentItem: TextField { id: inPopup } }
            function focusOn(name) {
                popup.close()
                if (name === "field") field.forceActiveFocus()
                if (name === "button") button.forceActiveFocus()
                if (name === "spin") spin.contentItem.forceActiveFocus()
                if (name === "popup") { popup.open(); inPopup.forceActiveFocus() }
            }
        }
    """, QUrl("focus.qml"))
    window = engine.rootObjects()[0]
    if not QTest.qWaitForWindowActive(window, 2000):
        pytest.skip("this platform does not activate windows")

    def focused_on(name):
        window.focusOn(name)
        QTest.qWait(50)
        app.processEvents()
        return text_input_has_focus()

    assert focused_on("field") is True
    assert focused_on("button") is False
    assert focused_on("spin") is True
    assert focused_on("popup") is True
    window.close()
    engine.deleteLater()
