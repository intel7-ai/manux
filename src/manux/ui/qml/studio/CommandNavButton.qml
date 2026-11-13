import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme

Button {
    id: button
    property string iconName: "control"
    property bool selected: false
    implicitHeight: 62
    implicitWidth: 72
    padding: 6
    Accessible.name: text
    contentItem: ColumnLayout {
        spacing: 6
        CommandIcon {
            Layout.alignment: Qt.AlignHCenter
            name: button.iconName
            color: button.selected ? Theme.Studio.mint : button.enabled ? Theme.Studio.railText : Theme.Studio.railMuted
            Layout.preferredWidth: 20
            Layout.preferredHeight: 20
        }
        Label {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: button.text
            color: button.selected ? Theme.Studio.mint : Theme.Studio.railMuted
            font.family: Theme.Studio.font
            font.pixelSize: 11
            font.weight: button.selected ? Font.DemiBold : Font.Normal
            elide: Text.ElideRight
        }
    }
    background: Rectangle {
        radius: 6
        color: button.selected ? Qt.lighter(Theme.Studio.rail, 1.65) : button.hovered || button.down ? Qt.lighter(Theme.Studio.rail, 1.4) : "transparent"
        border.width: button.activeFocus ? 1 : 0
        border.color: Theme.Studio.mint
        Rectangle {
            visible: button.selected
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: 2
            height: 28
            radius: 1
            color: Theme.Studio.mint
        }
    }
}
