import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: settings
    objectName: "studioSettings"
    required property var controller
    signal dataRequested()
    signal advancedRequested()
    property var settingRows: []
    property var draft: ({})
    property var edits: ({})
    function reload() {
        settingRows = controller.sensitivityRows
        let values = {}
        for (let row of settingRows) values[row.key] = row.value
        draft = values
        edits = {}
    }
    function changeValue(key, value) {
        let values = Object.assign({}, draft)
        let changes = Object.assign({}, edits)
        values[key] = value
        changes[key] = value
        draft = values
        edits = changes
    }
    anchors.centerIn: parent
    width: Math.min(660, parent.width - 48)
    height: Math.min(740, parent.height - 48)
    title: "설정"
    modal: true
    focus: true
    padding: 20
    background: Rectangle { color: "white"; radius: 14; border.color: "#DEE5EA" }
    onAboutToShow: reload()
    contentItem: ColumnLayout {
        spacing: 10
        TabBar {
            id: tabs
            Layout.fillWidth: true
            TabButton { text: "동작 민감도"; objectName: "pinchSensitivityTab" }
            TabButton { text: "커서 민감도"; objectName: "cursorSensitivityTab" }
            TabButton { text: "표시 · 데이터" }
        }
        Label {
            Layout.fillWidth: true
            visible: tabs.currentIndex < 2
            text: tabs.currentIndex === 0
                ? "손바닥 모드의 핀치·클릭 감도입니다. 수치를 직접 입력할 수도 있습니다."
                : "느린 이동과 빠른 이동 감도를 따로 조절합니다. 주먹·드래그 배율은 손바닥 모드에 적용됩니다."
            wrapMode: Text.Wrap
            color: "#526575"
        }
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 12
                Repeater {
                    model: settings.settingRows
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        visible: (tabs.currentIndex === 0 && modelData.group === "pinch")
                                 || (tabs.currentIndex === 1 && modelData.group === "cursor")
                        spacing: 2
                        Label { text: modelData.label; font.bold: true; color: "#112331" }
                        RowLayout {
                            Layout.fillWidth: true
                            Slider {
                                objectName: "sensitivity-slider-" + modelData.key
                                Layout.fillWidth: true
                                from: modelData.minimum; to: modelData.maximum; stepSize: modelData.step
                                value: settings.draft[modelData.key] ?? modelData.value
                                enabled: modelData.editable && !settings.controller.sensitivityPending
                                onMoved: settings.changeValue(modelData.key, Number(value.toFixed(6)))
                            }
                            TextField {
                                objectName: "sensitivity-value-" + modelData.key
                                Layout.preferredWidth: 86
                                horizontalAlignment: TextInput.AlignRight
                                text: String(settings.draft[modelData.key] ?? modelData.value)
                                enabled: modelData.editable && !settings.controller.sensitivityPending
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                                selectByMouse: true
                                onTextEdited: settings.changeValue(modelData.key, text.trim() ? Number(text) : NaN)
                            }
                        }
                        Label {
                            text: modelData.editable ? modelData.hint : "현재 프로필이 지정한 값입니다. 기본 프로필에서 변경하세요."
                            Layout.fillWidth: true; wrapMode: Text.Wrap; color: "#526575"; font.pixelSize: 12
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: tabs.currentIndex === 1
                    Label { text: "검지 포인터 추가 감도 · " + settings.controller.pointerSensitivity + "%"; color: "#112331" }
                    Slider {
                        Layout.fillWidth: true
                        from: 50; to: 150; stepSize: 5
                        value: settings.controller.pointerSensitivity
                        onMoved: settings.controller.setPointerSensitivity(Math.round(value))
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: tabs.currentIndex === 2
                    CheckBox {
                        text: "커서 옆 손가락 표시"
                        checked: settings.controller.fingerOverlay
                        onClicked: settings.controller.setFingerOverlay(checked)
                    }
                    CheckBox {
                        text: "화면 구석 상태 표시"
                        checked: settings.controller.overlayVisible
                        onClicked: settings.controller.setOverlayVisible(checked)
                    }
                    Button {
                        text: "데이터 관리 · 녹화 별명 · train / eval"
                        Layout.fillWidth: true
                        onClicked: settings.dataRequested()
                    }
                    Label {
                        text: "이전 작업 가져오기와 별도 평가 검증도 데이터 관리에서 실행합니다."
                        wrapMode: Text.Wrap; Layout.fillWidth: true; color: "#526575"
                    }
                }
            }
        }
        Label {
            text: settings.controller.sensitivityStatus || "변경한 민감도는 ‘적용’을 누르면 저장됩니다. 적용 후 제어를 다시 시작하세요."
            wrapMode: Text.Wrap; Layout.fillWidth: true; color: "#526575"; font.pixelSize: 12
        }
        RowLayout {
            Layout.fillWidth: true
            Button {
                text: "현재 탭 기본값"
                visible: tabs.currentIndex < 2
                enabled: !settings.controller.sensitivityPending
                onClicked: {
                    let group = tabs.currentIndex === 0 ? "pinch" : "cursor"
                    for (let row of settings.settingRows)
                        if (row.group === group && row.editable) settings.changeValue(row.key, row.defaultValue)
                }
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "적용"
                objectName: "applySensitivity"
                enabled: Object.keys(settings.edits).length > 0 && !settings.controller.sensitivityPending
                onClicked: settings.controller.applySensitivity(settings.edits)
            }
            Button { text: "고급 설정"; onClicked: { settings.close(); settings.advancedRequested() } }
            Button { text: "닫기"; onClicked: settings.close() }
        }
    }
    Connections {
        target: settings.controller
        function onSensitivityApplied() { settings.reload() }
    }
    onClosed: controller.persistPointerSettings()
}
