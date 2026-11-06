import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme" as Theme

ColumnLayout {
    id: settings
    required property var controller
    property bool showDesktopSwitch: true
    property var speedRows: controller.sensitivityRows.filter(function(r) {
        return r.key === "cursor.relative_gain_min" || r.key === "cursor.relative_gain_max"
    })
    spacing: Theme.Studio.gap / 2
    Repeater {
        model: settings.speedRows
        delegate: ColumnLayout {
            required property var modelData
            Layout.fillWidth: true; spacing: 0
            Label {
                Layout.fillWidth: true
                text: (modelData.key === "cursor.relative_gain_min" ? "기본 속도" : "빠른 이동 가속") + " · " + Number(modelData.value).toFixed(2)
                color: Theme.Studio.ink; font.pixelSize: Theme.Studio.small
            }
            Slider {
                objectName: "quickSpeed_" + modelData.key
                Layout.fillWidth: true
                from: modelData.minimum; to: modelData.maximum; stepSize: modelData.step
                value: modelData.value
                enabled: modelData.editable && !settings.controller.sensitivityPending
                id: speedSlider
                onMoved: saveDelay.restart()
                Timer {
                    id: saveDelay; interval: 350
                    onTriggered: {
                        let edits = {}; edits[modelData.key] = Number(speedSlider.value.toFixed(6))
                        settings.controller.applySensitivity(edits)
                    }
                }
                Accessible.name: modelData.label
            }
        }
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: "편 손은 빠르게 · 주먹은 정밀하게"
        color: Theme.Studio.muted; font.pixelSize: Theme.Studio.small
    }
    CheckBox {
        visible: settings.showDesktopSwitch
        text: "바탕화면 보기 동작 사용"
        enabled: !settings.controller.advancedPending
        checked: settings.controller.showDesktopEnabled
        onClicked: settings.controller.setShowDesktopEnabled(checked)
    }
}
