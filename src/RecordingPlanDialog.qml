import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

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
        spacing: 4
        Caption { text: choice.label }
        ComboBox {
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
        property int minTenths: 0
        property int maxTenths: 600
        Layout.fillWidth: true
        Caption { text: seconds.label }
        SpinBox {
            Layout.fillWidth: true
            from: seconds.minTenths; to: seconds.maxTenths; stepSize: 5; editable: true
            value: Math.round(dialog.editor.selected[seconds.field] * 10)
            textFromValue: function(value, locale) { return (value / 10).toLocaleString(locale, 'f', 1) }
            valueFromText: function(text, locale) { return Math.round(Number.fromLocaleString(locale, text) * 10) }
            onValueModified: dialog.editor.setField(seconds.field, value / 10)
            Accessible.name: seconds.label
        }
    }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        radius: 6
                        color: dialog.editor.isMotionTask ? "#EDE9FE" : "#E6F8F3"
                        border.color: dialog.editor.isMotionTask ? "#C4B5FD" : "#8CE0CA"
                        implicitWidth: modeTagLbl.implicitWidth + 12
                        implicitHeight: 24
                        Caption {
                            id: modeTagLbl
                            anchors.centerIn: parent
                            text: dialog.editor.isMotionTask ? "시계열 모션 전용" : "정지 자세·핀치"
                            font.pixelSize: 11
                            font.bold: true
                            color: dialog.editor.isMotionTask ? "#5B21B6" : "#0F6E5B"
                        }
                    }
                    Caption {
                        text: dialog.editor.title
                        font.pixelSize: 20
                        font.bold: true
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }
                Caption {
                    text: dialog.editor.isMotionTask
                        ? "시계열 모션은 손바닥 정면 정지 자세가 아니라, 동작 고유의 손 모양으로 [시작점 준비 → 1회 쓸기 궤적 → 시작점 복귀]의 시간 흐름을 녹화합니다."
                        : "자세를 고르고 조건을 편집하세요. 동작마다 설정을 따로 저장합니다."
                    color: "#526575"
                    Layout.fillWidth: true
                }
            }
            Button { text: "닫기"; onClicked: dialog.close() }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Caption { text: dialog.editor.isMotionTask ? "모션 추천 코스" : "추천안"; font.bold: true }
            Button {
                text: dialog.editor.isMotionTask ? "기본 모션 3코스 (속도·폭 변화, 권장)" : "전체 화면 기본 3자세 (권장)"
                onClicked: dialog.editor.usePreset("basic")
            }
            Button {
                text: dialog.editor.isMotionTask ? "다양한 거리·자세 모션 6코스" : "다양한 조건 9자세"
                onClicked: dialog.editor.usePreset("diverse")
            }
            Button {
                text: dialog.editor.isMotionTask ? "시작 구역별 모션 5코스" : "화면 위치 9곳"
                onClicked: dialog.editor.usePreset("positions")
            }
            Button {
                text: "거리 코스 (1m → 2m → 3m, 움직이며)"
                onClicked: dialog.editor.usePreset("distance")
            }
            Item { Layout.fillWidth: true }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: instrLbl.implicitHeight + 14
            radius: 8
            color: dialog.editor.isMotionTask ? "#F5F3FF" : "#F0FDF9"
            border.color: dialog.editor.isMotionTask ? "#C4B5FD" : "#99F6E4"
            Caption {
                id: instrLbl
                anchors.fill: parent
                anchors.margins: 7
                text: dialog.editor.instruction
                color: dialog.editor.isMotionTask ? "#4C1D95" : "#0F6E5B"
                font.bold: true
                font.pixelSize: 12
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 14
            ColumnLayout {
                Layout.preferredWidth: 268
                Layout.minimumWidth: 250
                Layout.maximumWidth: 280
                Layout.fillHeight: true
                Caption {
                    text: dialog.editor.isMotionTask ? "모션 코스 순서 · 체크한 코스만 진행" : "녹화 순서 · 체크한 자세만 진행"
                    font.bold: true
                }
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
                        height: 68
                        radius: 8
                        color: index === dialog.editor.selectedIndex
                            ? (dialog.editor.isMotionTask ? "#EDE9FE" : "#DDF8EF")
                            : "#F4F6F8"
                        border.color: index === dialog.editor.selectedIndex
                            ? (dialog.editor.isMotionTask ? "#7C3AED" : "#16846E")
                            : "#DEE5EA"
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 6
                            spacing: 4
                            CheckBox {
                                checked: modelData.enabled
                                Accessible.name: modelData.name + " 녹화 포함"
                                onClicked: { dialog.editor.select(index); dialog.editor.setField("enabled", checked) }
                            }
                            Button {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                flat: true
                                leftPadding: 4; rightPadding: 4
                                onClicked: dialog.editor.select(index)
                                contentItem: ColumnLayout {
                                    spacing: 2
                                    Caption {
                                        text: (index + 1) + ". " + modelData.name
                                        font.bold: true
                                        font.pixelSize: 12
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                        maximumLineCount: 2
                                    }
                                    Caption { text: modelData.coverage; color: "#526575"; font.pixelSize: 11 }
                                }
                                Accessible.name: modelData.name + " 편집"
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Button { Layout.fillWidth: true; implicitWidth: 0; text: "추가"; onClicked: dialog.editor.add(false) }
                    Button { Layout.fillWidth: true; implicitWidth: 0; text: "복제"; onClicked: dialog.editor.add(true) }
                    Button { Layout.fillWidth: true; implicitWidth: 0; text: "빼기"; onClicked: dialog.editor.remove() }
                }
            }
            ScrollView {
                id: formScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                Item {
                    width: Math.max(240, formScroll.availableWidth - 16)
                    implicitHeight: formCol.implicitHeight + 12
                    ColumnLayout {
                        id: formCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.topMargin: 4
                        spacing: 10
                        Caption {
                            text: (dialog.editor.isMotionTask ? "모션 코스 " : "자세 ") + (dialog.editor.selectedIndex + 1) + " 설정"
                            font.bold: true
                            font.pixelSize: 16
                        }
                        TextField {
                            Layout.fillWidth: true
                            text: dialog.editor.selected.name
                            placeholderText: dialog.editor.isMotionTask
                                ? "예: 1코스 · 평소 속도·보통 폭 쓸기"
                                : "예: 소파에 기대서 오른손을 왼쪽 위로"
                            maximumLength: 80
                            onEditingFinished: dialog.editor.setField("name", text)
                            Accessible.name: dialog.editor.isMotionTask ? "모션 코스 이름" : "자세 이름"
                        }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; columnSpacing: 12; rowSpacing: 10
                            Choice {
                                field: "orientation"
                                label: dialog.editor.isMotionTask
                                    ? "모션 쓸기 방식 (속도·스트로크 폭)"
                                    : "손 방향 (동작 고유 방향 / 손목 회전)"
                            }
                            Choice {
                                field: "position"
                                label: dialog.editor.isMotionTask
                                    ? "모션 시작·이동 구역"
                                    : "화면 위치 (미리보기 기준)"
                            }
                            Choice { field: "distance"; label: "카메라와 손 사이 거리" }
                            Choice { field: "posture"; label: "몸 자세" }
                            Choice { field: "camera"; label: "카메라 각도" }
                            Choice { field: "lighting"; label: "조명" }
                        }
                        Rectangle {
                            visible: dialog.editor.orientationGuide !== ""
                            Layout.fillWidth: true
                            implicitHeight: orientGuideLbl.implicitHeight + 16
                            radius: 8
                            color: dialog.editor.isMotionTask ? "#F5F3FF" : "#EEFBF6"
                            border.color: dialog.editor.isMotionTask ? "#A78BFA" : "#3BC49A"
                            Caption {
                                id: orientGuideLbl
                                anchors.fill: parent
                                anchors.margins: 8
                                text: (dialog.editor.isMotionTask ? "🌊 모션 수행 안내: " : "💡 자세 안내: ") + dialog.editor.orientationGuide
                                color: dialog.editor.isMotionTask ? "#4C1D95" : "#0F6E4F"
                                font.pixelSize: 12
                            }
                        }
                        TextField {
                            Layout.fillWidth: true
                            text: dialog.editor.selected.notes
                            placeholderText: "직접 지정한 거리·각도나 준비 사항 (선택)"
                            maximumLength: 240
                            onEditingFinished: dialog.editor.setField("notes", text)
                            Accessible.name: "자세 준비 메모"
                        }
                        Caption {
                            text: dialog.editor.isMotionTask
                                ? "이 모션 코스의 스트로크 반복 횟수 및 1회 타이밍"
                                : "이 자세의 녹화량과 속도"
                            font.bold: true
                        }
                        RowLayout {
                            visible: !dialog.editor.neutral
                            Layout.fillWidth: true
                            Caption { text: "반복 횟수" }
                            SpinBox {
                                from: 1; to: 100; editable: true
                                value: dialog.editor.selected.reps
                                onValueModified: dialog.editor.setField("reps", value)
                                Accessible.name: "반복 횟수"
                            }
                            Caption {
                                text: dialog.editor.isMotionTask
                                    ? "회 · [시작점 준비 → 1회 모션 쓸기 → 제자리 복귀(쉬기)] 사이클로 녹화합니다."
                                    : "회 · 준비·쉬기를 0초로 두면 원하는 동작만 끊김 없이 연속 녹화합니다."
                                Layout.fillWidth: true
                                color: "#526575"
                            }
                        }
                        RowLayout {
                            visible: !dialog.editor.neutral
                            Layout.fillWidth: true
                            Seconds {
                                field: "prep"
                                label: dialog.editor.isMotionTask ? "시작점 준비 (초)" : "준비 (초)"
                                minTenths: 0; maxTenths: 100
                            }
                            Seconds {
                                field: "hold"
                                label: dialog.editor.isMotionTask ? "1회 모션 쓸기 (초)" : "동작 유지 (초)"
                                minTenths: 5; maxTenths: 600
                            }
                            Seconds {
                                field: "rest"
                                label: dialog.editor.isMotionTask ? "제자리 복귀·쉬기 (초)" : "쉬기 (초)"
                                minTenths: 0; maxTenths: 100
                            }
                        }
                        RowLayout {
                            visible: dialog.editor.neutral || dialog.editor.allGestures
                            Caption { text: "자유 이동 녹화" }
                            SpinBox {
                                from: 10; to: 900; stepSize: 10; editable: true
                                value: dialog.editor.selected.free_seconds
                                onValueModified: dialog.editor.setField("free_seconds", value)
                                Accessible.name: "자유 이동 시간"
                            }
                            Caption { text: "초" }
                        }
                        Caption {
                            Layout.fillWidth: true
                            text: dialog.editor.isMotionTask
                                ? "※ 시계열 모션은 손을 정지해 두는 것이 아니라 [1회 모션 쓸기] 시간 동안 시작점에서 끝점으로 손을 이동해 궤적을 만들고, 반드시 [제자리 복귀·쉬기] 시간 동안 원래 시작 위치로 손을 되돌려야 반대 방향 복귀 움직임이 오작동으로 학습되지 않습니다."
                                : "화면을 구역으로 나누지 않고 전체 화면(자유 이동)을 대상으로 녹화하며, 미리보기에 현재 모델이 학습된 영역이 색깔 음영(스프레이)으로 표시되어 부족한 영역을 자유롭게 채울 수 있습니다."
                            color: dialog.editor.isMotionTask ? "#5B21B6" : "#526575"
                            font.pixelSize: 12
                        }
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#DEE5EA" }
        Caption { text: dialog.editor.summary; font.bold: true; Layout.fillWidth: true }
        Caption { text: dialog.editor.message; visible: text !== ""; Layout.fillWidth: true; color: "#16846E"; font.pixelSize: 12 }
        RowLayout {
            Layout.fillWidth: true
            Caption {
                text: dialog.editor.isMotionTask
                    ? "코스마다 시작 위치에 손을 준비한 뒤 스페이스바로 시작 · 녹화 중 PC 마우스 제어 꺼짐"
                    : "자세마다 준비 후 시작 · 녹화 중 PC 마우스 제어 꺼짐"
                color: "#526575"
                Layout.fillWidth: true
            }
            Button { text: "설정 저장"; onClicked: dialog.editor.save() }
            Button {
                text: dialog.editor.isMotionTask ? "이 모션 코스로 녹화 시작" : "이 설정으로 녹화"
                highlighted: true
                onClicked: { if (dialog.editor.start()) dialog.close() }
            }
        }
    }
}
