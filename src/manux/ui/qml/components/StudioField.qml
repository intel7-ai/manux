import QtQuick
import QtQuick.Controls
import "../theme" as Theme
TextField {
    id: control
    implicitHeight: 42
    color: Theme.Studio.ink
    placeholderTextColor: Theme.Studio.muted
    selectionColor: Theme.Studio.mint
    selectedTextColor: Theme.Studio.mintText
    font.family: Theme.Studio.font
    font.pixelSize: 13
    leftPadding: 12; rightPadding: 12
    background: Rectangle {
        radius: 10
        color: Theme.Studio.paper
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.Studio.mintDark : Theme.Studio.line
    }
}
