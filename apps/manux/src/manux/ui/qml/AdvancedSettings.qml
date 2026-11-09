import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme" as T

Dialog {
    id: advanced
    objectName: "advancedSettings"
    required property var controller
    property var settingRows: []
    property var draft: ({})
    property var edits: ({})
    property var groups: ["전체"]
    function reload() {
        settingRows = controller.advancedRows
        let values = {}, names = ["전체"]
        for (let row of settingRows) {
            values[row.key] = row.value
            if (names.indexOf(row.group) < 0) names.push(row.group)
        }
        draft = values
        groups = names
        edits = {}
    }
    function changeValue(key, value) {
        let values = Object.assign({}, draft), changes = Object.assign({}, edits)
        values[key] = value
        changes[key] = value
        draft = values
        edits = changes
    }
    anchors.centerIn: parent
    width: Math.min(T.Studio.navWidth * 6, parent.width - T.Studio.pad * 2)
    height: Math.min(T.Studio.navWidth * 6, parent.height - T.Studio.pad * 2)
    padding: T.Studio.pad
    title: "고급 설정"
    modal: true
    focus: true
    font.family: T.Studio.font
    background: Rectangle { color: T.Studio.paper; radius: T.Studio.radius; border.color: T.Studio.line }
    onAboutToShow: reload()
    contentItem: ColumnLayout {
        spacing: T.Studio.gap
        Label {
            text: "변경한 항목만 적용합니다. 적용하면 제어가 멈추고, 연습 후 다시 시작할 수 있습니다."
            color: T.Studio.muted
            font.pixelSize: T.Studio.body
            Layout.fillWidth: true
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            ComboBox { id: group; model: advanced.groups; Layout.preferredWidth: T.Studio.navWidth * 1.5 }
            TextField {
                id: search
                objectName: "advancedSearch"
                Layout.fillWidth: true
                placeholderText: "설정 이름 찾기"
                selectByMouse: true
            }
        }
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth
                spacing: T.Studio.gap
                Repeater {
                    model: advanced.settingRows
                    delegate: ColumnLayout {
                        id: rowItem
                        required property var modelData
                        property var currentValue: advanced.draft[modelData.key] ?? modelData.value
                        Layout.fillWidth: true
                        spacing: T.Studio.gap / 3
                        visible: (group.currentIndex === 0 || group.currentText === modelData.group)
                                 && (!search.text || modelData.label.toLowerCase().indexOf(search.text.toLowerCase()) >= 0)
                        Label {
                            text: modelData.group + " · " + modelData.label
                            color: T.Studio.ink
                            font.pixelSize: T.Studio.body
                            font.bold: true
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            CheckBox {
                                visible: modelData.kind === "bool"
                                enabled: modelData.editable && !advanced.controller.advancedPending
                                text: checked ? "켜짐" : "꺼짐"
                                checked: Boolean(rowItem.currentValue)
                                onClicked: advanced.changeValue(modelData.key, checked)
                            }
                            ComboBox {
                                visible: modelData.kind === "choice"
                                enabled: modelData.editable && !advanced.controller.advancedPending
                                model: modelData.choiceLabels
                                currentIndex: modelData.choices.indexOf(String(rowItem.currentValue))
                                onActivated: advanced.changeValue(modelData.key, modelData.choices[currentIndex])
                                Layout.fillWidth: true
                            }
                            Slider {
                                objectName: "advanced-slider-" + modelData.key
                                visible: modelData.kind === "float" || modelData.kind === "int"
                                enabled: modelData.editable && !advanced.controller.advancedPending
                                from: modelData.minimum
                                to: modelData.maximum
                                stepSize: modelData.step
                                value: Number(rowItem.currentValue)
                                onMoved: advanced.changeValue(modelData.key, Number(value.toFixed(6)))
                                Layout.fillWidth: true
                            }
                            TextField {
                                objectName: "advanced-value-" + modelData.key
                                visible: modelData.kind === "float" || modelData.kind === "int"
                                enabled: modelData.editable && !advanced.controller.advancedPending
                                text: String(rowItem.currentValue)
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                selectByMouse: true
                                horizontalAlignment: TextInput.AlignRight
                                Layout.preferredWidth: T.Studio.navWidth
                                onTextEdited: advanced.changeValue(modelData.key, text.trim() ? Number(text) : NaN)
                            }
                        }
                        Label {
                            text: modelData.editable ? modelData.hint : "현재 프로필이 지정한 값입니다. 기본 프로필에서 변경하세요."
                            color: T.Studio.muted
                            font.pixelSize: T.Studio.small
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        Rectangle { height: 1; color: T.Studio.line; Layout.fillWidth: true }
                    }
                }
            }
        }
        Label {
            text: controller.advancedStatus || "카메라·모델·프로필은 각 전용 화면에서 변경합니다. 이전 버전의 미지원 항목은 제공하지 않습니다."
            color: T.Studio.muted
            font.pixelSize: T.Studio.small
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        RowLayout {
            Layout.fillWidth: true
            Button {
                text: "선택 그룹 기본값"
                enabled: !advanced.controller.advancedPending
                onClicked: {
                    for (let row of advanced.settingRows)
                        if (row.editable && (group.currentIndex === 0 || group.currentText === row.group))
                            advanced.changeValue(row.key, row.defaultValue)
                }
            }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "applyAdvancedSettings"
                text: "적용"
                enabled: Object.keys(advanced.edits).length > 0 && !advanced.controller.advancedPending
                onClicked: advanced.controller.applyAdvancedSettings(advanced.edits)
            }
            Button { text: "닫기"; onClicked: advanced.close() }
        }
    }
    Connections {
        target: advanced.controller
        function onAdvancedApplied() { advanced.reload() }
    }
}
