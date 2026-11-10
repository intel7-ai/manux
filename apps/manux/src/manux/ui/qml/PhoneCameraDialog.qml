import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "phoneCameraDialog"
    required property var controller
    readonly property var phone: controller.phoneCamera
    anchors.centerIn: parent
    width: Math.min(560, parent.width - 40)
    height: Math.min(680, parent.height - 40)
    title: "휴대폰 카메라 연결"
    modal: true
    focus: true
    padding: 18
    background: Rectangle { color: "white"; radius: 14; border.color: "#DEE5EA" }
    onOpened: controller.setPhoneDialogOpen(true)
    onClosed: controller.setPhoneDialogOpen(false)
    contentItem: ColumnLayout {
        spacing: 10
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 10
                Label { text: "PC와 휴대폰을 같은 Wi-Fi에 연결하세요."; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Label { text: dialog.phone.status || ""; font.bold: true; wrapMode: Text.Wrap; Layout.fillWidth: true }
                TextField {
                    id: address
                    objectName: "phoneHost"
                    Layout.fillWidth: true
                    visible: !dialog.phone.enabled
                    placeholderText: "PC의 LAN IP (비워 두면 자동 선택)"
                    selectByMouse: true
                }
                Button {
                    objectName: "startPhoneCamera"
                    text: "휴대폰 연결 켜기"
                    visible: !dialog.phone.enabled
                    enabled: dialog.phone.available === true
                    Layout.fillWidth: true
                    onClicked: dialog.controller.phoneCameraAction("start", address.text.trim())
                }
                Image {
                    objectName: "phoneQr"
                    visible: dialog.phone.enabled && !dialog.phone.blocked && source.toString() !== ""
                    source: dialog.controller.phoneQrUrl
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: dialog.height < 600 ? 160 : 200
                    Layout.preferredHeight: dialog.height < 600 ? 160 : 200
                    fillMode: Image.PreserveAspectFit
                    smooth: false
                    cache: false
                }
                TextField {
                    objectName: "phoneUrl"
                    visible: dialog.phone.enabled
                    Layout.fillWidth: true
                    text: dialog.phone.url || ""
                    readOnly: true
                    selectByMouse: true
                }
                Label {
                    objectName: "phonePairCode"
                    visible: (dialog.phone.code || "") !== ""
                    text: "연결 코드  " + (dialog.phone.code || "") + "  ·  " + (dialog.phone.seconds || 0) + "초"
                    font.pixelSize: 22
                    font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    visible: dialog.phone.enabled
                    text: dialog.phone.device ? "연결 기기: " + dialog.phone.device
                        : "1. 휴대폰 카메라로 QR을 스캔해 Chrome에서 엽니다.\n2. PC 주소를 확인하고 인증서 경고를 통과합니다.\n3. 연결 코드를 입력하고 카메라 권한을 허용하세요."
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    color: "#526575"
                }
                RowLayout {
                    visible: dialog.phone.enabled
                    Layout.fillWidth: true
                    Button { text: "주소 복사"; onClicked: dialog.controller.copyPhoneUrl() }
                    Button {
                        text: "새 코드"
                        enabled: !dialog.phone.connected && !dialog.phone.blocked && !dialog.phone.device
                        onClicked: dialog.controller.phoneCameraAction("pair", "")
                    }
                    Button {
                        text: "연결 끊기"
                        enabled: !!dialog.phone.device && !dialog.phone.blocked
                        onClicked: dialog.controller.phoneCameraAction("disconnect", "")
                    }
                }
                RowLayout {
                    visible: dialog.phone.enabled
                    Layout.fillWidth: true
                    Button {
                        text: dialog.phone.blocked ? "차단 해제" : "휴대폰 연결 차단"
                        onClicked: dialog.controller.phoneCameraAction(dialog.phone.blocked ? "unblock" : "block", "")
                    }
                }
                Label {
                    text: "연결이 끝나면 이 창을 닫고 제어 시작을 누르세요."
                    color: "#526575"; wrapMode: Text.Wrap; Layout.fillWidth: true
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Button {
                objectName: "useLocalCamera"
                text: "PC 카메라로 돌아가기"
                visible: dialog.phone.enabled
                onClicked: dialog.controller.phoneCameraAction("local", "")
            }
            Item { Layout.fillWidth: true }
            Button { text: "닫기"; onClicked: dialog.close() }
        }
    }
    Connections {
        target: dialog.controller
        function onPhoneCameraRequested() { dialog.open() }
    }
}
