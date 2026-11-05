import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme
Control {
    id: control
    property real value: 1
    property real from: 1
    property real to: 100
    property real stepSize: 1
    property int decimals: stepSize < 1 ? 1 : 0
    signal edited(real value)
    implicitWidth: 150; implicitHeight: 40
    Accessible.role: Accessible.SpinBox
    Accessible.name: value.toFixed(decimals)
    contentItem: RowLayout {
        spacing: 4
        StudioButton {
            text: "-"; implicitWidth: 36; implicitHeight: 38
            enabled: control.value > control.from
            Accessible.name: "값 줄이기"
            onClicked: control.edited(Math.max(control.from, control.value - control.stepSize))
        }
        Text {
            Layout.fillWidth: true
            text: control.value.toFixed(control.decimals)
            color: Theme.Studio.ink
            font.family: Theme.Studio.font; font.pixelSize: 14; font.bold: true
            horizontalAlignment: Text.AlignHCenter
        }
        StudioButton {
            text: "+"; implicitWidth: 36; implicitHeight: 38
            enabled: control.value < control.to
            Accessible.name: "값 늘리기"
            onClicked: control.edited(Math.min(control.to, control.value + control.stepSize))
        }
    }
    background: Rectangle { radius: 10; color: Theme.Studio.paper; border.color: Theme.Studio.line }
    Keys.onLeftPressed: edited(Math.max(from, value - stepSize))
    Keys.onRightPressed: edited(Math.min(to, value + stepSize))
}
