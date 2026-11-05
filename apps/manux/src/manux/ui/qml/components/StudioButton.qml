import QtQuick
import QtQuick.Controls
import "../theme" as Theme
Button {
    id: control
    property bool primary: highlighted
    implicitHeight: 40
    implicitWidth: label.implicitWidth + 28
    leftPadding: 14; rightPadding: 14
    opacity: enabled ? 1 : 0.42
    font.family: Theme.Studio.font
    Accessible.role: Accessible.Button
    contentItem: Label {
        id: label
        text: control.text
        font.family: Theme.Studio.font
        font.pixelSize: 13; font.bold: true
        color: Theme.Studio.ink
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 10
        color: control.down ? "#BCEFE1" : control.primary ? Theme.Studio.mint
               : control.hovered ? Theme.Studio.mintWash : control.flat ? "transparent" : Theme.Studio.surface
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.Studio.mintDark : control.flat || control.primary ? "transparent" : Theme.Studio.line
    }
}
