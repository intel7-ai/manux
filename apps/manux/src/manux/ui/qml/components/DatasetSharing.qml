import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../theme" as Theme

ScrollView {
    id: page
    required property var controller
    required property var library
    readonly property bool idle: !library.busy && !controller.trainingRunning && !controller.hubRunning && !controller.recActive
    contentWidth: availableWidth; clip: true
    component Copy: Label { color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: Theme.Studio.body; wrapMode: Text.Wrap }
    ColumnLayout {
        width: page.availableWidth; spacing: 16
        Copy { text: "팀 데이터 공유"; font.pixelSize: Theme.Studio.title; font.bold: true }
        Copy { Layout.fillWidth: true; text: "동작 탭에서 대상을 체크하고 녹화하세요. 학습과 평가는 서로 다른 시도로 수집합니다."; color: Theme.Studio.muted }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: selection.implicitHeight+28; radius: Theme.Studio.radius; color: Theme.Studio.mintWash
            Copy { id: selection; anchors {left:parent.left;right:parent.right;top:parent.top;margins:14} text: page.controller.selectedTrainingKeys.length ? "공유 대상 · "+page.controller.selectedTrainingRows.map(function(r){return r.name}).join(", ") : "공유 대상 · 전체 동작\n특정 동작만 공유하려면 동작 탭에서 체크하세요."; font.bold: true }
        }
        Copy { Layout.fillWidth: true; text: page.controller.trainingData+"\n"+page.controller.trainingStorage }
        Copy { text: "1  저장소 연결"; font.bold: true; font.pixelSize: 16 }
        Copy { Layout.fillWidth: true; text: page.controller.hubRepo+"\n인증은 .env에서 읽습니다. 앱에서 토큰을 입력할 필요가 없습니다."; color: Theme.Studio.muted }
        StudioButton { objectName: "connectDatasetHub"; text: "연결과 권한 확인"; enabled: page.idle; onClicked: page.controller.runHub("status") }
        Copy { Layout.fillWidth: true; text: page.controller.hubStatus; color: Theme.Studio.mintDark }
        Copy { text: "2  선택한 동작 데이터 공유"; font.bold: true; font.pixelSize: 16 }
        Copy { Layout.fillWidth: true; text: "업로드는 내 별명의 완료된 원본 관절 기록만 보냅니다. 여러 동작을 담은 녹화는 파일 전체가 공유됩니다. 영상·보정본·휴지통은 제외합니다."; color: Theme.Studio.muted }
        Flow {
            Layout.fillWidth: true; spacing: 8
            StudioButton { objectName: "uploadRecordings"; text: "내 데이터 업로드"; primary: true; enabled: page.idle && page.controller.trainingProfile!=="" && page.controller.hubCanUpload; onClicked: confirmation.open() }
            StudioButton { objectName: "downloadActionDatasets"; text: "팀 데이터 다운로드"; enabled: page.idle; onClicked: page.controller.runHub("download") }
        }
        Copy { Layout.fillWidth: true; visible: page.controller.trainingProfile===""; text: "상단에서 내 별명을 먼저 저장하세요."; color: Theme.Studio.amber }
        Copy { Layout.fillWidth: true; text: "받은 데이터는 원래 train/eval 구분을 유지합니다. 기존 파일을 덮어쓰지 않으며 해시·완료 상태를 검사합니다."; color: Theme.Studio.muted }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.Studio.line }
        Copy { text: "녹화에 팔·어깨도 표시"; font.bold: true }
        StudioButton { text: page.library.poseEnabled ? "팔·어깨 기록 켜짐" : "팔·어깨 기록 켜기"; primary: page.library.poseEnabled; enabled: page.idle; onClicked: page.library.togglePose() }
        Copy { Layout.fillWidth: true; text: page.library.poseStatus+"\n녹화 중에만 추가 처리하며 TCN 학습 특징에는 넣지 않습니다."; color: Theme.Studio.muted }
    }
    Popup {
        id: confirmation; objectName: "uploadConfirm"
        function accept() {close();page.controller.runHub("upload")}
        anchors.centerIn: parent; width: Math.min(460,page.width-24); padding: Theme.Studio.pad; modal: true; focus: true
        background: Rectangle { color: Theme.Studio.paper; radius: Theme.Studio.radius; border.color: Theme.Studio.line }
        contentItem: ColumnLayout {
            spacing: Theme.Studio.gap
            Copy { Layout.fillWidth: true; text: page.controller.trainingProfile+"의 선택 동작 녹화를 팀 저장소에 업로드합니다. 녹화에 포함된 관절 좌표와 라벨이 공유됩니다." }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                StudioButton { text:"취소";onClicked:confirmation.close() }
                StudioButton { text:"업로드";primary:true;enabled:page.idle;onClicked:confirmation.accept() }
            }
        }
    }
}
