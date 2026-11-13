// Original dialog and controls: origin/feat/hand-mouse-20261112 @ a700452.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "ai_theme" as Theme
Item {
    id: window
    // Source window is 1280x860. Keep that workspace for its unchanged two-column dialog
    // in a native popup, without resizing the user's existing main window.
    width: Math.max(1280, parent ? parent.width : 1280)
    height: Math.max(860, parent ? parent.height : 860)
    Component.onCompleted: {
        llmDialog.popupType = Popup.Window
        confirmDialog.popupType = Popup.Window
    }
    required property var dashboard
    readonly property bool opened: llmDialog.visible || confirmDialog.visible
    onOpenedChanged: if (opened) dashboard.refreshContext()
    readonly property color navy: Theme.Studio.ink
    readonly property color ink: Theme.Studio.ink
    readonly property color muted: Theme.Studio.muted
    readonly property color mint: Theme.Studio.mint
    readonly property color primaryBlue: Theme.Studio.mintDark
    readonly property color primaryBlueDark: Theme.Studio.mintDark
    readonly property color dangerRed: Theme.Studio.danger
    readonly property color line: Theme.Studio.line
    function open() { llmDialog.open() }
    function close() { llmDialog.close() }
    AiDrawOverlay { dashboard: window.dashboard }
    Connections {
        target: dashboard
        function onOverlapAsk(kind, what, words) {
            confirmDialog.ask("겹치는 동작이 있어요", words, "그래도 저장", function() { dashboard.overlapConfirmed(kind, what) })
        }
    }
    function stepText(st) {
        var names = {"key": "단축키", "type": "글자 입력", "wait": "기다리기", "open": "열기", "mouse": "마우스", "youtube": "유튜브 키"}
        if (st.do === "wait") return st.value + "초 기다리기"
        if (st.do === "type") return "'" + st.value + "' 입력"
        if (st.do === "open") return st.value + " 열기"
        return (names[st.do] || st.do) + " " + st.value
    }
    component TextLabel: Label {
        color: window.navy
        font.family: Theme.Studio.font
        font.pixelSize: 14
    }

    component ActionButton: Button {
        id: button
        focusPolicy: Qt.NoFocus
        property bool accent: false
        property bool primary: false
        property bool danger: false
        implicitHeight: 40
        leftPadding: 14
        rightPadding: 14
        opacity: enabled ? 1 : 0.42
        contentItem: TextLabel {
            text: button.text
            font.pixelSize: Theme.Studio.body
            font.weight: Font.DemiBold
            color: button.danger ? Theme.Studio.dangerText
                : (button.primary || button.down) ? Theme.Studio.mintText : Theme.Studio.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        // Command styling only; all original button behavior is preserved below.
        background: Rectangle {
            radius: Theme.Studio.radius
            color: button.danger ? (button.down ? Theme.Studio.dangerPressed : Theme.Studio.dangerFill)
                : button.down ? Theme.Studio.mintPressed
                : button.primary ? Theme.Studio.mint
                : button.accent ? Theme.Studio.mintWash
                : button.hovered ? Theme.Studio.hover : Theme.Studio.paper
            border.color: button.activeFocus || button.accent ? Theme.Studio.mintDark
                : (button.primary || button.danger) ? "transparent" : Theme.Studio.line
            border.width: button.activeFocus ? 2 : 1
        }
    }

    component CompactButton: Button {
        id: cbutton
        focusPolicy: Qt.NoFocus
        property bool accent: false
        property bool primary: false
        property bool danger: false
        implicitHeight: 30
        leftPadding: 10
        rightPadding: 10
        opacity: enabled ? 1 : 0.42
        contentItem: TextLabel {
            text: cbutton.text
            font.pixelSize: 12
            font.weight: Font.DemiBold
            color: cbutton.danger ? Theme.Studio.dangerText
                : (cbutton.primary || cbutton.down) ? Theme.Studio.mintText : Theme.Studio.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: Theme.Studio.radius
            color: cbutton.danger ? (cbutton.down ? Theme.Studio.dangerPressed : Theme.Studio.dangerFill)
                : cbutton.down ? Theme.Studio.mintPressed
                : cbutton.primary ? Theme.Studio.mint
                : cbutton.accent ? Theme.Studio.mintWash
                : cbutton.hovered ? Theme.Studio.hover : Theme.Studio.paper
            border.color: cbutton.activeFocus || cbutton.accent ? Theme.Studio.mintDark
                : (cbutton.primary || cbutton.danger) ? "transparent" : Theme.Studio.line
            border.width: cbutton.activeFocus ? 2 : 1
        }
    }

    component SectionLabel: TextLabel {
        font.pixelSize: 13
        font.weight: Font.DemiBold
        Layout.topMargin: 4
    }

    component FormLabel: TextLabel {
        Layout.preferredWidth: 84
        Layout.alignment: Qt.AlignTop
        topPadding: 6
        font.pixelSize: 12
        font.weight: Font.DemiBold
        color: window.muted
    }

    component Divider: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: window.line
    }

    component Chip: Rectangle {
        id: chip
        property alias text: chipLabel.text
        property bool warn: false
        implicitWidth: chipLabel.implicitWidth + 18
        implicitHeight: 26
        Layout.minimumWidth: Math.min(implicitWidth, 56)
        Layout.preferredWidth: implicitWidth
        radius: Theme.Studio.radius
        color: warn ? Theme.Studio.amberWash : Theme.Studio.mintWash
        border.color: warn ? Theme.Studio.sourcePreviewHighlight : "transparent"
        clip: true
        TextLabel {
            id: chipLabel
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 9
            anchors.rightMargin: 9
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            font.pixelSize: 12
            color: chip.warn ? Theme.Studio.amber : window.navy
        }
    }

    component StyledComboBox: ComboBox {
        id: scombo
        implicitHeight: 32
        leftPadding: 10
        rightPadding: 28
        font.pixelSize: 12
        contentItem: TextLabel {
            text: scombo.displayText
            font.pixelSize: 12
            color: window.navy
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        indicator: TextLabel {
            x: scombo.width - width - 9
            y: (scombo.availableHeight - height) / 2
            text: "▾"
            font.pixelSize: 12
            color: window.muted
        }
        background: Rectangle {
            radius: Theme.Studio.radius
            color: scombo.down ? Theme.Studio.mintWash : Theme.Studio.paper
            border.color: scombo.activeFocus ? Theme.Studio.mintDark : Theme.Studio.line
            border.width: scombo.activeFocus ? 2 : 1
        }
    }

    component BusySpinner: Item {
        id: spinnerRoot
        property bool active: false
        property color strokeColor: Theme.Studio.mintDark
        implicitWidth: 20
        implicitHeight: 20
        visible: active
        Canvas {
            id: spinnerCanvas
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var cx = width / 2, cy = height / 2
                var r = Math.min(width, height) / 2 - 2.5
                ctx.lineWidth = 2.6
                ctx.lineCap = "round"
                ctx.strokeStyle = Theme.Studio.line
                ctx.beginPath()
                ctx.arc(cx, cy, r, 0, Math.PI * 2)
                ctx.stroke()
                ctx.strokeStyle = spinnerRoot.strokeColor
                ctx.beginPath()
                ctx.arc(cx, cy, r, -Math.PI / 2, Math.PI * 0.85)
                ctx.stroke()
            }
            RotationAnimator on rotation {
                from: 0
                to: 360
                duration: 850
                loops: Animation.Infinite
                running: spinnerRoot.active && spinnerRoot.visible
            }
        }
    }
    Dialog {
        id: confirmDialog
        property string message: ""
        property string confirmText: "확인"
        property var onConfirm: null
        function ask(title, message, confirmText, callback) {
            confirmDialog.title = title
            confirmDialog.message = message
            confirmDialog.confirmText = confirmText || "확인"
            confirmDialog.onConfirm = callback
            confirmDialog.open()
        }
        anchors.centerIn: Overlay.overlay
        width: Math.min(440, window.width - 48)
        modal: true
        z: 1000
        padding: 20
        topPadding: 18
        background: Rectangle { radius: Theme.Studio.radius; color: Theme.Studio.paper; border.color: window.line }
        header: TextLabel {
            text: confirmDialog.title
            font.pixelSize: 17
            font.weight: Font.DemiBold
            leftPadding: 20; rightPadding: 20; topPadding: 18
            wrapMode: Text.Wrap
        }
        contentItem: ColumnLayout {
            spacing: 18
            TextLabel {
                Layout.fillWidth: true
                text: confirmDialog.message
                wrapMode: Text.Wrap
                lineHeight: 1.25
                color: Theme.Studio.muted
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                ActionButton { text: "취소"; onClicked: confirmDialog.close() }
                ActionButton {
                    text: confirmDialog.confirmText
                    danger: true
                    onClicked: {
                        var cb = confirmDialog.onConfirm
                        confirmDialog.onConfirm = null
                        confirmDialog.close()
                        if (cb) cb()
                    }
                }
            }
        }
        onClosed: onConfirm = null
    }

    // Apply Command chrome outside the imported dialog. Its entire original
    // content, validation, bindings, handlers and footer actions are preserved;
    // only explicit typed color tokens are normalized in the source hash test.
    Rectangle {
        id: commandDialogBackground
        color: Theme.Studio.surface
        radius: Theme.Studio.radius
        border.color: Theme.Studio.line
    }
    Rectangle {
        id: commandDialogHeader
        implicitHeight: 78
        color: Theme.Studio.paper
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.Studio.pad
            anchors.rightMargin: Theme.Studio.pad
            spacing: Theme.Studio.pad
            Image {
                Layout.preferredWidth: 119
                Layout.preferredHeight: 38
                source: Theme.Studio.logoSource
                fillMode: Image.PreserveAspectFit
                smooth: true
                Accessible.name: "MANUX"
            }
            Rectangle {
                Layout.preferredWidth: 1
                Layout.preferredHeight: 30
                color: Theme.Studio.line
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                TextLabel {
                    text: llmDialog.title
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }
                TextLabel {
                    text: "내 손으로, 내 방식대로."
                    font.pixelSize: Theme.Studio.small
                    color: Theme.Studio.muted
                }
            }
            TextLabel {
                text: "손 모양  →  작업 설명  →  확인·저장"
                font.pixelSize: Theme.Studio.small
                color: Theme.Studio.muted
            }
        }
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Theme.Studio.line
        }
    }
    Binding { target: llmDialog; property: "background"; value: commandDialogBackground }
    Binding { target: llmDialog; property: "header"; value: commandDialogHeader }
    Binding { target: llmDialog; property: "font.family"; value: Theme.Studio.font }
    Binding { target: llmDialog; property: "palette.window"; value: Theme.Studio.surface }
    Binding { target: llmDialog; property: "palette.base"; value: Theme.Studio.paper }
    Binding { target: llmDialog; property: "palette.button"; value: Theme.Studio.paper }
    Binding { target: llmDialog; property: "palette.buttonText"; value: Theme.Studio.ink }
    Binding { target: llmDialog; property: "palette.text"; value: Theme.Studio.ink }
    Binding { target: llmDialog; property: "palette.windowText"; value: Theme.Studio.ink }
    Binding { target: llmDialog; property: "palette.placeholderText"; value: Theme.Studio.muted }
    Binding { target: llmDialog; property: "palette.highlight"; value: Theme.Studio.mint }
    Binding { target: llmDialog; property: "palette.highlightedText"; value: Theme.Studio.mintText }
    Binding { target: llmDialog.footer; property: "palette.window"; value: Theme.Studio.paper }
    Binding { target: llmPanel; property: "color"; value: Theme.Studio.surface }
    Binding { target: llmPanel; property: "radius"; value: Theme.Studio.radius }

    Dialog {
        id: llmDialog
        objectName: "llmDialog"
        anchors.centerIn: parent
        width: Math.min(1560, window.width - 48)
        height: window.height - 48
        modal: true
        title: "말·글로 연결 (AI)"
        // 2026-11-11: [새 동작 등록] sits at the bottom beside Close - "done with this one, on to the next" (it was in
        // the teaching row, among the buttons of the shape being taught)
        footer: DialogButtonBox {
            onRejected: llmDialog.close()
            // (both buttons written out: with standardButtons + one more button only that one showed, 2026-11-11)
            Button {
                objectName: "llmClose"
                text: "닫기"
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
            Button {
                objectName: "poseTeachNew"
                readonly property var pose: dashboard.llmContext.pose || {}
                readonly property var teach: dashboard.llmContext.teach || {}
                text: "🆕 새 동작 등록"
                font.bold: true
                enabled: pose.state !== "countdown" && pose.state !== "holding" && teach.state !== "training"
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: {
                    llmScroll.ScrollBar.vertical.position = 0      // the look is shown at the top
                    dashboard.poseTeachNew()
                }
            }
        }
        contentItem: Item {
                    // =========================================================================
                    // SUB-TAB 4: CONNECT GESTURES IN WORDS (LLM: Claude / OpenAI / Gemini / OpenRouter / Ollama)
                    // src/llm_binding.py proposes, the user ticks, only then it is saved. Speech = Windows voice typing.
                    // =========================================================================
                    Rectangle {
                        id: llmPanel
                        objectName: "llmPanel"
                        anchors.fill: parent
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 10
                        color: Theme.Studio.sourceSurface
                        border.color: window.line
                        readonly property var cfg: dashboard.llmSettings
                        readonly property var result: dashboard.llmResult
                        readonly property var proposals: result.proposals || []
                        ScrollView {
                            id: llmScroll
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.left: parent.left
                            anchors.right: llmListPane.left
                            anchors.margins: 12
                            clip: true
                            contentWidth: availableWidth
                            ColumnLayout {
                                width: parent.width
                                spacing: 8

                                TextLabel {
                                    Layout.fillWidth: true
                                    text: "하고 싶은 연결을 말하거나 적으면, AI가 동작 연결을 제안합니다. 확인하고 고른 것만 저장돼요."
                                    font.bold: true
                                    wrapMode: Text.Wrap
                                }
                                // 1. where the request goes - a card that folds to one line once it is set up (2026-11-11: the
                                // connection rows took the top of the window and pushed the gesture part below the fold)
                                Rectangle {
                                    id: llmSetup
                                    objectName: "llmSetup"
                                    Layout.fillWidth: true
                                    radius: 8
                                    color: Theme.Studio.sourcePaper
                                    border.color: window.line
                                    property bool userOpen: false
                                    // no model or no key yet: always open, there is nothing to fold
                                    readonly property bool ready: (llmPanel.cfg.modelShown || "") !== "" && !!llmPanel.cfg.hasKey
                                    readonly property bool open: userOpen || !ready
                                    readonly property string providerLabel: {
                                        var ps = dashboard.llmProviders
                                        for (var i = 0; i < ps.length; i++) if (ps[i].id === llmPanel.cfg.provider) return ps[i].label
                                        return llmPanel.cfg.provider || ""
                                    }
                                    implicitHeight: setupCol.implicitHeight + 16
                                    ColumnLayout {
                                        id: setupCol
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: 8
                                        spacing: 8
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            Text {
                                                text: llmSetup.open ? "▾" : "▸"
                                                font.pixelSize: 14
                                                color: window.muted
                                            }
                                            Text {
                                                objectName: "llmSetupSummary"
                                                Layout.fillWidth: true
                                                text: "AI 연결  ·  " + llmSetup.providerLabel
                                                      + "  ·  " + ((llmPanel.cfg.modelShown || "") !== "" ? llmPanel.cfg.modelShown : "모델 미정")
                                                      + (llmPanel.cfg.needsKey ? "  ·  " + (llmPanel.cfg.hasKey ? "🔑 " + llmPanel.cfg.keySource : "⚠ 키 없음") : "")
                                                font.pixelSize: 13
                                                font.bold: true
                                                color: llmSetup.ready ? window.ink : Theme.Studio.sourceSetupError
                                                elide: Text.ElideRight
                                            }
                                            CompactButton {
                                                objectName: "llmSetupToggle"
                                                visible: llmSetup.ready
                                                text: llmSetup.open ? "접기" : "연결 설정"
                                                onClicked: llmSetup.userOpen = !llmSetup.userOpen
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            visible: llmSetup.open
                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 6
                                                FormLabel { text: "AI 연결" }
                                                Flow {
                                                    Layout.fillWidth: true
                                                    spacing: 6
                                                    Repeater {
                                                        model: dashboard.llmProviders
                                                        delegate: CompactButton {
                                                            required property var modelData
                                                            text: modelData.label
                                                            accent: llmPanel.cfg.provider === modelData.id
                                                            enabled: !dashboard.llmBusy
                                                            onClicked: dashboard.setLlmProvider(modelData.id)
                                                        }
                                                    }
                                                }
                                            }
                                            TextLabel {
                                                Layout.fillWidth: true
                                                Layout.leftMargin: 90
                                                text: {
                                                    var ps = dashboard.llmProviders
                                                    for (var i = 0; i < ps.length; i++) if (ps[i].id === llmPanel.cfg.provider) return ps[i].note
                                                    return ""
                                                }
                                                color: window.muted
                                                font.pixelSize: 12
                                                wrapMode: Text.Wrap
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 6
                                                FormLabel { text: "모델" }
                                                TextField {
                                                    id: llmModelField
                                                    Layout.fillWidth: true
                                                    text: llmPanel.cfg.model || ""
                                                    placeholderText: llmPanel.cfg.modelShown ? ("기본: " + llmPanel.cfg.modelShown) : "모델 이름 (목록에서 고르거나 직접 입력)"
                                                    font.pixelSize: 13
                                                    onEditingFinished: dashboard.setLlmModel(text)
                                                }
                                                StyledComboBox {
                                                    visible: dashboard.llmModels.length > 0
                                                    Layout.preferredWidth: 240
                                                    model: dashboard.llmModels
                                                    onActivated: function (i) { dashboard.setLlmModel(dashboard.llmModels[i]); llmModelField.text = dashboard.llmModels[i] }
                                                }
                                                CompactButton {
                                                    text: "모델 목록 불러오기"
                                                    enabled: !dashboard.llmBusy
                                                    onClicked: dashboard.loadLlmModels()
                                                }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 6
                                                visible: llmPanel.cfg.needsKey
                                                FormLabel { text: "API 키" }
                                                TextField {
                                                    id: llmKeyField
                                                    Layout.fillWidth: true
                                                    echoMode: TextInput.Password
                                                    placeholderText: llmPanel.cfg.keySource + " · 새 키를 붙여 넣고 [키 저장]"
                                                    font.pixelSize: 13
                                                }
                                                CompactButton {
                                                    text: "키 저장"
                                                    enabled: llmKeyField.text.length > 0
                                                    onClicked: { dashboard.saveLlmKey(llmKeyField.text); llmKeyField.text = "" }
                                                }
                                                CompactButton {
                                                    text: "키 지우기"
                                                    visible: llmPanel.cfg.keySaved
                                                    onClicked: dashboard.clearLlmKey()
                                                }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 6
                                                FormLabel { text: "주소 (고급)" }
                                                TextField {
                                                    Layout.fillWidth: true
                                                    text: llmPanel.cfg.baseUrl || ""
                                                    placeholderText: llmPanel.cfg.defaultBase ? ("기본: " + llmPanel.cfg.defaultBase) : "기본 주소 사용"
                                                    font.pixelSize: 12
                                                    onEditingFinished: dashboard.setLlmBaseUrl(text)
                                                }
                                            }
                                        }
                                    }
                                    TapHandler {
                                        // a tap anywhere on the folded card opens it
                                        enabled: llmSetup.ready && !llmSetup.open
                                        onTapped: llmSetup.userOpen = true
                                    }
                                }
                                Divider {}

                                // 2. the request, typed or spoken (Windows voice typing writes into this box)
                                // what 이/저 point to: a hand shape shown to the camera, a key combination pressed
                                TextLabel {
                                    Layout.fillWidth: true
                                    text: "\"이 손모양이면 저렇게 해줘\"처럼 말하려면 먼저 가리켜 두세요 (선택)"
                                    font.bold: true
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    readonly property var pose: dashboard.llmContext.pose || {}
                                    id: poseRow
                                    ActionButton {
                                        objectName: "poseProbeButton"
                                        text: "✋ 손 모양·움직임 보여주기"
                                        enabled: poseRow.pose.state !== "countdown" && poseRow.pose.state !== "holding"
                                        onClicked: dashboard.startPoseProbe()
                                    }
                                    // 2026-11-11: what was looked at (the user's own hand, joints drawn) -> what it was told as
                                    Rectangle {
                                        objectName: "poseProbeShot"
                                        visible: poseRow.pose.state === "done" && (dashboard.llmContext.shot || "") !== ""
                                        Layout.preferredWidth: 112
                                        Layout.preferredHeight: 112
                                        radius: 6
                                        clip: true
                                        color: Theme.Studio.sourceCaptureSurface
                                        // the look replayed like a GIF (memory only, src/pose_probe.PoseProbe.clip); one
                                        // picture when there is a single frame
                                        id: poseShot
                                        property int frame: 0
                                        readonly property int frames: dashboard.llmContext.shotFrames || 0
                                        onVisibleChanged: frame = 0
                                        onFramesChanged: frame = 0
                                        Timer {
                                            interval: 66
                                            repeat: true
                                            running: poseShot.visible && poseShot.frames > 1
                                            onTriggered: poseShot.frame = (poseShot.frame + 1) % poseShot.frames
                                        }
                                        Image {
                                            anchors.fill: parent
                                            source: !poseShot.visible ? ""
                                                  : poseShot.frames > 1 ? (dashboard.llmContext.shot + "/" + poseShot.frame)
                                                  : dashboard.llmContext.shot
                                            fillMode: Image.PreserveAspectFit
                                            cache: false
                                            smooth: true
                                        }
                                    }
                                    // 2026-11-11: the hand emoji that stood here (🖐 ✌ …) is gone - often wrong, and the photo beside it shows the hand anyway.
                                    // The arrow of a movement / turn stays: the photo does not show which way the hand went.
                                    Text {
                                        objectName: "poseProbeArrow"
                                        readonly property var arrows: ({"left": "←", "right": "→", "up": "↑", "down": "↓",
                                            "diag_up_left": "↖", "diag_up_right": "↗", "diag_down_left": "↙", "diag_down_right": "↘",
                                            "rotate_cw": "↻", "rotate_ccw": "↺"})
                                        visible: poseRow.pose.state === "done" && text !== ""
                                        text: poseRow.pose.kind === "motion" ? (arrows[poseRow.pose.direction] || "")
                                            : poseRow.pose.kind === "rotate" ? (arrows[poseRow.pose.rotate] || "") : ""
                                        font.pixelSize: 32
                                        color: Theme.Studio.mintDark
                                    }
                                    TextLabel {
                                        Layout.fillWidth: true
                                        wrapMode: Text.Wrap
                                        font.pixelSize: 13
                                        // an approximate answer (the nearest known shape / movement) says so
                                        readonly property string approxText: poseRow.pose.approx ? " · 가장 가까운 것으로 봤어요" : ""
                                        readonly property var teach: dashboard.llmContext.teach || {}
                                        color: poseRow.pose.state === "failed" || (poseRow.pose.unknown && !teach.adding) ? Theme.Studio.danger
                                             : poseRow.pose.state === "done" ? Theme.Studio.mintDark : window.muted
                                        font.bold: poseRow.pose.state === "countdown" || poseRow.pose.state === "done"
                                        text: poseRow.pose.state === "countdown"
                                                ? ((teach.adding ? "'" + teach.focusName + "' 모양으로 " : "") + "손을 준비하세요… " + poseRow.pose.seconds)
                                            // [한 번 더 보여주기]: this look is one more example of the shape being taught
                                            : poseRow.pose.state === "done" && teach.adding ? ("'" + teach.focusName + "' 견본으로 추가하는 중…")
                                            : poseRow.pose.state === "done" && !poseRow.pose.kind && poseRow.pose.taught
                                                ? ("✓ '" + poseRow.pose.name + "' 견본으로 저장했어요"
                                                   + (String(poseRow.pose.posture).indexOf("custom_") === 0 ? " · [학습] → [적용] 뒤부터 앱이 알아봐요" : ""))
                                            : poseRow.pose.state === "holding" ? ("그대로 유지! " + Math.round(poseRow.pose.progress * 100) + "%")
                                            // [새 동작 등록]: whatever the app took the look for, it is to be named
                                            : poseRow.pose.state === "done" && !poseRow.pose.kind && teach.naming
                                                ? ((poseRow.pose.unknown ? "목록에 없는 손 모양이에요."
                                                    : "앱은 '" + poseRow.pose.name + "'(으)로 봤어요. 다른 모양이면")
                                                   + " 아래에 새 이름을 지어 저장해 주세요.")
                                            // 2026-11-11: no listed shape / movement is said so, not forced into the nearest one
                                            : poseRow.pose.state === "done" && poseRow.pose.kind === "unknown_move" ? poseRow.pose.message
                                            : poseRow.pose.state === "done" && poseRow.pose.unknown
                                                ? "목록에 없는 손 모양이에요. 아래에 이름을 지어 새 손 모양으로 저장해 주세요."
                                            : poseRow.pose.state === "done" && poseRow.pose.kind === "rotate"
                                                ? ("이 움직임 = " + poseRow.pose.name + " 모양으로 " + poseRow.pose.rotate_name + approxText)
                                            : poseRow.pose.state === "done" && poseRow.pose.kind === "drawn"
                                                ? ("이 움직임 = " + poseRow.pose.shape_name + " 그리기 (" + Math.round(poseRow.pose.score * 100)
                                                   + "%) · 그리기 모드에서 검지로 그리면 실행돼요")
                                            : poseRow.pose.state === "done" && poseRow.pose.kind === "motion"
                                                ? ("이 움직임 = " + poseRow.pose.name + " 모양으로 " + poseRow.pose.direction_name + approxText)
                                            : poseRow.pose.state === "done" ? ("이 손모양 = " + poseRow.pose.name + approxText
                                                                               + (poseRow.pose.gesture_key ? "" : " · 아직 연결 없음 (연결하면 새 동작이 돼요)"))
                                            : poseRow.pose.state === "failed" ? poseRow.pose.message
                                            : "3초 뒤 2초 동안 카메라로 봅니다 · 가만히 = 손 모양, 한 모양으로 한 방향 움직이기 = 움직임"
                                    }
                                    CompactButton {
                                        visible: poseRow.pose.state === "done" || poseRow.pose.state === "failed"
                                        text: "지우기"
                                        onClicked: dashboard.clearPoseProbe()
                                    }
                                }
                                // 2026-11-12: this look is read as another gesture's hand too (src/gesture_overlap.py)
                                TextLabel {
                                    objectName: "llmOverlapNote"
                                    visible: text.length > 0
                                    Layout.fillWidth: true
                                    text: dashboard.llmContext.overlap ? "⚠ " + dashboard.llmContext.overlap : ""
                                    color: Theme.Studio.sourceOverlapWarning
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                }
                                // 2026-11-11: teach the shown hand shape to a small MLP (src/posture_teach.py)
                                RowLayout {
                                    id: teachSaveRow
                                    Layout.fillWidth: true
                                    spacing: 6
                                    readonly property var teach: dashboard.llmContext.teach || {}
                                    visible: poseRow.pose.state === "done" && !poseRow.pose.kind && !poseRow.pose.taught && !teach.adding
                                    // the shape being taught: one press adds this look to it, whatever the rules called it
                                    ActionButton {
                                        objectName: "poseTeachAddFocus"
                                        visible: !poseRow.pose.taught && (teachSaveRow.teach.focus || "") !== ""
                                        text: "➕ '" + (teachSaveRow.teach.focusName || "") + "' 견본으로 추가"
                                        onClicked: dashboard.poseTeachSave(teachSaveRow.teach.focus)
                                    }
                                    // a look that is no listed shape (or a listed one that should be its own): name it
                                    TextField {
                                        id: teachNewName
                                        objectName: "poseTeachNewName"
                                        Layout.preferredWidth: 130
                                        visible: !poseRow.pose.taught
                                        placeholderText: "새 손 모양 이름"
                                        onVisibleChanged: if (visible && teachSaveRow.teach.naming) forceActiveFocus()
                                        onAccepted: if (text.trim() !== "") { dashboard.poseTeachSaveNew(text); text = "" }
                                        maximumLength: 20
                                        font.pixelSize: 12
                                    }
                                    CompactButton {
                                        objectName: "poseTeachSaveNew"
                                        visible: !poseRow.pose.taught
                                        text: "새 손 모양으로 저장"
                                        enabled: teachNewName.text.trim() !== ""
                                        onClicked: { dashboard.poseTeachSaveNew(teachNewName.text); teachNewName.text = "" }
                                    }
                                    TextLabel {
                                        visible: poseRow.pose.taught || (teachSaveRow.teach.focus || "") === ""
                                        text: poseRow.pose.taught ? "✓ 견본 저장됨" : "또는 목록의 이 모양:"
                                        font.pixelSize: 12
                                        color: poseRow.pose.taught ? Theme.Studio.mintDark : window.muted
                                    }
                                    StyledComboBox {
                                        id: teachChoice
                                        objectName: "poseTeachChoice"
                                        Layout.preferredWidth: 190
                                        model: dashboard.postureChoices
                                        textRole: "name"
                                        currentIndex: {
                                            var c = dashboard.postureChoices
                                            for (var i = 0; i < c.length; i++) if (c[i].key === poseRow.pose.posture) return i
                                            return poseRow.pose.unknown ? -1 : 0
                                        }
                                    }
                                    CompactButton {
                                        objectName: "poseTeachSave"
                                        text: "이 이름으로 견본 저장"
                                        enabled: !poseRow.pose.taught && teachChoice.currentIndex >= 0
                                        onClicked: dashboard.poseTeachSave(dashboard.postureChoices[teachChoice.currentIndex].key)
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    readonly property var teach: dashboard.llmContext.teach || {}
                                    id: teachRow
                                    visible: teach.hasExamples || teach.active !== "" || teach.text !== ""
                                    CompactButton {
                                        objectName: "poseTeachTrain"
                                        text: teachRow.teach.state === "training" ? "학습 중…"
                                            : teachRow.teach.focus ? ("🧠 '" + teachRow.teach.focusName + "' 학습") : "🧠 가르친 손 모양 학습 (MLP)"
                                        enabled: teachRow.teach.hasExamples && teachRow.teach.state !== "training"
                                        onClicked: dashboard.poseTeachTrain()
                                    }
                                    TextLabel {
                                        Layout.fillWidth: true
                                        wrapMode: Text.Wrap
                                        font.pixelSize: 12
                                        color: teachRow.teach.state === "error" ? Theme.Studio.danger
                                             : teachRow.teach.state === "ready" ? (teachRow.teach.canApply ? Theme.Studio.mintDark : Theme.Studio.danger)
                                             : window.muted
                                        objectName: "poseTeachGuide"
                                        // the shape being taught now, step by step - nothing about the other taught shapes
                                        text: [teachRow.teach.steps || "", teachRow.teach.text || "", teachRow.teach.summary || ""]
                                              .filter(function (t) { return t !== "" }).join("\n")
                                    }
                                    ActionButton {
                                        objectName: "poseTeachAgain"
                                        visible: (teachRow.teach.focus || "") !== "" && teachRow.teach.state !== "training"
                                                 && poseRow.pose.state !== "countdown" && poseRow.pose.state !== "holding"
                                        text: "✋ 한 번 더 보여주기"
                                        onClicked: dashboard.poseTeachAgain()
                                    }
                                    CompactButton {
                                        visible: teachRow.teach.state === "ready" && teachRow.teach.canApply
                                        text: "적용"
                                        onClicked: dashboard.poseTeachApply()
                                    }
                                    CompactButton {
                                        visible: teachRow.teach.state === "ready"
                                        text: "버리기"
                                        onClicked: dashboard.poseTeachDiscard()
                                    }
                                    CompactButton {
                                        visible: teachRow.teach.active !== "" && teachRow.teach.state !== "ready"
                                        text: "끄기"
                                        onClicked: dashboard.poseTeachOff()
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    ActionButton {
                                        objectName: "keyCaptureButton"
                                        text: dashboard.llmContext.capturing ? "키를 누르세요…" : "⌨ 키 기억하기"
                                        enabled: !dashboard.llmContext.capturing
                                        onClicked: dashboard.captureKey()
                                    }
                                    TextLabel {
                                        Layout.fillWidth: true
                                        wrapMode: Text.Wrap
                                        font.pixelSize: 13
                                        font.bold: dashboard.llmContext.key !== ""
                                        color: dashboard.llmContext.key !== "" ? Theme.Studio.mintDark : window.muted
                                        text: dashboard.llmContext.key !== "" ? ("저렇게 = " + dashboard.llmContext.key)
                                            : "누른 뒤 8초 안에 원하는 키 조합을 직접 누르면 그 조합 하나만 기억해요"
                                    }
                                    CompactButton {
                                        visible: dashboard.llmContext.key !== ""
                                        text: "지우기"
                                        onClicked: dashboard.clearCapturedKey()
                                    }
                                }
                                TextLabel { text: "무엇을 연결할까요?"; font.bold: true }
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 96
                                    radius: 8
                                    color: Theme.Studio.paper
                                    border.color: llmRequest.activeFocus ? Theme.Studio.mintDark : window.line
                                    border.width: llmRequest.activeFocus ? 2 : 1
                                    ScrollView {
                                        anchors.fill: parent
                                        anchors.margins: 4
                                        TextArea {
                                            id: llmRequest
                                            objectName: "llmRequest"
                                            wrapMode: TextEdit.Wrap
                                            font.pixelSize: 14
                                            placeholderText: "예) 유튜브에서는 엄지+약지로 자막 켜고 끄기\n예) 크롬에서 엄지+중지는 새 탭, 다른 곳에서는 그대로\n예) V 사인으로 화면 캡처 (Win+Shift+S)"
                                            background: null
                                        }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    ActionButton {
                                        text: "🎤 말로 입력"
                                        enabled: !dashboard.llmBusy
                                        onClicked: { llmRequest.forceActiveFocus(); llmRequest.cursorPosition = llmRequest.length; dashboard.startVoiceTyping() }
                                    }
                                    ActionButton {
                                        objectName: "llmAskButton"
                                        text: dashboard.llmBusy ? "묻는 중…" : "제안 받기"
                                        primary: true
                                        enabled: !dashboard.llmBusy && llmRequest.text.trim().length > 0 && llmPanel.cfg.hasKey
                                        onClicked: dashboard.askLlm(llmRequest.text)
                                    }
                                    BusySpinner { active: dashboard.llmBusy }
                                    TextLabel {
                                        Layout.fillWidth: true
                                        text: llmPanel.cfg.privacy
                                        color: window.muted
                                        font.pixelSize: 11
                                        wrapMode: Text.Wrap
                                    }
                                }
                                TextLabel {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: dashboard.llmMessage
                                    color: Theme.Studio.sourceRequestInfo
                                    font.bold: true
                                    wrapMode: Text.Wrap
                                }

                                // COMMAND AUTOMATION BEGIN
                                ColumnLayout {
                                    objectName: "commandExecutionPanel"
                                    Layout.fillWidth: true
                                    visible: (dashboard.commandExecution.message || "") !== ""
                                    TextLabel { text: "명령 실행 결과 · " + (dashboard.commandExecution.message || ""); Layout.fillWidth: true; wrapMode: Text.Wrap }
                                    CommandScript {
                                        Layout.fillWidth: true
                                        visible: value !== ""
                                        value: dashboard.commandExecution.output || ""
                                    }
                                    CompactButton {
                                        text: "실행 중지"
                                        visible: dashboard.commandExecution.running === true
                                        onClicked: dashboard.cancelCommandExecution()
                                    }
                                }
                                // COMMAND AUTOMATION END
                                // 3. the proposals: checked against the real catalogue, ticked by the user, then saved
                                TextLabel {
                                    Layout.fillWidth: true
                                    visible: (llmPanel.result.summary || "") !== ""
                                    text: "AI 요약: " + (llmPanel.result.summary || "")
                                    wrapMode: Text.Wrap
                                }
                                Repeater {
                                    model: llmPanel.result.questions || []
                                    delegate: Chip { required property var modelData; warn: true; text: "질문: " + modelData; Layout.fillWidth: true; Layout.preferredWidth: -1 }
                                }
                                Repeater {
                                    model: llmPanel.proposals
                                    delegate: Rectangle {
                                        id: proposalCard
                                        required property var modelData
                                        required property int index
                                        Layout.fillWidth: true
                                        implicitHeight: proposalCol.implicitHeight + 16
                                        radius: 8
                                        color: !modelData.ok ? Theme.Studio.sourceProposalError : (modelData.risky ? Theme.Studio.amberWash : Theme.Studio.paper)
                                        border.color: modelData.checked ? Theme.Studio.mintDark : window.line
                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.margins: 8
                                            spacing: 8
                                            CheckBox {
                                                enabled: proposalCard.modelData.ok
                                                checked: proposalCard.modelData.checked
                                                onToggled: dashboard.setLlmProposalChecked(proposalCard.index, checked)
                                            }
                                            ColumnLayout {
                                                id: proposalCol
                                                Layout.fillWidth: true
                                                spacing: 2
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    text: proposalCard.modelData.gesture_name
                                                          + (proposalCard.modelData.mode_name ? "  ·  " + proposalCard.modelData.mode_name
                                                                 + (proposalCard.modelData.mode === "all" ? "" : " 전용") : "")
                                                          + (proposalCard.modelData.app_match ? "  ·  " + proposalCard.modelData.app_match + "에서만" : "  ·  모든 프로그램")
                                                    font.bold: true
                                                    wrapMode: Text.Wrap
                                                }
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    visible: proposalCard.modelData.ok
                                                    text: proposalCard.modelData.before + "  →  "
                                                          + ((proposalCard.modelData.steps || []).length ? "여러 단계" : proposalCard.modelData.after)
                                                    font.pixelSize: 13
                                                    wrapMode: Text.Wrap
                                                }
                                                Repeater {
                                                    model: proposalCard.modelData.steps || []
                                                    delegate: TextLabel {
                                                        required property var modelData
                                                        required property int index
                                                        Layout.fillWidth: true
                                                        Layout.leftMargin: 12
                                                        text: (index + 1) + ". " + window.stepText(modelData)
                                                        font.pixelSize: 13
                                                        wrapMode: Text.Wrap
                                                    }
                                                }
                                                // COMMAND AUTOMATION BEGIN
                                                CommandScript {
                                                    objectName: "powerShellProposalScript"
                                                    Layout.fillWidth: true
                                                    visible: proposalCard.modelData.action_type === "powershell"
                                                    value: visible ? proposalCard.modelData.action_value : ""
                                                }
                                                // COMMAND AUTOMATION END
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    visible: proposalCard.modelData.risky && proposalCard.modelData.ok
                                                    text: "⚠ " + (proposalCard.modelData.warning || "")
                                                    color: Theme.Studio.amber
                                                    font.pixelSize: 12
                                                    wrapMode: Text.Wrap
                                                }
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    visible: !proposalCard.modelData.ok
                                                    text: "적용 불가: " + proposalCard.modelData.problem + " (" + proposalCard.modelData.action_type + " " + proposalCard.modelData.action_value + ")"
                                                    color: Theme.Studio.danger
                                                    font.pixelSize: 12
                                                    wrapMode: Text.Wrap
                                                }
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    visible: proposalCard.modelData.reason !== ""
                                                    text: proposalCard.modelData.reason
                                                    color: window.muted
                                                    font.pixelSize: 12
                                                    wrapMode: Text.Wrap
                                                }
                                            }
                                        }
                                    }
                                }
                                RowLayout {
                                    visible: llmPanel.proposals.length > 0
                                    spacing: 6
                                    ActionButton {
                                        objectName: "llmApplyButton"
                                        text: "선택한 연결 적용"
                                        primary: true
                                        onClicked: dashboard.applyLlmProposals()
                                    }
                                    ActionButton { text: "버리기"; onClicked: dashboard.clearLlmResult() }
                                }

                                // the hand shapes the user named (src/custom_postures.py): a wrong name can be deleted
                                Divider { Layout.topMargin: 6; visible: dashboard.customPostures.length > 0 }
                                SectionLabel {
                                    visible: dashboard.customPostures.length > 0
                                    text: "내가 이름 지은 손 모양 (" + dashboard.customPostures.length + ")"
                                }
                                Repeater {
                                    model: dashboard.customPostures
                                    delegate: Rectangle {
                                        id: customCard
                                        objectName: "customPostureCard"
                                        required property var modelData
                                        Layout.fillWidth: true
                                        implicitHeight: customRow.implicitHeight + 16
                                        radius: 8
                                        color: Theme.Studio.paper
                                        border.color: window.line
                                        RowLayout {
                                            id: customRow
                                            anchors.fill: parent
                                            anchors.margins: 8
                                            spacing: 8
                                            TextLabel { text: customCard.modelData.name; font.bold: true }
                                            TextLabel {
                                                Layout.fillWidth: true
                                                wrapMode: Text.Wrap
                                                color: window.muted
                                                font.pixelSize: 12
                                                text: "견본 " + customCard.modelData.examples + "개 · "
                                                      + (customCard.modelData.uses ? "연결한 동작: " + customCard.modelData.usesText : "연결한 동작 없음")
                                            }
                                            CompactButton {
                                                text: "삭제"
                                                danger: true
                                                onClicked: confirmDialog.ask("손 모양 삭제",
                                                    "'" + customCard.modelData.name + "' 손 모양을 지웁니다. 견본 " + customCard.modelData.examples + "개"
                                                    + (customCard.modelData.uses ? "와 이 모양에 연결한 동작(" + customCard.modelData.usesText + ")" : "")
                                                    + "도 함께 지워지고 되돌릴 수 없어요.",
                                                    "삭제", function () { dashboard.removeCustomPosture(customCard.modelData.key) })
                                            }
                                        }
                                    }
                                }

                            }
                        }
                        // 2026-11-11: the AI connections in their own column on the right (the window had room to spare)
                        Rectangle {
                            id: llmListPane
                            objectName: "llmListPane"
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.right: parent.right
                            anchors.margins: 12
                            width: Math.max(340, Math.min(560, parent.width * 0.4))
                            radius: 8
                            color: Theme.Studio.sourcePaper
                            border.color: window.line
                            ScrollView {
                                id: llmListScroll
                                anchors.fill: parent
                                anchors.margins: 10
                                clip: true
                                contentWidth: availableWidth
                                ColumnLayout {
                                    width: llmListScroll.availableWidth
                                    spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    SectionLabel { text: "AI로 연결한 명령 (" + dashboard.llmHistory.length + ")" }
                                    Item { Layout.fillWidth: true }
                                    CheckBox { id: historyAll; text: "바뀐 것도 보기"; checked: false }
                                }
                                TextLabel {
                                    visible: dashboard.llmHistory.length === 0
                                    text: "아직 없어요. 왼쪽에서 제안을 받아 적용하면 여기에 쌓여요."
                                    color: window.muted
                                    font.pixelSize: 12
                                }
                                Repeater {
                                    model: dashboard.llmHistory
                                    delegate: Rectangle {
                                        id: historyCard
                                        objectName: "llmHistoryCard"
                                        required property var modelData
                                        // 2026-11-11: the command and the hold time can be changed later by hand
                                        property bool editing: false
                                        property string editType: "hotkey"
                                        property string editValue: ""
                                        property real editHold: -1
                                        readonly property bool hasHold: modelData.hold_seconds !== undefined
                                        function startEdit() {
                                            editType = String(modelData.action_type || "hotkey")
                                            editValue = editType === "macro" ? "" : String(modelData.action_value || "")
                                            if (editType === "macro") editType = "hotkey"     // a macro is re-made through the AI
                                            editHold = hasHold ? Number(modelData.hold_seconds) : -1
                                            editing = true
                                        }
                                        visible: modelData.current || historyAll.checked
                                        Layout.fillWidth: true
                                        implicitHeight: visible ? Math.max(historyCol.implicitHeight, historyButtons.implicitHeight,
                                                                           handPic.visible ? handPic.implicitHeight : 0) + 16 : 0
                                        radius: 8
                                        color: modelData.current ? Theme.Studio.paper : Theme.Studio.surface
                                        border.color: window.line
                                        // 2026-11-11: which hand this command was registered with, from that gesture's own saved
                                        // joints (src/gesture_preview.py), looping like a short clip
                                        readonly property var preview: dashboard.gesturePreview(String(modelData.gesture_key || ""))
                                        RowLayout {
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            anchors.top: parent.top
                                            anchors.margins: 8
                                            spacing: 8
                                            ColumnLayout {
                                                id: handPic
                                                objectName: "llmHistoryHand"
                                                readonly property var frames: historyCard.preview.frames || []
                                                visible: frames.length > 0
                                                Layout.alignment: Qt.AlignTop
                                                Layout.preferredWidth: 96
                                                spacing: 2
                                                Rectangle {
                                                    Layout.preferredWidth: 96
                                                    Layout.preferredHeight: 96
                                                    radius: 8
                                                    color: Theme.Studio.sourceHistorySurface
                                                    Canvas {
                                                        id: handCanvas
                                                        anchors.fill: parent
                                                        property int frame: 0
                                                        onFrameChanged: requestPaint()
                                                        onAvailableChanged: if (available) requestPaint()
                                                        Timer {
                                                            interval: 1000 / Math.max(1, historyCard.preview.fps || 15)
                                                            repeat: true
                                                            running: handPic.visible && historyCard.visible && handPic.frames.length > 1
                                                            onTriggered: handCanvas.frame = (handCanvas.frame + 1) % handPic.frames.length
                                                        }
                                                        onPaint: {
                                                            if (!available) return
                                                            let c = getContext("2d"); c.reset()
                                                            let fs = handPic.frames
                                                            if (!fs.length) return
                                                            let p = fs[Math.min(frame, fs.length - 1)]
                                                            let W = width, H = height
                                                            let chains = [[0,1,2,3,4],[0,5,6,7,8],[0,9,10,11,12],[0,13,14,15,16],[0,17,18,19,20],[5,9,13,17]]
                                                            c.lineCap = "round"; c.lineJoin = "round"
                                                            for (let pass = 0; pass < 2; pass++) {
                                                                c.strokeStyle = pass === 0 ? Theme.Studio.sourceBoneShadow : Theme.Studio.mint
                                                                c.lineWidth = pass === 0 ? 4 : 2
                                                                for (let chain of chains) {
                                                                    c.beginPath()
                                                                    for (let i = 0; i < chain.length; i++) {
                                                                        let v = p[chain[i]]
                                                                        if (i === 0) c.moveTo(v[0] * W, v[1] * H); else c.lineTo(v[0] * W, v[1] * H)
                                                                    }
                                                                    c.stroke()
                                                                }
                                                            }
                                                            c.fillStyle = Theme.Studio.sourceJoint
                                                            for (let i of [4, 8, 12, 16, 20]) {
                                                                c.beginPath(); c.arc(p[i][0] * W, p[i][1] * H, 2.4, 0, Math.PI * 2); c.fill()
                                                            }
                                                            // the movement: an arrow for a direction, a word for the rest
                                                            let m = historyCard.preview.move || ""
                                                            let dirs = {"left": [-1, 0], "right": [1, 0], "up": [0, -1], "down": [0, 1],
                                                                        "diag_up_left": [-.7, -.7], "diag_up_right": [.7, -.7],
                                                                        "diag_down_left": [-.7, .7], "diag_down_right": [.7, .7]}
                                                            if (dirs[m]) {
                                                                let d = dirs[m], cx = W - 18, cy = 18, r = 11
                                                                let x0 = cx - d[0] * r, y0 = cy - d[1] * r, x1 = cx + d[0] * r, y1 = cy + d[1] * r
                                                                c.strokeStyle = Theme.Studio.sourcePreviewHighlight; c.fillStyle = Theme.Studio.sourcePreviewHighlight; c.lineWidth = 2.5
                                                                c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke()
                                                                let a = Math.atan2(y1 - y0, x1 - x0)
                                                                c.beginPath(); c.moveTo(x1, y1)
                                                                c.lineTo(x1 - 7 * Math.cos(a - .5), y1 - 7 * Math.sin(a - .5))
                                                                c.lineTo(x1 - 7 * Math.cos(a + .5), y1 - 7 * Math.sin(a + .5))
                                                                c.closePath(); c.fill()
                                                            } else if (m) {
                                                                let label = m === "double_pinch" ? "두 번 집기" : m.indexOf("draw_") === 0 ? "그리기"
                                                                          : m === "sequence" ? "" : ""
                                                                if (m === "sequence") {
                                                                    let st = historyCard.preview.steps || [], n = 0
                                                                    for (let i = 0; i < st.length; i++) if (frame >= st[i]) n = i + 1
                                                                    label = n + " / " + st.length
                                                                }
                                                                if (label) {
                                                                    c.font = "bold 11px sans-serif"; c.fillStyle = Theme.Studio.sourcePreviewHighlight
                                                                    c.fillText(label, 6, 14)
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                                Text {
                                                    Layout.preferredWidth: 96
                                                    text: historyCard.preview.source || ""
                                                    font.pixelSize: 10
                                                    color: window.muted
                                                    wrapMode: Text.Wrap
                                                    horizontalAlignment: Text.AlignHCenter
                                                }
                                            }
                                            ColumnLayout {
                                                id: historyCol
                                                Layout.fillWidth: true
                                                spacing: 2
                                                // the name on its own line, wrapping: a long name used to push the marks and the
                                                // buttons out of the card (2026-11-11, seen when the "motion mode only" mark was added)
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    text: historyCard.modelData.gesture_name
                                                          + (historyCard.modelData.app_match ? "  ·  " + historyCard.modelData.app_match + "에서만" : "")
                                                    font.bold: true
                                                    wrapMode: Text.Wrap
                                                }
                                                RowLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 6
                                                    Chip {
                                                        text: historyCard.modelData.current ? "적용 중" : "이후 바뀜"
                                                        warn: !historyCard.modelData.current
                                                    }
                                                    // which world the gesture lives in (2026-11-12): the two hand modes are separate
                                                    Chip {
                                                        objectName: "llmModeChip"
                                                        visible: !!historyCard.modelData.mode_name && historyCard.modelData.mode !== "motion"
                                                        text: historyCard.modelData.mode_works === false
                                                              ? historyCard.modelData.mode_name + " 전용 · 지금 방식에서는 안 쓰임"
                                                              : historyCard.modelData.mode === "all" ? "모든 손 모드" : historyCard.modelData.mode_name + " 전용"
                                                        warn: historyCard.modelData.mode_works === false
                                                    }
                                                    Chip {
                                                        visible: historyCard.hasHold
                                                        text: "유지 " + Number(historyCard.modelData.hold_seconds || 0).toFixed(1) + "초"
                                                    }
                                                    Chip {
                                                        objectName: "llmMotionOnlyChip"
                                                        visible: historyCard.modelData.motion_only === true
                                                        text: "모션 모드에서만"
                                                    }
                                                    Item { Layout.fillWidth: true }
                                                }
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    text: (historyCard.modelData.before_text || "") + "  →  "
                                                          + ((historyCard.modelData.steps || []).length ? "여러 단계" : historyCard.modelData.after)
                                                    font.pixelSize: 13
                                                    wrapMode: Text.Wrap
                                                }
                                                Repeater {
                                                    model: historyCard.modelData.steps || []
                                                    delegate: TextLabel {
                                                        required property var modelData
                                                        required property int index
                                                        Layout.fillWidth: true
                                                        Layout.leftMargin: 12
                                                        text: (index + 1) + ". " + window.stepText(modelData)
                                                        font.pixelSize: 13
                                                        wrapMode: Text.Wrap
                                                    }
                                                }
                                                TextLabel {
                                                    Layout.fillWidth: true
                                                    visible: (historyCard.modelData.request || "") !== ""
                                                    text: "요청: " + historyCard.modelData.request + "  ·  " + historyCard.modelData.time
                                                          + (historyCard.modelData.edited ? "  ·  직접 고침 " + historyCard.modelData.edited : "")
                                                    color: window.muted
                                                    font.pixelSize: 11
                                                    wrapMode: Text.Wrap
                                                }
                                                // ---- one more look at this command's hand shape, started from its editor
                                                // (poseTeachMore): what is going on stays on the card, because the list is
                                                // built again with every step and the editor closes
                                                TextLabel {
                                                    id: teachLine
                                                    objectName: "llmHistoryTeachLine"
                                                    readonly property var teach: dashboard.llmContext.teach || {}
                                                    readonly property var pose: dashboard.llmContext.pose || {}
                                                    readonly property bool mine: !!historyCard.modelData.teach_posture
                                                                                 && teach.focus === historyCard.modelData.teach_posture
                                                    visible: mine && text.length > 0
                                                    Layout.fillWidth: true
                                                    Layout.topMargin: 4
                                                    text: !mine ? ""
                                                        : teach.adding && pose.state === "countdown" ? ("'" + teach.focusName + "' 모양으로 손을 준비하세요… " + pose.seconds)
                                                        : teach.adding && pose.state === "holding" ? ("그대로 유지! " + Math.round(pose.progress * 100) + "%")
                                                        : teach.adding ? ("'" + teach.focusName + "' 견본으로 추가하는 중…")
                                                        : (teach.text || "")
                                                    color: teach.state === "error" ? Theme.Studio.danger : Theme.Studio.mintDark
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                    wrapMode: Text.Wrap
                                                }
                                                // ---- the editor: hold time (hold gestures) and command
                                                ColumnLayout {
                                                    objectName: "llmHistoryEditor"
                                                    visible: historyCard.editing
                                                    Layout.fillWidth: true
                                                    Layout.topMargin: 6
                                                    spacing: 6
                                                    RowLayout {
                                                        visible: historyCard.hasHold
                                                        Layout.fillWidth: true
                                                        spacing: 6
                                                        TextLabel { text: "발동까지 유지"; font.pixelSize: 12; font.bold: true }
                                                        Slider {
                                                            id: holdSlider
                                                            objectName: "llmHoldSlider"
                                                            Layout.fillWidth: true
                                                            from: historyCard.modelData.hold_min || 0.4
                                                            to: historyCard.modelData.hold_max || 3.0
                                                            stepSize: 0.1
                                                            snapMode: Slider.SnapAlways
                                                            value: historyCard.editHold
                                                            onMoved: historyCard.editHold = Math.round(value * 10) / 10
                                                        }
                                                        TextLabel {
                                                            text: historyCard.editHold.toFixed(1) + "초"
                                                                  + (historyCard.editHold <= (historyCard.modelData.hold_min || 0.4) + 0.001 ? " (최소)" : "")
                                                            font.pixelSize: 12
                                                        }
                                                        CompactButton {
                                                            text: "최소로"
                                                            enabled: historyCard.editHold > (historyCard.modelData.hold_min || 0.4) + 0.001
                                                            onClicked: historyCard.editHold = historyCard.modelData.hold_min || 0.4
                                                        }
                                                    }
                                                    TextLabel {
                                                        visible: historyCard.hasHold
                                                        Layout.fillWidth: true
                                                        text: (historyCard.modelData.hold_min || 0.4) >= 1.0
                                                              ? "평소 조작 중에도 자주 하는 손 모양이라 최소 1초예요. 잘못 실행되면 늘려 보세요."
                                                              : "짧을수록 빨리 실행되고, 지나가던 손 모양에 잘못 실행되면 늘려 보세요."
                                                        color: window.muted
                                                        font.pixelSize: 11
                                                        wrapMode: Text.Wrap
                                                    }
                                                    // 2026-11-12 request ("수정 메뉴에 mlp 추가 학습시키기 옵션도"): the hand shape of
                                                    // this command shown once more = one more example for the taught model
                                                    TextLabel {
                                                        visible: !!historyCard.modelData.teach_posture
                                                        text: "손 모양 인식 추가 학습 (MLP)"
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                    }
                                                    ColumnLayout {
                                                        visible: !!historyCard.modelData.teach_posture
                                                        Layout.fillWidth: true
                                                        spacing: 4
                                                        TextLabel {
                                                            readonly property var teach: dashboard.llmContext.teach || {}
                                                            readonly property int n: (teach.counts || {})[historyCard.modelData.teach_posture] || 0
                                                            Layout.fillWidth: true
                                                            text: "이 손 모양을 한 번 더 보여 주면 견본으로 저장하고 다시 학습해요 (지금 견본 " + n + "개"
                                                                  + (n < 1 ? ", 2개부터 학습" : "") + "). 누르면 3초 뒤 2초 동안 손을 봐요. "
                                                                  + "잘 안 걸리는 각도·거리로 보여 주면 좋아요."
                                                            color: window.muted
                                                            font.pixelSize: 11
                                                            wrapMode: Text.Wrap
                                                        }
                                                        CompactButton {
                                                            objectName: "llmEditTeach"
                                                            Layout.alignment: Qt.AlignRight
                                                            text: "✋ 보여 주고 추가 학습"
                                                            enabled: (dashboard.llmContext.teach || {}).state !== "training"
                                                            onClicked: dashboard.poseTeachMore(historyCard.modelData.teach_posture)
                                                        }
                                                    }
                                                    TextLabel { text: "실행할 명령"; font.pixelSize: 12; font.bold: true }
                                                    Flow {
                                                        Layout.fillWidth: true
                                                        spacing: 6
                                                        Repeater {
                                                            model: [["hotkey", "단축키"], ["youtube_key", "유튜브·미디어 키"],
                                                                    ["launch_app", "프로그램·폴더·웹사이트"], ["mouse_action", "마우스·시스템"], ["system_action", "PC·Windows 기능"], ["powershell", "PowerShell"]]
                                                            delegate: CompactButton {
                                                                required property var modelData
                                                                text: modelData[1]
                                                                accent: historyCard.editType === modelData[0]
                                                                onClicked: {
                                                                    if (historyCard.editType !== modelData[0]) historyCard.editValue = ""
                                                                    historyCard.editType = modelData[0]
                                                                }
                                                            }
                                                        }
                                                    }
                                                    TextLabel {
                                                        visible: historyCard.modelData.action_type === "macro"
                                                        Layout.fillWidth: true
                                                        text: "지금은 여러 단계(매크로)예요. 여기서 고르면 한 가지 명령으로 바뀌어요. 단계를 바꾸려면 AI에게 다시 부탁해 주세요."
                                                        color: window.muted
                                                        font.pixelSize: 11
                                                        wrapMode: Text.Wrap
                                                    }
                                                    TextField {
                                                        objectName: "llmEditValue"
                                                        visible: !["mouse_action", "system_action", "powershell"].includes(historyCard.editType)
                                                        Layout.fillWidth: true
                                                        text: historyCard.editValue
                                                        placeholderText: historyCard.editType === "hotkey" ? "예: ctrl+shift+t, win+d, f5"
                                                                       : historyCard.editType === "youtube_key" ? "예: k (재생/정지), f (전체 화면), m (음소거)"
                                                                       : "예: chrome.exe --incognito https://www.youtube.com 또는 %USERPROFILE%\\Desktop\\폴더"
                                                        font.pixelSize: 13
                                                        onTextEdited: historyCard.editValue = text
                                                    }
                                                    // COMMAND AUTOMATION BEGIN
                                                    CommandScript {
                                                        objectName: "powerShellConnectionEditor"
                                                        visible: historyCard.editType === "powershell"
                                                        Layout.fillWidth: true
                                                        editable: true
                                                        value: historyCard.editValue
                                                        onEdited: function(text) { historyCard.editValue = text }
                                                    }
                                                    StyledComboBox {
                                                        visible: historyCard.editType === "system_action"
                                                        Layout.fillWidth: true
                                                        model: dashboard.systemActionPresets
                                                        textRole: "label"
                                                        currentIndex: {
                                                            var ps = dashboard.systemActionPresets
                                                            for (var i = 0; i < ps.length; i++) if (ps[i].value === historyCard.editValue) return i
                                                            return -1
                                                        }
                                                        onActivated: function(i) { historyCard.editValue = dashboard.systemActionPresets[i].value }
                                                    }
                                                    // COMMAND AUTOMATION END
                                                    StyledComboBox {
                                                        visible: historyCard.editType === "mouse_action"
                                                        Layout.fillWidth: true
                                                        model: dashboard.mouseActionPresets
                                                        textRole: "label"
                                                        currentIndex: {
                                                            var ps = dashboard.mouseActionPresets
                                                            for (var i = 0; i < ps.length; i++) if (ps[i].value === historyCard.editValue) return i
                                                            return -1
                                                        }
                                                        onActivated: function (i) { historyCard.editValue = dashboard.mouseActionPresets[i].value }
                                                    }
                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 6
                                                        Item { Layout.fillWidth: true }
                                                        CompactButton {
                                                            text: "취소"
                                                            onClicked: historyCard.editing = false
                                                        }
                                                        CompactButton {
                                                            objectName: "llmEditSave"
                                                            text: "저장"
                                                            accent: true
                                                            enabled: historyCard.editValue.trim().length > 0
                                                            onClicked: {
                                                                if (dashboard.editLlmConnection(historyCard.modelData.index, historyCard.editType,
                                                                                                historyCard.editValue.trim(), historyCard.hasHold ? historyCard.editHold : -1))
                                                                    historyCard.editing = false
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                            ColumnLayout {
                                                id: historyButtons
                                                Layout.alignment: Qt.AlignTop
                                                Layout.minimumWidth: implicitWidth      // as wide as its widest button (a fixed 118 px let the labels spill out, 2026-11-11)
                                                spacing: 4
                                                CompactButton {
                                                    objectName: "llmEditButton"
                                                    visible: historyCard.modelData.editable === true && !historyCard.editing
                                                    Layout.fillWidth: true
                                                    text: "수정"                       // the editor below says what can change (time, command)
                                                    onClicked: historyCard.startEdit()
                                                }
                                                CompactButton {
                                                    // 2026-11-11: the gesture counts only while motion mode is on (press again: everywhere)
                                                    objectName: "llmMotionOnlyButton"
                                                    visible: historyCard.modelData.mode_option === true && !historyCard.editing
                                                    Layout.fillWidth: true
                                                    text: historyCard.modelData.motion_only ? "평소에도 쓰기" : "모션 모드에서만"
                                                    onClicked: dashboard.setLlmMotionOnly(historyCard.modelData.index, !historyCard.modelData.motion_only)
                                                }
                                                CompactButton {
                                                    // back one step: what was there just before this AI change
                                                    visible: historyCard.modelData.current && !historyCard.modelData.created
                                                    Layout.fillWidth: true
                                                    text: "되돌리기"
                                                    onClicked: confirmDialog.ask("AI 연결 되돌리기",
                                                        "'" + historyCard.modelData.gesture_name + "'을(를) " + (historyCard.modelData.before_text || "이전 연결") + "(으)로 되돌립니다.",
                                                        "되돌리기", function () { dashboard.undoLlmConnection(historyCard.modelData.index) })
                                                }
                                                CompactButton {
                                                    // the connection itself goes: a created gesture is deleted, an existing one gets its built-in command
                                                    visible: historyCard.modelData.current
                                                    Layout.fillWidth: true
                                                    text: "삭제"
                                                    danger: true
                                                    onClicked: confirmDialog.ask("AI 연결 삭제",
                                                        historyCard.modelData.created
                                                            ? "'" + historyCard.modelData.gesture_name + "' 동작을 지웁니다."
                                                            : historyCard.modelData.app_match
                                                                ? "'" + historyCard.modelData.gesture_name + "'의 " + historyCard.modelData.app_match + " 전용 명령을 지웁니다."
                                                                : "'" + historyCard.modelData.gesture_name + "'에 AI가 연결한 명령을 지우고 기본 명령으로 돌립니다.",
                                                        "삭제", function () { dashboard.deleteLlmConnection(historyCard.modelData.index) })
                                                }
                                                CompactButton {
                                                    visible: !historyCard.modelData.current
                                                    text: "목록에서 지우기"
                                                    onClicked: dashboard.forgetLlmConnection(historyCard.modelData.index)
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
    }
}
