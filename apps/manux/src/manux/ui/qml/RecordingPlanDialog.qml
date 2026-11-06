import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "theme" as Theme

Dialog {
    id: dialog
    required property var editor
    anchors.centerIn: parent
    width: Math.min(980, parent.width - 32)
    height: Math.min(680, parent.height - 32)
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: 20
    palette.window: "#FFFFFF"
    palette.windowText: "#112331"
    palette.base: "#F4F6F8"
    palette.text: "#112331"
    palette.button: "#EDF3F5"
    palette.buttonText: "#112331"
    palette.highlight: "#46E5BD"
    palette.highlightedText: "#112331"
    palette.mid: "#DEE5EA"
    palette.dark: "#526575"
    background: Rectangle { radius: 16; color: "white"; border.color: "#DEE5EA" }

    component Caption: Label { color: "#112331"; font.pixelSize: 13; wrapMode: Text.Wrap }
    component Choice: ColumnLayout {
        id: choice
        property string field
        property string label
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 4
        Caption { text: choice.label }
        StudioSelect {
            objectName: "planChoice_" + choice.field
            Layout.fillWidth: true
            model: dialog.editor.choices[choice.field] || []
            currentIndex: Math.max(0, model.indexOf(dialog.editor.selected[choice.field]))
            onActivated: dialog.editor.setField(choice.field, currentText)
            Accessible.name: choice.label
        }
    }
    component Seconds: ColumnLayout {
        id: seconds
        property string field
        property string label
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Caption { text: seconds.label }
        StudioStepper {
            Layout.fillWidth: true
            from: seconds.field === "hold" ? 0.2 : 0.5; to: 10; stepSize: seconds.field === "hold" ? 0.1 : 0.5;
            value: dialog.editor.selected[seconds.field]
            onEdited: dialog.editor.setField(seconds.field, value)
            Accessible.name: seconds.label
        }
    }
    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                Caption { text: dialog.editor.title; font.pixelSize: 21; font.bold: true; Layout.fillWidth: true }
                Caption { text: "자세를 고르고 조건을 편집하세요. 동작마다 설정을 따로 저장합니다."; color: "#526575" }
            }
            StudioButton { text: "닫기"; onClicked: dialog.close() }
        }
        RowLayout {
            Layout.fillWidth: true
            Caption { text: "추천안"; font.bold: true }
            StudioButton { text: "기본 3자세"; onClicked: dialog.editor.usePreset("basic") }
            StudioButton { text: "다양한 조건 9자세"; onClicked: dialog.editor.usePreset("diverse") }
            StudioButton { text: "화면 위치 9곳"; onClicked: dialog.editor.usePreset("positions") }
            StudioButton { objectName: "quickClickPreset"; text: "짧은 클릭 · 각도×속도"; visible: dialog.editor.clickGesture; onClicked: dialog.editor.usePreset("quick_click") }
            Item { Layout.fillWidth: true }
        }
        Caption { text: dialog.editor.instruction; Layout.fillWidth: true; color: "#16846E" }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16
            ColumnLayout {
                Layout.preferredWidth: dialog.width < 850 ? 180 : 235
                Layout.minimumWidth: 180
                Layout.maximumWidth: 235
                Layout.fillHeight: true
                Caption { text: "녹화 순서 · 체크한 자세만 진행"; font.bold: true; Layout.fillWidth: true }
                ListView {
                    id: poseList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 6
                    model: dialog.editor.rows
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        width: poseList.width - 12
                        height: 64
                        radius: 8
                        color: index === dialog.editor.selectedIndex ? "#DDF8EF" : "#F4F6F8"
                        border.color: index === dialog.editor.selectedIndex ? "#16846E" : "#DEE5EA"
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 6
                            StudioCheck {
                                checked: modelData.enabled
                                Accessible.name: modelData.name + " 녹화 포함"
                                onClicked: { dialog.editor.select(index); dialog.editor.setField("enabled", checked) }
                            }
                            StudioButton {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                Layout.minimumWidth: 0
                                flat: true
                                onClicked: dialog.editor.select(index)
                                contentItem: ColumnLayout {
                                    Caption { text: (index + 1) + ". " + modelData.name; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight; maximumLineCount: 1 }
                                    Caption { text: modelData.coverage; color: "#526575"; font.pixelSize: 11 }
                                }
                                Accessible.name: modelData.name + " 편집"
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    StudioButton { Layout.fillWidth: true; implicitWidth: 0; text: "추가"; onClicked: dialog.editor.add(false) }
                    StudioButton { Layout.fillWidth: true; implicitWidth: 0; text: "복제"; onClicked: dialog.editor.add(true) }
                    StudioButton { Layout.fillWidth: true; implicitWidth: 0; text: "빼기"; onClicked: dialog.editor.remove() }
                }
            }
            ScrollView {
                id: formScroll
                objectName: "recordingForm"
                Layout.minimumWidth: 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: formScroll.availableWidth
                    spacing: 12
                    Caption { text: "자세 " + (dialog.editor.selectedIndex + 1) + " 설정"; font.bold: true; font.pixelSize: 17 }
                    StudioField {
                        Layout.fillWidth: true
                        text: dialog.editor.selected.name
                        placeholderText: "예: 소파에 기대서 오른손을 왼쪽 위로"
                        maximumLength: 80
                        onEditingFinished: dialog.editor.setField("name", text)
                        Accessible.name: "자세 이름"
                    }
                    GridLayout {
                        Layout.fillWidth: true; columns: dialog.width < 850 ? 1 : 2; columnSpacing: 12; rowSpacing: 10
                        Choice { field: "orientation"; label: "손 방향" }
                        Choice { field: "position"; label: "화면 위치 (미리보기 기준)" }
                        Choice { field: "distance"; label: "카메라와 손 사이 거리" }
                        Choice { field: "posture"; label: "몸 자세" }
                        Choice { field: "camera"; label: "카메라 각도" }
                        Choice { field: "lighting"; label: "조명" }
                    }
                    StudioField {
                        Layout.fillWidth: true
                        text: dialog.editor.selected.notes
                        placeholderText: "직접 지정한 거리·각도나 준비 사항 (선택)"
                        maximumLength: 240
                        onEditingFinished: dialog.editor.setField("notes", text)
                        Accessible.name: "자세 준비 메모"
                    }
                    Caption { text: "이 자세의 녹화량과 속도"; font.bold: true }
                    GridLayout {
                        visible: !dialog.editor.neutral
                        Layout.fillWidth: true
                        columns: dialog.width < 850 ? 2 : 3
                        Caption { text: "반복 횟수" }
                        StudioStepper {
                            from: 1; to: 100;
                            value: dialog.editor.selected.reps
                            onEdited: dialog.editor.setField("reps", value)
                            Accessible.name: "반복 횟수"
                        }
                        Caption { text: "회 · 동작을 유지하다가 쉬기 안내에 맞춰 풀어주세요."; Layout.fillWidth: true; color: "#526575" }
                    }
                    GridLayout {
                        visible: !dialog.editor.neutral
                        Layout.fillWidth: true
                        columns: dialog.width < 850 ? 1 : 3
                        Seconds { field: "prep"; label: "준비 (초)" }
                        Seconds { field: "hold"; label: "동작 유지 (초)" }
                        Seconds { field: "rest"; label: "쉬기 (초)" }
                    }
                    RowLayout {
                        visible: dialog.editor.neutral || dialog.editor.allGestures
                        Caption { text: "자유 이동 녹화" }
                        StudioStepper {
                            from: 10; to: 900; stepSize: 10;
                            value: dialog.editor.selected.free_seconds
                            onEdited: dialog.editor.setField("free_seconds", value)
                            Accessible.name: "자유 이동 시간"
                        }
                        Caption { text: "초" }
                    }
                    Caption {
                        Layout.fillWidth: true
                        text: "방향·거리·자세는 직접 맞춰주세요. 녹화 화면에 조건과 위치 안내가 표시됩니다. 손의 3D 관절을 놓치면 타이머가 멈추고, 다시 잡히면 이어집니다."
                        color: "#526575"; font.pixelSize: 12
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#DEE5EA" }
        Caption { text: dialog.editor.summary; font.bold: true; Layout.fillWidth: true }
        Caption { text: dialog.editor.message; visible: text !== ""; Layout.fillWidth: true; color: "#16846E"; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            Caption { text: "녹화 중 PC 마우스 제어 꺼짐"; color: "#526575"; Layout.fillWidth: true }
            StudioButton { text: "설정 저장"; onClicked: dialog.editor.save() }
            StudioButton {
                text: "이 설정으로 녹화"
                highlighted: true
                onClicked: { if (dialog.editor.start()) dialog.close() }
            }
        }
    }
}
