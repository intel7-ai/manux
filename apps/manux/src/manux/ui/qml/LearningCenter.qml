import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "theme" as Theme

Popup {
    id: studio
    objectName: "trainingCenter"
    required property var controller
    signal recordingRequested()
    readonly property bool idle: !controller.recActive && !controller.trainingRunning
                                 && !controller.hubRunning && controller.trainingBusy === ""
    anchors.centerIn: parent
    width: Math.min(1040, parent.width - 32)
    height: Math.min(820, parent.height - 32)
    modal: true
    focus: true
    padding: 20
    closePolicy: Popup.CloseOnEscape
    onOpened: controller.openTraining()
    onClosed: controller.closeTraining()
    background: Rectangle { radius: 16; color: Theme.Studio.surface; border.color: Theme.Studio.line }

    component Copy: Label {
        color: Theme.Studio.ink
        font.family: Theme.Studio.font
        font.pixelSize: 13
        wrapMode: Text.Wrap
    }
    component Panel: Rectangle {
        color: Theme.Studio.paper
        border.color: Theme.Studio.line
        radius: 12
    }

    contentItem: ColumnLayout {
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                Copy { text: "학습 센터"; font.pixelSize: 24; font.bold: true }
                Copy { Layout.fillWidth: true; text: "동작 선택 → 학습·평가 따로 녹화 → 팀 공유 → 검증 후 적용"; color: Theme.Studio.muted }
            }
            StudioButton { text: "닫기"; onClicked: studio.close() }
        }
        ScrollView {
            id: page
            objectName: "learningPage"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: page.availableWidth
                spacing: 14
                Panel {
                    Layout.fillWidth: true
                    implicitHeight: identityBody.implicitHeight + 28
                    ColumnLayout {
                        id: identityBody
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                        spacing: 10
                        Copy { text: "01  녹화 준비"; font.bold: true; font.pixelSize: 16 }
                        RowLayout {
                            Layout.fillWidth: true
                            Copy { text: "내 별명" }
                            StudioField {
                                id: alias
                                objectName: "trainingAlias"
                                Layout.fillWidth: true
                                maximumLength: 32
                                text: studio.controller.trainingProfile
                                placeholderText: "실명 대신 영문·숫자·_·-"
                                enabled: studio.idle
                                Accessible.name: "녹화 별명"
                                onAccepted: studio.controller.saveTrainingProfile(text)
                            }
                            StudioButton {
                                text: "별명 저장"; enabled: studio.idle
                                onClicked: studio.controller.saveTrainingProfile(alias.text)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Copy { text: "이번 녹화" }
                            StudioButton {
                                text: "학습용 · train"
                                primary: studio.controller.recordingSplit === "train"
                                enabled: studio.idle
                                onClicked: studio.controller.setRecordingSplit("train")
                            }
                            StudioButton {
                                text: "별도 평가용 · eval"
                                primary: studio.controller.recordingSplit === "eval"
                                enabled: studio.idle
                                onClicked: studio.controller.setRecordingSplit("eval")
                            }
                            Item { Layout.fillWidth: true }
                        }
                        Copy { Layout.fillWidth: true; text: "평가용은 같은 동작을 새로 수행해 녹화하세요. 녹화 중에는 PC 마우스 제어가 꺼집니다."; color: Theme.Studio.muted }
                    }
                }
                Panel {
                    Layout.fillWidth: true
                    implicitHeight: tasksBody.implicitHeight + 28
                    ColumnLayout {
                        id: tasksBody
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                        spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Copy { text: "02  동작 선택"; font.pixelSize: 16; font.bold: true; Layout.fillWidth: true }
                            Copy { text: studio.controller.selectedTrainingKeys.length + "개 선택"; color: Theme.Studio.mintDark; font.bold: true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            StudioSelect {
                                objectName: "trainingFilter"
                                Layout.fillWidth: true
                                model: studio.controller.trainingCategories
                                currentIndex: Math.max(0, model.indexOf(studio.controller.trainingFilter))
                                onActivated: studio.controller.setTrainingFilter(currentText)
                                Accessible.name: "동작 분류"
                            }
                            StudioButton { text: "표시 항목 선택"; onClicked: studio.controller.selectVisibleTraining(true) }
                            StudioButton { text: "표시 항목 해제"; onClicked: studio.controller.selectVisibleTraining(false) }
                        }
                        Copy { Layout.fillWidth: true; text: studio.controller.trainingCatalog; color: Theme.Studio.muted }
                        Copy {
                            Layout.fillWidth: true
                            text: "필터를 바꿔도 체크한 동작은 유지됩니다. 선택: "
                                  + (studio.controller.selectedTrainingRows.map(function(row) { return row.name }).join(", ") || "없음")
                            color: Theme.Studio.mintDark
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            StudioButton {
                                objectName: "recordSelection"
                                text: "선택한 동작 녹화"; primary: true
                                enabled: studio.idle && studio.controller.selectedTrainingKeys.length > 0
                                onClicked: {
                                    if (studio.controller.recordTrainingSelection()) {
                                        studio.close()
                                        studio.recordingRequested()
                                    }
                                }
                            }
                            StudioButton {
                                objectName: "trainSelection"
                                text: "선택한 동작 포함 재학습"
                                enabled: studio.idle && studio.controller.selectionCanTrain
                                onClicked: studio.controller.trainTrainingSelection()
                            }
                        }
                        ListView {
                            id: taskList
                            objectName: "trainingTasks"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(340, contentHeight)
                            clip: true
                            spacing: 8
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOn }
                            model: studio.controller.trainingRows
                            delegate: Rectangle {
                                id: task
                                required property var modelData
                                width: taskList.width - 12
                                height: taskBody.implicitHeight + 24
                                radius: 10
                                color: modelData.selected ? Theme.Studio.mintWash : Theme.Studio.surface
                                border.color: modelData.selected ? Theme.Studio.mintDark : Theme.Studio.line
                                RowLayout {
                                    id: taskBody
                                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
                                    spacing: 12
                                    StudioCheck {
                                        objectName: "taskCheck_" + task.modelData.key
                                        checked: task.modelData.selected
                                        Accessible.name: task.modelData.name + " 선택"
                                        onClicked: studio.controller.setTrainingSelected(task.modelData.key, checked)
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 4
                                        Copy { Layout.fillWidth: true; text: task.modelData.name; font.pixelSize: 15; font.bold: true }
                                        Copy { Layout.fillWidth: true; text: task.modelData.instruction; color: Theme.Studio.muted }
                                        Copy { Layout.fillWidth: true; text: task.modelData.data_text + " · " + task.modelData.score_text }
                                        Copy { Layout.fillWidth: true; text: task.modelData.advice; font.pixelSize: 12; color: Theme.Studio.muted }
                                    }
                                    Copy {
                                        Layout.preferredWidth: 76
                                        text: task.modelData.can_train ? "학습 지원" : task.modelData.label === "none" ? "공통 중립" : "녹화·공유"
                                        color: task.modelData.can_train ? Theme.Studio.mintDark : Theme.Studio.muted
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }
                            }
                        }
                        Copy { Layout.fillWidth: true; text: "재학습은 선택한 동작과 기존 클래스를 함께 학습합니다. 현재 직접 적용은 pinch의 tap/span 동작을 지원합니다."; color: Theme.Studio.muted }
                    }
                }
                Panel {
                    Layout.fillWidth: true
                    implicitHeight: shareBody.implicitHeight + 28
                    ColumnLayout {
                        id: shareBody
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                        spacing: 10
                        Copy { text: "03  팀 데이터 공유"; font.pixelSize: 16; font.bold: true }
                        Copy { Layout.fillWidth: true; text: "저장소 · " + studio.controller.hubRepo; font.bold: true }
                        Copy { Layout.fillWidth: true; text: ".env 설정으로 연결합니다. 연결 확인 후 내 별명의 완료된 관절 기록만 공유합니다. 영상은 전송하지 않습니다."; color: Theme.Studio.muted }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            StudioButton { text: "연결 확인"; enabled: studio.idle; onClicked: studio.controller.runHub("status") }
                            StudioButton { text: "팀 데이터 받기"; enabled: studio.idle; onClicked: studio.controller.runHub("download") }
                            StudioButton { objectName: "uploadRecordings"; text: "내 녹화 업로드"; enabled: studio.idle && studio.controller.hubConnected && studio.controller.trainingProfile !== ""; onClicked: uploadConfirm.open() }
                        }
                        Copy { Layout.fillWidth: true; text: studio.controller.hubStatus; color: Theme.Studio.mintDark }
                    }
                }
                Panel {
                    Layout.fillWidth: true
                    implicitHeight: progressBody.implicitHeight + 28
                    ColumnLayout {
                        id: progressBody
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                        spacing: 10
                        Copy { text: "04  학습과 적용"; font.pixelSize: 16; font.bold: true }
                        Copy { Layout.fillWidth: true; text: studio.controller.trainingData + " · " + studio.controller.trainingModel }
                        Copy { Layout.fillWidth: true; text: studio.controller.trainingStorage; color: studio.controller.trainingStorageWarn ? Theme.Studio.amber : Theme.Studio.muted }
                        Copy { Layout.fillWidth: true; text: studio.controller.trainingBusy || studio.controller.trainingStage || "통합 학습 → 별도 eval 검증 → 통과 시 앱 적용" }
                        Rectangle {
                            Layout.fillWidth: true; height: 6; radius: 3; color: Theme.Studio.line
                            Rectangle { width: parent.width * Math.max(0, Math.min(1, studio.controller.trainingProgress)); height: parent.height; radius: 3; color: Theme.Studio.mintDark }
                        }
                        Copy { Layout.fillWidth: true; text: studio.controller.trainingNote; visible: text !== ""; color: Theme.Studio.mintDark }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            StudioButton { text: "학습 멈춤"; visible: studio.controller.trainingRunning; onClicked: studio.controller.stopTraining() }
                            StudioButton { text: "새로고침"; enabled: studio.idle; onClicked: studio.controller.refreshTraining() }
                            StudioButton { text: "모델 내보내기"; enabled: studio.idle; onClicked: studio.controller.exportModel() }
                            StudioButton { text: "내보낸 폴더 열기"; onClicked: studio.controller.openExports() }
                        }
                        Copy { Layout.fillWidth: true; text: "실패하거나 검증 전에 취소하면 기존 모델을 유지합니다. 평가 수치는 해당 시도의 결과이며 실제 사용 정확도를 보장하지 않습니다."; color: Theme.Studio.muted }
                    }
                }
            }
        }
    }
    Popup {
        id: uploadConfirm
        objectName: "uploadConfirm"
        anchors.centerIn: parent
        width: Math.min(500, studio.width - 40)
        modal: true; focus: true; padding: 20
        closePolicy: Popup.CloseOnEscape
        function accept() { studio.controller.runHub("upload"); close() }
        background: Rectangle { radius: 12; color: Theme.Studio.paper; border.color: Theme.Studio.line }
        contentItem: ColumnLayout {
            spacing: 14
            Copy { text: "내 녹화 공유"; font.pixelSize: 20; font.bold: true }
            Copy { Layout.fillWidth: true; text: studio.controller.trainingProfile + " 별명의 완료된 손 관절 기록을\n" + studio.controller.hubRepo + " 에 올립니다.\n학습·평가 라벨을 포함하며 영상은 포함하지 않습니다." }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                StudioButton { text: "취소"; onClicked: uploadConfirm.close() }
                StudioButton { text: "업로드"; primary: true; enabled: studio.idle; onClicked: uploadConfirm.accept() }
            }
        }
    }
}
