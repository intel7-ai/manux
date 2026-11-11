import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme

ScrollView {
    id: page
    required property var controller
    required property var library
    property string selectedId: ""
    property string draftName: ""
    property string pendingId: ""
    property string pendingAction: ""
    readonly property var activeInfo: controller ? controller.tcnActiveModelInfo : ({})
    readonly property var defaultInfo: controller ? controller.tcnDefaultModelInfo : ({})
    readonly property var selectedModel: library && library.localModels ? (library.localModels.find(function(row){return row.id===page.selectedId}) || ({})) : ({})
    readonly property bool idle: !library.busy && !controller.trainingRunning && !controller.hubRunning && !controller.recActive
    contentWidth: availableWidth; clip: true

    component Copy: Label { textFormat: Text.PlainText; color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap }

    component Badge: Rectangle {
        property alias text: badgeLabel.text
        property color textColor: Theme.Studio.mintDark
        property color bgColor: Theme.Studio.mintWash
        height: 24; radius: 12
        color: bgColor
        implicitWidth: badgeLabel.implicitWidth + 16
        Label {
            id: badgeLabel
            anchors.centerIn: parent
            color: parent.textColor
            font.pixelSize: 11
            font.bold: true
        }
    }

    ColumnLayout {
        width: page.availableWidth; spacing: Theme.Studio.gap

        // Header
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                Copy { text: "TCN 모델 관리 및 버전 보관함"; font.pixelSize: Theme.Studio.title; font.bold: true }
                Copy { text: "현재 작동 중인 TCN 모델과 맞춤 학습 모델들을 확인하고 변경합니다."; color: Theme.Studio.muted }
            }
            StudioButton { text: "새로고침"; enabled: page.idle; onClicked: { page.controller.refreshTraining(); page.library.refresh() } }
            StudioButton { text: "모델 파일 가져오기"; enabled: page.idle; onClicked: page.library.importModel() }
        }

        // 1. Current Active Model (현재 실행 중인 모델)
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: activeCol.implicitHeight + 28
            radius: Theme.Studio.radius
            color: Theme.Studio.paper
            border.color: Theme.Studio.mintDark
            border.width: 2

            ColumnLayout {
                id: activeCol
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Badge { text: "● 현재 사용 중"; textColor: "#08775E"; bgColor: "#E3F8F1" }
                    Copy {
                        text: page.activeInfo.title || "기본 TCN 제스처 모델 (Official)"
                        font.pixelSize: 17; font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    StudioButton {
                        text: "기본 모델로 되돌리기"
                        visible: !page.activeInfo.isDefault
                        enabled: page.idle
                        onClicked: page.controller.resetTcnToDefault()
                    }
                }

                Copy {
                    Layout.fillWidth: true
                    text: "파일명: " + (page.activeInfo.filename || "gesture_tcn.onnx") + "  ·  학습 일시: " + (page.activeInfo.trainedAt || "2026-11-06")
                    color: Theme.Studio.muted
                    font.pixelSize: Theme.Studio.small
                }

                Copy { text: "인식 가능한 동작 (" + (page.activeInfo.classCount || 0) + "개):"; font.bold: true; font.pixelSize: Theme.Studio.small }

                Flow {
                    Layout.fillWidth: true; spacing: 6
                    Repeater {
                        model: page.activeInfo.classes || []
                        Rectangle {
                            required property string modelData
                            height: 26; radius: 6
                            color: "#EBF3F5"
                            border.color: "#D0DEE5"
                            implicitWidth: classLabel.implicitWidth + 14
                            Copy {
                                id: classLabel
                                anchors.centerIn: parent
                                text: modelData
                                font.pixelSize: 11
                            }
                        }
                    }
                }
            }
        }

        // 2. Official Default Model Reference (공식 기본 탑재 모델 카드)
        Rectangle {
            Layout.fillWidth: true
            visible: !page.activeInfo.isDefault
            implicitHeight: defaultCol.implicitHeight + 24
            radius: Theme.Studio.radius
            color: Theme.Studio.paper
            border.color: Theme.Studio.line

            ColumnLayout {
                id: defaultCol
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Badge { text: "공식 기본 모델"; textColor: "#4B5563"; bgColor: "#F3F4F6" }
                    Copy { text: page.defaultInfo.title || "기본 탑재 모델 (Official TCN)"; font.bold: true }
                    Item { Layout.fillWidth: true }
                    StudioButton {
                        text: "이 기본 모델 사용"
                        enabled: page.idle
                        primary: true
                        onClicked: page.controller.resetTcnToDefault()
                    }
                }
                Copy {
                    Layout.fillWidth: true
                    text: page.defaultInfo.description || "노이즈 및 오작동 방지 최적화가 완료된 표준 모델"
                    color: Theme.Studio.muted
                    font.pixelSize: Theme.Studio.small
                }
            }
        }

        // 3. Custom Trained Models Section (내 맞춤 모델 보관함)
        Copy { text: "내 맞춤 모델 보관함 (" + page.library.localModels.length + "개)"; font.bold: true; font.pixelSize: 16 }

        Repeater {
            model: page.library.localModels
            Rectangle {
                required property var modelData
                id: modelRow
                Layout.fillWidth: true; implicitHeight: modelBody.implicitHeight+24; radius: Theme.Studio.radius
                color: page.selectedId===modelData.id ? Theme.Studio.mintWash : Theme.Studio.paper
                border.color: page.selectedId===modelData.id ? Theme.Studio.mintDark : Theme.Studio.line
                TapHandler { onTapped: {page.selectedId=modelRow.modelData.id;page.draftName=modelRow.modelData.name} }
                ColumnLayout {
                    id: modelBody; anchors {left:parent.left;right:parent.right;top:parent.top;margins:12}
                    RowLayout {
                        Layout.fillWidth: true
                        Copy { text: modelRow.modelData.name; font.bold: true; font.pixelSize: 16 }
                        Item { Layout.fillWidth: true }
                        StudioButton {
                            text: page.selectedId===modelRow.modelData.id ? "선택됨" : "이 모델 선택"
                            primary: page.selectedId===modelRow.modelData.id
                            onClicked: {page.selectedId=modelRow.modelData.id;page.draftName=modelRow.modelData.name}
                        }
                    }
                    Copy {
                        Layout.fillWidth: true
                        text: (modelRow.modelData.format==="spatial" ? "포즈 MLP · " : "TCN 제어 모델 · ") + "포함 동작: " + modelRow.modelData.classes.join(", ")
                        color: Theme.Studio.muted
                        font.pixelSize: Theme.Studio.small
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: page.library.localModels.length === 0
            implicitHeight: emptyCol.implicitHeight + 28
            radius: Theme.Studio.radius
            color: "#F9FAFB"
            border.color: "#E5E7EB"
            ColumnLayout {
                id: emptyCol
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                spacing: 4
                Copy {
                    text: "아직 새로 학습한 맞춤 모델이 없습니다."
                    font.bold: true
                    color: Theme.Studio.muted
                }
                Copy {
                    text: "[동작 선택 및 녹화] 탭에서 원하는 제스처를 녹화하고 [선택 동작 모델에 학습/반영]을 실행하면 여기에 새 버전 모델로 보관됩니다."
                    color: Theme.Studio.muted
                    font.pixelSize: Theme.Studio.small
                }
            }
        }

        // Model Actions Bar
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            visible: page.selectedId !== ""
            StudioField {
                objectName: "modelDisplayName"
                Layout.fillWidth: true
                text: page.draftName
                onTextEdited: page.draftName=text
                maximumLength: 80
                placeholderText: "모델 이름 변경"
                Accessible.name: "모델 표시 이름"
                enabled: page.selectedId!=="" && page.idle
            }
            StudioButton {
                text: "이름 저장"
                enabled: page.idle && page.selectedId!=="" && page.draftName.trim()!==""
                onClicked: page.library.modelAction("rename",page.selectedId,page.draftName)
            }
            StudioButton {
                objectName: "previewNamedModel"
                text: page.selectedModel.format==="spatial" ? "포즈 미리보기" : "이 모델로 시험"
                enabled: page.idle && page.selectedId!==""
                onClicked: page.library.modelAction("preview",page.selectedId,"")
            }
            StudioButton {
                objectName: "applyNamedModel"
                text: "이 모델 사용하기"
                primary: true
                visible: page.selectedModel.format!=="spatial"
                enabled: page.idle && page.selectedId!==""
                onClicked: {page.pendingId=page.selectedId;page.pendingAction="apply";confirmation.open()}
            }
        }

        Copy { text: page.library.status; color: Theme.Studio.mintDark }
    }

    Popup {
        id: confirmation; anchors.centerIn: parent; width: Math.min(460,page.width-24); padding: Theme.Studio.pad; modal: true; focus: true
        background: Rectangle {color:Theme.Studio.paper;radius:Theme.Studio.radius;border.color:Theme.Studio.line}
        contentItem: ColumnLayout {
            spacing: Theme.Studio.gap
            Copy {
                Layout.fillWidth: true
                text: page.pendingAction==="restore" ? "기본 모델 또는 이전 저장 버전으로 복원합니다." : "선택한 맞춤 모델을 실시간 제어 모델로 적용합니다."
                font.bold: true
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                StudioButton {text:"취소";onClicked:confirmation.close()}
                StudioButton {
                    text:"적용"
                    primary: true
                    enabled: page.idle
                    onClicked: {
                        confirmation.close()
                        if(page.pendingAction==="restore") page.library.restore(page.pendingId)
                        else page.library.modelAction(page.pendingAction,page.pendingId,page.draftName)
                    }
                }
            }
        }
    }
}
