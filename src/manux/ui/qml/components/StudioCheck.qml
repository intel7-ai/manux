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
        border.color: control.checked || control.activeFocus ? Theme.Studio.mintDark : Theme.Studio.line
        Rectangle {
            anchors.centerIn: parent
            visible: control.checked
            width: 10; height: 6
            rotation: -45
            color: "transparent"
            Rectangle { width: 2; height: parent.height; color: Theme.Studio.mintText }
            Rectangle { y: parent.height - 2; width: parent.width; height: 2; color: Theme.Studio.mintText }
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
