import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme
import ".." as Current

ScrollView {
    id: page
    required property var controller
    property bool aiSelected: false
    readonly property var ai: controller.aiCustomization
    function updateAiVisibility() { if (controller) controller.setAiPoseStudioVisible(visible && aiSelected) }
    onAiSelectedChanged: updateAiVisibility()
    onVisibleChanged: updateAiVisibility()
    Component.onDestruction: if (controller) controller.setAiPoseStudioVisible(false)
    readonly property bool idle: !controller.trainingRunning && !controller.hubRunning && !controller.recActive && !controller.learningStudio.busy
    function triggerCapture() {
        if (visible && idle) {
            if (aiSelected) ai.capturePoseStudioExample()
            else controller.triggerStudioCapture()
        }
    }
    contentWidth: availableWidth; clip: true

    Shortcut {
        sequence: "Space"
        enabled: page.visible && page.idle
        autoRepeat: false
        onActivated: page.triggerCapture()
    }
    component Copy: Label { color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap }
    ColumnLayout {
        width: page.availableWidth; spacing: Theme.Studio.gap
        Copy { text: "포즈 표본 스튜디오"; font.pixelSize: Theme.Studio.title; font.bold: true }
        Copy { Layout.fillWidth: true; text: page.aiSelected ? "AI 동작에 사용한 손 모양을 여기서 추가 학습합니다. 움직임 조건과 연결한 명령은 유지됩니다." : "자세를 선택하고 카메라에 보여주세요. 표본을 모아 빠른 포즈 미리보기 모델을 만듭니다. TCN 제어 모델과는 별개입니다."; color: Theme.Studio.muted }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: poseGuide.implicitHeight + Theme.Studio.gap * 2
            color: Theme.Studio.mintWash; radius: Theme.Studio.radius
            ColumnLayout {
                id: poseGuide
                anchors {left:parent.left;right:parent.right;top:parent.top;margins:Theme.Studio.gap}
                spacing: 3
                Copy { Layout.fillWidth: true; text: page.aiSelected ? "AI 동작 추가 학습" : "포즈 표본 5단계"; font.bold: true }
                Repeater {
                    model: page.aiSelected ? ["1. 동작을 고르고 ‘수집 시작’을 누르세요. 대기시간 없이 연속 수집합니다.", "2. ‘수집 정지’ → ‘표본 저장’ 후 손을 풀고 다시 수집하세요. ‘동작 아님·휴식’도 모아주세요.", "3. ‘MLP 학습’을 누르세요. 진행·검증 결과가 표시되고 통과한 모델은 바로 적용됩니다."] : ["1. 아래에서 모을 자세를 고르세요.", "2. 카메라 관절 표시와 선택한 정답 이름을 확인하세요.", "3. ‘한 장씩’ 또는 ‘연속 수집’으로 표본을 모으고 손을 풀어 다시 시도하세요.", "4. ‘표본 저장’ 후 ‘MLP 학습’을 누르세요.", "5. 손을 풀고 다시 보여주며 모델의 인식 결과를 확인하세요."]
                    Copy { required property string modelData; Layout.fillWidth: true; text: modelData; font.pixelSize: Theme.Studio.small }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: Math.max(180,Math.min(280,page.height*.38)); radius: Theme.Studio.radius; color: Theme.Studio.stage; clip: true
            Image { anchors.fill: parent; source: page.controller.previewUrl; cache: false; fillMode: Image.PreserveAspectFit
                    Current.HandSkeleton { anchors.fill: parent; bridge: page.controller; imageWidth: parent.paintedWidth; imageHeight: parent.paintedHeight }
                }
            Rectangle {
                anchors {left:parent.left;right:parent.right;bottom:parent.bottom}
                height: page.aiSelected ? livePoseLabels.implicitHeight + 16 : 40
                color: page.aiSelected && page.ai.poseStudioState.match === false ? Theme.Studio.dangerFill : Theme.Studio.stage
                ColumnLayout {
                    id: livePoseLabels
                    anchors {left:parent.left;right:parent.right;top:parent.top;margins:8}
                    spacing: 2
                    Copy { Layout.fillWidth:true; color:Theme.Studio.stageText; font.bold:true; text: page.aiSelected ? ("정답: " + (page.ai.poseStudioState.name || "동작 선택") + " · " + page.ai.poseStudioState.compareBadge) : page.controller.studioHandTracked ? page.controller.studioGroundTruthLabel+" · "+page.controller.studioCompareBadge : "카메라에 손을 보여주세요" }
                    Copy { objectName:"aiPosePreviewPrediction"; Layout.fillWidth:true; visible:page.aiSelected; color:Theme.Studio.stageText; text: "MLP: " + (page.ai.poseStudioState.modelText || "") + "  /  최종: " + (page.ai.poseStudioState.finalText || "") }
                }
            }
        }
        Flow {
            Layout.fillWidth:true;spacing:6
            Repeater {
                model:page.controller.studioCategories
                StudioButton {required property var modelData;text:modelData.name;primary:!page.aiSelected && modelData.selected;enabled:page.idle;onClicked:{page.aiSelected=false;page.controller.setStudioCategory(modelData.id)}}
            }
            StudioButton { objectName: "aiPoseCategory"; text: "AI 동작"; primary: page.aiSelected; enabled: page.idle; onClicked: page.aiSelected=true }
        }
        AiPoseSnapshots { Layout.fillWidth: true; visible: page.aiSelected; ai: page.ai; controller:page.controller; idle: page.idle }
        ColumnLayout {
        Layout.fillWidth: true; spacing: Theme.Studio.gap; visible: !page.aiSelected
        Flow {
            Layout.fillWidth: true; spacing: 6
            Repeater {
                model: page.controller.studioCategoryActions
                StudioButton { required property var modelData; width: Math.max(140,(page.availableWidth-8)/2); text: modelData.short+" · "+modelData.totalCount; primary: modelData.selected; enabled: page.idle; onClicked: page.controller.setStudioGroundTruth(modelData.id) }
            }
        }
        Copy { Layout.fillWidth: true; text: page.controller.studioGroundTruthDesc; font.bold: true }
        Copy { Layout.fillWidth: true; text: "규칙 · "+page.controller.studioSystemAction+"    모델 · "+page.controller.studioAiAction; color: Theme.Studio.muted }
        Flow {
            Layout.fillWidth: true; spacing: 6
            StudioButton { text:"연속 수집";primary:page.controller.studioCaptureMode==="stream";enabled:page.idle;onClicked:page.controller.setStudioCaptureMode("stream") }
            StudioButton { text:"한 장씩";primary:page.controller.studioCaptureMode==="snapshot";enabled:page.idle;onClicked:page.controller.setStudioCaptureMode("snapshot") }
            StudioCheck { text:"불일치 표본만";checked:page.controller.studioDiscrepancyOnly;enabled:page.idle;onClicked:page.controller.setStudioDiscrepancyOnly(checked) }
        }
        Flow {
            Layout.fillWidth: true; spacing: 6
            StudioButton { objectName:"capturePoseSample";text:page.controller.studioStreaming ? "수집 정지" : page.controller.studioCaptureMode==="stream" ? "수집 시작" : "한 장 캡처";primary:true;enabled:page.idle;onClicked:page.controller.triggerStudioCapture() }
            StudioButton { text:"마지막 표본 취소";enabled:page.idle;onClicked:page.controller.undoStudioSample() }
            StudioButton { objectName:"savePoseSamples";text:"표본 저장";enabled:page.idle;onClicked:page.controller.saveStudioDataset() }
            StudioButton { text:"새 수집";enabled:page.idle;onClicked:page.controller.clearStudioSamples() }
            StudioButton { objectName:"trainPoseModel";text:"MLP 학습";enabled:page.idle;onClicked:page.controller.trainStudioModel() }
        }
        Copy { Layout.fillWidth:true;text:page.controller.studioSummaryText }
        Copy { Layout.fillWidth:true;text:page.controller.studioNote;color:Theme.Studio.mintDark }
        Copy { Layout.fillWidth:true;text:page.controller.studioModelText;color:Theme.Studio.muted }
        }
    }
}
