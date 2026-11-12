import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "phoneCameraDialog"
    required property var controller
    readonly property var phone: controller.phoneCamera
    readonly property bool bluetooth: phone.transport === "bluetooth"
    readonly property bool internet: phone.transport === "internet"
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
                Label { text: dialog.internet ? "서로 다른 Wi-Fi·모바일 데이터에서도 인터넷으로 연결합니다." : dialog.bluetooth ? "Android MANUX 앱을 Bluetooth로 연결합니다." : "인터넷·같은 Wi-Fi·Bluetooth 중 연결 방식을 선택하세요."; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Label { text: dialog.phone.status || ""; font.bold: true; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Label {
                    visible: dialog.internet && !!dialog.phone.media_mode
                    text: dialog.phone.media_mode === "webrtc" ? "영상 전송: WebRTC" : "영상 전송: 인터넷 중계"
                    color: "#2364AA"
                    Layout.fillWidth: true
                }
                Label {
                    objectName: "bluetoothPcName"
                    visible: dialog.bluetooth && !!dialog.phone.local_name
                    text: "휴대폰에서 선택할 PC: " + (dialog.phone.local_name || "")
                    font.bold: true
                    font.pixelSize: 18
                    color: "#2364AA"
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                Button {
                    objectName: "startInternetCamera"
                    text: "인터넷 연결 켜기"
                    visible: !dialog.phone.enabled
                    enabled: dialog.phone.internet_available === true
                    Layout.fillWidth: true
                    onClicked: dialog.controller.phoneCameraAction("internet", "")
                }
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
                    text: "같은 Wi-Fi 연결 켜기 (QR)"
                    visible: !dialog.phone.enabled
                    enabled: dialog.phone.available === true
                    Layout.fillWidth: true
                    onClicked: dialog.controller.phoneCameraAction("start", address.text.trim())
                }
                Button {
                    objectName: "startBluetoothCamera"
                    text: "Bluetooth 연결 켜기 (Android 앱)"
                    visible: !dialog.phone.enabled
                    enabled: dialog.phone.bluetooth_available === true
                    Layout.fillWidth: true
                    onClicked: dialog.controller.phoneCameraAction("bluetooth", "")
                }
                Image {
                    objectName: "phoneQr"
                    visible: dialog.phone.enabled && !dialog.bluetooth && !dialog.phone.blocked && source.toString() !== ""
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
                    visible: dialog.phone.enabled && !dialog.bluetooth
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
                    objectName: "phoneConnectionInstructions"
                    visible: dialog.phone.enabled
                    text: dialog.phone.device ? "연결 기기: " + dialog.phone.device
                        : dialog.internet ? "1. 휴대폰 MANUX 앱에서 ‘인터넷 연결’을 여세요.\n2. 위 주소와 연결 코드를 입력하세요. QR은 브라우저로도 열 수 있습니다.\n3. 연결이 끝나면 이 창을 닫고 제어 시작을 누르세요.\n" + (dialog.phone.fixed_address === true
                            ? "고정 주소는 다시 켜도 같습니다. 영상 연결에는 PC 앱이 켜져 있어야 하며, 연결 코드는 매번 새로 확인하세요."
                            : "테스트 주소는 이 PC 앱이 켜져 있을 때만 사용할 수 있고 다시 켜면 바뀝니다.")
                        : dialog.bluetooth ? "1. Windows 설정에서 휴대폰을 Bluetooth 페어링하세요.\n2. 휴대폰의 MANUX 앱에서 이 PC를 선택하세요.\n3. 위 연결 코드를 입력해 연결하세요.\nBluetooth는 가까운 거리에서 사용하세요. 영상 속도는 Wi-Fi보다 낮을 수 있습니다."
                        : "1. 휴대폰 카메라로 QR을 스캔해 Chrome에서 엽니다.\n2. PC 주소를 확인하고 인증서 경고를 통과합니다.\n3. 연결 코드를 입력하고 카메라 권한을 허용하세요."
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                    color: "#526575"
                }
                RowLayout {
                    visible: dialog.phone.enabled
                    Layout.fillWidth: true
                    Button { text: "주소 복사"; visible: !dialog.bluetooth; onClicked: dialog.controller.copyPhoneUrl() }
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
