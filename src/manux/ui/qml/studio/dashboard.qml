import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".." as Current
import "../theme" as Theme

ApplicationWindow {
    id: window
    objectName: "raspJunStudio"
    readonly property bool learningDialogOpen: advancedSettings.visible || trainingCenter.visible || capturePlan.visible
        || trainingProgressPage.visible || dataCenter.visible || studioSettings.visible || pointerSettings.visible
    readonly property bool interactionBusy: learningDialogOpen || dashboard.currentPage === "studio"
    onInteractionBusyChanged: dashboard.setInteractionBusy(interactionBusy)
    width: 1140
    height: 720
    minimumWidth: 780
    minimumHeight: 560
    visible: true
    title: dashboard.appTitle
    color: "#F4F6F8"
    palette.window: "#FFFFFF"
    palette.windowText: "#112331"
    palette.base: "#F4F6F8"
    palette.text: "#112331"
    palette.button: "#EDF3F5"
    palette.buttonText: "#112331"
    palette.highlight: "#46E5BD"
    palette.highlightedText: "#112331"
    palette.mid: "#DEE5EA"
    palette.dark: "#526575"
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
    font.family: Theme.Studio.font

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
            ActionButton {
                text: "엔진 설정"
                onClicked: {
                    pointerSettings.close()
                    dashboard.command(111)
                }
            }
        }
    }

    Shortcut { sequence: "Return"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() }
    Shortcut { sequence: "Enter"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (dashboard.calibratingPointer); autoRepeat: false; onActivated: dashboard.capturePointerSample() }
    Shortcut { sequence: "Esc"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(27) }
    Shortcut { sequence: "Space"; enabled: !window.learningDialogOpen && !dashboard.trackingActive && (!pointerSettings.visible); onActivated: dashboard.command(32) }
    Shortcut { sequence: "Q"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(81) }
    Shortcut { sequence: "O"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(111) }
    Shortcut { sequence: "M"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(109) }
    Shortcut { sequence: "N"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(110) }
    Shortcut { sequence: "F"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.markFailure() }
    Shortcut { sequence: "C"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(99) }
    Shortcut { sequence: "P"; enabled: !window.learningDialogOpen && !dashboard.trackingActive; onActivated: dashboard.command(112) }

    Timer { interval: 700; running: dashboard.stage === "splash"; onTriggered: dashboard.finishSplash() }

    Current.PointerSetupPage {
        anchors.fill: parent
        z: 2
        visible: dashboard.stage === "tutorial" && dashboard.pointerSetupActive
        controller: dashboard
    }

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
                    Current.HandSkeleton { anchors.fill: parent; bridge: dashboard; imageWidth: parent.paintedWidth; imageHeight: parent.paintedHeight }
                }
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

    Current.TutorialPage {
        anchors.fill: parent
        visible: dashboard.stage === "tutorial" && !dashboard.pointerSetupActive
        controller: dashboard
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
                Image {
                    objectName: "mainBrandLogo"
                    source: "../assets/manux.svg"
                    Layout.preferredWidth: window.width < 1000 ? Theme.Studio.navWidth : Theme.Studio.navWidth * 1.3
                    Layout.preferredHeight: Theme.Studio.pad * 3
                    fillMode: Image.PreserveAspectFit
                    Accessible.name: dashboard.appName
                }
                TextLabel { visible: window.width >= 1000; text: "INPUT STUDIO"; font.pixelSize: 11; font.letterSpacing: 1.4; color: window.muted }
                Item { Layout.fillWidth: true }
                ActionButton { objectName: "openLearningCenter"; text: window.width < 1000 ? "학습" : "학습 센터"; onClicked: {dataCenter.pageIndex=0;dataCenter.open()} }
                ActionButton { objectName: "openDataStudio"; text: window.width < 1000 ? "녹화" : "녹화 보관함"; onClicked: {dataCenter.pageIndex=0;dataCenter.recordingSubTab=1;dataCenter.open()} }
                ActionButton {
                    text: window.width < 1000 ? (dashboard.chosenHand || "손 설정") : dashboard.chosenHand ? dashboard.chosenHand + " · 다시 설정" : "사용할 손 설정"
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
                ActionButton { visible: window.width >= 1000; text: "고급 설정"; onClicked: { dashboard.command(27); advancedSettings.open() } }
                ActionButton { objectName: "mainControlToggle"; text: dashboard.controlLabel; accent: true; onClicked: dashboard.command(32) }
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
                    Current.QuickSpeedControls { Layout.fillWidth: true; controller: dashboard }
                    ActionButton { text: "동작 다시 배우기"; Layout.fillWidth: true; onClicked: dashboard.beginTutorial() }
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
                            ActionButton {
                                Layout.fillWidth: true
                                visible: dashboard.pointerSetupAvailable && !dashboard.pointerOnly
                                enabled: dashboard.recMode === "off" && !dashboard.trackingActive
                                text: "커서 자동 설정"
                                onClicked: dashboard.beginPointerSetup()
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

                    Current.HandSkeleton { anchors.fill: parent; bridge: dashboard; imageWidth: parent.paintedWidth; imageHeight: parent.paintedHeight }
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
                        ActionButton { visible: dashboard.recWaiting; text: "시작"; onClicked: dashboard.recStart() }
                        ActionButton { visible: dashboard.recMode === "guided" && !dashboard.recWaiting; text: dashboard.recPaused ? "계속" : "일시정지"; onClicked: dashboard.toggleRecordingPause() }
                        ActionButton { visible: dashboard.recMode !== "off"; text: "녹화 끝"; onClicked: dashboard.setRecMode("off") }
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
                            TextLabel {
                                Layout.fillWidth: true
                                text: dashboard.mlpBadge
                                color: dashboard.mlpBadge.indexOf("적용 중") >= 0 ? "#08775E" : window.muted
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                            }
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
    Connections {
        target: dashboard
        function onSettingsRequested() { studioSettings.open() }
        function onTrainingStarted() {
            trainingCenter.close()
            dataCenter.close()
            trainingProgressPage.open()
        }
    }

    TrainingProgressPage {
        id: trainingProgressPage
        controller: dashboard
        onBackRequested: {
            close()
            trainingCenter.open()
        }
    }

    Popup {
        id: trainingCenter
        objectName: "trainingCenter"
        anchors.centerIn: parent
        width: Math.min(1040, window.width - 40)
        height: Math.min(820, window.height - 40)
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
                    TextLabel { text: "학습 센터"; font.pixelSize: 22; font.bold: true }
                    TextLabel {
                        text: "동작별 집중 녹화 → 원하는 동작 선택 학습 → 성적 확인 → 내보내기"
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        font.pixelSize: 12; color: window.muted
                    }
                }
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "open-training-progress"
                    text: "학습 진행 보기"
                    accent: dashboard.trainingRunning
                    onClicked: {
                        trainingCenter.close()
                        trainingProgressPage.open()
                    }
                }
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
            TextLabel {
                Layout.fillWidth: true
                    text: "평가 = 별도로 녹화한 자료의 동작 인식 결과 · 학습 = 학습 데이터 점수 · 별도 평가 전에는 평가 없음으로 표시됩니다."
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
            RowLayout {
                Layout.fillWidth: true
                ActionButton {
                    text: "녹화된 동작 전체 선택"
                    enabled: !dashboard.trainingRunning && dashboard.trainingBusy === ""
                    onClicked: dashboard.selectAllTraining(true)
                }
                ActionButton {
                    text: "선택 해제"
                    enabled: !dashboard.trainingRunning && dashboard.trainingBusy === ""
                    onClicked: dashboard.selectAllTraining(false)
                }
                TextLabel {
                    Layout.fillWidth: true
                    text: dashboard.trainingSelectionText
                    font.pixelSize: 12; wrapMode: Text.Wrap
                }
            }
            TextLabel {
                Layout.fillWidth: true
                    text: "새 모델은 시험 모드로 시작합니다. 선택하지 않은 분류기는 유지하며, 별도 평가를 통과한 모델만 동작별로 적용할 수 있습니다."
                font.pixelSize: 11; color: window.muted; wrapMode: Text.Wrap
            }
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
                        model: dashboard.studioTrainingRows
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
                                CheckBox {
                                    objectName: "train-select-" + row.modelData.key
                                    visible: !row.modelData.free
                                    checked: row.modelData.selected
                                    enabled: !dashboard.trainingRunning && dashboard.trainingBusy === ""
                                             && (row.modelData.can_train || checked)
                                    Accessible.name: row.modelData.name + " 학습 선택"
                                    onClicked: dashboard.selectTrainingTask(row.modelData.key, checked)
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 5
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8
                                        TextLabel { text: row.modelData.name; font.pixelSize: 16; font.bold: true; Layout.fillWidth: true; wrapMode: Text.Wrap }
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
                                    TextLabel {
                                        text: row.modelData.model_text + (row.modelData.free ? " · 선택 학습 시 자동 포함"
                                              : row.modelData.can_train ? " · 학습에 사용할 녹화 " + row.modelData.recordings + "개"
                                              : " · 집중 녹화 후 선택 가능")
                                        font.pixelSize: 11; color: window.muted
                                        Layout.fillWidth: true; wrapMode: Text.Wrap
                                    }
                                    TextLabel {
                                        visible: !row.modelData.free
                                        text: row.modelData.interaction_text
                                        Layout.fillWidth: true; wrapMode: Text.Wrap
                                        font.pixelSize: 11; color: window.muted
                                    }
                                    RowLayout {
                                        visible: !row.modelData.free
                                        Layout.fillWidth: true
                                        TextLabel { text: "TCN 동작"; font.pixelSize: 12 }
                                        ComboBox {
                                            objectName: "tcn-mode-" + row.modelData.key
                                            model: ["기존 규칙", "시험 · 기록만", "TCN 적용"]
                                            currentIndex: row.modelData.tcn_mode
                                            enabled: row.modelData.tcn_available && !dashboard.trainingRunning && dashboard.trainingBusy === ""
                                            onActivated: dashboard.setTaskTcnMode(row.modelData.key, currentIndex)
                                        }
                                        TextLabel {
                                            Layout.fillWidth: true; wrapMode: Text.Wrap
                                            font.pixelSize: 11; color: window.muted
                                            text: !row.modelData.tcn_available ? "학습 후 선택 가능" : row.modelData.tcn_mode === 1 ? "마우스 동작을 바꾸지 않고 비교 기록" : row.modelData.tcn_mode === 2 ? "검증된 TCN으로 이 동작 판정" : "이 동작은 기존 규칙 사용"
                                        }
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
                                    ActionButton {
                                        objectName: "train-single-" + row.modelData.key
                                        text: "이 동작만 학습"
                                        visible: row.modelData.can_train
                                        accent: true
                                        enabled: !dashboard.trainingRunning && dashboard.recMode === "off" && dashboard.trainingBusy === ""
                                        onClicked: dashboard.trainSelectedGesture(row.modelData.key)
                                    }
                                    ActionButton {
                                        objectName: "focus-record-" + row.modelData.key
                                        text: "집중 녹화 설정"
                                        accent: row.modelData.level === "need"
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
                              : "선택한 동작만 자세별로 평가하고 학습합니다. 기존 다른 동작의 모델은 유지됩니다."
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
                    TextLabel {
                        text: dashboard.trainingProgressSummary
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        font.pixelSize: 12; color: window.muted
                    }
                    TextLabel {
                        Layout.fillWidth: true
                        visible: dashboard.trainingNote !== ""
                        text: dashboard.trainingNote
                        font.pixelSize: 12; color: window.muted; wrapMode: Text.Wrap
                    }
                }
                ActionButton {
                    text: dashboard.trainingRunning ? "학습 멈춤" : "선택 동작 학습 (" + dashboard.trainingSelectedCount + ")"
                    accent: !dashboard.trainingRunning
                    enabled: dashboard.trainingBusy === "" && dashboard.recMode === "off"
                             && (dashboard.trainingRunning || dashboard.trainingSelectedCount > 0)
                    onClicked: dashboard.trainingRunning ? dashboard.stopTraining() : dashboard.startTraining()
                }
                ActionButton {
                    text: "내보내기"
                    enabled: !dashboard.trainingRunning && dashboard.trainingBusy === ""
                    onClicked: dashboard.exportModel()
                }
                ActionButton { text: "폴더 열기"; onClicked: dashboard.openExports() }
            }
        }
    }

    RecordingPlanDialog { id: capturePlan; objectName: "recordingPlanDialog"; editor: dashboard.recordingPlan }

    // Shared primary learning center: same recording, model and sharing UI in both layouts.
    Current.LearningCenter {
        id: dataCenter
        objectName: "studioDataManagement"
        controller: dashboard
        onRecordingRequested: capturePlan.open()
        onProgressRequested: trainingProgressPage.open()
    }
    SettingsDialog {
        id: studioSettings
        controller: dashboard
        onDataRequested: { close(); dataCenter.open() }
        onAdvancedRequested: advancedSettings.open()
    }

    Current.AdvancedSettings { id: advancedSettings; controller: dashboard }

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
                    font.family: Theme.Studio.font
                }
            }
        }
    }

    Current.FingerTracker { controller: dashboard }

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
                    font.family: Theme.Studio.font
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
