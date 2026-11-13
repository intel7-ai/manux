import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "theme" as Theme

Popup {
    id: studio
    objectName: "trainingCenter"
    required property var controller
    readonly property var library: controller.learningStudio
    property string profileDraft: ""
    property int pageIndex: 0
    readonly property bool idle: !controller.recActive && !controller.trainingRunning && !controller.hubRunning
                                 && controller.trainingBusy === "" && !library.busy
    signal recordingRequested()
    signal progressRequested()
    anchors.centerIn: parent
    width: Math.min(1240, parent.width-24); height: Math.min(900, parent.height-24)
    modal: true; focus: true; padding: Theme.Studio.pad; closePolicy: Popup.CloseOnEscape
    onPageIndexChanged: {if(controller)controller.setPoseStudioVisible(opened && pageIndex===0)}
    onOpened: {controller.setPoseStudioVisible(pageIndex===0); posePage.contentItem.contentY=0; profileDraft=controller.trainingProfile; controller.openTraining();library.open() }
    onClosed: {library.hide();controller.closeTraining()}
    background: Rectangle { radius: Theme.Studio.radius; color: Theme.Studio.surface; border.color: Theme.Studio.line }
    component Copy: Label {
        color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap
    }
    component Panel: Rectangle { color: Theme.Studio.paper; border.color: Theme.Studio.line; radius: Theme.Studio.radius }
    contentItem: ColumnLayout {
        spacing: Theme.Studio.gap
        RowLayout {
            Layout.fillWidth: true; Layout.minimumHeight: implicitHeight
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                Copy { text: "학습 센터"; font.pixelSize: Theme.Studio.title; font.bold: true }
                Copy { text: "포즈 표본을 모아 MLP를 학습하고 모델을 관리하세요"; color: Theme.Studio.muted }
            }
            Item { Layout.fillWidth: true }
            StudioButton { objectName: "openTrainingProgress"; text: "학습 진행"; primary: studio.controller.trainingRunning; onClicked: {studio.close();studio.progressRequested()} }
            StudioButton { text: "새로고침"; enabled: studio.idle; onClicked: {studio.controller.refreshTraining();studio.library.refresh()} }
            StudioButton { text: "닫기"; onClicked: studio.close() }
        }
        Panel {
            Layout.fillWidth: true; implicitHeight: identityRow.implicitHeight + Theme.Studio.gap*2; Layout.minimumHeight:implicitHeight
            RowLayout {
                id: identityRow
                anchors { fill: parent; margins: Theme.Studio.gap }
                spacing: 8
                Copy { text: "내 별명"; font.bold: true }
                StudioField {
                    id: alias; objectName: "trainingAlias"
                    Layout.fillWidth: true; Layout.minimumWidth: 90; Layout.maximumWidth: 220
                    maximumLength: 32; text: studio.profileDraft; onTextEdited: studio.profileDraft=text
                    placeholderText: "영문·숫자·_·-"; enabled: studio.idle; Accessible.name: "학습 별명"
                    onAccepted: studio.controller.saveTrainingProfile(text)
                    Keys.onSpacePressed: function(event) {
                        if (studio.pageIndex === 0 && posePage.idle && event.modifiers === Qt.NoModifier) {
                            if (!event.isAutoRepeat) posePage.triggerCapture()
                            event.accepted = true
                        } else event.accepted = false
                    }
                }
                StudioButton { objectName: "saveTrainingAlias"; text: studio.controller.trainingProfile===studio.profileDraft && studio.profileDraft!=="" ? "저장됨" : "별명 저장"; enabled: studio.idle; onClicked: studio.controller.saveTrainingProfile(alias.text) }
                Item { Layout.fillWidth: true }
                StudioButton { text: "학습용"; primary: studio.controller.recordingSplit==="train"; enabled: studio.idle; onClicked: studio.controller.setRecordingSplit("train") }
                StudioButton { text: "별도 평가"; primary: studio.controller.recordingSplit==="eval"; enabled: studio.idle; onClicked: studio.controller.setRecordingSplit("eval") }
            }
        }
        Panel {
            Layout.fillWidth: true; implicitHeight: firstUse.implicitHeight + Theme.Studio.gap * 2; Layout.minimumHeight: implicitHeight
            ColumnLayout {
                id: firstUse
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme.Studio.gap }
                spacing: 3
                Copy { Layout.fillWidth: true; font.bold: true; text: "① 별명 저장 → ② 포즈 표본 수집·저장 → ③ MLP 학습" }
                Copy { Layout.fillWidth: true; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted; text: "포즈 표본에서 학습하고, 모델 관리에서 보관한 모델을 확인하세요." }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0; Layout.preferredHeight: 0; spacing: Theme.Studio.gap; clip:true
            ScrollView {
                id: navigationScroll
                objectName: "learningNavigationScroll"
                Layout.preferredWidth: studio.width < 900 ? 104 : Theme.Studio.navWidth
                Layout.minimumWidth: studio.width < 900 ? 104 : Theme.Studio.navWidth
                Layout.maximumWidth: Theme.Studio.navWidth
                Layout.fillHeight: true; Layout.minimumHeight: 0
                contentWidth: availableWidth; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                ColumnLayout {
                    width: navigationScroll.availableWidth; spacing: 6
                    Repeater {
                        model: ["포즈 표본", "모델 관리"]
                        StudioButton {
                            required property string modelData; required property int index
                            objectName: "studioTab_" + index
                            Layout.fillWidth: true; text: modelData; primary: studio.pageIndex===index
                            onClicked: studio.pageIndex=index
                        }
                    }
                    Copy { Layout.fillWidth: true; visible: studio.height>620; text: "평가 표본은 학습과 다른 시도로 모으세요. 센터에서는 PC 제어를 멈춥니다."; font.pixelSize: Theme.Studio.small; color: Theme.Studio.muted }
                }
            }
            StackLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true; Layout.minimumHeight: 0; currentIndex: studio.pageIndex
                PoseSnapshots { id:posePage; objectName:"learningPosePage"; Layout.fillWidth:true;Layout.fillHeight:true;Layout.minimumHeight:0;controller:studio.controller }
                ModelLibrary { Layout.fillWidth:true;Layout.fillHeight:true;Layout.minimumHeight:0;controller:studio.controller;library:studio.library }
            }
        }
    }
}
