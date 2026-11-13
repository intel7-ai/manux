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
        font.pixelSize: Theme.Studio.body; font.weight: Font.DemiBold
        color: control.down ? Theme.Studio.ink : control.primary ? Theme.Studio.primaryText : Theme.Studio.ink
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: Theme.Studio.radius
        color: control.down ? Theme.Studio.mintWash : control.primary ? Theme.Studio.primaryBackground
               : control.hovered ? Theme.Studio.mintWash : control.flat ? "transparent" : Theme.Studio.surface
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.Studio.mintDark : control.flat || control.primary ? "transparent" : Theme.Studio.line
    }
}
