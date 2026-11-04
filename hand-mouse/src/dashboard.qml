import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1140
    height: 720
    minimumWidth: 780
    minimumHeight: 560
    visible: true
    title: "Hand-Mouse · MANUX Qt"
    color: "#F4F6F8"
    onClosing: function (close) {
        if (dashboard.hidesToTray) {
            close.accepted = false   // tray mode: keep running, quit from the tray menu
            dashboard.hideDashboard()
        } else {
            dashboard.requestClose()
        }
    }
    readonly property color navy: "#112331"
    readonly property color muted: "#526575"
    readonly property color mint: "#46E5BD"
    readonly property color line: "#DEE5EA"
    font.family: "Malgun Gothic"

    component TextLabel: Label {
        color: window.navy
        font.pixelSize: 14
    }
    component ActionButton: Button {
        id: button
        property bool accent: false
        implicitHeight: 40
        leftPadding: 16
        rightPadding: 16
        contentItem: TextLabel {
            text: button.text
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            color: button.down ? "#BCEFE1" : button.accent ? window.mint : "#F0F3F6"
            border.color: button.activeFocus ? "#16846E" : "transparent"
            border.width: 2
        }
    }

    Dialog {
        id: pointerSettings
        anchors.centerIn: parent
        width: Math.min(400, window.width - 48)
        modal: true
        title: "검지 포인터 설정"
        standardButtons: Dialog.Close
        onClosed: dashboard.persistPointerSettings()
        contentItem: ColumnLayout {
            spacing: 10
            TextLabel { text: "커서 감도 · " + dashboard.pointerSensitivity + "%"; font.bold: true }
            Slider {
                Layout.fillWidth: true
                from: 50
                to: 150
                stepSize: 5
                snapMode: Slider.SnapAlways
                value: dashboard.pointerSensitivity
                onMoved: dashboard.setPointerSensitivity(Math.round(value))
            }
            RowLayout {
                Layout.fillWidth: true
                TextLabel { text: "느리게"; color: window.muted }
                Item { Layout.fillWidth: true }
                TextLabel { text: "빠르게"; color: window.muted }
            }
            ActionButton { text: "기본값 복원"; onClicked: dashboard.setPointerSensitivity(100) }
            Divider {}
            TextLabel { text: "어디서든 쓰는 단축키"; font.bold: true }
            Repeater {
                model: [["toggle", "켜기 / 끄기"], ["hide", "창 숨기기 / 보이기"]]
                delegate: RowLayout {
                    required property var modelData
                    readonly property string shortcutAction: modelData[0]   // not "action": Button has one
                    Layout.fillWidth: true
                    TextLabel { text: modelData[1]; Layout.preferredWidth: 110 }
                    ActionButton {
                        id: captureButton
                        Layout.fillWidth: true
                        property bool capturing: false
                        text: capturing ? "새 조합을 누르세요 (Esc 취소)" : (dashboard.shortcutLabels[shortcutAction] || "")
                        onClicked: { capturing = true; shortcutError.text = ""; forceActiveFocus() }
                        onActiveFocusChanged: if (!activeFocus) capturing = false
                        Keys.onPressed: function (event) {
                            if (!capturing) return
                            event.accepted = true
                            if (event.key === Qt.Key_Escape) { capturing = false; return }
                            var key = ""
                            if (event.key >= Qt.Key_A && event.key <= Qt.Key_Z) key = String.fromCharCode(event.key).toLowerCase()
                            else if (event.key >= Qt.Key_0 && event.key <= Qt.Key_9) key = String.fromCharCode(event.key)
                            else if (event.key >= Qt.Key_F1 && event.key <= Qt.Key_F12) key = "f" + (event.key - Qt.Key_F1 + 1)
                            if (key === "") return        // a modifier alone: wait for the rest of the combination
                            var parts = []
                            if (event.modifiers & Qt.ControlModifier) parts.push("ctrl")
                            if (event.modifiers & Qt.AltModifier) parts.push("alt")
                            if (event.modifiers & Qt.ShiftModifier) parts.push("shift")
                            parts.push(key)
                            shortcutError.text = dashboard.setShortcut(shortcutAction, parts.join("+"))
                            capturing = false
                        }
                    }
                    ActionButton { text: "기본"; onClicked: dashboard.resetShortcut(shortcutAction) }
                }
            }
            TextLabel {
                id: shortcutError
                visible: text !== ""
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: "#B33E27"
                font.pixelSize: 12
            }
            TextLabel {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: window.muted
                font.pixelSize: 11
                text: "다른 프로그램을 쓰는 중에도 동작합니다. 앱 안에서는 Space로도 켜고 끕니다."
            }
            ActionButton {
                text: "엔진 설정"
                onClicked: {
                    pointerSettings.close()
                    dashboard.command(111)
                }
            }
        }
    }

    Shortcut { sequence: "Return"; enabled: !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() }
    Shortcut { sequence: "Enter"; enabled: !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() }
    Shortcut { sequence: "Esc"; enabled: !dashboard.trackingActive; onActivated: dashboard.command(27) }
    Shortcut { sequence: "Space"; enabled: !dashboard.trackingActive && (!pointerSettings.visible); onActivated: dashboard.command(32) }
    Shortcut { sequence: "Q"; enabled: !dashboard.trackingActive; onActivated: dashboard.command(81) }
    Shortcut { sequence: "O"; enabled: !dashboard.trackingActive; onActivated: dashboard.command(111) }
    Shortcut { sequence: "M"; enabled: !dashboard.trackingActive; onActivated: dashboard.command(109) }
    Shortcut { sequence: "N"; enabled: !dashboard.trackingActive; onActivated: dashboard.command(110) }
    Shortcut { sequence: "F"; enabled: !dashboard.trackingActive; onActivated: dashboard.markFailure() }
    Shortcut { sequence: "C"; enabled: !dashboard.trackingActive; onActivated: dashboard.command(99) }
    Shortcut { sequence: "P"; enabled: !dashboard.trackingActive; onActivated: dashboard.command(112) }

    Timer { interval: 700; running: dashboard.stage === "splash"; onTriggered: dashboard.finishSplash() }

    Rectangle {
        anchors.fill: parent
        visible: dashboard.stage !== "main"
        color: "#F4F6F8"
        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 64, 670)
            spacing: 16
            Image {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: dashboard.stage === "splash" ? 260 : 110
                Layout.preferredHeight: dashboard.stage === "splash" ? 96 : 38
                source: "assets/manux.svg"
                fillMode: Image.PreserveAspectFit
            }
            TextLabel {
                visible: dashboard.stage !== "splash"
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                text: dashboard.stage === "welcome" ? "안녕하세요" : "어느 손으로 조작할까요?"
                font.pixelSize: 38
                font.bold: true
            }
            TextLabel {
                visible: dashboard.stage !== "splash"
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                color: window.muted
                text: dashboard.stage === "welcome"
                    ? "편하게 쓸 손을 한 번 정하면, 다음부터 바로 시작할 수 있어요."
                    : "카메라에 사용할 손과 움직일 공간을 보여주세요.\n한 손만 5초 동안 보여주면 자동으로 시작합니다."
            }
            ActionButton {
                visible: dashboard.stage === "welcome"
                Layout.alignment: Qt.AlignHCenter
                text: "시작하기"
                accent: true
                onClicked: dashboard.beginHandSetup()
            }
            Rectangle {
                visible: dashboard.stage === "capture"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(300, window.height * .45)
                radius: 16
                color: window.navy
                Image { anchors.fill: parent; source: dashboard.previewUrl; cache: false; fillMode: Image.PreserveAspectFit }
            }
            TextLabel {
                visible: dashboard.stage === "capture"
                Layout.alignment: Qt.AlignHCenter
                text: dashboard.handCandidate ? dashboard.handCandidate + " 감지됨 · " + dashboard.secondsRemaining + "초 남음" : "한 손을 기다리는 중"
                color: dashboard.handCandidate ? "#08775E" : window.muted
                font.bold: true
            }
            RowLayout {
                visible: dashboard.stage === "capture"
                Layout.alignment: Qt.AlignHCenter
                ActionButton {
                    visible: dashboard.chosenHand !== ""
                    text: "취소"
                    onClicked: dashboard.cancelHandSetup()
                }

            }
        }
    }

    component SectionLabel: TextLabel {
        font.pixelSize: 13
        font.bold: true
        Layout.topMargin: 4
    }
    component Divider: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: window.line
    }
    // All choices visible at once; the current one is highlighted (was: one button that cycled)
    component Segmented: RowLayout {
        id: seg
        property var options: []      // [[value, label], ...]
        property string current: ""
        signal picked(string value)
        Layout.fillWidth: true
        spacing: 4
        Repeater {
            model: seg.options
            delegate: Button {
                id: option
                required property var modelData
                readonly property bool selected: seg.current === modelData[0]
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                implicitHeight: 34
                enabled: seg.enabled
                onClicked: seg.picked(modelData[0])
                contentItem: TextLabel {
                    text: option.modelData[1]
                    font.pixelSize: 12
                    font.bold: option.selected
                    color: option.enabled ? (option.selected ? window.navy : window.muted) : "#A9B6C0"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    radius: 8
                    color: option.selected ? window.mint : option.down ? "#DDE7EC" : "#F0F3F6"
                    border.color: option.activeFocus ? "#16846E" : "transparent"
                    border.width: 2
                }
            }
        }
    }
    component Chip: Rectangle {
        id: chip
        property alias text: chipLabel.text
        property bool warn: false
        implicitWidth: chipLabel.implicitWidth + 20
        implicitHeight: 28
        radius: 14
        color: warn ? "#FFF1D6" : "#E8F7F2"
        border.color: warn ? "#F2B544" : "transparent"
        TextLabel { id: chipLabel; anchors.centerIn: parent; font.pixelSize: 12; color: chip.warn ? "#8A5A00" : window.navy }
    }

    ColumnLayout {
        visible: dashboard.stage === "main"
        anchors.fill: parent
        spacing: 0
        Rectangle {
            color: "white"
            Layout.fillWidth: true
            Layout.preferredHeight: 72
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                spacing: 12
                TextLabel { text: "MANUX"; font.pixelSize: 26; font.bold: true }
                TextLabel { text: "INPUT STUDIO"; font.pixelSize: 11; font.letterSpacing: 1.4; color: window.muted }
                Item { Layout.fillWidth: true }
                ActionButton {
                    text: dashboard.chosenHand ? dashboard.chosenHand + " · 다시 설정" : "사용할 손 설정"
                    onClicked: dashboard.beginHandSetup()
                }
                ActionButton {
                    text: "설정"
                    enabled: !dashboard.calibratingPointer
                    onClicked: {
                        if (dashboard.pointerOnly) {
                            dashboard.command(27)
                            pointerSettings.open()
                        } else {
                            dashboard.command(111)
                        }
                    }
                }
                ActionButton { text: dashboard.controlLabel; accent: true; onClicked: dashboard.command(32) }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 20
            spacing: 16
            Rectangle {
                Layout.preferredWidth: 280
                Layout.fillHeight: true
                radius: 16
                color: "white"
                border.color: window.line
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8
                    ScrollView {
                        id: sideScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: sideScroll.availableWidth
                            spacing: 8
                            TextLabel { text: "포인터"; font.pixelSize: 20; font.bold: true }
                            Segmented {
                                enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                options: [["index", "검지 포인터"], ["palm", "손바닥 (Dan)"]]
                                current: dashboard.pointerOnly ? "index" : "palm"
                                onPicked: function (value) { dashboard.setPointerPreset(value === "index") }
                            }
                            ActionButton {
                                Layout.fillWidth: true
                                enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                text: dashboard.motionLabel
                                onClicked: dashboard.toggleMotion()
                            }
                            ActionButton {
                                Layout.fillWidth: true
                                enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                text: dashboard.profileLabel + (dashboard.profileCount > 0 ? "  (눌러서 바꾸기)" : "  (없음)")
                                onClicked: dashboard.cycleProfile()
                            }
                            ColumnLayout {
                                visible: dashboard.pointerOnly
                                Layout.fillWidth: true
                                spacing: 8
                                SectionLabel { text: "클릭" }
                                Segmented {
                                    enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                    options: [["pinch", "핀치"], ["trigger", "방아쇠"], ["off", "끔"]]
                                    current: dashboard.clickStyle
                                    onPicked: function (value) { dashboard.setClickStyle(value) }
                                }
                                SectionLabel { text: "커서 기준점" }
                                Segmented {
                                    enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                    options: [["index", "검지 끝"], ["palm", "손바닥 중심"]]
                                    current: dashboard.pointerAnchor
                                    onPicked: function (value) { dashboard.setAnchor(value) }
                                }
                                SectionLabel { text: "커서 옆 손가락 표시" }
                                Segmented {
                                    options: [["off", "끔"], ["on", "켬"]]
                                    current: dashboard.fingerOverlay ? "on" : "off"
                                    onPicked: function (value) { dashboard.setFingerOverlay(value === "on") }
                                }
                                SectionLabel { text: "손 주변 확대 (시험)" }
                                Segmented {
                                    enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                    options: [["off", "끔"], ["on", "켬"]]
                                    current: dashboard.handCrop ? "on" : "off"
                                    onPicked: function (value) { dashboard.setHandCrop(value === "on") }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    ActionButton {
                                        Layout.fillWidth: true
                                        enabled: !dashboard.calibratingPointer
                                        text: dashboard.pointerCalibrated ? "방향 보정 다시" : "방향 보정"
                                        onClicked: dashboard.beginPointerCalibration()
                                    }
                                    ActionButton {
                                        visible: dashboard.pointerCalibrated && !dashboard.calibratingPointer
                                        text: "초기화"
                                        onClicked: dashboard.resetPointerCalibration()
                                    }
                                }
                            }
                            TextLabel {
                                Layout.fillWidth: true
                                text: "설정을 바꾸면 입력이 멈춥니다. 다시 제어 시작을 누르세요."
                                color: window.muted
                                wrapMode: Text.Wrap
                                font.pixelSize: 11
                            }
                            Divider {}
                            SectionLabel { text: "동작 안내" }
                            Repeater {
                                model: dashboard.pointerOnly ? (dashboard.clickOff
                                    ? [[dashboard.pointerAnchor === "palm" ? "손바닥 따라가기" : "검지 끝 따라가기", "포인터 이동"], ["클릭", "꺼짐"]]
                                    : dashboard.triggerClick
                                    ? [["검지만 펴기", "포인터 이동"], ["엄지로 검지 옆 짧게", "좌클릭"],
                                       ["누른 채 가만히", "우클릭"], ["누른 채 움직이기", "드래그"]]
                                    : [["검지만 펴기", "포인터 이동"], ["엄지 + 중지", "좌클릭"], ["엄지 + 약지", "우클릭"]]) : [
                                    ["손 이동", "포인터 이동"], ["엄지 + 검지", "스크롤"],
                                    ["엄지 + 중지", "좌클릭"], ["엄지 + 약지", "우클릭"],
                                    ["엄지 + 새끼", "창 선택"]
                                ]
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    TextLabel { text: modelData[0]; font.pixelSize: 12; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    TextLabel { text: modelData[1]; font.pixelSize: 12; color: window.muted }
                                }
                            }
                            TextLabel {
                                visible: dashboard.pointerOnly
                                Layout.fillWidth: true
                                text: dashboard.clickOff
                                    ? "클릭을 판정하지 않습니다. 손이 보이면 손 모양과 관계없이 커서가 따라갑니다."
                                    : dashboard.triggerClick
                                    ? "엄지가 검지 옆에 다가오면 커서가 고정되고, 누르기 직전 위치에서 클릭합니다."
                                    : "검지를 움직이는 동안에는 클릭하지 않습니다. 멈춘 뒤 엄지를 뗐다가 중지·약지에 대면 클릭합니다."
                                color: window.muted
                                wrapMode: Text.Wrap
                                font.pixelSize: 11
                            }
                            TextLabel {
                                visible: dashboard.pointerOnly && diagnostics.visible
                                Layout.fillWidth: true
                                text: "0 엄지 · 1 검지 · 2 중지 · 3 약지 · 4 새끼\n조합: " + (dashboard.detectedContacts || "—")
                                wrapMode: Text.Wrap
                                color: window.muted
                                font.pixelSize: 12
                            }
                            Divider { visible: dashboard.pointerOnly }
                            SectionLabel { visible: dashboard.pointerOnly; text: "트래킹 측정" }
                            ActionButton {
                                visible: dashboard.pointerOnly
                                enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                Layout.fillWidth: true
                                text: "측정 시작 (클릭 없음)"
                                onClicked: dashboard.beginTrackingTest()
                            }
                            TextLabel {
                                visible: dashboard.pointerOnly && dashboard.recentResults.length === 0
                                Layout.fillWidth: true
                                text: "아직 측정 기록이 없습니다. 설정을 바꿔 가며 측정해 비교하세요."
                                color: window.muted
                                wrapMode: Text.Wrap
                                font.pixelSize: 11
                            }
                            Repeater {
                                model: dashboard.pointerOnly ? dashboard.recentResults : []
                                delegate: TextLabel {
                                    required property string modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    text: modelData
                                    wrapMode: Text.Wrap
                                    font.pixelSize: 11
                                    font.bold: index === 0
                                    color: index === 0 ? window.navy : window.muted
                                }
                            }
                            Divider { visible: dashboard.pointerOnly }
                            SectionLabel { visible: dashboard.pointerOnly; text: "실패 기록" }
                            TextLabel {
                                visible: dashboard.pointerOnly
                                Layout.fillWidth: true
                                text: dashboard.failureSummary + (dashboard.failureLast ? "\n최근: " + dashboard.failureLast : "")
                                    + "\n손을 놓치면 앞뒤 3초의 관절 좌표를 logs/failures에 저장합니다(영상 없음)."
                                color: window.muted
                                wrapMode: Text.Wrap
                                font.pixelSize: 11
                            }
                            ActionButton {
                                visible: dashboard.pointerOnly
                                Layout.fillWidth: true
                                text: "방금 이상했어요 (F)"
                                onClicked: dashboard.markFailure()
                            }
                            ActionButton {
                                Layout.fillWidth: true
                                text: "로그 묶어 내보내기"
                                onClicked: dashboard.exportLogs()
                            }
                            TextLabel {
                                visible: dashboard.exportText !== ""
                                Layout.fillWidth: true
                                text: dashboard.exportText + "
성능·실패 기록과 설정만 담깁니다. 영상, 창 제목, 프로필 이름은 넣지 않습니다."
                                color: window.muted
                                wrapMode: Text.Wrap
                                font.pixelSize: 11
                            }
                        }
                    }
                    Divider {}
                    TextLabel {
                        Layout.fillWidth: true
                        text: "Space 시작/정지 · 어디서든 " + (dashboard.shortcutLabels["toggle"] || "") + " · Esc 정지 · F 실패 표시"
                        font.pixelSize: 11
                        color: window.muted
                        wrapMode: Text.Wrap
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 16
                color: "white"
                border.color: window.line
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        TextLabel { text: "카메라"; font.pixelSize: 20; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Chip { visible: dashboard.cameraMode !== ""; text: dashboard.cameraMode }
                        Chip {
                            visible: dashboard.brightness >= 0
                            warn: dashboard.lowLight
                            text: (dashboard.lowLight ? "어두움 " : "밝기 ") + Math.round(dashboard.brightness / 2.55) + "%"
                        }
                        Chip { text: dashboard.tracking }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 10
                        color: window.navy
                        clip: true
                        Image {
                            anchors.fill: parent
                            source: dashboard.previewUrl
                            cache: false
                            fillMode: Image.PreserveAspectFit
                        }
                        Rectangle {
                            visible: dashboard.lowLight
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 12
                            implicitHeight: lowLightText.implicitHeight + 16
                            radius: 8
                            color: "#F2B544"
                            TextLabel {
                                id: lowLightText
                                anchors.fill: parent
                                anchors.margins: 8
                                text: "어두워요. 움직일 때 손을 놓치기 쉽습니다. 조명을 켜거나 모니터를 밝게 하고, 손을 카메라에 조금 더 가까이 두세요."
                                wrapMode: Text.Wrap
                                font.pixelSize: 12
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }
                    RowLayout {
                        visible: dashboard.calibratingPointer
                        Layout.fillWidth: true
                        TextLabel {
                            Layout.fillWidth: true
                            text: dashboard.calibrationHint
                            wrapMode: Text.Wrap
                        }
                        ActionButton { text: "취소"; onClicked: dashboard.cancelPointerCalibration() }
                    }
                    RowLayout {
                        visible: !dashboard.calibratingPointer
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            TextLabel { text: dashboard.inputState; font.bold: true }
                            TextLabel { Layout.fillWidth: true; text: dashboard.currentAction; color: window.muted; wrapMode: Text.Wrap }
                        }
                        ActionButton { text: diagnostics.visible ? "상세 닫기" : "상세 정보"; onClicked: diagnostics.visible = !diagnostics.visible }
                    }
                    TextLabel {
                        id: diagnostics
                        visible: false
                        onVisibleChanged: dashboard.showFingertips(visible)
                        Layout.fillWidth: true
                        text: dashboard.details
                        color: window.muted
                        wrapMode: Text.Wrap
                        font.pixelSize: 12
                    }
                }
            }
        }
    }

    // Reception overlay: small click-through pill in a screen corner (tray mode / --overlay)
    Window {
        id: overlay
        title: "MANUX 상태"
        // Independent of the dashboard window: it stays usable while the dashboard is hidden in the tray
        transientParent: null
        visible: dashboard.overlayVisible
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
               | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        width: pill.implicitWidth
        height: 34
        readonly property var area: dashboard.overlayArea
        readonly property string corner: dashboard.overlayCorner
        x: corner.indexOf("left") >= 0 ? area[0] + 16 : area[0] + area[2] - width - 16
        y: corner.indexOf("top") >= 0 ? area[1] + 16 : area[1] + area[3] - height - 16
        Rectangle {
            id: pill
            anchors.fill: parent
            implicitWidth: pillRow.implicitWidth + 24
            radius: height / 2
            color: "#E6112331"
            Row {
                id: pillRow
                anchors.centerIn: parent
                spacing: 8
                Rectangle {
                    width: 10; height: 10; radius: 5
                    anchors.verticalCenter: parent.verticalCenter
                    color: dashboard.receptionState === "ok" ? "#2FBF8F"
                         : dashboard.receptionState === "weak" ? "#F2B544"
                         : dashboard.receptionState === "none" ? "#8A9AA6" : "#526575"
                }
                Label {
                    text: "MANUX · " + dashboard.receptionText
                    color: "#F5F7F8"
                    font.pixelSize: 13
                    font.family: "Malgun Gothic"
                }
            }
        }
    }

    // Finger tracker: where the fingertips are and what the hand does, drawn around the Windows cursor
    // (park's HUD idea, src/finger_overlay.py). Click-through; fades when frames stop.
    Window {
        id: fingerTracker
        title: "MANUX 손가락 표시"
        transientParent: null
        visible: dashboard.fingerOverlay
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
               | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        width: 320
        height: 320
        readonly property var frame: dashboard.fingerFrame
        readonly property bool hasHand: frame.tips !== undefined
        readonly property real palmPx: 46
        readonly property var colors: ({ idle: "#94A3B8", move: "#46E5BD", left: "#10B981", right: "#3B82F6", scroll: "#C084FC", other: "#94A3B8" })
        readonly property var labels: ({ idle: "쉬는 중", move: "커서 이동", left: "좌클릭 · 드래그", right: "우클릭", scroll: "스크롤", other: "손 인식 중" })
        x: hasHand ? frame.cursor[0] - width / 2 : x
        y: hasHand ? frame.cursor[1] - height / 2 : y
        Item {
            anchors.fill: parent
            opacity: fingerTracker.hasHand ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: fingerTracker.hasHand ? 80 : 500 } }
            Canvas {
                id: fingerCanvas
                anchors.fill: parent
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    var f = fingerTracker.frame
                    if (!fingerTracker.hasHand) return
                    var color = fingerTracker.colors[f.mode] || fingerTracker.colors.other
                    var cx = width / 2, cy = height / 2, s = fingerTracker.palmPx
                    function at(i) { return [cx + f.tips[i][0] * s, cy + f.tips[i][1] * s] }
                    ctx.lineWidth = 3
                    ctx.strokeStyle = color
                    if (f.contact > 0) {
                        var a = at(0), b = at(f.contact)
                        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke()
                    }
                    for (var i = 0; i < 5; i++) {
                        var p = at(i)
                        var active = i === 0 || i === f.contact
                        ctx.beginPath()
                        ctx.arc(p[0], p[1], i === 1 ? 7 : 5, 0, Math.PI * 2)
                        ctx.fillStyle = active ? color : "rgba(255,255,255,0.85)"
                        ctx.fill()
                        ctx.lineWidth = 2
                        ctx.stroke()
                    }
                }
                Connections {
                    target: dashboard
                    function onFingerFrameChanged() { fingerCanvas.requestPaint() }
                }
            }
            Rectangle {
                x: parent.width / 2 + 18
                y: parent.height / 2 + 26
                width: trackerLabel.implicitWidth + 20
                height: 24
                radius: 12
                color: "#E0112331"
                Label {
                    id: trackerLabel
                    anchors.centerIn: parent
                    text: fingerTracker.hasHand ? (fingerTracker.labels[fingerTracker.frame.mode] || fingerTracker.labels.other) : ""
                    color: fingerTracker.hasHand ? (fingerTracker.colors[fingerTracker.frame.mode] || fingerTracker.colors.other) : "#94A3B8"
                    font.pixelSize: 13
                    font.bold: true
                    font.family: "Malgun Gothic"
                }
            }
        }
    }

    // Motion-mode badge: always on top and click-through, in the top-right corner of the screen, so the user
    // can tell the mode from anywhere (also when this window is hidden or behind others).
    Window {
        id: motionBadge
        title: "MANUX 모션 모드"
        transientParent: null
        visible: dashboard.motionBadgeVisible
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
               | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        width: badgePill.implicitWidth
        height: 44
        readonly property var area: dashboard.overlayArea
        x: area[0] + area[2] - width - 16
        // below the small reception pill when that one is showing in the same corner
        y: area[1] + 16 + (dashboard.overlayVisible && dashboard.overlayCorner === "top-right" ? 42 : 0)
        Rectangle {
            id: badgePill
            anchors.fill: parent
            implicitWidth: badgeRow.implicitWidth + 32
            radius: height / 2
            color: dashboard.motionBadgeOn ? "#F2B544" : "#E6112331"
            border.width: 2
            border.color: dashboard.motionBadgeOn ? "#FFF3D6" : "#526575"
            Row {
                id: badgeRow
                anchors.centerIn: parent
                spacing: 10
                Rectangle {
                    width: 12; height: 12; radius: 6
                    anchors.verticalCenter: parent.verticalCenter
                    color: dashboard.motionBadgeOn ? "#112331" : "#F2B544"
                }
                Label {
                    text: dashboard.motionBadgeText
                    color: dashboard.motionBadgeOn ? "#112331" : "#F5F7F8"
                    font.pixelSize: 16
                    font.bold: true
                    font.family: "Malgun Gothic"
                }
            }
        }
    }

    Window {
        id: trackingWindow
        title: "MANUX 트래킹 측정"
        // Independent of the dashboard window: it stays usable while the dashboard is hidden in the tray
        transientParent: null
        color: "#0E1B26"
        visibility: dashboard.trackingActive ? Window.FullScreen : Window.Hidden
        flags: Qt.Window
        // Take keyboard focus when shown, so Space / Esc reach this window right away
        onVisibilityChanged: function (mode) {
            if (mode === Window.FullScreen) {
                requestActivate()
                keyCatcher.forceActiveFocus()
            }
        }
        screen: {
            const list = Qt.application.screens
            for (let i = 0; i < list.length; i++)
                if (list[i].name === dashboard.trackingScreen) return list[i]
            return list.length ? list[0] : null
        }

        // Keys are read directly here, and the main window's shortcuts are off while the
        // test runs: they also caught keys pressed in this window, and two "Space" shortcuts
        // clashed so neither fired (2026-11-03, Windows: stuck on "Space를 눌러 시작").
        Item {
            id: keyCatcher
            anchors.fill: parent
            focus: true
            Keys.onPressed: function (event) {
                if (event.key === Qt.Key_Space) {
                    dashboard.command(32)
                    event.accepted = true
                } else if (event.key === Qt.Key_Escape) {
                    dashboard.closeTrackingTest()
                    event.accepted = true
                }
            }
        }
        MouseArea {
            // Clicking the window (with the real mouse) also gives it the keyboard back
            anchors.fill: parent
            onPressed: function (mouse) { keyCatcher.forceActiveFocus(); mouse.accepted = false }
        }

        Rectangle {
            id: target
            visible: !dashboard.trackingDone && dashboard.trackingTargetX >= 0
            width: Math.max(6, dashboard.trackingTargetSize * trackingWindow.width)
            height: width
            radius: width / 2
            x: dashboard.trackingTargetX * trackingWindow.width - width / 2
            y: dashboard.trackingTargetY * trackingWindow.height - height / 2
            color: "#46E5BD"
            opacity: .85
        }
        Rectangle {
            // where the controller puts the cursor (also visible in --dry-run)
            width: 16; height: 16; radius: 8
            x: dashboard.trackingCursorX * trackingWindow.width - 8
            y: dashboard.trackingCursorY * trackingWindow.height - 8
            color: "transparent"
            border.color: "#F5F7F8"
            border.width: 2
        }
        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 24
            spacing: 6
            Label {
                anchors.horizontalCenter: parent.horizontalCenter
                text: dashboard.trackingStatus
                color: "#F5F7F8"
                font.pixelSize: 20
                font.bold: true
            }
            Label {
                anchors.horizontalCenter: parent.horizontalCenter
                text: dashboard.trackingDone ? "Esc 닫기 · 결과는 hand-mouse/logs/tracking-*.csv"
                                             : "Space 제어 시작/일시정지 · Esc 그만두기 · 클릭은 하지 않습니다"
                color: "#9FB3C2"
                font.pixelSize: 14
            }
        }
        Label {
            visible: dashboard.trackingPaused
            anchors.centerIn: parent
            text: "일시정지됨\nSpace를 눌러 시작"
            horizontalAlignment: Text.AlignHCenter
            color: "#F5F7F8"
            font.pixelSize: 40
            font.bold: true
        }
        Button {
            visible: dashboard.trackingDone
            anchors.centerIn: parent
            text: "닫기"
            onClicked: dashboard.closeTrackingTest()
        }
    }
}
