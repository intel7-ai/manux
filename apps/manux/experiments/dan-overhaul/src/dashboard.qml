import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1280
    height: 860
    minimumWidth: 960
    minimumHeight: 660
    visible: true
    title: dashboard.appTitle
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
    readonly property color primaryBlue: "#1D4ED8"
    readonly property color primaryBlueDark: "#1E3A8A"
    readonly property color dangerRed: "#DC2626"
    readonly property color line: "#DEE5EA"
    font.family: "Malgun Gothic"

    component TextLabel: Label {
        color: window.navy
        font.pixelSize: 14
    }
    // accent = 선택/활성화된 상태(연두/민트색 #46E5BD), primary = 주요 실행 버튼(진파랑 #1D4ED8), 기본 = 중립 실행 버튼
    component ActionButton: Button {
        id: button
        property bool accent: false
        property bool primary: false
        property bool danger: false
        implicitHeight: 40
        leftPadding: 16
        rightPadding: 16
        contentItem: TextLabel {
            text: button.text
            font.bold: true
            color: !button.enabled ? "#9AA8B4" : ((button.primary || button.danger) ? "#FFFFFF" : window.navy)
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            color: !button.enabled ? "#E9EEF2"
                : (button.danger ? (button.down ? "#991B1B" : window.dangerRed)
                : (button.primary ? (button.down ? window.primaryBlueDark : window.primaryBlue)
                : (button.accent ? (button.down ? "#35CFA8" : window.mint)
                : (button.down ? "#BCEFE1" : "#F0F3F6"))))
            border.color: button.activeFocus ? "#16846E"
                : (button.danger ? "#B91C1C"
                : (button.primary ? "#1E40AF"
                : (button.accent ? "#16846E" : "#D5DFE6")))
            border.width: (button.accent || button.activeFocus) ? 2 : 1
        }
    }
    component CompactButton: Button {
        id: cbutton
        property bool accent: false
        property bool primary: false
        property bool danger: false
        implicitHeight: 30
        leftPadding: 10
        rightPadding: 10
        contentItem: TextLabel {
            text: cbutton.text
            font.pixelSize: 12
            font.bold: true
            color: !cbutton.enabled ? "#9AA8B4" : ((cbutton.primary || cbutton.danger) ? "#FFFFFF" : window.navy)
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 7
            color: !cbutton.enabled ? "#E9EEF2"
                : (cbutton.danger ? (cbutton.down ? "#991B1B" : window.dangerRed)
                : (cbutton.primary ? (cbutton.down ? window.primaryBlueDark : window.primaryBlue)
                : (cbutton.accent ? (cbutton.down ? "#35CFA8" : window.mint)
                : (cbutton.down ? "#BCEFE1" : "#EBF0F4"))))
            border.color: cbutton.activeFocus ? "#16846E"
                : (cbutton.danger ? "#B91C1C"
                : (cbutton.primary ? "#1E40AF"
                : (cbutton.accent ? "#16846E" : "#CBD5E1")))
            border.width: cbutton.accent ? 2 : 1
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
                primary: true
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
                TextLabel { text: dashboard.appName; font.pixelSize: 26; font.bold: true }
                TextLabel { text: "INPUT STUDIO"; font.pixelSize: 11; font.letterSpacing: 1.4; color: window.muted }
                Item { Layout.fillWidth: true }
                ActionButton { text: "학습 센터"; onClicked: trainingCenter.open() }
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
                            RowLayout {
                                visible: !dashboard.pointerOnly
                                Layout.fillWidth: true
                                spacing: 6
                                ActionButton {
                                    Layout.fillWidth: true
                                    enabled: !dashboard.palmCalibrating && !dashboard.trackingActive
                                    text: dashboard.palmCalibrated ? "커서 방향 보정 다시" : "커서 방향 보정"
                                    onClicked: dashboard.beginPalmCalibration()
                                }
                                ActionButton {
                                    visible: dashboard.palmCalibrated && !dashboard.palmCalibrating
                                    text: "초기화"
                                    onClicked: dashboard.resetPalmCalibration()
                                }
                            }
                            SectionLabel { visible: !dashboard.pointerOnly && dashboard.tcnAvailable; text: "손짓 확인 (TCN: 학습된 동작)" }
                            Segmented {
                                visible: !dashboard.pointerOnly && dashboard.tcnAvailable
                                options: [["off", "끔"], ["on", "켬"]]
                                current: dashboard.tcnGate ? "on" : "off"
                                onPicked: function (value) { dashboard.setTcnGate(value === "on") }
                            }
                            SectionLabel { visible: !dashboard.pointerOnly; text: "학습 데이터 녹화" }
                            Segmented {
                                visible: !dashboard.pointerOnly
                                enabled: !dashboard.palmCalibrating && !dashboard.trackingActive
                                options: [["off", "끔"], ["guided", "안내 녹화"], ["free", "평소 녹화"]]
                                current: dashboard.recMode
                                onPicked: function (value) {
                                    if (value === "guided") {
                                        if (dashboard.focusRecord("")) capturePlan.open()
                                    } else { dashboard.setRecMode(value) }
                                }
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
                        }
                    }
                    Divider {}
                    TextLabel {
                        Layout.fillWidth: true
                        text: "Space 시작/정지 · Esc 정지 · N 다음 모니터 · F 실패 표시"
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
                            id: capturePreview
                            anchors.fill: parent
                            source: dashboard.previewUrl
                            cache: false
                            fillMode: Image.PreserveAspectFit
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
                        ActionButton { visible: dashboard.recWaiting; text: "시작"; primary: true; onClicked: dashboard.recStart() }
                        ActionButton { visible: dashboard.recMode === "guided" && !dashboard.recWaiting; text: dashboard.recPaused ? "계속" : "일시정지"; accent: dashboard.recPaused; onClicked: dashboard.toggleRecordingPause() }
                        ActionButton { visible: dashboard.recMode !== "off"; text: "녹화 끝 (저장)"; primary: true; onClicked: dashboard.setRecMode("off") }
                        ActionButton { visible: dashboard.recMode !== "off"; text: "녹화 취소 (버리기)"; danger: true; onClicked: dashboard.discardRecording() }
                    }
                    Rectangle {
                        visible: dashboard.hasRecReview && dashboard.recMode === "off"
                        Layout.fillWidth: true
                        implicitHeight: recReviewCol.implicitHeight + 20
                        radius: 10
                        color: dashboard.recReviewStatus === "fail" ? "#FFF1F0" : (dashboard.recReviewStatus === "warning" ? "#FFF8EA" : "#EEFCF8")
                        border.color: dashboard.recReviewStatus === "fail" ? "#E5484D" : (dashboard.recReviewStatus === "warning" ? "#F2B544" : "#45E0B4")
                        ColumnLayout {
                            id: recReviewCol
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Chip {
                                    warn: dashboard.recReviewStatus !== "pass"
                                    text: dashboard.recReviewStatus === "fail" ? "품질 미달 (자동 제외됨)" : (dashboard.recReviewStatus === "warning" ? "품질 주의" : "녹화 완료")
                                }
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: "방금 끝난 녹화: " + dashboard.recReviewFile + " — 학습 데이터에 반영할까요?"
                                    font.bold: true
                                    font.pixelSize: 13
                                    wrapMode: Text.Wrap
                                }
                            }
                            TextLabel {
                                Layout.fillWidth: true
                                text: dashboard.recReviewMessage
                                font.pixelSize: 12
                                color: window.muted
                                wrapMode: Text.Wrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                ActionButton {
                                    text: "학습에 포함"
                                    primary: dashboard.recReviewStatus !== "fail"
                                    onClicked: dashboard.keepLastRecording(false)
                                }
                                ActionButton {
                                    text: "포함 후 바로 학습"
                                    primary: true
                                    enabled: !dashboard.trainingRunning
                                    onClicked: dashboard.keepLastRecording(true)
                                }
                                ActionButton {
                                    text: "학습에서 제외 (파일 보관)"
                                    onClicked: dashboard.excludeLastRecording()
                                }
                                ActionButton {
                                    text: "녹화 파일 삭제"
                                    danger: true
                                    onClicked: dashboard.deleteLastRecording()
                                }
                            }
                        }
                    }
                    RowLayout {
                        visible: dashboard.palmCalibrating || (dashboard.palmAxesHint !== "" && !dashboard.calibratingPointer)
                        Layout.fillWidth: true
                        TextLabel {
                            Layout.fillWidth: true
                            text: dashboard.palmAxesHint
                            wrapMode: Text.Wrap
                        }
                        ActionButton { visible: dashboard.palmCalibrating; text: "취소"; onClicked: dashboard.cancelPalmCalibration() }
                    }
                    RowLayout {
                        visible: !dashboard.calibratingPointer && !dashboard.palmCalibrating
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

    // Training center (src/training_center.py): data per gesture, scores, focused recording, training, export
    Popup {
        id: trainingCenter
        property string centerTab: "gestures"
        property string selectedSimFile: ""
        anchors.centerIn: parent
        width: Math.min(1250, window.width - 20)
        height: Math.min(836, window.height - 20)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: dashboard.openTraining()
        onClosed: dashboard.closeTraining()
        padding: 20
        background: Rectangle { radius: 16; color: "white"; border.color: window.line }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    spacing: 2
                    TextLabel { text: "학습 센터 · 데이터 품질 · 시뮬레이터"; font.pixelSize: 22; font.bold: true }
                    TextLabel {
                        text: "녹화 품질 평가 및 선별 → 동작별 선택/통합 학습 → 시뮬레이터 검증 → 동작별 초기화 및 모델 버전 복원"
                        font.pixelSize: 12; color: window.muted
                    }
                }
                Item { Layout.fillWidth: true }
                ActionButton { text: "새로고침"; enabled: dashboard.trainingBusy === ""; onClicked: dashboard.refreshTraining() }
                ActionButton { text: "닫기"; onClicked: trainingCenter.close() }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Chip { text: dashboard.trainingData !== "" ? dashboard.trainingData : "녹화 읽는 중…" }
                Chip { text: dashboard.trainingModel !== "" ? dashboard.trainingModel : "모델 확인 중…" }
                Chip {
                    visible: dashboard.trainingStorage !== "" && !dashboard.trainingStorageWarn
                    text: dashboard.trainingStorage
                }
                Item { Layout.fillWidth: true }
                TextLabel { visible: dashboard.trainingBusy !== ""; text: dashboard.trainingBusy; font.pixelSize: 12; color: window.muted }
            }
            Rectangle {
                visible: dashboard.trainingStorageWarn
                Layout.fillWidth: true
                implicitHeight: storageWarn.implicitHeight + 14
                radius: 8
                color: "#FFF1D6"
                border.color: "#F2B544"
                TextLabel {
                    id: storageWarn
                    anchors.fill: parent
                    anchors.margins: 7
                    text: dashboard.trainingStorage
                    font.pixelSize: 12
                    color: "#8A5A00"
                    wrapMode: Text.Wrap
                }
            }
            // Top tab navigation for Training Center
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                ActionButton {
                    text: "1. 동작별 학습 · 초기화"
                    accent: trainingCenter.centerTab === "gestures"
                    onClicked: trainingCenter.centerTab = "gestures"
                }
                ActionButton {
                    text: "2. 녹화 품질 평가 · 선별 (" + dashboard.trainingFiles.length + ")"
                    accent: trainingCenter.centerTab === "recordings"
                    onClicked: trainingCenter.centerTab = "recordings"
                }
                ActionButton {
                    text: "3. 학습 데이터 · 모델 시뮬레이터"
                    accent: trainingCenter.centerTab === "simulator"
                    onClicked: {
                        trainingCenter.centerTab = "simulator"
                        if (!dashboard.simulationResult.file && dashboard.trainingFiles.length > 0) {
                            dashboard.runSimulation(dashboard.trainingFiles[0].file)
                        }
                    }
                }
                ActionButton {
                    text: "4. 모델 버전 복원 · 앱 자가 진단"
                    accent: trainingCenter.centerTab === "diagnostics"
                    onClicked: {
                        trainingCenter.centerTab = "diagnostics"
                        if (dashboard.diagnosticsChecks.length === 0) {
                            dashboard.runDiagnostics()
                        }
                    }
                }
                Item { Layout.fillWidth: true }
            }

            // =========================================================================
            // TAB 1: 동작별 학습 · 초기화
            // =========================================================================
            ColumnLayout {
                visible: trainingCenter.centerTab === "gestures"
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8
                TextLabel {
                    Layout.fillWidth: true
                    text: "인식 = 학습에 쓰지 않은 자세에서 맞힌 비율 (실전 실력) · 특정 동작 학습이 잘못된 경우 [학습 초기화]로 해당 동작만 즉시 규칙 모드로 되돌릴 수 있습니다."
                    font.pixelSize: 11; color: window.muted; wrapMode: Text.Wrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    Repeater {
                        model: dashboard.trainingCategories
                        ActionButton {
                            required property string modelData
                            text: modelData
                            accent: dashboard.trainingFilter === modelData
                            onClicked: dashboard.setTrainingFilter(modelData)
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                TextLabel { text: dashboard.trainingCatalog; font.pixelSize: 12; color: window.muted }
                ScrollView {
                    id: trainScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: trainScroll.availableWidth
                        spacing: 8
                        Repeater {
                            model: dashboard.trainingRows
                            delegate: Rectangle {
                                id: row
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: rowBody.implicitHeight + 24
                                radius: 12
                                color: modelData.level === "need" ? "#FFF8EA" : "#F7FAFB"
                                border.color: modelData.level === "need" ? "#F2B544" : window.line
                                RowLayout {
                                    id: rowBody
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 14
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 5
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            TextLabel { text: row.modelData.name; font.pixelSize: 16; font.bold: true; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                            Chip {
                                                visible: Boolean(row.modelData.is_disabled)
                                                warn: true
                                                text: "초기화됨 (규칙 동작)"
                                            }
                                            Chip {
                                                warn: row.modelData.level === "need"
                                                text: row.modelData.level === "ok" ? "좋아요" : row.modelData.level === "need" ? "더 녹화" : "다시 학습"
                                            }
                                        }
                                        TextLabel { text: row.modelData.instruction; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12; color: window.muted }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            TextLabel { text: "데이터"; font.pixelSize: 12; color: window.muted; Layout.preferredWidth: 40 }
                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.maximumWidth: 260
                                                height: 8; radius: 4; color: "#E3E9EE"
                                                Rectangle {
                                                    width: parent.width * Math.max(0, Math.min(1, row.modelData.data_ratio))
                                                    height: parent.height; radius: 4
                                                    color: row.modelData.data_ratio >= 1 ? "#45E0B4" : "#F2B544"
                                                }
                                            }
                                            TextLabel { text: row.modelData.data_text; font.pixelSize: 12 }
                                        }
                                        RowLayout {
                                            spacing: 16
                                            TextLabel { text: row.modelData.score_text; font.pixelSize: 13; font.bold: true }
                                            TextLabel { text: row.modelData.fit_text; font.pixelSize: 12; color: window.muted }
                                        }
                                        TextLabel { text: row.modelData.model_text; font.pixelSize: 11; color: window.muted }
                                        TextLabel {
                                            visible: row.modelData.label === "thumb_middle"
                                            text: "좌클릭과 중지 유지 드래그가 같은 모델을 공유합니다."
                                            font.pixelSize: 11
                                            color: window.muted
                                        }
                                        TextLabel {
                                            Layout.fillWidth: true
                                            text: "→ " + row.modelData.advice
                                            font.pixelSize: 12
                                            color: row.modelData.level === "need" ? "#8A5A00" : window.muted
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    ColumnLayout {
                                        spacing: 6
                                        ActionButton {
                                            text: "이 동작만 학습"
                                            visible: row.modelData.can_train
                                            primary: true
                                            enabled: !dashboard.trainingRunning && !dashboard.recActive && dashboard.trainingBusy === ""
                                            onClicked: dashboard.trainSelectedGesture(row.modelData.key)
                                        }
                                        ActionButton {
                                            text: "학습 초기화"
                                            visible: Boolean(row.modelData.can_train && row.modelData.is_trained)
                                            enabled: !dashboard.trainingRunning && !dashboard.recActive && dashboard.trainingBusy === ""
                                            onClicked: dashboard.resetGestureTraining(row.modelData.key)
                                        }
                                        TextLabel {
                                            visible: !row.modelData.can_train
                                            text: "선택 학습의 공통 오답 자료"
                                            font.pixelSize: 11
                                            color: window.muted
                                        }
                                        ActionButton {
                                            text: "집중 녹화 설정"
                                            enabled: !dashboard.trainingRunning && dashboard.recMode === "off"
                                            onClicked: {
                                                if (dashboard.focusRecord(row.modelData.key)) {
                                                    trainingCenter.close()
                                                    capturePlan.open()
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // =========================================================================
            // TAB 2: 녹화 데이터 품질 평가 및 선별
            // =========================================================================
            ColumnLayout {
                visible: trainingCenter.centerTab === "recordings"
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    TextLabel {
                        Layout.fillWidth: true
                        text: "녹화별 품질 점수(손 유실률·프레임 떨림·동작 구간 유효성)를 평가하고, 학습에 사용할 녹화만 체크하여 선별할 수 있습니다."
                        font.pixelSize: 12; color: window.muted; wrapMode: Text.Wrap
                    }
                    ActionButton {
                        text: "전체 품질 재검사"
                        enabled: dashboard.trainingBusy === "" && !dashboard.trainingRunning
                        onClicked: dashboard.evaluateAllRecordings(false)
                    }
                    ActionButton {
                        text: "불량 녹화 자동 제외"
                        enabled: dashboard.trainingBusy === "" && !dashboard.trainingRunning
                        onClicked: dashboard.evaluateAllRecordings(true)
                    }
                }
                ScrollView {
                    id: filesScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: filesScroll.availableWidth
                        spacing: 8
                        Repeater {
                            model: dashboard.trainingFiles
                            delegate: Rectangle {
                                id: fileRow
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: fileRowBody.implicitHeight + 22
                                radius: 10
                                color: !modelData.selected ? "#F2F4F6" : (modelData.quality_status === "fail" ? "#FFF1F0" : (modelData.quality_status === "warning" ? "#FFF8EA" : "#F7FAFB"))
                                border.color: !modelData.selected ? window.line : (modelData.quality_status === "fail" ? "#E5484D" : (modelData.quality_status === "warning" ? "#F2B544" : window.line))
                                RowLayout {
                                    id: fileRowBody
                                    anchors.fill: parent
                                    anchors.margins: 11
                                    spacing: 12
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 4
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            Chip {
                                                warn: !fileRow.modelData.selected
                                                text: fileRow.modelData.selected ? "학습 포함" : "학습 제외됨"
                                            }
                                            Chip {
                                                warn: fileRow.modelData.quality_status !== "pass"
                                                text: fileRow.modelData.quality_status === "pass" ? "품질 양호" : (fileRow.modelData.quality_status === "warning" ? "품질 주의" : "품질 불량")
                                            }
                                            Chip {
                                                text: fileRow.modelData.recorded_mode_label || "가이드 녹화"
                                            }
                                            TextLabel {
                                                text: fileRow.modelData.file
                                                font.bold: true
                                                font.pixelSize: 14
                                                Layout.fillWidth: true
                                                elide: Text.ElideMiddle
                                            }
                                        }
                                        TextLabel {
                                            Layout.fillWidth: true
                                            text: "녹화 목표 동작: " + (fileRow.modelData.target_gestures_text || "지정된 동작 없음")
                                                + (fileRow.modelData.target_conditions_text ? ("  |  촬영 자세: " + fileRow.modelData.target_conditions_text) : "")
                                            font.bold: true
                                            font.pixelSize: 12
                                            color: "#0F6E5B"
                                            wrapMode: Text.Wrap
                                        }
                                        TextLabel {
                                            Layout.fillWidth: true
                                            text: fileRow.modelData.detail_text
                                            font.pixelSize: 12
                                            color: window.muted
                                            wrapMode: Text.Wrap
                                        }
                                        TextLabel {
                                            Layout.fillWidth: true
                                            text: fileRow.modelData.quality_text
                                            font.pixelSize: 12
                                            color: fileRow.modelData.quality_status === "fail" ? "#C62828" : window.muted
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    RowLayout {
                                        spacing: 6
                                        ActionButton {
                                            text: fileRow.modelData.selected ? "학습 포함됨" : "학습 포함하기"
                                            accent: Boolean(fileRow.modelData.selected)
                                            enabled: !dashboard.trainingRunning && dashboard.trainingBusy === ""
                                            onClicked: dashboard.setRecordingSelected(fileRow.modelData.file, !fileRow.modelData.selected)
                                        }
                                        ActionButton {
                                            text: "시뮬레이터 검증"
                                            enabled: dashboard.trainingBusy === ""
                                            onClicked: {
                                                trainingCenter.selectedSimFile = fileRow.modelData.file
                                                trainingCenter.centerTab = "simulator"
                                                dashboard.evaluateSimRecording(fileRow.modelData.file)
                                                dashboard.runSimulation(fileRow.modelData.file, true)
                                            }
                                        }
                                        ActionButton {
                                            text: "삭제"
                                            enabled: !dashboard.trainingRunning && dashboard.trainingBusy === ""
                                            onClicked: dashboard.deleteRecording(fileRow.modelData.file)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // =========================================================================
            // TAB 3: 학습 데이터 · 모델 시뮬레이터 (데이터 평가 · 보정 · 증강 설정 포함)
            // =========================================================================
            ColumnLayout {
                id: simTabRoot
                visible: trainingCenter.centerTab === "simulator"
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 6
                property int calibInterpGap: 3
                property real calibJointJump: 0.38
                property int playFrameIdx: 0
                property bool isPlaying: false
                property real playSpeed: 1.0
                property bool expandSkeleton: false
                property bool showArmPose: true
                property bool fullScreenCoordMode: true

                readonly property int totalFrames: dashboard.simPlaybackCount
                property var curFrame: null

                function refreshCurFrame() {
                    if (totalFrames > 0 && playFrameIdx >= 0 && playFrameIdx < totalFrames) {
                        curFrame = dashboard.getSimPlaybackFrame(playFrameIdx)
                    } else {
                        curFrame = null
                    }
                    skeletonCanvas.requestPaint()
                }

                onTotalFramesChanged: {
                    if (playFrameIdx >= totalFrames)
                        playFrameIdx = 0
                    refreshCurFrame()
                }
                onPlayFrameIdxChanged: refreshCurFrame()
                onExpandSkeletonChanged: skeletonCanvas.requestPaint()
                onShowArmPoseChanged: skeletonCanvas.requestPaint()
                onFullScreenCoordModeChanged: skeletonCanvas.requestPaint()

                function currentSimFilename() {
                    if (trainingCenter.selectedSimFile !== "")
                        return trainingCenter.selectedSimFile
                    var idx = simFileCombo.currentIndex
                    if (idx >= 0 && idx < dashboard.trainingFiles.length)
                        return dashboard.trainingFiles[idx].file
                    return dashboard.trainingFiles.length > 0 ? dashboard.trainingFiles[0].file : ""
                }

                function seekFrame(targetIdx) {
                    if (totalFrames <= 0) {
                        playFrameIdx = 0
                        return
                    }
                    playFrameIdx = Math.max(0, Math.min(totalFrames - 1, Math.round(targetIdx)))
                }

                function jumpToNextEvent() {
                    if (totalFrames <= 0)
                        return
                    playFrameIdx = dashboard.findNextSimEventFrame(playFrameIdx)
                }

                Timer {
                    id: simPlayTimer
                    interval: Math.max(12, Math.round(33 / Math.max(0.25, simTabRoot.playSpeed)))
                    repeat: true
                    running: simTabRoot.visible && simTabRoot.isPlaying && simTabRoot.totalFrames > 0
                    onTriggered: {
                        var step = simTabRoot.playSpeed >= 3.5 ? 2 : 1
                        if (simTabRoot.playFrameIdx + step < simTabRoot.totalFrames) {
                            simTabRoot.playFrameIdx += step
                        } else {
                            simTabRoot.playFrameIdx = 0
                            simTabRoot.isPlaying = false
                        }
                    }
                }

                // Top selector & simulation trigger row inside a properly sized background Rectangle
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: topSelectorRow.implicitHeight + 14
                    radius: 10
                    color: "#F7FAFB"
                    border.color: window.line
                    RowLayout {
                        id: topSelectorRow
                        anchors.fill: parent
                        anchors.margins: 7
                        spacing: 8
                        TextLabel { text: "대상 녹화:"; font.bold: true; font.pixelSize: 13 }
                        ComboBox {
                            id: simFileCombo
                            Layout.fillWidth: true
                            Layout.minimumWidth: 320
                            implicitHeight: 32
                            model: dashboard.trainingFiles
                            textRole: "combo_label"
                            onActivated: function(idx) {
                                if (idx >= 0 && idx < dashboard.trainingFiles.length) {
                                    var f = dashboard.trainingFiles[idx].file
                                    trainingCenter.selectedSimFile = f
                                    simTabRoot.isPlaying = false
                                    simTabRoot.playFrameIdx = 0
                                    dashboard.evaluateSimRecording(f)
                                    dashboard.runSimulation(f, true)
                                }
                            }
                        }
                        ActionButton {
                            text: "데이터 평가"
                            enabled: dashboard.trainingBusy === "" && dashboard.trainingFiles.length > 0
                            onClicked: dashboard.evaluateSimRecording(simTabRoot.currentSimFilename())
                        }
                        ActionButton {
                            text: "시뮬레이션 실행 (보정본)"
                            primary: true
                            enabled: dashboard.trainingBusy === "" && dashboard.trainingFiles.length > 0
                            onClicked: {
                                simTabRoot.isPlaying = false
                                simTabRoot.playFrameIdx = 0
                                dashboard.runSimulation(simTabRoot.currentSimFilename(), true)
                            }
                        }
                        ActionButton {
                            text: "원본 시뮬레이션"
                            enabled: dashboard.trainingBusy === "" && dashboard.trainingFiles.length > 0
                            onClicked: {
                                simTabRoot.isPlaying = false
                                simTabRoot.playFrameIdx = 0
                                dashboard.runSimulation(simTabRoot.currentSimFilename(), false)
                            }
                        }
                        Chip {
                            visible: dashboard.simulationResult.file !== undefined && dashboard.simulationResult.file !== ""
                            text: dashboard.simulationResult.using_cleaned ? "현재: 보정본 재생" : "현재: 원본 재생"
                        }
                    }
                }

                // Explicit Recording Target Intent & Screen Region Coverage Banner
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: recIntentCol.implicitHeight + 14
                    radius: 10
                    color: "#EEF8F6"
                    border.color: "#8CE0CA"
                    ColumnLayout {
                        id: recIntentCol
                        anchors.fill: parent
                        anchors.margins: 7
                        spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Chip {
                                text: dashboard.simRecordedModeLabel
                            }
                            TextLabel {
                                Layout.fillWidth: true
                                text: "녹화 목표 동작: " + dashboard.simTargetGesturesText
                                font.bold: true
                                font.pixelSize: 13
                                color: "#0B4F40"
                                wrapMode: Text.Wrap
                            }
                            Chip {
                                warn: dashboard.simPoseCoveragePct === 0
                                text: dashboard.simPoseCoveragePct > 0
                                    ? ("팔·어깨 스켈레톤(Pose) " + dashboard.simPoseCoveragePct + "% 수록")
                                    : "팔·어깨 스켈레톤 추정 모드"
                            }
                        }
                        TextLabel {
                            Layout.fillWidth: true
                            text: "촬영 자세 조건: " + dashboard.simTargetConditionsText
                                + "   |   화면 영역 기록 분포: " + dashboard.simScreenZonesText
                                + (dashboard.simTargetInstructionText !== "" ? ("   |   수행 방법: " + dashboard.simTargetInstructionText) : "")
                            font.pixelSize: 11
                            color: "#235347"
                            wrapMode: Text.Wrap
                        }
                    }
                }

                // 2-Column Enlarged Cards: Left = 데이터 평가·보정, Right = 데이터 증강 설정 (hidden when skeleton visualizer is expanded)
                RowLayout {
                    visible: !simTabRoot.expandSkeleton
                    Layout.fillWidth: true
                    spacing: 10

                    // 1) 데이터 평가 및 보정 카드 (충분한 높이 및 줄 분리로 버튼 이탈 방지)
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        implicitHeight: Math.max(evalCalibCol.implicitHeight, augCol.implicitHeight) + 20
                        radius: 10
                        color: dashboard.simQualityStatus === "fail" ? "#FFF1F0" : (dashboard.simQualityStatus === "warning" ? "#FFF8EA" : "#F7FAFB")
                        border.color: dashboard.simQualityStatus === "fail" ? "#E5484D" : (dashboard.simQualityStatus === "warning" ? "#F2B544" : window.line)
                        ColumnLayout {
                            id: evalCalibCol
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                TextLabel { text: "데이터 품질 평가 · 보정"; font.bold: true; font.pixelSize: 13 }
                                Chip {
                                    warn: dashboard.simQualityStatus !== "pass"
                                    text: dashboard.simCleanedExists ? "보정본 적용 중" : "원본 상태"
                                }
                                Item { Layout.fillWidth: true }
                                ActionButton {
                                    text: "데이터 보정 실행"
                                    enabled: dashboard.trainingBusy === "" && !dashboard.trainingRunning && dashboard.trainingFiles.length > 0
                                    onClicked: dashboard.calibrateSimRecording(
                                        simTabRoot.currentSimFilename(),
                                        simTabRoot.calibInterpGap,
                                        simTabRoot.calibJointJump,
                                        4
                                    )
                                }
                                ActionButton {
                                    text: "원본 복구"
                                    enabled: dashboard.trainingBusy === "" && !dashboard.trainingRunning && dashboard.simCleanedExists
                                    onClicked: dashboard.resetCalibratedSimRecording(simTabRoot.currentSimFilename())
                                }
                            }
                            TextLabel {
                                Layout.fillWidth: true
                                text: dashboard.simQualityHeadline
                                font.pixelSize: 11
                                color: window.ink
                                wrapMode: Text.Wrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                TextLabel { text: "손끊김 결측 보간:"; font.pixelSize: 11; color: window.muted; Layout.preferredWidth: 96 }
                                Repeater {
                                    model: [
                                        { label: "1프레임", val: 1 },
                                        { label: "3프레임 (권장)", val: 3 },
                                        { label: "5프레임", val: 5 }
                                    ]
                                    delegate: CompactButton {
                                        required property var modelData
                                        text: modelData.label
                                        accent: simTabRoot.calibInterpGap === modelData.val
                                        onClicked: simTabRoot.calibInterpGap = modelData.val
                                    }
                                }
                                Item { Layout.fillWidth: true }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                TextLabel { text: "관절 점프 억제:"; font.pixelSize: 11; color: window.muted; Layout.preferredWidth: 96 }
                                Repeater {
                                    model: [
                                        { label: "엄격 (0.25)", val: 0.25 },
                                        { label: "표준 (0.38)", val: 0.38 },
                                        { label: "완화 (0.50)", val: 0.50 }
                                    ]
                                    delegate: CompactButton {
                                        required property var modelData
                                        text: modelData.label
                                        accent: Math.abs(simTabRoot.calibJointJump - modelData.val) < 0.01
                                        onClicked: simTabRoot.calibJointJump = modelData.val
                                    }
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }
                    }

                    // 2) 데이터 증강 설정 카드 (충분한 높이 및 강도/수량 줄 분리로 박스 이탈 완전 차단)
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        implicitHeight: Math.max(evalCalibCol.implicitHeight, augCol.implicitHeight) + 20
                        radius: 10
                        color: "#F7FAFB"
                        border.color: window.line
                        ColumnLayout {
                            id: augCol
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                TextLabel { text: "데이터 증강 설정"; font.bold: true; font.pixelSize: 13 }
                                CompactButton {
                                    text: dashboard.augEnabled ? "증강 사용 중 (ON)" : "증강 꺼짐 (OFF)"
                                    accent: dashboard.augEnabled
                                    enabled: dashboard.trainingBusy === "" && !dashboard.trainingRunning
                                    onClicked: dashboard.setAugmentationConfig(!dashboard.augEnabled, dashboard.augStrength, dashboard.augFactor)
                                }
                                Item { Layout.fillWidth: true }
                                ActionButton {
                                    text: dashboard.augEnabled ? "이 녹화 증강 생성" : "이 녹화 증강 삭제"
                                    enabled: dashboard.trainingBusy === "" && !dashboard.trainingRunning && dashboard.trainingFiles.length > 0
                                    onClicked: dashboard.applySimAugmentation(
                                        simTabRoot.currentSimFilename(),
                                        dashboard.augEnabled,
                                        dashboard.augStrength,
                                        dashboard.augFactor
                                    )
                                }
                                ActionButton {
                                    text: dashboard.augEnabled ? "전체 선택 녹화 증강" : "전체 증강본 정리"
                                    enabled: dashboard.trainingBusy === "" && !dashboard.trainingRunning && dashboard.trainingFiles.length > 0
                                    onClicked: dashboard.applySimAugmentation(
                                        "__all__",
                                        dashboard.augEnabled,
                                        dashboard.augStrength,
                                        dashboard.augFactor
                                    )
                                }
                            }
                            TextLabel {
                                Layout.fillWidth: true
                                text: dashboard.augSummary
                                font.pixelSize: 11
                                color: window.ink
                                wrapMode: Text.Wrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                TextLabel { text: "회전·노이즈 강도:"; font.pixelSize: 11; color: window.muted; Layout.preferredWidth: 96 }
                                Repeater {
                                    model: [
                                        { key: "light", label: "약 (±20°)" },
                                        { key: "normal", label: "보통 (±35°)" },
                                        { key: "strong", label: "강 (±50°)" }
                                    ]
                                    delegate: CompactButton {
                                        required property var modelData
                                        text: modelData.label
                                        accent: dashboard.augStrength === modelData.key
                                        enabled: dashboard.augEnabled && dashboard.trainingBusy === "" && !dashboard.trainingRunning
                                        onClicked: dashboard.setAugmentationConfig(dashboard.augEnabled, modelData.key, dashboard.augFactor)
                                    }
                                }
                                Item { Layout.fillWidth: true }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                TextLabel { text: "녹화당 증강 배수:"; font.pixelSize: 11; color: window.muted; Layout.preferredWidth: 96 }
                                Repeater {
                                    model: [1, 2, 4, 6]
                                    delegate: CompactButton {
                                        required property int modelData
                                        text: modelData + "배 생성"
                                        accent: dashboard.augFactor === modelData
                                        enabled: dashboard.augEnabled && dashboard.trainingBusy === "" && !dashboard.trainingRunning
                                        onClicked: dashboard.setAugmentationConfig(dashboard.augEnabled, dashboard.augStrength, modelData)
                                    }
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }
                    }
                }

                // 3) 팔·어깨(Pose) + 21관절 손 스켈레톤 & 카메라 화면 영역(3x3) 시각화 재생기 (화면 확대 지원)
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: simTabRoot.expandSkeleton ? 460 : 268
                    radius: 10
                    color: "#F7FAFB"
                    border.color: window.line
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 9
                        spacing: 10

                        // Left: Expandable Arm/Shoulder + 21-Joint Hand Skeleton & 3x3 Camera Screen Zone Visualizer Canvas
                        Rectangle {
                            Layout.preferredWidth: simTabRoot.expandSkeleton ? 540 : 330
                            Layout.fillHeight: true
                            radius: 10
                            color: "#0D1B26"
                            border.color: (simTabRoot.curFrame && simTabRoot.curFrame.event_kind === "pinch") ? "#46E5BD"
                                : ((simTabRoot.curFrame && simTabRoot.curFrame.event_kind === "swipe") ? "#FF9F1C"
                                : ((simTabRoot.curFrame && simTabRoot.curFrame.event_kind === "sign") ? "#5BC0EB" : "#22384A"))
                            border.width: (simTabRoot.curFrame && simTabRoot.curFrame.event_kind !== "none") ? 2 : 1
                            clip: true

                            Canvas {
                                id: skeletonCanvas
                                anchors.fill: parent
                                onPaint: {
                                    var ctx = getContext("2d")
                                    var w = width
                                    var h = height
                                    ctx.clearRect(0, 0, w, h)

                                    var f = simTabRoot.curFrame
                                    var padX = 18
                                    var padY = 28
                                    var drawW = Math.max(40, w - padX * 2)
                                    var drawH = Math.max(40, h - padY * 2)

                                    // 3x3 Camera Screen Region Grid (explicitly shows which part of the screen is recorded)
                                    var zoneLabels = [
                                        ["왼쪽 위", "위", "오른쪽 위"],
                                        ["왼쪽", "중앙", "오른쪽"],
                                        ["왼쪽 아래", "아래", "오른쪽 아래"]
                                    ]
                                    var activeZone = (f && f.screen_zone) ? f.screen_zone : ""
                                    for (var r = 0; r < 3; r++) {
                                        for (var c = 0; c < 3; c++) {
                                            var zx = padX + (drawW * c) / 3.0
                                            var zy = padY + (drawH * r) / 3.0
                                            var zw = drawW / 3.0
                                            var zh = drawH / 3.0
                                            var zName = zoneLabels[r][c]
                                            if (simTabRoot.fullScreenCoordMode && activeZone === zName) {
                                                ctx.fillStyle = "rgba(70, 229, 189, 0.11)"
                                                ctx.fillRect(zx, zy, zw, zh)
                                            }
                                            ctx.strokeStyle = "rgba(255, 255, 255, 0.08)"
                                            ctx.lineWidth = 1
                                            ctx.strokeRect(zx, zy, zw, zh)
                                            if (simTabRoot.fullScreenCoordMode) {
                                                ctx.fillStyle = activeZone === zName ? "rgba(70, 229, 189, 0.78)" : "rgba(255, 255, 255, 0.20)"
                                                ctx.font = (activeZone === zName ? "bold " : "") + "10px 'Malgun Gothic'"
                                                ctx.textAlign = "left"
                                                ctx.fillText(zName, zx + 5, zy + 13)
                                            }
                                        }
                                    }

                                    var hasHand = Boolean(f && f.hand_present && f.lm && f.lm.length >= 21)
                                    var arm = (f && f.active_arm) ? f.active_arm : null
                                    if (!hasHand && !arm) {
                                        ctx.fillStyle = "rgba(255, 255, 255, 0.65)"
                                        ctx.font = "bold 13px 'Malgun Gothic'"
                                        ctx.textAlign = "center"
                                        ctx.fillText(
                                            simTabRoot.totalFrames > 0
                                                ? "이 프레임에 감지된 손/팔 스켈레톤 없음"
                                                : "[시뮬레이션 실행]을 누르면 팔·어깨 + 손 스켈레톤 및 화면 위치가 표시됩니다",
                                            w / 2,
                                            h / 2
                                        )
                                        return
                                    }

                                    // Compute bounding box across hand (and arm if shown) or map directly to full camera screen [0..1]
                                    var minX = 1.0, maxX = 0.0, minY = 1.0, maxY = 0.0
                                    function includePt(px, py) {
                                        if (px < minX) minX = px
                                        if (px > maxX) maxX = px
                                        if (py < minY) minY = py
                                        if (py > maxY) maxY = py
                                    }
                                    if (hasHand) {
                                        for (var i = 0; i < 21; i++) {
                                            includePt(f.lm[i][0], f.lm[i][1])
                                        }
                                    }
                                    if (simTabRoot.showArmPose && arm) {
                                        if (arm.shoulder) includePt(arm.shoulder[0], arm.shoulder[1])
                                        if (arm.elbow) includePt(arm.elbow[0], arm.elbow[1])
                                        if (arm.wrist) includePt(arm.wrist[0], arm.wrist[1])
                                        if (arm.other_shoulder && simTabRoot.expandSkeleton) {
                                            includePt(arm.other_shoulder[0], arm.other_shoulder[1])
                                        }
                                    }

                                    var spanX = Math.max(maxX - minX, 0.22)
                                    var spanY = Math.max(maxY - minY, 0.22)
                                    var cx = (minX + maxX) * 0.5
                                    var cy = (minY + maxY) * 0.5
                                    var zoomScale = Math.min(drawW / spanX, drawH / spanY) * 0.78

                                    function mapX(nx) {
                                        if (simTabRoot.fullScreenCoordMode) {
                                            return padX + Math.max(-0.08, Math.min(1.08, nx)) * drawW
                                        }
                                        return w * 0.5 + (nx - cx) * zoomScale
                                    }
                                    function mapY(ny) {
                                        if (simTabRoot.fullScreenCoordMode) {
                                            return padY + Math.max(-0.08, Math.min(1.08, ny)) * drawH
                                        }
                                        return h * 0.52 + (ny - cy) * zoomScale
                                    }

                                    // 0. Draw Arm & Shoulder Skeleton (Clavicle -> Shoulder -> Elbow -> Wrist)
                                    if (simTabRoot.showArmPose && arm && arm.shoulder && arm.elbow) {
                                        var sx = mapX(arm.shoulder[0]), sy = mapY(arm.shoulder[1])
                                        var ex = mapX(arm.elbow[0]), ey = mapY(arm.elbow[1])
                                        var wx = hasHand ? mapX(f.lm[0][0]) : mapX(arm.wrist[0])
                                        var wy = hasHand ? mapY(f.lm[0][1]) : mapY(arm.wrist[1])
                                        var isRealPose = (arm.source === "pose")

                                        // Clavicle line across shoulders if opposite shoulder is available
                                        if (arm.other_shoulder) {
                                            var osx = mapX(arm.other_shoulder[0]), osy = mapY(arm.other_shoulder[1])
                                            ctx.beginPath()
                                            ctx.moveTo(osx, osy)
                                            ctx.lineTo(sx, sy)
                                            ctx.strokeStyle = "rgba(148, 163, 184, 0.50)"
                                            ctx.lineWidth = 3.0
                                            ctx.stroke()

                                            ctx.beginPath()
                                            ctx.arc(osx, osy, 5.0, 0, Math.PI * 2)
                                            ctx.fillStyle = "#94A3B8"
                                            ctx.fill()
                                        }

                                        // Upper arm (Shoulder -> Elbow) and Forearm (Elbow -> Wrist)
                                        ctx.beginPath()
                                        ctx.moveTo(sx, sy)
                                        ctx.lineTo(ex, ey)
                                        ctx.lineTo(wx, wy)
                                        ctx.strokeStyle = isRealPose ? "#38BDF8" : "rgba(56, 189, 248, 0.68)"
                                        ctx.lineWidth = simTabRoot.expandSkeleton ? 5.0 : 4.0
                                        ctx.stroke()

                                        // Shoulder joint node
                                        ctx.beginPath()
                                        ctx.arc(sx, sy, simTabRoot.expandSkeleton ? 7.5 : 6.0, 0, Math.PI * 2)
                                        ctx.fillStyle = "#F59E0B"
                                        ctx.fill()
                                        ctx.strokeStyle = "#0D1B26"
                                        ctx.lineWidth = 1.5
                                        ctx.stroke()

                                        // Elbow joint node
                                        ctx.beginPath()
                                        ctx.arc(ex, ey, simTabRoot.expandSkeleton ? 7.0 : 5.5, 0, Math.PI * 2)
                                        ctx.fillStyle = "#38BDF8"
                                        ctx.fill()
                                        ctx.strokeStyle = "#0D1B26"
                                        ctx.lineWidth = 1.5
                                        ctx.stroke()

                                        // Labels for Shoulder & Elbow + Angle
                                        ctx.fillStyle = "#FDE68A"
                                        ctx.font = "bold 10px 'Malgun Gothic'"
                                        ctx.textAlign = "left"
                                        ctx.fillText((arm.side_ko || "팔") + " 어깨", sx + 8, sy + 4)
                                        ctx.fillStyle = "#BAE6FD"
                                        var degTxt = arm.elbow_deg !== null && arm.elbow_deg !== undefined ? (" " + Math.round(arm.elbow_deg) + "°") : ""
                                        ctx.fillText("팔꿈치" + degTxt + (isRealPose ? "" : " (추정)"), ex + 8, ey + 4)
                                    }

                                    if (!hasHand) {
                                        return
                                    }

                                    var lm = f.lm

                                    // 1. Draw recent palm trajectory trail (last 14 frames) for swipe / screen motion visualization
                                    var trail = f.trail || []
                                    ctx.beginPath()
                                    var started = false
                                    for (var ti = 0; ti < trail.length; ti++) {
                                        var tp = trail[ti]
                                        if (tp && tp.length === 2) {
                                            var tx = mapX(tp[0])
                                            var ty = mapY(tp[1])
                                            if (!started) {
                                                ctx.moveTo(tx, ty)
                                                started = true
                                            } else {
                                                ctx.lineTo(tx, ty)
                                            }
                                        }
                                    }
                                    if (started) {
                                        ctx.strokeStyle = f.event_kind === "swipe" ? "rgba(255, 159, 28, 0.88)" : "rgba(70, 229, 189, 0.45)"
                                        ctx.lineWidth = f.event_kind === "swipe" ? 3.5 : 2.2
                                        ctx.stroke()
                                    }

                                    // 2. Draw anatomical hand bone chains
                                    var chains = [
                                        { pts: [0, 1, 2, 3, 4], color: "#FF6B6B" },     // Thumb
                                        { pts: [0, 5, 6, 7, 8], color: "#46E5BD" },     // Index
                                        { pts: [0, 9, 10, 11, 12], color: "#FFD166" },  // Middle
                                        { pts: [0, 13, 14, 15, 16], color: "#5BC0EB" }, // Ring
                                        { pts: [0, 17, 18, 19, 20], color: "#C792EA" }, // Pinky
                                        { pts: [5, 9, 13, 17], color: "rgba(255,255,255,0.40)" } // Palm arch
                                    ]
                                    for (var c2 = 0; c2 < chains.length; c2++) {
                                        var ch = chains[c2]
                                        ctx.beginPath()
                                        for (var k = 0; k < ch.pts.length; k++) {
                                            var jIdx = ch.pts[k]
                                            var jx = mapX(lm[jIdx][0])
                                            var jy = mapY(lm[jIdx][1])
                                            if (k === 0) ctx.moveTo(jx, jy)
                                            else ctx.lineTo(jx, jy)
                                        }
                                        ctx.strokeStyle = ch.color
                                        ctx.lineWidth = simTabRoot.expandSkeleton ? 3.0 : 2.4
                                        ctx.stroke()
                                    }

                                    // 3. Draw 21 joint nodes
                                    var pinchArr = f.pinch_joints || []
                                    var signArr = f.sign_joints || []
                                    for (var j = 0; j < 21; j++) {
                                        var x = mapX(lm[j][0])
                                        var y = mapY(lm[j][1])
                                        var isPinchJoint = pinchArr.indexOf(j) !== -1
                                        var isSignJoint = signArr.indexOf(j) !== -1
                                        var isTip = (j === 4 || j === 8 || j === 12 || j === 16 || j === 20)
                                        ctx.beginPath()
                                        ctx.arc(x, y, isPinchJoint ? 6.5 : (isSignJoint ? 6.0 : (isTip ? 4.0 : 2.8)), 0, Math.PI * 2)
                                        ctx.fillStyle = isPinchJoint ? "#46E5BD" : (isSignJoint ? "#5BC0EB" : (j === 0 ? "#FFFFFF" : "#E2EDF5"))
                                        ctx.fill()
                                        if (isPinchJoint || isSignJoint) {
                                            ctx.beginPath()
                                            ctx.arc(x, y, 10.5, 0, Math.PI * 2)
                                            ctx.strokeStyle = isPinchJoint ? "rgba(70, 229, 189, 0.85)" : "rgba(91, 192, 235, 0.85)"
                                            ctx.lineWidth = 2
                                            ctx.stroke()
                                        }
                                    }

                                    // 4. Visual PINCH connector & burst ring between thumb(4) and active finger(s)
                                    if (pinchArr.length >= 2) {
                                        var tx0 = mapX(lm[pinchArr[0]][0])
                                        var ty0 = mapY(lm[pinchArr[0]][1])
                                        var mx = tx0, my = ty0
                                        for (var pi = 1; pi < pinchArr.length; pi++) {
                                            var tx1 = mapX(lm[pinchArr[pi]][0])
                                            var ty1 = mapY(lm[pinchArr[pi]][1])
                                            mx += tx1
                                            my += ty1
                                            ctx.beginPath()
                                            ctx.moveTo(tx0, ty0)
                                            ctx.lineTo(tx1, ty1)
                                            ctx.strokeStyle = "#46E5BD"
                                            ctx.lineWidth = 3
                                            ctx.stroke()
                                        }
                                        mx /= pinchArr.length
                                        my /= pinchArr.length
                                        ctx.beginPath()
                                        ctx.arc(mx, my, 15, 0, Math.PI * 2)
                                        ctx.fillStyle = "rgba(70, 229, 189, 0.24)"
                                        ctx.fill()
                                        ctx.strokeStyle = "#46E5BD"
                                        ctx.lineWidth = 2
                                        ctx.stroke()
                                    }

                                    // 5. Visual SWIPE direction arrow overlay
                                    if (f.swipe_dir && f.swipe_dir !== "") {
                                        var ax0 = w * 0.5, ay0 = h - 22
                                        var ax1 = ax0, ay1 = ay0
                                        if (f.swipe_dir === "left") { ax0 = w * 0.68; ax1 = w * 0.32 }
                                        else if (f.swipe_dir === "right") { ax0 = w * 0.32; ax1 = w * 0.68 }
                                        else if (f.swipe_dir === "down") { ay0 = h * 0.28; ay1 = h * 0.72; ax0 = w - 26; ax1 = w - 26 }

                                        ctx.beginPath()
                                        ctx.moveTo(ax0, ay0)
                                        ctx.lineTo(ax1, ay1)
                                        ctx.strokeStyle = "#FF9F1C"
                                        ctx.lineWidth = 4
                                        ctx.stroke()

                                        var ang = Math.atan2(ay1 - ay0, ax1 - ax0)
                                        ctx.beginPath()
                                        ctx.moveTo(ax1, ay1)
                                        ctx.lineTo(ax1 - 10 * Math.cos(ang - 0.5), ay1 - 10 * Math.sin(ang - 0.5))
                                        ctx.lineTo(ax1 - 10 * Math.cos(ang + 0.5), ay1 - 10 * Math.sin(ang + 0.5))
                                        ctx.closePath()
                                        ctx.fillStyle = "#FF9F1C"
                                        ctx.fill()
                                    }
                                }
                            }

                            // Top-left time / frame HUD
                            Rectangle {
                                anchors.top: parent.top
                                anchors.left: parent.left
                                anchors.margins: 6
                                radius: 5
                                color: "#C80B151E"
                                width: frameHudText.implicitWidth + 12
                                height: 22
                                TextLabel {
                                    id: frameHudText
                                    anchors.centerIn: parent
                                    color: "#E2EDF5"
                                    font.pixelSize: 11
                                    font.bold: true
                                    text: simTabRoot.curFrame
                                        ? (simTabRoot.curFrame.t.toFixed(2) + "s · #" + (simTabRoot.playFrameIdx + 1) + "/" + simTabRoot.totalFrames + " · " + simTabRoot.curFrame.handedness)
                                        : "0.00s · 0/0"
                                }
                            }

                            // Top-right active Event Badge (PINCH / SWIPE / SIGN)
                            Rectangle {
                                visible: simTabRoot.curFrame !== null
                                anchors.top: parent.top
                                anchors.right: parent.right
                                anchors.margins: 6
                                radius: 5
                                color: !simTabRoot.curFrame ? "#334E68"
                                    : (simTabRoot.curFrame.event_kind === "pinch" ? "#16846E"
                                    : (simTabRoot.curFrame.event_kind === "swipe" ? "#D97706"
                                    : (simTabRoot.curFrame.event_kind === "sign" ? "#0284C7" : "#243B4A")))
                                width: evBadgeText.implicitWidth + 12
                                height: 22
                                TextLabel {
                                    id: evBadgeText
                                    anchors.centerIn: parent
                                    color: "white"
                                    font.pixelSize: 11
                                    font.bold: true
                                    text: simTabRoot.curFrame ? simTabRoot.curFrame.event_text : "대기"
                                }
                            }

                            // Bottom-left Screen Position & Arm Pose Status HUD
                            Rectangle {
                                visible: simTabRoot.curFrame !== null
                                anchors.bottom: parent.bottom
                                anchors.left: parent.left
                                anchors.margins: 6
                                radius: 5
                                color: "#C80B151E"
                                width: posHudText.implicitWidth + 12
                                height: 22
                                TextLabel {
                                    id: posHudText
                                    anchors.centerIn: parent
                                    color: "#8CE0CA"
                                    font.pixelSize: 10
                                    font.bold: true
                                    text: {
                                        var f = simTabRoot.curFrame
                                        if (!f) return ""
                                        var z = f.screen_zone ? ("화면영역: " + f.screen_zone) : "화면영역: -"
                                        if (f.palm_xy_pct && f.palm_xy_pct.length === 2) {
                                            z += " (" + f.palm_xy_pct[0] + "%, " + f.palm_xy_pct[1] + "%)"
                                        }
                                        if (f.target_position && f.target_position !== "") {
                                            z += " · 목표위치: " + f.target_position
                                        }
                                        if (f.cursor && f.cursor.length === 2) {
                                            z += " · 커서(" + f.cursor[0] + "," + f.cursor[1] + ")"
                                        }
                                        return z
                                    }
                                }
                            }
                        }

                        // Right: Frame Event Inspector + Interactive Timeline + Media Player Controls + Visualizer Mode Controls
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            spacing: 6

                            // Summary Banner + Skeleton View Mode Controls
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 30
                                    radius: 7
                                    color: "#EEFCF8"
                                    border.color: "#45E0B4"
                                    TextLabel {
                                        anchors.fill: parent
                                        anchors.leftMargin: 8
                                        anchors.rightMargin: 8
                                        verticalAlignment: Text.AlignVCenter
                                        text: dashboard.simulationSummary
                                        font.bold: true
                                        font.pixelSize: 11
                                        elide: Text.ElideRight
                                    }
                                }
                                CompactButton {
                                    text: simTabRoot.showArmPose ? "팔·어깨 표시 켜짐" : "팔·어깨 표시 꺼짐"
                                    accent: simTabRoot.showArmPose
                                    onClicked: simTabRoot.showArmPose = !simTabRoot.showArmPose
                                }
                                CompactButton {
                                    text: simTabRoot.fullScreenCoordMode ? "화면 전체위치(3x3)" : "손 확대 보기"
                                    accent: simTabRoot.fullScreenCoordMode
                                    onClicked: simTabRoot.fullScreenCoordMode = !simTabRoot.fullScreenCoordMode
                                }
                                CompactButton {
                                    text: simTabRoot.expandSkeleton ? "시각화 화면 축소 ⤡" : "시각화 화면 크게 ⤢"
                                    accent: simTabRoot.expandSkeleton
                                    onClicked: simTabRoot.expandSkeleton = !simTabRoot.expandSkeleton
                                }
                            }

                            // 3-Box Live Frame Inspector (목표 동작+화면영역 / 모델 판정 / 규칙·팔어깨 각도)
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    implicitHeight: 42
                                    radius: 7
                                    color: (simTabRoot.curFrame && simTabRoot.curFrame.phase === "do") ? "#E6F8F3" : "white"
                                    border.color: (simTabRoot.curFrame && simTabRoot.curFrame.phase === "do") ? "#16846E" : window.line
                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 8
                                        anchors.rightMargin: 8
                                        spacing: 0
                                        TextLabel { text: "목표 라벨 · 화면 위치"; font.pixelSize: 10; color: window.muted }
                                        TextLabel {
                                            text: simTabRoot.curFrame
                                                ? (simTabRoot.curFrame.target_name
                                                    + (simTabRoot.curFrame.phase === "do" ? " [수행]" : " [중립]")
                                                    + (simTabRoot.curFrame.screen_zone ? (" · " + simTabRoot.curFrame.screen_zone) : ""))
                                                : "-"
                                            font.bold: true
                                            font.pixelSize: 12
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    implicitHeight: 42
                                    radius: 7
                                    color: (simTabRoot.curFrame && simTabRoot.curFrame.model_hit) ? "#E6F8F3" : "white"
                                    border.color: (simTabRoot.curFrame && simTabRoot.curFrame.model_hit) ? "#16846E" : window.line
                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 8
                                        anchors.rightMargin: 8
                                        spacing: 0
                                        TextLabel { text: "TCN 모델 실시간 예측"; font.pixelSize: 10; color: window.muted }
                                        TextLabel {
                                            text: simTabRoot.curFrame
                                                ? (simTabRoot.curFrame.model_name + " · 확신도 " + Math.round(simTabRoot.curFrame.model_prob * 100) + "%" + (simTabRoot.curFrame.model_hit ? " (발동)" : ""))
                                                : "-"
                                            font.bold: true
                                            font.pixelSize: 12
                                            color: (simTabRoot.curFrame && simTabRoot.curFrame.model_hit) ? "#0F6E5B" : window.navy
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    implicitHeight: 42
                                    radius: 7
                                    color: (simTabRoot.curFrame && simTabRoot.curFrame.issue_text !== "") ? "#FFF1F0" : "white"
                                    border.color: (simTabRoot.curFrame && simTabRoot.curFrame.issue_text !== "") ? "#E5484D" : window.line
                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 8
                                        anchors.rightMargin: 8
                                        spacing: 0
                                        TextLabel { text: "팔·어깨 각도 / 프레임 품질"; font.pixelSize: 10; color: window.muted }
                                        TextLabel {
                                            text: !simTabRoot.curFrame ? "-"
                                                : (simTabRoot.curFrame.issue_text !== ""
                                                    ? ("⚠ " + simTabRoot.curFrame.issue_text)
                                                    : ((simTabRoot.curFrame.active_arm && simTabRoot.curFrame.active_arm.elbow_deg !== null)
                                                        ? (simTabRoot.curFrame.active_arm.side_ko + " 팔꿈치 " + Math.round(simTabRoot.curFrame.active_arm.elbow_deg) + "° · vx=" + simTabRoot.curFrame.vx + ", vy=" + simTabRoot.curFrame.vy)
                                                        : ("속도 vx=" + simTabRoot.curFrame.vx + ", vy=" + simTabRoot.curFrame.vy)))
                                            font.bold: true
                                            font.pixelSize: 11
                                            color: (simTabRoot.curFrame && simTabRoot.curFrame.issue_text !== "") ? "#C62828" : window.navy
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                            }

                            // Interactive Probability Timeline + Playhead Scrubber
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                implicitHeight: 64
                                radius: 8
                                color: "white"
                                border.color: window.line
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 6
                                    spacing: 2
                                    RowLayout {
                                        Layout.fillWidth: true
                                        TextLabel {
                                            text: "시간축 확신도 & 프레임 탐색 바 (클릭·드래그하여 원하는 시점으로 이동 · 초록=목표 인식, 주황=목표 미달, 빨강=오작동)"
                                            font.pixelSize: 10
                                            color: window.muted
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                    Item {
                                        id: timelineTrack
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        RowLayout {
                                            anchors.fill: parent
                                            spacing: 1
                                            Repeater {
                                                model: dashboard.simulationTimeline
                                                delegate: Item {
                                                    required property var modelData
                                                    Layout.fillWidth: true
                                                    Layout.fillHeight: true
                                                    Rectangle {
                                                        anchors.bottom: parent.bottom
                                                        width: parent.width
                                                        height: Math.max(3, parent.height * Math.min(1.0, modelData.prob))
                                                        radius: 1
                                                        color: modelData.target !== "none"
                                                            ? (modelData.confirmed ? "#16846E" : "#F2B544")
                                                            : (modelData.confirmed ? "#E5484D" : "#CBD5E0")
                                                    }
                                                }
                                            }
                                        }
                                        // Playhead indicator line
                                        Rectangle {
                                            visible: simTabRoot.totalFrames > 0
                                            width: 3
                                            height: parent.height
                                            radius: 1
                                            color: "#112331"
                                            x: simTabRoot.totalFrames > 1
                                                ? Math.min(parent.width - 3, Math.max(0, (simTabRoot.playFrameIdx / (simTabRoot.totalFrames - 1)) * (parent.width - 3)))
                                                : 0
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            enabled: simTabRoot.totalFrames > 0
                                            onPressed: function(mouse) {
                                                simTabRoot.isPlaying = false
                                                var ratio = Math.max(0, Math.min(1, mouse.x / Math.max(1, width)))
                                                simTabRoot.seekFrame(ratio * (simTabRoot.totalFrames - 1))
                                            }
                                            onPositionChanged: function(mouse) {
                                                if (pressed) {
                                                    var ratio = Math.max(0, Math.min(1, mouse.x / Math.max(1, width)))
                                                    simTabRoot.seekFrame(ratio * (simTabRoot.totalFrames - 1))
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // Media Player Transport Controls (여유 있게 배치 + 재생 중일 때만 연두색 선택 표시)
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 5
                                CompactButton {
                                    text: "⏮ 처음"
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        simTabRoot.isPlaying = false
                                        simTabRoot.seekFrame(0)
                                    }
                                }
                                CompactButton {
                                    text: "⏪ -10F"
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        simTabRoot.isPlaying = false
                                        simTabRoot.seekFrame(simTabRoot.playFrameIdx - 10)
                                    }
                                }
                                CompactButton {
                                    text: "◀ -1F"
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        simTabRoot.isPlaying = false
                                        simTabRoot.seekFrame(simTabRoot.playFrameIdx - 1)
                                    }
                                }
                                CompactButton {
                                    text: simTabRoot.isPlaying ? "⏸ 일시정지" : "▶ 재생"
                                    accent: simTabRoot.isPlaying
                                    primary: !simTabRoot.isPlaying
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        if (!simTabRoot.isPlaying && simTabRoot.playFrameIdx >= simTabRoot.totalFrames - 1)
                                            simTabRoot.playFrameIdx = 0
                                        simTabRoot.isPlaying = !simTabRoot.isPlaying
                                    }
                                }
                                CompactButton {
                                    text: "⏹ 정지"
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        simTabRoot.isPlaying = false
                                        simTabRoot.seekFrame(0)
                                    }
                                }
                                CompactButton {
                                    text: "+1F ▶"
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        simTabRoot.isPlaying = false
                                        simTabRoot.seekFrame(simTabRoot.playFrameIdx + 1)
                                    }
                                }
                                CompactButton {
                                    text: "+10F ⏩"
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        simTabRoot.isPlaying = false
                                        simTabRoot.seekFrame(simTabRoot.playFrameIdx + 10)
                                    }
                                }
                                CompactButton {
                                    text: "다음 이벤트 ⏭"
                                    enabled: simTabRoot.totalFrames > 0
                                    onClicked: {
                                        simTabRoot.isPlaying = false
                                        simTabRoot.jumpToNextEvent()
                                    }
                                }
                                Item { Layout.fillWidth: true }
                                TextLabel { text: "재생 배속:"; font.pixelSize: 11; color: window.muted }
                                Repeater {
                                    model: [0.25, 0.5, 1.0, 2.0, 4.0]
                                    delegate: CompactButton {
                                        required property real modelData
                                        text: modelData + "x"
                                        accent: Math.abs(simTabRoot.playSpeed - modelData) < 0.01
                                        onClicked: simTabRoot.playSpeed = modelData
                                    }
                                }
                            }
                        }
                    }
                }

                ScrollView {
                    id: simScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: simScroll.availableWidth
                        spacing: 6
                        TextLabel {
                            visible: dashboard.simQualityIssues.length > 0
                            text: "감지된 데이터 이상 구간 (" + dashboard.simQualityIssues.length + "건 · 항목 클릭 시 해당 프레임으로 이동)"
                            font.bold: true
                            font.pixelSize: 12
                        }
                        Repeater {
                            model: dashboard.simQualityIssues
                            delegate: Rectangle {
                                id: issRow
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: 30
                                radius: 8
                                color: issRow.modelData.severity === "fail" ? "#FFF1F0" : "#FFF8EA"
                                border.color: issRow.modelData.severity === "fail" ? "#E5484D" : "#F2B544"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    spacing: 8
                                    Chip {
                                        warn: true
                                        text: issRow.modelData.severity === "fail" ? "치명" : "주의"
                                    }
                                    TextLabel {
                                        text: issRow.modelData.start_t + "s ~ " + issRow.modelData.end_t + "s (프레임 " + issRow.modelData.start_frame + "~" + issRow.modelData.end_frame + ")"
                                        font.bold: true
                                        font.pixelSize: 11
                                        Layout.preferredWidth: 185
                                    }
                                    TextLabel {
                                        text: issRow.modelData.message
                                        font.pixelSize: 11
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    CompactButton {
                                        text: "이동 ▶"
                                        onClicked: {
                                            simTabRoot.isPlaying = false
                                            simTabRoot.seekFrame(issRow.modelData.start_frame || 0)
                                        }
                                    }
                                }
                            }
                        }
                        TextLabel {
                            visible: dashboard.simulationGestures.length > 0
                            text: "동작별 모델 vs 규칙 비교"
                            font.bold: true
                            font.pixelSize: 13
                        }
                        Repeater {
                            model: dashboard.simulationGestures
                            delegate: Rectangle {
                                id: simRow
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: simRowCol.implicitHeight + 12
                                radius: 8
                                color: "#F7FAFB"
                                border.color: window.line
                                RowLayout {
                                    id: simRowCol
                                    anchors.fill: parent
                                    anchors.margins: 6
                                    spacing: 10
                                    TextLabel {
                                        text: simRow.modelData.name
                                        font.bold: true
                                        font.pixelSize: 12
                                        Layout.preferredWidth: 150
                                    }
                                    Chip {
                                        warn: !simRow.modelData.trained
                                        text: simRow.modelData.trained ? "학습됨" : "미학습"
                                    }
                                    TextLabel {
                                        text: simRow.modelData.reps > 0
                                            ? ("반복 " + simRow.modelData.reps + "회 중 모델 " + simRow.modelData.model_hits + "회 (" + Math.round((simRow.modelData.model_rate || 0) * 100) + "%) · 규칙 " + simRow.modelData.rule_hits + "회 (" + Math.round((simRow.modelData.rule_rate || 0) * 100) + "%)")
                                            : "이 녹화에 목표 반복 없음"
                                        font.pixelSize: 11
                                        Layout.fillWidth: true
                                    }
                                    TextLabel {
                                        text: "최고 확신도 " + Math.round(simRow.modelData.max_prob * 100) + "% · 중립 오작동 " + simRow.modelData.false_fires + "회"
                                        font.pixelSize: 11
                                        color: simRow.modelData.false_fires > 0 ? "#C62828" : window.muted
                                    }
                                }
                            }
                        }
                        TextLabel {
                            visible: dashboard.simulationReps.length > 0
                            text: "회차별 상세 판정 기록 (" + dashboard.simulationReps.length + "회 · 클릭 시 해당 구간 재생)"
                            font.bold: true
                            font.pixelSize: 13
                        }
                        Repeater {
                            model: dashboard.simulationReps
                            delegate: Rectangle {
                                id: repRow
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: 32
                                radius: 8
                                color: repRow.modelData.model_ok ? "#F2FBF9" : "#FFF8EA"
                                border.color: repRow.modelData.model_ok ? "#B2DFDB" : "#F2B544"
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    spacing: 8
                                    TextLabel { text: "#" + repRow.modelData.rep + " (" + repRow.modelData.time_s + "초)"; font.bold: true; font.pixelSize: 11; Layout.preferredWidth: 85 }
                                    TextLabel { text: repRow.modelData.name; font.pixelSize: 11; Layout.preferredWidth: 150 }
                                    TextLabel { text: "최고 확률 " + Math.round(repRow.modelData.peak_prob * 100) + "% (평균 " + Math.round(repRow.modelData.mean_prob * 100) + "%)"; font.pixelSize: 11; Layout.fillWidth: true }
                                    Chip { warn: !repRow.modelData.model_ok; text: repRow.modelData.model_ok ? "모델 통과" : "모델 미달" }
                                    Chip { warn: !repRow.modelData.rule_ok; text: repRow.modelData.rule_ok ? "규칙 통과" : "규칙 미달" }
                                    CompactButton {
                                        text: "구간 재생 ▶"
                                        onClicked: {
                                            simTabRoot.seekFrame(repRow.modelData.start_frame || 0)
                                            simTabRoot.isPlaying = true
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // =========================================================================
            // TAB 4: 모델 버전 복원(롤백) · 앱 자가 진단
            // =========================================================================
            ColumnLayout {
                visible: trainingCenter.centerTab === "diagnostics"
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    TextLabel {
                        text: "앱 자가 진단 및 버그 탐지"
                        font.bold: true
                        font.pixelSize: 16
                    }
                    TextLabel {
                        Layout.fillWidth: true
                        text: dashboard.diagnosticsSummary
                        font.pixelSize: 12
                        color: window.muted
                    }
                    ActionButton {
                        text: "자가 진단 다시 실행"
                        enabled: dashboard.trainingBusy === ""
                        onClicked: dashboard.runDiagnostics()
                    }
                }
                ScrollView {
                    id: diagScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: diagScroll.availableWidth
                        spacing: 8
                        Repeater {
                            model: dashboard.diagnosticsChecks
                            delegate: Rectangle {
                                id: chkRow
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: chkBody.implicitHeight + 16
                                radius: 10
                                color: chkRow.modelData.status === "fail" ? "#FFF1F0" : (chkRow.modelData.status === "warn" ? "#FFF8EA" : "#F7FAFB")
                                border.color: chkRow.modelData.status === "fail" ? "#E5484D" : (chkRow.modelData.status === "warn" ? "#F2B544" : window.line)
                                RowLayout {
                                    id: chkBody
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 10
                                    Chip {
                                        warn: chkRow.modelData.status !== "ok"
                                        text: chkRow.modelData.status === "ok" ? "정상" : (chkRow.modelData.status === "warn" ? "주의" : "오류")
                                    }
                                    TextLabel {
                                        text: chkRow.modelData.name
                                        font.bold: true
                                        font.pixelSize: 13
                                        Layout.preferredWidth: 230
                                    }
                                    TextLabel {
                                        text: chkRow.modelData.detail
                                        font.pixelSize: 12
                                        color: window.muted
                                        Layout.fillWidth: true
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
                        TextLabel {
                            text: "이전 모델 버전으로 되돌리기 (자동 백업 및 내보내기 스냅샷)"
                            font.bold: true
                            font.pixelSize: 16
                        }
                        TextLabel {
                            Layout.fillWidth: true
                            text: "새로 학습한 모델이 마음에 들지 않을 때, 학습 직전 자동 백업본이나 이전에 내보낸 모델 버전으로 즉시 복원합니다."
                            font.pixelSize: 12
                            color: window.muted
                            wrapMode: Text.Wrap
                        }
                        TextLabel {
                            visible: dashboard.modelBackups.length === 0
                            text: "아직 저장된 이전 모델 백업이 없습니다. (다음 학습이나 동작 초기화 시 자동으로 백업됩니다)"
                            font.pixelSize: 12
                            color: window.muted
                        }
                        Repeater {
                            model: dashboard.modelBackups
                            delegate: Rectangle {
                                id: bakRow
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: bakBody.implicitHeight + 18
                                radius: 10
                                color: "#F7FAFB"
                                border.color: window.line
                                RowLayout {
                                    id: bakBody
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 12
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 3
                                        RowLayout {
                                            spacing: 8
                                            Chip {
                                                text: bakRow.modelData.source === "export" ? "내보내기 본" : "자동 백업"
                                            }
                                            TextLabel {
                                                text: bakRow.modelData.name
                                                font.bold: true
                                                font.pixelSize: 13
                                            }
                                        }
                                        TextLabel {
                                            Layout.fillWidth: true
                                            text: bakRow.modelData.summary
                                            font.pixelSize: 12
                                            color: window.muted
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    ActionButton {
                                        text: "이 버전으로 복원"
                                        enabled: !dashboard.trainingRunning && !dashboard.recActive && dashboard.trainingBusy === ""
                                        onClicked: dashboard.restoreModelBackup(bakRow.modelData.id)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    TextLabel {
                        Layout.fillWidth: true
                        text: dashboard.trainingStage !== "" ? dashboard.trainingStage
                              : "학습: 자세 하나씩 빼고 평가한 뒤 전체로 최종 모델을 만들어요 (CPU로 몇 분)"
                        font.pixelSize: 13; wrapMode: Text.Wrap
                    }
                    Rectangle {
                        visible: dashboard.trainingRunning || dashboard.trainingProgress > 0
                        Layout.fillWidth: true
                        height: 6; radius: 3; color: "#E3E9EE"
                        Rectangle {
                            width: parent.width * Math.max(0, Math.min(1, dashboard.trainingProgress))
                            height: parent.height; radius: 3; color: "#45E0B4"
                        }
                    }
                    Canvas {
                        id: lossChart
                        visible: dashboard.trainingLosses.length > 1
                        Layout.fillWidth: true
                        Layout.preferredHeight: 34
                        property var losses: dashboard.trainingLosses
                        onLossesChanged: requestPaint()
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.reset()
                            var v = losses
                            if (!v || v.length < 2) return
                            var hi = Math.max.apply(null, v), lo = Math.min.apply(null, v)
                            var span = Math.max(1e-6, hi - lo)
                            ctx.strokeStyle = "#16846E"
                            ctx.lineWidth = 2
                            ctx.beginPath()
                            for (var i = 0; i < v.length; i++) {
                                var x = i / (v.length - 1) * (width - 4) + 2
                                var y = 3 + (1 - (v[i] - lo) / span) * (height - 6)
                                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                            }
                            ctx.stroke()
                        }
                    }
                    TextLabel {
                        Layout.fillWidth: true
                        visible: dashboard.trainingNote !== ""
                        text: dashboard.trainingNote
                        font.pixelSize: 12; color: window.muted; wrapMode: Text.Wrap
                    }
                }
                ActionButton {
                    text: dashboard.trainingRunning ? "학습 멈춤" : "전체 통합 학습"
                    primary: !dashboard.trainingRunning
                    danger: dashboard.trainingRunning
                    enabled: dashboard.trainingBusy === "" && !dashboard.recActive
                    onClicked: dashboard.trainingRunning ? dashboard.stopTraining() : dashboard.startTraining()
                }
                ActionButton {
                    text: "내보내기"
                    enabled: !dashboard.trainingRunning && dashboard.trainingBusy === ""
                    onClicked: dashboard.exportModel()
                }
                ActionButton {
                    text: "허브 업로드"
                    enabled: !dashboard.trainingRunning && dashboard.trainingBusy === "" && !dashboard.recActive
                    onClicked: dashboard.pushToHub()
                }
                ActionButton {
                    text: "허브 받기"
                    enabled: !dashboard.trainingRunning && dashboard.trainingBusy === "" && !dashboard.recActive
                    onClicked: dashboard.pullFromHub()
                }
                ActionButton { text: "폴더 열기"; onClicked: dashboard.openExports() }
            }
        }
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
