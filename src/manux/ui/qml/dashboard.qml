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
    readonly property bool learningDialogOpen: advancedSettings.visible || settingsDialog.visible || trainingCenter.visible || capturePlan.visible || trainingProgress.visible || aiCustomization.opened
    readonly property bool interactionBusy: learningDialogOpen || dashboard.currentPage === "studio"
    AiCustomizationDialog { id: aiCustomization; dashboard: window.aiBridge }
    readonly property var aiBridge: dashboard.aiCustomization
    onInteractionBusyChanged: dashboard.setInteractionBusy(interactionBusy)
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

    AdvancedSettings { id: advancedSettings; controller: dashboard }

    // Settings: everything that is not needed every day. Basic things first; engine options under "고급".
    PhoneCameraDialog { id: phoneCameraDialog; controller: dashboard }

    Dialog {
        id: settingsDialog
        anchors.centerIn: parent
        width: Math.min(520, window.width - 48)
        height: Math.min(window.height - 48, 680)
        modal: true
        title: "설정"
        padding: 20
        topPadding: 8
        onClosed: dashboard.persistPointerSettings()
        property bool advanced: false
        background: Rectangle { color: "white"; radius: 14 }
        header: RowLayout {
            spacing: 8
            Item { Layout.preferredWidth: 12 }
            TextLabel { text: "설정"; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true; Layout.topMargin: 16 }
            ActionButton { text: "닫기"; Layout.topMargin: 12; Layout.rightMargin: 12; onClicked: settingsDialog.close() }
        }
        contentItem: ScrollView {
            id: settingsScroll
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: settingsScroll.availableWidth
                spacing: 10
                ActionButton { text: "휴대폰 카메라 연결"; onClicked: { settingsDialog.close(); phoneCameraDialog.open() } }
                ActionButton {
                    objectName: "refreshPcCamera"
                    text: "카메라 새로고침"
                    enabled: dashboard.pcCameraAvailable && dashboard.recMode === "off" && !dashboard.recActive
                             && !dashboard.calibratingPointer && !dashboard.palmCalibrating && !dashboard.trackingActive
                    onClicked: { settingsDialog.close(); dashboard.reconnectCamera() }
                }
                SectionLabel { text: "사용하는 손" }
                RowLayout {
                    Layout.fillWidth: true
                    TextLabel { text: dashboard.chosenHand || "정하지 않음"; Layout.fillWidth: true }
                    ActionButton {
                        text: "다시 정하기"
                        onClicked: { settingsDialog.close(); dashboard.beginHandSetup() }
                    }
                }
                ColumnLayout {
                    visible: dashboard.pointerOnly
                    Layout.fillWidth: true
                    SectionLabel { text: "커서 감도 · " + dashboard.pointerSensitivity + "%" }
                    Slider {
                        Layout.fillWidth: true
                        from: 50; to: 150; stepSize: 5
                        snapMode: Slider.SnapAlways
                        value: dashboard.pointerSensitivity
                        onMoved: dashboard.setPointerSensitivity(Math.round(value))
                    }
                }
                SectionLabel { text: "화면 표시" }
                RowLayout {
                    Layout.fillWidth: true
                    TextLabel { text: "커서 옆 손가락 표시"; Layout.fillWidth: true }
                    Segmented {
                        Layout.fillWidth: false
                        Layout.preferredWidth: 160
                        options: [["off", "끔"], ["on", "켬"]]
                        current: dashboard.fingerOverlay ? "on" : "off"
                        onPicked: function (value) { dashboard.setFingerOverlay(value === "on") }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    TextLabel { text: "화면 구석 상태 표시"; Layout.fillWidth: true }
                    Segmented {
                        Layout.fillWidth: false
                        Layout.preferredWidth: 160
                        options: [["off", "끔"], ["on", "켬"]]
                        current: dashboard.overlayVisible ? "on" : "off"
                        onPicked: function (value) { dashboard.setOverlayVisible(value === "on") }
                    }
                }
                Divider {}
                SectionLabel { text: "어디서든 쓰는 단축키" }
                Repeater {
                    model: [["toggle", "켜기 / 끄기"], ["hide", "창 숨기기 / 보이기"]]
                    delegate: RowLayout {
                        required property var modelData
                        readonly property string shortcutAction: modelData[0]   // not "action": Button has one
                        Layout.fillWidth: true
                        TextLabel { text: modelData[1]; Layout.preferredWidth: 120 }
                        ActionButton {
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
                    text: "다른 프로그램을 쓰는 중에도 동작합니다. MANUX 창 안에서는 Space로도 켜고 끕니다."
                }
                Divider {}
                SectionLabel { text: "문제가 있었나요?" }
                TextLabel {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    color: window.muted
                    font.pixelSize: 11
                    text: dashboard.failureSummary
                }
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { Layout.fillWidth: true; text: "방금 이상했어요 (F)"; onClicked: dashboard.markFailure() }
                    ActionButton { Layout.fillWidth: true; text: "로그 묶어 내보내기"; onClicked: dashboard.exportLogs() }
                }
                TextLabel {
                    visible: dashboard.exportText !== ""
                    Layout.fillWidth: true
                    text: dashboard.exportText + "\n성능·실패 기록과 설정만 담깁니다. 영상, 창 제목, 프로필 이름은 넣지 않습니다."
                    color: window.muted
                    wrapMode: Text.Wrap
                    font.pixelSize: 11
                }
                Divider {}
                ActionButton {
                    Layout.fillWidth: true
                    text: "모든 고급 설정"
                    onClicked: { settingsDialog.close(); advancedSettings.open() }
                }
                ColumnLayout {
                    visible: settingsDialog.advanced
                    Layout.fillWidth: true
                    spacing: 8
                    TextLabel {
                        Layout.fillWidth: true
                        text: "고급 설정을 바꾸면 입력이 멈춥니다. 다시 켜세요."
                        color: window.muted
                        wrapMode: Text.Wrap
                        font.pixelSize: 11
                    }
                    SectionLabel { text: "이동 방식" }
                    Segmented {
                        enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                        options: [["palm", "손바닥 (기본)"], ["index", "검지 포인터 (시험)"]]
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
                        SectionLabel { text: "손 주변 확대 (시험)" }
                        Segmented {
                            enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                            options: [["off", "끔"], ["on", "켬"]]
                            current: dashboard.handCrop ? "on" : "off"
                            onPicked: function (value) { dashboard.setHandCrop(value === "on") }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            ActionButton {
                                Layout.fillWidth: true
                                enabled: !dashboard.calibratingPointer
                                text: dashboard.pointerCalibrated ? "방향 보정 다시" : "방향 보정"
                                onClicked: { settingsDialog.close(); dashboard.beginPointerCalibration() }
                            }
                            ActionButton {
                                visible: dashboard.pointerCalibrated && !dashboard.calibratingPointer
                                text: "초기화"
                                onClicked: dashboard.resetPointerCalibration()
                            }
                        }
                        SectionLabel { text: "트래킹 측정" }
                        ActionButton {
                            Layout.fillWidth: true
                            enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                            text: "측정 시작 (클릭 없음)"
                            onClicked: { settingsDialog.close(); dashboard.beginTrackingTest() }
                        }
                        Repeater {
                            model: dashboard.recentResults
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
                    }
                    ActionButton {
                        Layout.fillWidth: true
                        text: "엔진 세부 설정"
                        onClicked: { settingsDialog.close(); dashboard.command(111) }
                    }
                }
            }
        }
    }

    Shortcut { sequence: "Return"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() }
    Shortcut { sequence: "Enter"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() }
    Shortcut { sequence: "Esc"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive; onActivated: dashboard.command(27) }
    Shortcut { sequence: "Space"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && (!settingsDialog.visible) && !newModelField.activeFocus; onActivated: dashboard.command(32) }
    Shortcut { sequence: "Q"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(81) }
    Shortcut { sequence: "O"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(111) }
    Shortcut { sequence: "M"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(109) }
    Shortcut { sequence: "N"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(110) }
    Shortcut { sequence: "F"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.markFailure() }
    Shortcut { sequence: "C"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(99) }
    Shortcut { sequence: "P"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && !dashboard.trackingActive && !newModelField.activeFocus; onActivated: dashboard.command(112) }
    Shortcut { sequence: "1"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(49) }
    Shortcut { sequence: "2"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(50) }
    Shortcut { sequence: "3"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(51) }
    Shortcut { sequence: "4"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(52) }
    Shortcut { sequence: "5"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(53) }
    Shortcut { sequence: "6"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(54) }
    Shortcut { sequence: "7"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(55) }
    Shortcut { sequence: "8"; enabled: !window.learningDialogOpen && !phoneCameraDialog.visible && dashboard.currentPage === "studio" && !newModelField.activeFocus; onActivated: dashboard.command(56) }

    Timer { interval: 700; running: dashboard.stage === "splash"; onTriggered: dashboard.finishSplash() }

    Rectangle {
        anchors.fill: parent
        visible: dashboard.stage !== "main" && dashboard.stage !== "tutorial"
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
                Image { anchors.fill: parent; source: dashboard.previewUrl; cache: false; fillMode: Image.PreserveAspectFit
                    HandSkeleton { anchors.fill: parent; bridge: dashboard; imageWidth: parent.paintedWidth; imageHeight: parent.paintedHeight }
                }
            }
            TextLabel {
                visible: dashboard.stage === "capture"
                Layout.alignment: Qt.AlignHCenter
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: dashboard.tracking === "카메라 없음" ? dashboard.currentAction
                    : dashboard.handCandidate ? dashboard.handCandidate + " 감지됨 · " + dashboard.secondsRemaining + "초 남음" : "한 손을 기다리는 중"
                color: dashboard.handCandidate ? "#08775E" : window.muted
                font.bold: true
            }
            RowLayout {
                visible: dashboard.stage === "capture"
                Layout.alignment: Qt.AlignHCenter
                ActionButton {
                    objectName: "refreshSetupCamera"
                    text: "카메라 새로고침"
                    enabled: dashboard.pcCameraAvailable
                    onClicked: dashboard.reconnectCamera()
                }
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

    TutorialPage {
        anchors.fill: parent
        visible: dashboard.stage === "tutorial" && !dashboard.pointerSetupActive
        controller: dashboard
    }
    PointerSetupPage {
        anchors.fill: parent
        visible: dashboard.stage === "tutorial" && dashboard.pointerSetupActive
        controller: dashboard
    }

    // Main screen (2026-11-04): on/off, what the camera sees, and what the hand can do. Nothing else;
    // options live in the settings dialog.
    ColumnLayout {
        visible: dashboard.stage === "main"
        anchors.fill: parent
        spacing: 0
        Rectangle {
            color: "white"
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                spacing: 12
                Image {
                    // the official horizontal logo: mark + MANUX wordmark
                    source: "assets/manux.svg"
                    Layout.preferredHeight: 34
                    Layout.preferredWidth: 34 * 1552 / 494
                    sourceSize.height: 68
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                }
                Item { Layout.fillWidth: true }
                ActionButton { text: "고급 설정"; onClicked: { dashboard.command(27); advancedSettings.open() } }
                ActionButton { text: "학습 센터"; onClicked: trainingCenter.open() }
                ActionButton { objectName: "openAiCustomization"; text: "동작 추가 (AI)"; onClicked: aiCustomization.open() }
                TextLabel { text: dashboard.chosenHand; color: window.muted; font.pixelSize: 13 }
                ActionButton {
                    text: dashboard.controlLabel === "일시정지" ? "끄기" : "켜기"
                    accent: dashboard.controlLabel !== "일시정지"
                    Layout.preferredWidth: 96
                    onClicked: dashboard.command(32)
                }
            }
        }
        QuickSpeedControls { Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; controller: dashboard }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            Rectangle {
                Layout.preferredWidth: 200
                Layout.fillHeight: true
                color: "white"
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: window.line }
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 4
                    Repeater {
                        model: [["home", "홈"], ["profiles", "프로필"], ["customize", "맞춤 설정"]]
                        delegate: Button {
                            id: navItem
                            required property var modelData
                            readonly property bool selected: dashboard.currentPage === modelData[0]
                            Layout.fillWidth: true
                            implicitHeight: 42
                            onClicked: dashboard.setPage(modelData[0])
                            contentItem: TextLabel {
                                text: navItem.modelData[1]
                                font.bold: navItem.selected
                                leftPadding: 8
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 10
                                color: navItem.selected ? "#E3F8F1" : navItem.down ? "#DDE7EC" : "transparent"
                                border.color: navItem.activeFocus ? "#16846E" : "transparent"
                                border.width: 2
                            }
                        }
                    }
                    Item { Layout.fillHeight: true }
                    Button {
                        id: trainingNav
                        Layout.fillWidth: true
                        implicitHeight: 42
                        enabled: !dashboard.calibratingPointer
                        onClicked: trainingCenter.open()
                        contentItem: TextLabel { text: "학습 센터"; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle {
                            radius: 10
                            color: trainingNav.down ? "#DDE7EC" : "transparent"
                            border.color: trainingNav.activeFocus ? "#16846E" : "transparent"
                            border.width: 2
                        }
                    }
                    Button {
                        id: settingsNav
                        Layout.fillWidth: true
                        implicitHeight: 42
                        enabled: !dashboard.calibratingPointer
                        onClicked: { dashboard.command(27); settingsDialog.open() }
                        contentItem: TextLabel { text: "설정"; leftPadding: 8; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle {
                            radius: 10
                            color: settingsNav.down ? "#DDE7EC" : "transparent"
                            border.color: settingsNav.activeFocus ? "#16846E" : "transparent"
                            border.width: 2
                        }
                    }
                }
            }
        ScrollView {
            id: mainScroll
            visible: dashboard.currentPage === "home"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: Math.min(mainScroll.availableWidth - 48, 880)
                x: (mainScroll.availableWidth - width) / 2
                spacing: 16
                Item { Layout.preferredHeight: 4 }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: statusColumn.implicitHeight + 32
                    radius: 16
                    color: "white"
                    border.color: window.line
                    ColumnLayout {
                        id: statusColumn
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 12
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 16
                            Rectangle {
                                id: previewBox
                                property bool large: false
                                readonly property bool expanded: large || dashboard.recActive
                                Layout.preferredWidth: expanded ? statusColumn.width : 240
                                Layout.preferredHeight: expanded ? statusColumn.width * 9 / 16 : 135
                                radius: 10
                                color: window.navy
                                clip: true
                                Image {
                                    id: capturePreview
                                    anchors.fill: parent
                                    source: dashboard.previewUrl
                                    cache: false
                                    fillMode: Image.PreserveAspectFit

                    HandSkeleton { anchors.fill: parent; bridge: dashboard; imageWidth: parent.paintedWidth; imageHeight: parent.paintedHeight }
                }
                                Item {
                                    visible: dashboard.recMode === "guided" && dashboard.recCondition !== ""
                                    x: (parent.width - capturePreview.paintedWidth) / 2
                                    y: (parent.height - capturePreview.paintedHeight) / 2
                                    width: capturePreview.paintedWidth
                                    height: capturePreview.paintedHeight
                                    Repeater {
                                        model: ["왼쪽 위", "위", "오른쪽 위", "왼쪽", "중앙", "오른쪽", "왼쪽 아래", "아래", "오른쪽 아래"]
                                        delegate: Rectangle {
                                            required property int index
                                            required property string modelData
                                            x: (index % 3) * width; y: Math.floor(index / 3) * height
                                            width: parent.width / 3; height: parent.height / 3
                                            color: modelData === dashboard.recPosition ? "#2046E5BD" : "transparent"
                                            border.width: modelData === dashboard.recPosition ? 3 : 1
                                            border.color: modelData === dashboard.recPosition ? "#46E5BD" : "#60FFFFFF"
                                            Label { visible: modelData === dashboard.recPosition; text: "위치 안내 · " + modelData; color: "white"; padding: 8; font.bold: true }
                                        }
                                    }
                                }
                                TextLabel {
                                    anchors.right: parent.right
                                    anchors.bottom: parent.bottom
                                    anchors.margins: 6
                                    text: previewBox.expanded ? "작게" : "크게"
                                    color: "#DDE7EC"
                                    font.pixelSize: 11
                                }
                                MouseArea { anchors.fill: parent; onClicked: previewBox.large = !previewBox.large }
                            }
                            ColumnLayout {
                                visible: !previewBox.expanded
                                Layout.fillWidth: true
                                spacing: 6
                                TextLabel {
                                    text: dashboard.controlLabel === "일시정지" ? "켜짐" : "꺼짐"
                                    font.pixelSize: 26
                                    font.bold: true
                                    color: dashboard.controlLabel === "일시정지" ? "#08775E" : window.navy
                                }
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: dashboard.currentAction
                                    color: window.muted
                                    font.pixelSize: 15
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    spacing: 6
                                    Chip { text: dashboard.tracking }
                                    Chip {
                                        visible: dashboard.lowLight
                                        warn: true
                                        text: "어두움 " + Math.round(dashboard.brightness / 2.55) + "%"
                                    }
                                    Chip { visible: dashboard.motionBadgeOn; warn: true; text: "모션 모드" }
                                }
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: dashboard.mlpBadge
                                    color: dashboard.mlpBadge.indexOf("적용 중") >= 0 ? "#08775E" : window.muted
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                }
                                TextLabel {
                                    text: "Space 또는 어디서든 " + (dashboard.shortcutLabels["toggle"] || "") + " 로 켜고 끕니다"
                                    color: window.muted
                                    font.pixelSize: 11
                                }
                            }
                        }
                        Rectangle {
                            visible: dashboard.lowLight
                            Layout.fillWidth: true
                            implicitHeight: lowLightText.implicitHeight + 16
                            radius: 8
                            color: "#FFF1D6"
                            TextLabel {
                                id: lowLightText
                                anchors.fill: parent
                                anchors.margins: 8
                                text: "어두워요. 조명을 켜거나 모니터를 밝게 하고, 손을 카메라에 조금 더 가까이 두세요."
                                wrapMode: Text.Wrap
                                font.pixelSize: 12
                                color: "#8A5A00"
                            }
                        }
                        RowLayout {
                            visible: dashboard.calibratingPointer
                            Layout.fillWidth: true
                            TextLabel { Layout.fillWidth: true; text: dashboard.calibrationHint; wrapMode: Text.Wrap }
                            ActionButton { text: "취소"; onClicked: dashboard.cancelPointerCalibration() }
                        }
                        RowLayout {
                            visible: !dashboard.pointerOnly && !dashboard.calibratingPointer
                            Layout.fillWidth: true
                            spacing: 12
                            TextLabel { text: "학습 데이터 녹화"; font.bold: true; color: window.muted; font.pixelSize: 12 }
                            Segmented {
                                Layout.fillWidth: false
                                Layout.preferredWidth: 260
                                enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                                options: [["off", "끔"], ["guided", "안내 녹화"], ["free", "평소 녹화"]]
                                current: dashboard.recMode
                                onPicked: function (value) {
                                    if (value === "guided") {
                                        if (dashboard.focusRecord("")) capturePlan.open()
                                    } else {
                                        dashboard.setRecMode(value)
                                    }
                                }
                            }
                            Item { Layout.fillWidth: true }
                            ActionButton { text: "학습 센터 열기"; onClicked: trainingCenter.open() }
                        }
                        RowLayout {
                            visible: dashboard.recActive
                            Layout.fillWidth: true
                            spacing: 10
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                TextLabel {
                                    Layout.fillWidth: true
                                    visible: dashboard.recCondition !== ""
                                    text: dashboard.recCondition
                                    font.pixelSize: 12
                                    color: "#16846E"
                                    wrapMode: Text.Wrap
                                }
                                TextLabel {
                                    Layout.fillWidth: true
                                    visible: dashboard.recPrompt !== ""
                                    text: dashboard.recPrompt
                                    font.pixelSize: 22
                                    font.bold: true
                                    wrapMode: Text.Wrap
                                }
                                Rectangle {
                                    visible: dashboard.recMode === "guided" && dashboard.recPrompt !== ""
                                    Layout.fillWidth: true
                                    height: 6
                                    radius: 3
                                    color: "#E3E9EE"
                                    Rectangle {
                                        width: parent.width * Math.max(0, Math.min(1, dashboard.recProgress))
                                        height: parent.height
                                        radius: 3
                                        color: "#45E0B4"
                                    }
                                }
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: dashboard.recSub
                                    font.pixelSize: 12
                                    color: window.muted
                                    wrapMode: Text.Wrap
                                }
                            }
                            ActionButton { visible: dashboard.recWaiting; text: "시작"; accent: true; onClicked: dashboard.recStart() }
                            ActionButton { visible: dashboard.recMode === "guided" && !dashboard.recWaiting; text: dashboard.recPaused ? "계속" : "일시정지"; onClicked: dashboard.toggleRecordingPause() }
                            ActionButton { visible: dashboard.recMode !== "off"; text: "녹화 끝"; onClicked: dashboard.setRecMode("off") }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    TextLabel { text: "쓸 수 있는 동작"; font.pixelSize: 18; font.bold: true }
                    Item { Layout.fillWidth: true }
                    ActionButton {
                        visible: dashboard.pointerSetupAvailable && !dashboard.pointerOnly
                        enabled: dashboard.recMode === "off" && !dashboard.trackingActive
                        text: "커서 자동 설정"
                        onClicked: dashboard.beginPointerSetup()
                    }
                    ActionButton {
                        visible: dashboard.tutorialAvailable
                        text: "동작 다시 배우기"
                        onClicked: dashboard.beginTutorial()
                    }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: width > 640 ? 2 : 1
                    columnSpacing: 10
                    rowSpacing: 10
                    Repeater {
                        model: dashboard.gestureCards
                        delegate: Rectangle {
                            required property var modelData
                            readonly property bool live: dashboard.liveGesture === modelData[0]
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            implicitHeight: cardText.implicitHeight + 24
                            radius: 12
                            color: live ? "#E3F8F1" : "white"
                            border.color: live ? window.mint : window.line
                            border.width: live ? 2 : 1
                            ColumnLayout {
                                id: cardText
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 4
                                TextLabel { text: modelData[1]; font.bold: true; font.pixelSize: 15 }
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: modelData[2]
                                    color: window.muted
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    TextLabel {
                        id: diagnostics
                        visible: false
                        onVisibleChanged: dashboard.showFingertips(visible)
                        Layout.fillWidth: true
                        text: dashboard.details + (dashboard.cameraMode ? "  ·  카메라 " + dashboard.cameraMode : "")
                        color: window.muted
                        wrapMode: Text.Wrap
                        font.pixelSize: 11
                    }
                    Item { visible: !diagnostics.visible; Layout.fillWidth: true }
                    ActionButton { text: diagnostics.visible ? "상세 닫기" : "상세 정보"; onClicked: diagnostics.visible = !diagnostics.visible }
                }
                Item { Layout.preferredHeight: 8 }
            }
        }

            // Pose Snapshot Studio: park-style continuous frame stream collection + 4-second Spatial MLP training
            ScrollView {
                id: studioScroll
                visible: dashboard.currentPage === "studio"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: Math.min(studioScroll.availableWidth - 48, 880)
                    x: (studioScroll.availableWidth - width) / 2
                    spacing: 14
                    Item { Layout.preferredHeight: 4 }

                    // Header & Mode Switcher
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            TextLabel {
                                text: "⚡ 포즈 스냅샷 & 영상 프레임 스튜디오 (Spatial MLP)"
                                font.pixelSize: 20
                                font.bold: true
                            }
                            TextLabel {
                                Layout.fillWidth: true
                                text: "복잡한 카운트다운 없이 숫자키(1~8)로 동작을 고르고, Space 하나로 영상 프레임을 초당 20장씩 최대 1,000개 수집·즉시 학습합니다."
                                color: window.muted
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                            }
                        }
                        Segmented {
                            Layout.fillWidth: false
                            Layout.preferredWidth: 270
                            options: [["stream", "🎥 영상 연속 수집 (스트림)"], ["snapshot", "📷 단일 캡처 (스냅샷)"]]
                            current: dashboard.studioCaptureMode
                            onPicked: function (value) { dashboard.setStudioCaptureMode(value) }
                        }
                    }

                    // Camera Preview + Live 3-Way Judgment Comparison Banner
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: studioLiveCol.implicitHeight + 28
                        radius: 16
                        color: "white"
                        border.color: dashboard.studioStreaming ? "#10B981" : window.line
                        border.width: dashboard.studioStreaming ? 2 : 1
                        ColumnLayout {
                            id: studioLiveCol
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 12

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 16

                                // Live Webcam Box
                                Rectangle {
                                    Layout.preferredWidth: 260
                                    Layout.preferredHeight: 146
                                    radius: 10
                                    color: window.navy
                                    clip: true
                                    Image {
                                        anchors.fill: parent
                                        source: dashboard.previewUrl
                                        cache: false
                                        fillMode: Image.PreserveAspectFit

                    HandSkeleton { anchors.fill: parent; bridge: dashboard; imageWidth: parent.paintedWidth; imageHeight: parent.paintedHeight }
                }
                                    Rectangle {
                                        visible: dashboard.studioStreaming
                                        anchors.top: parent.top
                                        anchors.left: parent.left
                                        anchors.margins: 8
                                        width: recBadgeText.implicitWidth + 16
                                        height: 24
                                        radius: 12
                                        color: "#DC2626"
                                        TextLabel {
                                            id: recBadgeText
                                            anchors.centerIn: parent
                                            text: "● REC " + dashboard.studioTotalSamples + "/" + dashboard.studioMaxSamples
                                            color: "white"
                                            font.pixelSize: 11
                                            font.bold: true
                                        }
                                    }
                                }

                                // Live 3-Way Comparison (System Rule vs Spatial MLP AI vs Ground Truth)
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 8

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Chip {
                                            text: dashboard.studioHandTracked ? "✋ 21관절 추적 정상" : "⚠️ 손 대기 중"
                                            warn: !dashboard.studioHandTracked
                                        }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 28
                                            radius: 14
                                            color: !dashboard.studioHandTracked ? "#F0F3F6"
                                                 : dashboard.studioLiveMatch ? "#E8F7F2" : "#FEE2E2"
                                            border.color: !dashboard.studioHandTracked ? "transparent"
                                                        : dashboard.studioLiveMatch ? "#10B981" : "#EF4444"
                                            TextLabel {
                                                anchors.centerIn: parent
                                                text: dashboard.studioCompareBadge
                                                font.pixelSize: 12
                                                font.bold: true
                                                color: !dashboard.studioHandTracked ? window.muted
                                                     : dashboard.studioLiveMatch ? "#065F46" : "#991B1B"
                                            }
                                        }
                                    }

                                    // 3 Columns: Rule | AI | Ground Truth
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredWidth: 1
                                            implicitHeight: 54
                                            radius: 10
                                            color: "#F8FAFC"
                                            border.color: window.line
                                            ColumnLayout {
                                                anchors.fill: parent
                                                anchors.margins: 8
                                                spacing: 2
                                                TextLabel { text: "시스템 판정"; font.pixelSize: 11; color: window.muted }
                                                TextLabel { text: dashboard.studioSystemAction; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                            }
                                        }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredWidth: 1
                                            implicitHeight: 54
                                            radius: 10
                                            color: dashboard.studioModelLoaded ? "#F0FDF4" : "#F8FAFC"
                                            border.color: dashboard.studioModelLoaded ? "#86EFAC" : window.line
                                            ColumnLayout {
                                                anchors.fill: parent
                                                anchors.margins: 8
                                                spacing: 2
                                                TextLabel { text: "Spatial MLP (AI 예측)"; font.pixelSize: 11; color: window.muted }
                                                TextLabel { text: dashboard.studioAiAction; font.pixelSize: 14; font.bold: true; color: dashboard.studioModelLoaded ? "#065F46" : window.muted; elide: Text.ElideRight; Layout.fillWidth: true }
                                            }
                                        }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredWidth: 1
                                            implicitHeight: 54
                                            radius: 10
                                            color: "#E3F8F1"
                                            border.color: window.mint
                                            border.width: 2
                                            ColumnLayout {
                                                anchors.fill: parent
                                                anchors.margins: 8
                                                spacing: 2
                                                TextLabel { text: "지정 정답 (Ground Truth)"; font.pixelSize: 11; color: "#08775E"; font.bold: true }
                                                TextLabel { text: dashboard.studioGroundTruthLabel; font.pixelSize: 14; font.bold: true; color: window.navy; elide: Text.ElideRight; Layout.fillWidth: true }
                                            }
                                        }
                                    }

                                    // Collection Filters: Discrepancy-only & Full-joints-only
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 6

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            CheckBox {
                                                id: discrepancyCheck
                                                checked: dashboard.studioDiscrepancyOnly
                                                onToggled: dashboard.setStudioDiscrepancyOnly(checked)
                                            }
                                            TextLabel {
                                                Layout.fillWidth: true
                                                text: "🚨 불일치(오인식 사각지대) 프레임만 자동 선별 수집 — 손을 이리저리 돌릴 때 틀리는 각도만 쏙쏙 골라 담습니다"
                                                font.pixelSize: 12
                                                font.bold: dashboard.studioDiscrepancyOnly
                                                color: dashboard.studioDiscrepancyOnly ? "#B91C1C" : window.navy
                                                wrapMode: Text.Wrap
                                                MouseArea {
                                                    anchors.fill: parent
                                                    onClicked: dashboard.setStudioDiscrepancyOnly(!dashboard.studioDiscrepancyOnly)
                                                }
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            CheckBox {
                                                id: fullJointsCheck
                                                checked: dashboard.studioRequireFullJoints
                                                onToggled: dashboard.setStudioRequireFullJoints(checked)
                                            }
                                            TextLabel {
                                                Layout.fillWidth: true
                                                text: "✋ 21개 관절 완전 인식 시에만 수집 — 손가락이 화면 가장자리에 잘리거나 가려진 불량 프레임 저장을 방지합니다"
                                                font.pixelSize: 12
                                                font.bold: dashboard.studioRequireFullJoints
                                                color: dashboard.studioRequireFullJoints ? "#065F46" : window.navy
                                                wrapMode: Text.Wrap
                                                MouseArea {
                                                    anchors.fill: parent
                                                    onClicked: dashboard.setStudioRequireFullJoints(!dashboard.studioRequireFullJoints)
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // Progress Bar for 1,000 samples
                            Rectangle {
                                Layout.fillWidth: true
                                height: 8
                                radius: 4
                                color: "#E2E8F0"
                                Rectangle {
                                    width: parent.width * Math.min(1.0, dashboard.studioTotalSamples / Math.max(1, dashboard.studioMaxSamples))
                                    height: parent.height
                                    radius: 4
                                    color: dashboard.studioStreaming ? "#10B981" : window.mint
                                }
                            }

                            // Action Toolbar: Start/Stop Stream, Undo, Clear, Save, Instant 4s Train
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                ActionButton {
                                    Layout.fillWidth: true
                                    accent: true
                                    text: dashboard.studioCaptureMode === "stream"
                                        ? (dashboard.studioStreaming ? "⏹️ 수집 정지 (Space)" : "▶️ 연속 수집 시작 (Space)")
                                        : "📸 1프레임 캡처 (Space)"
                                    onClicked: dashboard.triggerStudioCapture()
                                }
                                ActionButton {
                                    text: "↩️ 직전 취소"
                                    enabled: dashboard.studioTotalSamples > 0
                                    onClicked: dashboard.undoStudioSample()
                                }
                                ActionButton {
                                    text: "🗑️ 비우기"
                                    enabled: dashboard.studioTotalSamples > 0
                                    onClicked: dashboard.clearStudioSamples()
                                }
                                ActionButton {
                                    text: "💾 데이터셋 저장 (" + dashboard.studioTotalSamples + ")"
                                    enabled: dashboard.studioTotalSamples > 0
                                    onClicked: dashboard.saveStudioDataset()
                                }
                                ActionButton {
                                    accent: true
                                    text: "⚡ 즉시 학습 (0.2초)"
                                    onClicked: dashboard.trainStudioModel()
                                }
                            }

                            // Status Note & Summary Bar
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: dashboard.studioNote
                                    font.pixelSize: 12
                                    font.bold: true
                                    color: "#08775E"
                                    wrapMode: Text.Wrap
                                }
                                ActionButton {
                                    implicitHeight: 30
                                    text: "📂 데이터 폴더"
                                    onClicked: dashboard.openStudioSnapshotsFolder()
                                }
                            }
                        }
                    }

                    // 3 Category Tabs (As in user's diagram: Green highlight for active tab, cards side-by-side)
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Repeater {
                            model: dashboard.studioCategories
                            delegate: Rectangle {
                                required property var modelData
                                readonly property bool isSelected: modelData.selected
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                implicitHeight: 56
                                radius: 10
                                color: isSelected ? "#059669" : "white"
                                border.color: isSelected ? "#047857" : window.line
                                border.width: isSelected ? 2 : 1

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 14
                                    anchors.rightMargin: 14
                                    spacing: 10
                                    TextLabel {
                                        text: modelData.icon
                                        font.pixelSize: 20
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        TextLabel {
                                            text: modelData.name
                                            font.bold: true
                                            font.pixelSize: 14
                                            color: isSelected ? "white" : window.navy
                                        }
                                        TextLabel {
                                            text: modelData.desc
                                            font.pixelSize: 11
                                            color: isSelected ? "#D1FAE5" : window.muted
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                    Rectangle {
                                        radius: 10
                                        color: isSelected ? "#047857" : (modelData.totalCount > 0 ? "#E0F2FE" : "#F3F4F6")
                                        implicitHeight: 22
                                        implicitWidth: catBadgeText.implicitWidth + 12
                                        TextLabel {
                                            id: catBadgeText
                                            anchors.centerIn: parent
                                            text: modelData.totalCount + "개"
                                            font.bold: true
                                            font.pixelSize: 11
                                            color: isSelected ? "white" : window.navy
                                        }
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: dashboard.setStudioCategory(modelData.id)
                                }
                            }
                        }
                    }

                    // Selected Category Header & Keyboard Shortcut Info
                    RowLayout {
                        Layout.fillWidth: true
                        TextLabel {
                            text: "수집할 정답 동작 선택 (단축키 1 ~ " + dashboard.studioCategoryActions.length + " / Tab: 탭 전환)"
                            font.pixelSize: 16
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        TextLabel {
                            text: dashboard.studioSummaryText
                            font.pixelSize: 12
                            color: window.muted
                        }
                    }

                    // 2-Column Action Cards Grid (Selected Category Actions)
                    GridLayout {
                        Layout.fillWidth: true
                        columns: width > 640 ? 2 : 1
                        columnSpacing: 10
                        rowSpacing: 10
                        Repeater {
                            model: dashboard.studioCategoryActions
                            delegate: Rectangle {
                                required property var modelData
                                readonly property bool chosen: modelData.selected
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                implicitHeight: actionCardCol.implicitHeight + 20
                                radius: 12
                                color: chosen ? "#E3F8F1" : "white"
                                border.color: chosen ? window.mint : window.line
                                border.width: chosen ? 2 : 1
                                ColumnLayout {
                                    id: actionCardCol
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        TextLabel {
                                            text: modelData.icon + "  " + modelData.label
                                            font.bold: true
                                            font.pixelSize: 14
                                            Layout.fillWidth: true
                                        }
                                        Chip {
                                            visible: modelData.accuracyText !== ""
                                            text: "AI " + modelData.accuracyText
                                        }
                                        Chip {
                                            text: "이번 " + modelData.sessionCount + " · 누적 " + modelData.totalCount
                                            warn: modelData.totalCount === 0
                                        }
                                    }
                                    TextLabel {
                                        Layout.fillWidth: true
                                        text: modelData.desc
                                        color: window.muted
                                        font.pixelSize: 12
                                        wrapMode: Text.Wrap
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: dashboard.setStudioGroundTruth(modelData.id)
                                }
                            }
                        }
                        // Informational Companion Card for single-item category (e.g. guard) to balance 2-column grid
                        Rectangle {
                            visible: dashboard.studioCategoryActions.length === 1
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            implicitHeight: guardCol.implicitHeight + 20
                            radius: 12
                            color: "#F8FAFC"
                            border.color: window.line
                            border.width: 1
                            ColumnLayout {
                                id: guardCol
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 4
                                RowLayout {
                                    Layout.fillWidth: true
                                    TextLabel {
                                        text: "🛡️  0ms 하이브리드 오동작 방어 가드"
                                        font.bold: true
                                        font.pixelSize: 14
                                        color: window.navy
                                    }
                                }
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: "Spatial MLP가 마우스 클릭이나 제어 스위치가 아닌 모든 손 동작 및 휴식 상태를 0.0001초 만에 즉시 감지하여 헛클릭을 원천 차단합니다."
                                    color: window.muted
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                    }

                    // Bottom Model Management Center (Multiple Spatial MLP Models)
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: modelManagerCol.implicitHeight + 24
                        radius: 12
                        color: "white"
                        border.color: window.line

                        ColumnLayout {
                            id: modelManagerCol
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 12

                            // Header Row
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                TextLabel {
                                    text: "🤖 Spatial MLP 모델 관리 (다중 모델)"
                                    font.pixelSize: 15
                                    font.bold: true
                                    color: window.navy
                                }
                                Item { Layout.fillWidth: true }
                                ActionButton {
                                    text: "➕ 새 모델 추가"
                                    accent: true
                                    implicitHeight: 32
                                    onClicked: {
                                        newModelInputRow.visible = !newModelInputRow.visible
                                        if (newModelInputRow.visible) {
                                            newModelField.forceActiveFocus()
                                        }
                                    }
                                }
                                ActionButton {
                                    text: "기존 학습 센터 (TCN)"
                                    implicitHeight: 32
                                    onClicked: trainingCenter.open()
                                }
                            }

                            // Inline New Model Input
                            RowLayout {
                                id: newModelInputRow
                                visible: false
                                Layout.fillWidth: true
                                spacing: 8
                                TextField {
                                    id: newModelField
                                    Layout.fillWidth: true
                                    implicitHeight: 36
                                    placeholderText: "새 모델 이름 입력 (비워두면 '새_모델_N'으로 자동 생성)"
                                    onAccepted: {
                                        dashboard.createStudioModel(newModelField.text.trim())
                                        newModelField.text = ""
                                        newModelInputRow.visible = false
                                    }
                                }
                                ActionButton {
                                    text: "생성 및 선택"
                                    accent: true
                                    implicitHeight: 36
                                    onClicked: {
                                        dashboard.createStudioModel(newModelField.text.trim())
                                        newModelField.text = ""
                                        newModelInputRow.visible = false
                                    }
                                }
                                ActionButton {
                                    text: "취소"
                                    implicitHeight: 36
                                    onClicked: newModelInputRow.visible = false
                                }
                            }

                            // Model List Cards
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Repeater {
                                    model: dashboard.studioModelList
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        implicitHeight: 56
                                        radius: 10
                                        color: modelData.active ? "#F0FDF4" : "#F8FAFC"
                                        border.color: modelData.active ? "#86EFAC" : window.line
                                        border.width: modelData.active ? 2 : 1

                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.margins: 12
                                            spacing: 12

                                            // Status Bullet
                                            Rectangle {
                                                implicitWidth: 10
                                                implicitHeight: 10
                                                radius: 5
                                                color: modelData.active ? "#10B981" : "#94A3B8"
                                            }

                                            // Model Title & Details
                                            ColumnLayout {
                                                spacing: 2
                                                TextLabel {
                                                    text: modelData.name + (modelData.active ? (modelData.exists ? "  [✅ 현재 엔진 적용 중]" : "  [📌 현재 학습 대상 슬롯]") : "")
                                                    font.pixelSize: 13
                                                    font.bold: true
                                                    color: modelData.active ? "#065F46" : window.navy
                                                }
                                                TextLabel {
                                                    text: modelData.exists
                                                        ? ("학습 데이터 내부 확인(실제 정확도 아님) " + modelData.test_accuracy.toFixed(1) + "% · 표본 " + modelData.total_samples + "개 · " + modelData.trained_at + " · " + modelData.file_size_kb + "KB")
                                                        : "미학습 슬롯 — 상단에서 표본 수집 후 [⚡ 즉시 학습]을 누르면 이 모델로 저장됩니다"
                                                    font.pixelSize: 11
                                                    color: window.muted
                                                }
                                            }

                                            Item { Layout.fillWidth: true }

                                            // Action Buttons
                                            ActionButton {
                                                text: modelData.exists ? "적용" : "선택"
                                                implicitHeight: 30
                                                visible: !modelData.active
                                                accent: true
                                                onClicked: dashboard.activateStudioModel(modelData.id)
                                            }
                                            ActionButton {
                                                text: "삭제"
                                                implicitHeight: 30
                                                visible: dashboard.studioModelList.length > 1 || modelData.exists
                                                onClicked: dashboard.deleteStudioModel(modelData.id)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Item { Layout.preferredHeight: 12 }
                }
            }

            // Profiles: which calibration profile the engine uses
            ScrollView {
                id: profilesScroll
                visible: dashboard.currentPage === "profiles"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: Math.min(profilesScroll.availableWidth - 48, 720)
                    x: 24
                    spacing: 12
                    Item { Layout.preferredHeight: 8 }
                    TextLabel { text: "프로필"; font.pixelSize: 22; font.bold: true }
                    TextLabel {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: window.muted
                        text: "프로필은 내 손에 맞춰 잰 값입니다. 기본값 위에 덮어써서, 같은 동작도 사람마다 잘 맞게 합니다. 바꾸면 입력이 멈추니 다시 켜세요."
                    }
                    Repeater {
                        model: dashboard.profileNames
                        delegate: Rectangle {
                            required property var modelData
                            readonly property bool chosen: dashboard.profileName === modelData[0]
                            Layout.fillWidth: true
                            implicitHeight: 52
                            radius: 12
                            color: chosen ? "#E3F8F1" : "white"
                            border.color: chosen ? window.mint : window.line
                            border.width: chosen ? 2 : 1
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 16
                                anchors.rightMargin: 16
                                TextLabel { text: modelData[1]; font.bold: chosen; Layout.fillWidth: true }
                                TextLabel { visible: chosen; text: "사용 중"; color: "#08775E"; font.pixelSize: 12 }
                            }
                            MouseArea { anchors.fill: parent; onClicked: dashboard.selectProfile(modelData[0]) }
                        }
                    }
                    TextLabel {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: window.muted
                        font.pixelSize: 12
                        text: "새 프로필 만들기(내 손에 맞추기)는 맞춤 설정 화면에 곧 추가됩니다."
                    }
                }
            }

            // Customization: what the user can change without a developer (docs/design/decision-customization.md)
            ScrollView {
                id: customizeScroll
                visible: dashboard.currentPage === "customize"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: Math.min(customizeScroll.availableWidth - 48, 720)
                    x: 24
                    spacing: 12
                    Item { Layout.preferredHeight: 8 }
                    TextLabel { text: "맞춤 설정"; font.pixelSize: 22; font.bold: true }
                    TextLabel {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: window.muted
                        text: dashboard.pointerOnly
                            ? "지금은 검지 포인터(시험)를 쓰고 있습니다. 동작 켜고 끄기는 손바닥(기본) 방식에서 쓸 수 있어요."
                            : "쓰지 않는 동작은 꺼 두면 잘못 인식될 일이 줄어듭니다. 바꾸면 입력이 멈추니 다시 켜세요."
                    }
                    SectionLabel { text: "동작 켜고 끄기" }
                    Repeater {
                        model: dashboard.pointerOnly ? [] : dashboard.switchableCards
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: switchRow.implicitHeight + 24
                            radius: 12
                            color: "white"
                            border.color: window.line
                            RowLayout {
                                id: switchRow
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    TextLabel { text: modelData[1]; font.bold: true; color: modelData[3] ? window.navy : "#A9B6C0" }
                                    TextLabel {
                                        Layout.fillWidth: true
                                        text: modelData[2]
                                        color: modelData[3] ? window.muted : "#A9B6C0"
                                        font.pixelSize: 12
                                        wrapMode: Text.Wrap
                                    }
                                }
                                Segmented {
                                    Layout.fillWidth: false
                                    Layout.preferredWidth: 140
                                    options: [["off", "끔"], ["on", "켬"]]
                                    current: modelData[3] ? "on" : "off"
                                    onPicked: function (value) { dashboard.setCardEnabled(modelData[0], value === "on") }
                                }
                            }
                        }
                    }
                    Divider {}
                    SectionLabel { text: "곧 추가됩니다" }
                    Repeater {
                        model: [["동작 바꾸기", "원하는 동작을 다섯 번 보여 주면, 그 동작으로 클릭·우클릭 등을 하게 바꿉니다."],
                                ["내 손에 맞추기", "지금 동작이 잘 안 될 때, 몇 번 해 보이면 내 손에 맞는 값으로 프로필을 만듭니다."]]
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 2
                            TextLabel { text: modelData[0]; font.bold: true; color: window.muted }
                            TextLabel { Layout.fillWidth: true; text: modelData[1]; color: window.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
                        }
                    }
                }
            }
        }
    }

    LearningCenter {
        id: trainingCenter
        controller: dashboard
        onRecordingRequested: capturePlan.open()
        onProgressRequested: trainingProgress.open()
    }

    TrainingProgressPage {
        id: trainingProgress
        controller: dashboard
        onBackRequested: trainingCenter.open()
    }
    Connections {
        target: dashboard
        function onTrainingStarted() { trainingCenter.close(); trainingProgress.open() }
    }

    RecordingPlanDialog { id: capturePlan; editor: dashboard.recordingPlan }

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

    FingerTracker { controller: dashboard }

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
                text: dashboard.trackingDone ? "Esc 닫기 · 결과는 MANUX 로그 폴더의 tracking-*.csv"
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
