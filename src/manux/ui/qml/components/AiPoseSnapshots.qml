import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme

ColumnLayout {
    id: panel
    required property var ai
    required property var controller
    required property bool idle
    readonly property var state: ai.poseStudioState
    readonly property var selected: ai.poseStudioSelection
    spacing: Theme.Studio.gap
    component Copy: Label { Layout.fillWidth: true; color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap }
    Copy { text: "등록한 AI 동작"; font.bold: true }
    Rectangle {
        Layout.fillWidth: true; implicitHeight: liveJudge.implicitHeight + 20
        color: panel.state.match === false ? Theme.Studio.dangerWash : panel.state.match === true ? Theme.Studio.mintWash : Theme.Studio.soft
        radius: Theme.Studio.radius
        ColumnLayout {
            id: liveJudge
            anchors {left:parent.left;right:parent.right;top:parent.top;margins:10}
            spacing:4
            Copy { objectName:"aiPoseCompareBadge"; text:"실시간 판정 · " + panel.state.compareBadge; font.bold:true; color:panel.state.match === false ? Theme.Studio.danger : Theme.Studio.ink }
            Copy { text:"정답: " + (panel.state.name || "동작 선택") + "    규칙: " + panel.state.ruleText }
            Copy { objectName:"aiPoseModelPrediction"; text:panel.state.modelName + ": " + panel.state.modelText; font.bold:true }
            Copy { text:"최종 손 모양: " + panel.state.finalText + " · " + panel.state.decisionNote; font.pixelSize:Theme.Studio.small; color:Theme.Studio.muted }
            Copy { text:"기본 포즈 MLP (" + panel.controller.studioActiveModelId + ", 참고): " + panel.controller.studioAiAction; font.pixelSize:Theme.Studio.small; color:Theme.Studio.muted }
        }
    }
    Copy {
        visible: panel.ai.poseStudioActions.length === 0
        text: "아직 등록한 AI 동작이 없습니다. 메인의 AI 동작에서 손 모양을 등록하거나 기능을 연결하면 여기에 바로 추가됩니다."
        color: Theme.Studio.muted
    }
    Flow {
        Layout.fillWidth: true; spacing: 6
        Repeater {
            model: panel.ai.poseStudioActions
            StudioButton {
                required property var modelData
                objectName: "aiPoseAction_" + modelData.id
                text: modelData.name + " · " + modelData.frames + "개" + (modelData.enabled ? "" : " · 연결 꺼짐")
                primary: modelData.selected; enabled: panel.idle && !panel.state.busy
                onClicked: panel.ai.selectPoseStudioAction(modelData.id)
            }
        }
    }
    Copy { visible: !!panel.selected.id; text: panel.selected.description || ""; color: Theme.Studio.muted }
    Flow {
        Layout.fillWidth: true; spacing: 6
        visible: !!panel.selected.id
        Repeater {
            model: panel.selected.poses || []
            StudioButton {
                required property var modelData
                text: modelData.name
                primary: modelData.key === panel.state.posture; enabled: panel.idle && !panel.state.busy
                onClicked: panel.ai.selectPoseStudioPosture(modelData.key)
            }
        }
        StudioButton { text: "동작 아님·휴식 · " + panel.state.negativeFrames + "개"; primary: panel.state.neutral; enabled: panel.idle && !panel.state.busy; onClicked: panel.ai.selectPoseStudioPosture("other") }
    }
    Copy { visible: !!panel.selected.id; text: panel.state.reason || ("선택: " + panel.state.name + " · 학습 표본 " + panel.state.frames + "개 / " + panel.state.attempts + "회 수집 · 별도 평가 " + panel.state.evalFrames + "개"); font.bold: true }
    Flow {
        Layout.fillWidth: true; spacing: 6; visible: !!panel.selected.id
        StudioButton { text: "연속 수집"; primary: panel.state.mode === "stream"; enabled: panel.idle && !panel.state.busy; onClicked: panel.ai.setPoseStudioCaptureMode("stream") }
        StudioButton { text: "한 장씩"; primary: panel.state.mode === "snapshot"; enabled: panel.idle && !panel.state.busy; onClicked: panel.ai.setPoseStudioCaptureMode("snapshot") }
        StudioCheck { objectName:"aiPoseDiscrepancyOnly"; text:"불일치 표본만"; checked:panel.state.discrepancyOnly; enabled:panel.idle && !panel.state.busy; onClicked:panel.ai.setPoseStudioDiscrepancyOnly(checked) }
    }
    Flow {
        Layout.fillWidth: true; spacing: 6; visible: !!panel.selected.id
        StudioButton { objectName: "captureAiPoseSample"; text: panel.state.capturing ? "수집 정지" : panel.state.mode === "stream" ? "수집 시작" : "한 장 캡처"; primary: true; enabled: panel.idle && panel.state.trainable && !panel.state.busy; onClicked: panel.ai.capturePoseStudioExample() }
        StudioButton { text: "마지막 표본 취소"; enabled: panel.idle && !panel.state.busy && panel.state.pending > 0; onClicked: panel.ai.undoPoseStudioSample() }
        StudioButton { objectName: "saveAiPoseSamples"; text: "표본 저장"; enabled: panel.idle && !panel.state.busy && panel.state.pending > 0; onClicked: panel.ai.savePoseStudioSamples() }
        StudioButton { text: "새 수집"; enabled: panel.idle && !panel.state.busy; onClicked: panel.ai.clearPoseStudioSamples() }
        StudioButton { objectName: "trainAiPoseModel"; text: panel.state.training ? "MLP 학습 중…" : "MLP 학습"; primary: panel.state.canTrain; enabled: panel.idle && panel.state.canTrain && !panel.state.busy; onClicked: panel.ai.trainPoseStudioModel() }
    }
    Copy { visible: !!panel.selected.id; text: "미저장 표본 " + panel.state.pending + " / 1,000개 · " + (panel.state.capturing ? "연속 수집 중" : "수집 대기") }
    Copy { visible: !!panel.state.text && panel.state.trainable; text: panel.state.text || ""; color: Theme.Studio.mintDark }
    Copy {
        visible: !!panel.selected.id && panel.state.trainable
        text: "수집 시작을 누르면 대기 없이 바로 모읍니다. 수집 정지 → 표본 저장 → MLP 학습 순서로 진행하세요. 동작과 ‘동작 아님·휴식’을 각각 두 차례 이상 나누어 수집하면 별도 시도로 검증할 수 있습니다. 학습을 누르면 미저장 표본도 저장합니다."
        color: Theme.Studio.muted; font.pixelSize: Theme.Studio.small
    }
    Copy { visible: !!panel.selected.id; text: panel.state.active ? "사용 중: AI 동작 MLP · 손 모양 " + panel.state.activeShapes + "개 · 현재 인식: " + panel.state.prediction + " (" + Math.round(panel.state.confidence * 100) + "%)" : "사용 중인 AI 동작 MLP가 없습니다. 표본을 저장하고 MLP 학습을 실행하세요."; color: Theme.Studio.muted }
    Copy { visible: !!panel.selected.id; text: panel.state.metrics; color: Theme.Studio.muted; font.pixelSize: Theme.Studio.small }
}
