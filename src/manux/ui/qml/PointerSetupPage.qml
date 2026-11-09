import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme" as Theme

Rectangle {
    id: page
    objectName: "pointerSetupPage"
    required property var controller
    readonly property var setup: controller.pointerSetup
    readonly property string phase: setup.phase || ""
    readonly property var steps: ["rest", "horizontal", "up", "down"]
    readonly property bool measuring: steps.indexOf(phase) >= 0
    readonly property bool tuning: phase === "aim" || phase === "review" || phase === "preview"
    color: Theme.Studio.surface

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 820)
        spacing: Theme.Studio.gap

        Label {
            Layout.fillWidth: true
            text: page.measuring ? "손바닥 보정  " + (page.steps.indexOf(page.phase) + 1) + " / 4"
                                 : page.phase === "review" ? "움직임이 편한가요?" : "내 자세에 맞는 커서"
            color: Theme.Studio.ink
            font.pixelSize: 28
            font.bold: true
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.Studio.muted
            text: {
                switch (page.phase) {
                case "intro": return "팔을 편하게 받쳐 주세요. 좌우는 손을 움직이고, 위아래는 손목만 기울여 커서를 움직입니다."
                case "waiting": return "다음 단계를 준비하고 있어요."
                case "rest": return "카메라에 손 전체를 보여 주세요. 팔을 받친 평소 자세에서 중립 기울기를 측정합니다."
                case "horizontal": return "측정을 누른 뒤 편한 범위의 왼쪽 끝과 오른쪽 끝을 왕복하세요. 이 범위를 화면 너비에 맞춥니다."
                case "up": return "팔을 받친 채 손끝을 편하게 위로 들고 유지하세요. 이 각도가 화면 위쪽입니다."
                case "down": return "팔을 받친 채 손을 앞으로 숙여 손끝이 아래를 향하게 유지하세요. 이 각도가 화면 아래쪽입니다."
                case "aim": return "손을 움직여 흰 점을 초록색 원 안에 넣고 잠깐 멈추세요."
                case "preview": return "바뀐 감도로 자유롭게 움직여 보세요. 조정을 마치면 다시 시험하세요."
                case "review": return "이대로 사용하거나 원하는 항목만 바꿔 다시 시험하세요."
                default: return "움직임을 다시 측정해 주세요."
                }
            }
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(320, page.height * (page.tuning ? .43 : .51))
            visible: page.measuring || page.phase === "aim" || page.phase === "preview"

            Rectangle {
                anchors.fill: parent
                visible: page.measuring
                color: Theme.Studio.stage
                radius: Theme.Studio.radius
                clip: true
                Image {
                    anchors.fill: parent
                    source: parent.visible ? page.controller.previewUrl : ""
                    fillMode: Image.PreserveAspectFit
                    cache: false
                }
            }
            Rectangle {
                id: arena
                readonly property real screenAspect: page.setup.aspect || 16 / 9
                anchors.centerIn: parent
                width: Math.min(parent.width, parent.height * screenAspect)
                height: width / screenAspect
                visible: page.phase === "aim" || page.phase === "preview"
                color: Theme.Studio.stage
                radius: Theme.Studio.radius
                clip: true
                Rectangle {
                    objectName: "pointerSetupTarget"
                    readonly property var point: page.setup.target
                    visible: point != null && point.length === 3
                    width: visible ? point[2] * arena.width : 0
                    height: width
                    x: visible ? point[0] * arena.width - width / 2 : 0
                    y: visible ? point[1] * arena.height - height / 2 : 0
                    radius: width / 2
                    color: page.setup.holding ? Theme.Studio.mint : Theme.Studio.mintDark
                    border.color: Theme.Studio.mint
                    border.width: 2
                }
                Rectangle {
                    objectName: "pointerSetupCursor"
                    readonly property var point: page.setup.cursor
                    visible: point != null && point.length === 2
                    width: 10
                    height: width
                    radius: width / 2
                    x: visible ? point[0] * arena.width - width / 2 : 0
                    y: visible ? point[1] * arena.height - height / 2 : 0
                    color: page.setup.hand ? Theme.Studio.paper : Theme.Studio.muted
                }
            }
        }
        ProgressBar {
            Layout.fillWidth: true
            visible: page.measuring && page.setup.capturing
            value: page.setup.progress || 0
        }
        Label {
            Layout.fillWidth: true
            visible: page.measuring || page.phase === "aim" || !!page.setup.note
            wrapMode: Text.Wrap
            color: Theme.Studio.muted
            text: {
                if ((page.measuring || page.phase === "aim") && !page.setup.hand)
                    return "카메라에 조작 손 전체를 보여 주세요."
                if (page.phase === "aim")
                    return (page.setup.index + 1) + " / " + page.setup.total + (page.setup.note ? " · " + page.setup.note : "")
                if (page.measuring)
                    return (page.setup.note ? page.setup.note + " · " : "") + "손 기울기 " + page.setup.pitch + "°"
                return page.setup.note || ""
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: page.tuning
            spacing: 14
            Repeater {
                model: [
                    {key: "palm_x_gain", label: "좌우 감도", low: .65, high: 1.65, step: .05,
                     hint: "높일수록 좌우로 더 멀리 갑니다."},
                    {key: "palm_y_gain", label: "상하 감도", low: .65, high: 1.65, step: .05,
                     hint: "높일수록 작은 기울기로 더 멀리 갑니다."},
                    {key: "smoothing", label: "반응 속도", low: .15, high: .75, step: .05,
                     hint: "낮출수록 떨림을 더 줄입니다."}
                ]
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Label { text: modelData.label + "  " + control.value.toFixed(2); color: Theme.Studio.ink }
                    Slider {
                        id: control
                        Layout.fillWidth: true
                        from: modelData.low
                        to: modelData.high
                        stepSize: modelData.step
                        value: page.setup[modelData.key] || from
                        onMoved: page.controller.pointerSetupAdjust(modelData.key, value)
                    }
                    Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: modelData.hint; color: Theme.Studio.muted }
                }
            }
        }
        Label {
            Layout.fillWidth: true
            visible: page.tuning && !!page.setup.rangeText
            text: page.setup.rangeText || ""
            color: Theme.Studio.muted
            wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            Button {
                text: "건너뛰기"
                onClicked: page.controller.pointerSetupAction("skip")
            }
            Button {
                visible: page.phase === "intro" || page.phase === "retry" || page.tuning
                text: page.phase === "intro" ? "측정 시작" : "다시 측정"
                onClicked: page.controller.pointerSetupAction("start")
            }
            Button {
                visible: page.phase === "review" && page.setup.canApply === true
                text: "이대로 사용"
                highlighted: true
                onClicked: page.controller.pointerSetupAction("apply")
            }
            Button {
                visible: page.measuring
                enabled: Boolean(page.setup.hand) && !page.setup.capturing
                text: page.setup.capturing ? "측정 중…" : "이 단계 측정 (Space)"
                onClicked: page.controller.pointerSetupAction("capture")
            }
            Button {
                visible: page.tuning
                text: "이 값으로 다시 시험"
                onClicked: page.controller.pointerSetupAction("retest")
            }
        }
    }
}
