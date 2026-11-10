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
    readonly property var selectedModel: library.localModels.find(function(row){return row.id===page.selectedId}) || ({})
    readonly property bool idle: !library.busy && !controller.trainingRunning && !controller.hubRunning && !controller.recActive
    contentWidth: availableWidth; clip: true
    component Copy: Label { textFormat: Text.PlainText; color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap }
    ColumnLayout {
        width: page.availableWidth; spacing: Theme.Studio.gap
        Copy { text: "내 모델 보관함"; font.pixelSize: Theme.Studio.title; font.bold: true }
        Copy { Layout.fillWidth: true; text: page.controller.trainingModel; color: Theme.Studio.mintDark }
        Copy { Layout.fillWidth: true; text: "로컬에서 학습한 제어 모델을 보관하고 시험/적용합니다. 이전 버전으로 복원할 수도 있습니다."; color: Theme.Studio.muted }
        Flow {
            Layout.fillWidth: true; spacing: 6
            StudioButton { text: "로컬 새로고침"; enabled: page.idle; onClicked: page.library.refresh() }
            StudioButton { text: "모델 파일 가져오기"; enabled: page.idle; onClicked: page.library.importModel() }
            StudioButton { text: "진단"; enabled: page.idle; onClicked: page.library.diagnose() }
        }
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
                    Copy { Layout.fillWidth: true; text: modelRow.modelData.name; font.bold: true; font.pixelSize: 16 }
                    Copy { Layout.fillWidth: true; text: (modelRow.modelData.format==="spatial" ? "포즈 미리보기 · " : "TCN 제어 모델 · ")+modelRow.modelData.id.slice(0,8)+" · "+modelRow.modelData.classes.join(", "); color: Theme.Studio.muted }
                    StudioButton { text: page.selectedId===modelRow.modelData.id ? "선택됨" : "이 모델 선택"; primary: page.selectedId===modelRow.modelData.id; onClicked: {page.selectedId=modelRow.modelData.id;page.draftName=modelRow.modelData.name} }
                }
            }
        }
        Copy { Layout.fillWidth: true; visible: page.library.localModels.length===0; text: "아직 보관 모델이 없습니다. 동작 탭에서 학습을 실행하세요."; color: Theme.Studio.muted }
        StudioField { objectName: "modelDisplayName"; Layout.fillWidth: true; text: page.draftName; onTextEdited: page.draftName=text; maximumLength: 80; placeholderText: "모델 이름 · 예: 책상 오른손 v2"; Accessible.name: "모델 표시 이름"; enabled: page.selectedId!=="" && page.idle }
        Flow {
            Layout.fillWidth: true; spacing: 6
            StudioButton { text: "이름 저장"; enabled: page.idle && page.selectedId!=="" && page.draftName.trim()!==""; onClicked: page.library.modelAction("rename",page.selectedId,page.draftName) }
            StudioButton { objectName: "previewNamedModel"; text: page.selectedModel.format==="spatial" ? "포즈 미리보기로 선택" : "이 모델로 시험"; enabled: page.idle && page.selectedId!==""; onClicked: page.library.modelAction("preview",page.selectedId,"") }
            StudioButton { objectName: "applyNamedModel"; text: "검증 후 이 모델 사용"; primary: true; visible: page.selectedModel.format!=="spatial"; enabled: page.idle && page.selectedId!==""; onClicked: {page.pendingId=page.selectedId;page.pendingAction="apply";confirmation.open()} }
        }
        Copy { text: "이전 사용 버전으로 돌아가기"; font.bold: true; font.pixelSize: 17 }
        Repeater {
            model: page.library.modelVersions
            RowLayout {
                required property var modelData
                id: versionRow
                Layout.fillWidth: true
                Copy { Layout.fillWidth: true; text: (versionRow.modelData.active ? "사용 중 · " : "보관 · ")+(versionRow.modelData.name || versionRow.modelData.trained)+" · "+versionRow.modelData.version.slice(0,8) }
                StudioButton { text: "검증 후 복원"; enabled: page.idle && versionRow.modelData.valid && !versionRow.modelData.active; onClicked: {page.pendingId=versionRow.modelData.version;page.pendingAction="restore";confirmation.open()} }
            }
        }
        Repeater { model: page.library.diagnosticRows; Copy { required property var modelData; text: modelData.name+" · "+modelData.value; color: Theme.Studio.muted } }
        Copy { Layout.fillWidth: true; text: page.library.status; color: Theme.Studio.mintDark }
    }
    Popup {
        id: confirmation; anchors.centerIn: parent; width: Math.min(460,page.width-24); padding: Theme.Studio.pad; modal: true; focus: true
        background: Rectangle {color:Theme.Studio.paper;radius:Theme.Studio.radius;border.color:Theme.Studio.line}
        contentItem: ColumnLayout {
            spacing: Theme.Studio.gap
            Copy { Layout.fillWidth:true; text: page.pendingAction==="upload" ? "선택한 모델과 이름을 팀 저장소에 공유합니다." : "현재 별명의 별도 평가로 전체 동작을 검사합니다. 평가 부족·회귀·취소 시 사용 모델을 유지합니다. 포즈 MLP는 스튜디오 미리보기만 바꿉니다." }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                StudioButton {text:"취소";onClicked:confirmation.close()}
                StudioButton {text:"진행";primary:true;enabled:page.idle;onClicked:{confirmation.close();if(page.pendingAction==="restore")page.library.restore(page.pendingId);else page.library.modelAction(page.pendingAction,page.pendingId,page.draftName)}}
            }
        }
    }
}
