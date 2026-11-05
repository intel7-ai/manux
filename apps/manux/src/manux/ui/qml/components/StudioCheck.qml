import QtQuick
import QtQuick.Controls
import "../theme" as Theme
CheckBox {
    id: control
    implicitWidth: 24; implicitHeight: 30
    padding: 0
    indicator: Rectangle {
        x: 1; y: (control.height - height) / 2
        width: 22; height: 22; radius: 6
        color: control.checked ? Theme.Studio.mint : Theme.Studio.paper
        border.width: control.activeFocus ? 2 : 1
        border.color: control.checked || control.activeFocus ? Theme.Studio.mintDark : "#B8C8CF"
        Text {
            anchors.centerIn: parent
            text: control.checked ? "✓" : ""
            color: Theme.Studio.ink
            font.family: Theme.Studio.font; font.pixelSize: 16; font.bold: true
        }
    }
    contentItem: Text {
        text: control.text
        leftPadding: control.text ? 32 : 0
        color: Theme.Studio.ink
        font.family: Theme.Studio.font; font.pixelSize: 13
        verticalAlignment: Text.AlignVCenter
    }
}
