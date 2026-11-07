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
    readonly property bool measuring: phase === "rest" || phase === "horizontal" || phase === "vertical"
    color: Theme.Studio.surface

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 820)
        spacing: Theme.Studio.gap

        Label {
            Layout.fillWidth: true
            text: page.phase === "review" ? "움직임이 편한가요?" : "내 자세에 맞는 커서"
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
                case "intro": return "평소 자세에서 편한 손 이동 범위를 측정하고, 화면의 점으로 감도를 맞춥니다."
                case "waiting": return "다음 단계를 준비하고 있어요."
                case "rest": return "팔을 편하게 받치고 손을 잠깐 멈춰 주세요."
                case "horizontal": return "편한 범위에서 손을 좌우로 몇 번 왕복하세요."
                case "vertical": return "팔을 받친 채 편한 범위에서 손을 위아래로 몇 번 왕복하세요."
                case "aim": return "초록색 점에 가상 커서를 올려 잠깐 멈추세요."
                case "review": return "이대로 사용하거나, 감도를 바꿔 새 점으로 다시 확인할 수 있어요."
                default: return "움직임을 다시 측정해 주세요."
                }
            }
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(350, page.height * .56)
            visible: page.measuring || page.phase === "aim"

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
                visible: page.phase === "aim"
                color: Theme.Studio.stage
                radius: Theme.Studio.radius
                clip: true
                Rectangle {
                    readonly property var point: page.setup.target
                    visible: !!point
                    width: point ? point[2] * arena.width : 0
                    height: width
                    x: point ? point[0] * arena.width - width / 2 : 0
                    y: point ? point[1] * arena.height - height / 2 : 0
                    radius: width / 2
                    color: page.setup.holding ? Theme.Studio.mint : Theme.Studio.mintDark
                    border.color: Theme.Studio.mint
                    border.width: 2
                }
                Rectangle {
                    readonly property var point: page.setup.cursor
                    visible: !!point
                    width: 10
                    height: width
                    radius: width / 2
                    x: point ? point[0] * arena.width - width / 2 : 0
                    y: point ? point[1] * arena.height - height / 2 : 0
                    color: page.setup.hand ? Theme.Studio.paper : Theme.Studio.muted
                }
            }
        }
        ProgressBar {
            Layout.fillWidth: true
            visible: page.measuring
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
                return page.setup.note || ""
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            Button {
                text: "건너뛰기"
                onClicked: page.controller.pointerSetupAction("skip")
            }
            Button {
                visible: page.phase === "intro" || page.phase === "retry"
                text: page.phase === "intro" ? "측정 시작" : "다시 측정"
                onClicked: page.controller.pointerSetupAction("start")
            }
            Button {
                visible: page.phase === "review"
                text: "이대로 사용"
                highlighted: true
                onClicked: page.controller.pointerSetupAction("apply")
            }
            Button {
                visible: page.phase === "review"
                text: "더 빠르게"
                onClicked: page.controller.pointerSetupAction("faster")
            }
            Button {
                visible: page.phase === "review"
                text: "더 안정적으로"
                onClicked: page.controller.pointerSetupAction("steadier")
            }
            Button {
                visible: page.phase === "review"
                text: "위아래만 더 멀리"
                onClicked: page.controller.pointerSetupAction("vertical")
            }
        }
    }
}
