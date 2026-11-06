import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: settings
    objectName: "studioSettings"
    required property var controller
    signal dataRequested()
    anchors.centerIn: parent
    width: Math.min(500, parent.width - 48)
    title: "설정"
    modal: true
    focus: true
    padding: 20
    standardButtons: Dialog.Close
    background: Rectangle { color: "white"; radius: 14; border.color: "#DEE5EA" }
    contentItem: ColumnLayout {
        spacing: 12
        Label { text: "커서 감도 · " + settings.controller.pointerSensitivity + "%"; color: "#112331" }
        Slider {
            Layout.fillWidth: true
            from: 50; to: 150; stepSize: 5
            value: settings.controller.pointerSensitivity
            onMoved: settings.controller.setPointerSensitivity(Math.round(value))
        }
        CheckBox {
            text: "커서 옆 손가락 표시"
            checked: settings.controller.fingerOverlay
            onClicked: settings.controller.setFingerOverlay(checked)
        }
        CheckBox {
            text: "화면 구석 상태 표시"
            checked: settings.controller.overlayVisible
            onClicked: settings.controller.setOverlayVisible(checked)
        }
        Button {
            text: "데이터 관리 · 녹화 별명 · train / eval"
            Layout.fillWidth: true
            onClicked: settings.dataRequested()
        }
        Label {
            text: "이전 작업 가져오기와 별도 평가 검증도 데이터 관리에서 실행합니다."
            wrapMode: Text.Wrap; Layout.fillWidth: true; color: "#526575"
        }
    }
    onClosed: controller.persistPointerSettings()
}
