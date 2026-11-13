import QtQuick
import QtQuick.Controls
import "../theme" as Theme
ComboBox {
    id: control
    implicitHeight: 42
    leftPadding: 12; rightPadding: 32
    font.family: Theme.Studio.font
    font.pixelSize: 13
    contentItem: Text {
        text: control.displayText
        color: Theme.Studio.ink
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Item {
        width: 10; height: 10
        x: control.width - width - 12
        y: (control.height - height) / 2
        Rectangle { x: 1; y: 3; width: 6; height: 2; rotation: 45; color: Theme.Studio.muted }
        Rectangle { x: 5; y: 3; width: 6; height: 2; rotation: -45; color: Theme.Studio.muted }
    }
    background: Rectangle {
        radius: Theme.Studio.radius; color: Theme.Studio.paper
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.Studio.mintDark : Theme.Studio.line
    }
    delegate: ItemDelegate {
        required property var modelData
        required property int index
        width: control.width
        implicitHeight: 40
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: modelData; color: Theme.Studio.ink
            font: control.font; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 7
            color: index === control.currentIndex || parent.highlighted ? Theme.Studio.mintWash : Theme.Studio.paper
        }
    }
    popup: Popup {
        y: control.height + 5
        width: control.width
        padding: 5
        implicitHeight: Math.min(contentItem.implicitHeight + 10, 260)
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle { radius: Theme.Studio.radius; color: Theme.Studio.paper; border.color: Theme.Studio.line }
    }
}
