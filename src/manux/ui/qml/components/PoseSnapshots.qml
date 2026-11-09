import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme
import ".." as Current

ScrollView {
    id: page
    required property var controller
    readonly property bool idle: !controller.trainingRunning && !controller.hubRunning && !controller.recActive && !controller.learningStudio.busy
    contentWidth: availableWidth; clip: true

    Shortcut {
        sequence: "Space"
        enabled: page.visible && page.idle && !shareConfirm.visible
        autoRepeat: false
        onActivated: page.controller.triggerStudioCapture()
    }
    component Copy: Label { color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap }
    ColumnLayout {
        width: page.availableWidth; spacing: Theme.Studio.gap
        Copy { text: "포즈 표본 스튜디오"; font.pixelSize: Theme.Studio.title; font.bold: true }
        Copy { Layout.fillWidth: true; text: "자세를 선택하고 카메라에 보여주세요. 표본을 모아 빠른 포즈 미리보기 모델을 만듭니다. TCN 제어 모델과는 별개입니다."; color: Theme.Studio.muted }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: poseGuide.implicitHeight + Theme.Studio.gap * 2
            color: Theme.Studio.mintWash; radius: Theme.Studio.radius
            ColumnLayout {
                id: poseGuide
                anchors {left:parent.left;right:parent.right;top:parent.top;margins:Theme.Studio.gap}
                spacing: 3
                Copy { Layout.fillWidth: true; text: "포즈 표본 5단계"; font.bold: true }
                Repeater {
                    model: ["1. 아래에서 모을 자세를 고르세요.", "2. 카메라 관절 표시와 선택한 정답 이름을 확인하세요.", "3. ‘한 장씩’ 또는 ‘연속 수집’으로 표본을 모으고 손을 풀어 다시 시도하세요.", "4. ‘표본 저장’ 후 ‘포즈 모델 학습’을 누르세요.", "5. 새 시도로 규칙·모델 결과를 비교하세요. 적용은 자동이 아니며 보조 차단은 별도 선택입니다."]
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
                anchors {left:parent.left;right:parent.right;bottom:parent.bottom} height:40; color:Theme.Studio.stage
                Copy {anchors {fill:parent;margins:10} color:Theme.Studio.stageText; text: page.controller.studioHandTracked ? page.controller.studioGroundTruthLabel+" · "+page.controller.studioCompareBadge : "카메라에 손을 보여주세요"}
            }
        }
        Flow {
            Layout.fillWidth:true;spacing:6
            Repeater {
                model:page.controller.studioCategories
                StudioButton {required property var modelData;text:modelData.name;primary:modelData.selected;enabled:page.idle;onClicked:page.controller.setStudioCategory(modelData.id)}
            }
        }
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
            StudioButton { objectName:"trainPoseModel";text:"포즈 모델 학습";enabled:page.idle;onClicked:page.controller.trainStudioModel() }
        }
        Copy { Layout.fillWidth:true;text:page.controller.studioSummaryText }
        Copy { Layout.fillWidth:true;text:page.controller.studioNote;color:Theme.Studio.mintDark }
        Copy { Layout.fillWidth:true;text:page.controller.studioModelText;color:Theme.Studio.muted }
        StudioButton {text:page.controller.spatialGuardEnabled ? "실험 보조 차단 켜짐 · 끄기" : "실험 보조 차단 켜기";primary:page.controller.spatialGuardEnabled;enabled:page.idle && page.controller.studioModelLoaded;onClicked:page.controller.toggleSpatialGuard()}
        Copy {Layout.fillWidth:true;text:"보조 차단은 기존 명령을 거부하기만 합니다. 포즈 모델이 TCN이나 규칙의 명령을 새로 만들지는 않습니다. 기본 꺼짐이며 실제 오차·지연은 PC에서 확인하세요.";color:Theme.Studio.muted}
        Flow {
            Layout.fillWidth:true;spacing:6
            StudioButton {text:"이 포즈 표본 업로드";enabled:page.idle && page.controller.trainingProfile!=="";onClicked:shareConfirm.open()}
            StudioButton {text:"이 포즈 표본 다운로드";enabled:page.idle;onClicked:page.controller.learningStudio.poseDatasetAction("download")}
        }
        Copy {Layout.fillWidth:true;text:"공유 대상은 위에서 고른 포즈입니다. 모델·관절 표본만 공유하며 원시 영상은 보내지 않습니다. 학습과 평가는 별도 수집하세요.";color:Theme.Studio.muted}
        Copy { Layout.fillWidth:true;text:"연속 프레임은 독립 시도가 아닙니다. 포즈 학습 점수는 참고값이며, 학습만으로 실제 제어에 자동 적용하지 않습니다. 학습한 모델은 모델 탭에서 이름을 붙여 공유할 수 있습니다.";color:Theme.Studio.muted }
    }
    Popup {
        id:shareConfirm;anchors.centerIn:parent;width:Math.min(450,page.width-24);padding:Theme.Studio.pad;modal:true
        background:Rectangle{color:Theme.Studio.paper;radius:Theme.Studio.radius;border.color:Theme.Studio.line}
        contentItem:ColumnLayout{
            spacing:Theme.Studio.gap
            Copy{Layout.fillWidth:true;text:page.controller.studioGroundTruthLabel+"의 저장된 관절 좌표·정답 라벨을 내 별명으로 팀 저장소에 공유합니다."}
            RowLayout{Layout.alignment:Qt.AlignRight
                StudioButton{text:"취소";onClicked:shareConfirm.close()}
                StudioButton{text:"업로드";primary:true;enabled:page.idle;onClicked:{shareConfirm.close();page.controller.learningStudio.poseDatasetAction("upload")}}
            }
        }
    }
}
