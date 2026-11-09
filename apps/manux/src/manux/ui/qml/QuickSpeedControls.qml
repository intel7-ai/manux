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
    // Light palette on the controls themselves: the standard window sets none, so a dark Windows theme
    // would draw black handles and an unreadable check box (the studio window sets the same values).
    component LightPalette: Palette {
        window: "#FFFFFF"; windowText: "#112331"; base: "#F4F6F8"; text: "#112331"
        button: "#EDF3F5"; buttonText: "#112331"; highlight: "#46E5BD"; highlightedText: "#112331"
        mid: "#DEE5EA"; dark: "#526575"; light: "#FFFFFF"; midlight: "#F4F6F8"; shadow: "#526575"
    }
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
                palette: LightPalette {}
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
        palette: LightPalette {}
        text: "바탕화면 보기 동작 사용"
        enabled: !settings.controller.advancedPending
        checked: settings.controller.showDesktopEnabled
        onClicked: settings.controller.setShowDesktopEnabled(checked)
    }
}
