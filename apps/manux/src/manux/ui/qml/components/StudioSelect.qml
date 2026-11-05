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
    indicator: Text {
        x: control.width - width - 12
        y: (control.height - height) / 2
        text: "⌄"; color: Theme.Studio.muted
        font.pixelSize: 18
    }
    background: Rectangle {
        radius: 10; color: Theme.Studio.paper
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
        background: Rectangle { radius: 12; color: Theme.Studio.paper; border.color: Theme.Studio.line }
    }
}
