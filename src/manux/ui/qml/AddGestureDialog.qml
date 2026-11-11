import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "theme" as Theme

Dialog {
    id: dialog
    objectName: "addGestureDialog"
    required property var controller
    anchors.centerIn: parent
    width: Math.min(560, parent.width - 32)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: 24

    property string gestureName: ""
    property string selectedDirection: "up"
    property var selectedKeys: ["ctrl", "w"]
    property string errorMessage: ""

    background: Rectangle {
        radius: 16
        color: "white"
        border.color: Theme.Studio.line
        border.width: 1
    }

    component Copy: Label {
        textFormat: Text.PlainText
        color: Theme.Studio.ink
        font.family: Theme.Studio.font
        font.pixelSize: Theme.Studio.body
        wrapMode: Text.Wrap
    }

    contentItem: ColumnLayout {
        spacing: 16

        // Title
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            Copy {
                text: "새 제스처 동작 추가"
                font.pixelSize: 20
                font.bold: true
            }
            Copy {
                text: "손동작 형태와 컴퓨터에서 실행할 단축키를 지정하여 학습 목록에 추가합니다."
                color: Theme.Studio.muted
                font.pixelSize: Theme.Studio.small
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.Studio.line }

        // 1. 동작 이름 입력
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Copy { text: "1. 동작 이름"; font.bold: true }
            StudioField {
                id: nameField
                objectName: "customGestureNameField"
                Layout.fillWidth: true
                placeholderText: "예: 창 닫기 (Ctrl+W), 새로고침 등"
                text: dialog.gestureName
                onTextEdited: dialog.gestureName = text
            }
        }

        // 2. 손동작 형태 선택
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Copy { text: "2. 손동작 형태 (스와이프 방향)"; font.bold: true }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: [
                        ["up", "위로 쓸기 ↑"],
                        ["down", "아래로 쓸기 ↓"],
                        ["left", "왼쪽 쓸기 ←"],
                        ["right", "오른쪽 쓸기 →"]
                    ]
                    StudioButton {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData[1]
                        primary: dialog.selectedDirection === modelData[0]
                        onClicked: dialog.selectedDirection = modelData[0]
                    }
                }
            }
        }

        // 3. 실행할 컴퓨터 단축키 선택
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Copy { text: "3. 실행할 컴퓨터 명령 (단축키 템플릿)"; font.bold: true }
            
            ComboBox {
                id: presetCombo
                Layout.fillWidth: true
                model: [
                    "창/탭 닫기 (Ctrl + W)",
                    "새로고침 (F5)",
                    "새 탭 열기 (Ctrl + T)",
                    "닫은 탭 다시 열기 (Ctrl + Shift + T)",
                    "화면 캡처 (Win + Shift + S)",
                    "볼륨 올리기",
                    "볼륨 내리기",
                    "미디어 재생 / 일시정지",
                    "바탕화면 보기 (Win + D)"
                ]
                currentIndex: 0
                onActivated: function(index) {
                    if (index === 0) {
                        dialog.selectedKeys = ["ctrl", "w"]
                        if (!dialog.gestureName) dialog.gestureName = "창/탭 닫기 (Ctrl+W)"
                    } else if (index === 1) {
                        dialog.selectedKeys = ["f5"]
                        if (!dialog.gestureName) dialog.gestureName = "새로고침 (F5)"
                    } else if (index === 2) {
                        dialog.selectedKeys = ["ctrl", "t"]
                        if (!dialog.gestureName) dialog.gestureName = "새 탭 열기 (Ctrl+T)"
                    } else if (index === 3) {
                        dialog.selectedKeys = ["ctrl", "shift", "t"]
                        if (!dialog.gestureName) dialog.gestureName = "닫은 탭 다시 열기 (Ctrl+Shift+T)"
                    } else if (index === 4) {
                        dialog.selectedKeys = ["win", "shift", "s"]
                        if (!dialog.gestureName) dialog.gestureName = "화면 캡처 (Win+Shift+S)"
                    } else if (index === 5) {
                        dialog.selectedKeys = ["volumeup"]
                        if (!dialog.gestureName) dialog.gestureName = "볼륨 올리기"
                    } else if (index === 6) {
                        dialog.selectedKeys = ["volumedown"]
                        if (!dialog.gestureName) dialog.gestureName = "볼륨 내리기"
                    } else if (index === 7) {
                        dialog.selectedKeys = ["playpause"]
                        if (!dialog.gestureName) dialog.gestureName = "미디어 재생/일시정지"
                    } else if (index === 8) {
                        dialog.selectedKeys = ["win", "d"]
                        if (!dialog.gestureName) dialog.gestureName = "바탕화면 보기 (Win+D)"
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Copy { text: "설정된 키 조합:"; color: Theme.Studio.muted; font.pixelSize: Theme.Studio.small }
                Rectangle {
                    height: 24; radius: 6
                    color: "#F3F4F6"
                    border.color: "#D1D5DB"
                    implicitWidth: keyChordLabel.implicitWidth + 14
                    Label {
                        id: keyChordLabel
                        anchors.centerIn: parent
                        text: dialog.selectedKeys.join(" + ").toUpperCase()
                        font.bold: true
                        font.pixelSize: 11
                        color: "#111827"
                    }
                }
            }
        }

        // Error message if any
        Copy {
            Layout.fillWidth: true
            text: dialog.errorMessage
            visible: dialog.errorMessage !== ""
            color: Theme.Studio.danger
            font.pixelSize: Theme.Studio.small
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.Studio.line }

        // Action Buttons
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            StudioButton {
                text: "취소"
                onClicked: dialog.close()
            }
            StudioButton {
                objectName: "confirmAddGesture"
                text: "새 동작 등록하기"
                primary: true
                onClicked: {
                    const name = dialog.gestureName.trim()
                    if (!name) {
                        dialog.errorMessage = "동작 이름을 입력해 주세요."
                        return
                    }
                    const res = dialog.controller.addCustomGesture(name, dialog.selectedDirection, dialog.selectedKeys, "")
                    if (res && res.success) {
                        dialog.errorMessage = ""
                        dialog.gestureName = ""
                        dialog.close()
                    } else {
                        dialog.errorMessage = (res && res.error) ? res.error : "동작 등록에 실패했습니다."
                    }
                }
            }
        }
    }
}

