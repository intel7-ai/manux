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
    onClosing: dashboard.requestClose()
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
                ActionButton { text: dashboard.chosenHand + " · 다시 설정"; onClicked: dashboard.beginHandSetup() }
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
                Layout.preferredWidth: 236
                Layout.fillHeight: true
                radius: 16
                color: "white"
                border.color: window.line
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 10
                    TextLabel { text: "시작하기"; font.pixelSize: 20; font.bold: true }
                    TextLabel {
                        Layout.fillWidth: true
                        text: "카메라에 손을 보여준 뒤 제어를 시작하세요."
                        color: window.muted
                        wrapMode: Text.Wrap
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
                    TextLabel { text: "포인터 프리셋"; font.bold: true }
                    ActionButton {
                        Layout.fillWidth: true
                        enabled: !dashboard.calibratingPointer
                        text: dashboard.pointerPreset
                        onClicked: dashboard.togglePointerPreset()
                    }
                    TextLabel {
                        Layout.fillWidth: true
                        text: "눌러서 방식 전환 · 바꾼 뒤 제어 시작"
                        color: window.muted
                        wrapMode: Text.Wrap
                        font.pixelSize: 11
                    }
                    ActionButton {
                        visible: dashboard.pointerOnly
                        enabled: !dashboard.calibratingPointer
                        Layout.fillWidth: true
                        text: dashboard.clickStyleLabel
                        onClicked: dashboard.toggleClickStyle()
                    }
                    ActionButton {
                        visible: dashboard.pointerOnly
                        enabled: !dashboard.calibratingPointer
                        Layout.fillWidth: true
                        text: dashboard.anchorLabel
                        onClicked: dashboard.toggleAnchor()
                    }
                    ActionButton {
                        visible: dashboard.pointerOnly
                        enabled: !dashboard.calibratingPointer && !dashboard.trackingActive
                        Layout.fillWidth: true
                        text: "트래킹 측정 (클릭 없음)"
                        onClicked: dashboard.beginTrackingTest()
                    }
                    ActionButton {
                        visible: dashboard.pointerOnly
                        enabled: !dashboard.calibratingPointer
                        Layout.fillWidth: true
                        text: dashboard.pointerCalibrated ? "방향 보정 다시 하기" : "방향 보정"
                        onClicked: dashboard.beginPointerCalibration()
                    }
                    ActionButton {
                        visible: dashboard.pointerOnly && dashboard.pointerCalibrated && !dashboard.calibratingPointer
                        Layout.fillWidth: true
                        text: "기본 방향 복원"
                        onClicked: dashboard.resetPointerCalibration()
                    }
                    TextLabel { text: "동작 안내"; font.bold: true }
                    Repeater {
                        model: dashboard.pointerOnly ? (dashboard.clickOff
                            ? [[dashboard.anchorLabel.replace("기준점 · ", "") + " 따라가기", "포인터 이동"], ["클릭", "꺼짐"]]
                            : dashboard.triggerClick
                            ? [["검지만 펴기", "포인터 이동"], ["엄지로 검지 옆 짧게 누르기", "좌클릭"],
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
                            ? "엄지가 검지 옆에 다가오면 커서가 고정되고, 누르기 직전 위치에서 클릭합니다. 상세 정보의 '방아쇠' 값이 작을수록 가깝습니다."
                            : "엄지와 중지 또는 약지를 모으면 커서가 고정됩니다. 손끝을 떼면 클릭합니다."
                        color: window.muted
                        wrapMode: Text.Wrap
                        font.pixelSize: 11
                    }
                    TextLabel {
                        visible: dashboard.pointerOnly && diagnostics.visible
                        Layout.fillWidth: true
                        text: "0 엄지 · 1 검지 · 2 중지\n3 약지 · 4 새끼\n조합: " + (dashboard.detectedContacts || "—")
                        wrapMode: Text.Wrap
                        color: window.muted
                        font.pixelSize: 12
                    }
                    Item { Layout.fillHeight: true }
                    TextLabel { text: "Esc  입력 정지"; font.pixelSize: 12; color: window.muted }
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
                        TextLabel { text: "카메라"; font.pixelSize: 20; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            implicitWidth: trackingLabel.implicitWidth + 20
                            implicitHeight: 28
                            radius: 14
                            color: "#E8F7F2"
                            TextLabel { id: trackingLabel; anchors.centerIn: parent; text: dashboard.tracking; font.pixelSize: 12 }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 10
                        color: window.navy
                        Image {
                            anchors.fill: parent
                            source: dashboard.previewUrl
                            cache: false
                            fillMode: Image.PreserveAspectFit
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

    Window {
        id: trackingWindow
        title: "MANUX 트래킹 측정"
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
