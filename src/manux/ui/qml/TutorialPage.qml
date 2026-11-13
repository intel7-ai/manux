import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme" as Theme

Rectangle {
    id: page
    objectName: "onboardingPage"
    required property var controller
    color: Theme.Studio.surface
    ColumnLayout {
        anchors.fill: parent; anchors.margins: Theme.Studio.pad
        spacing: Theme.Studio.gap
        ScrollView {
            id: lessonScroll
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
            contentWidth: availableWidth; clip: true
        ColumnLayout {
            width: Math.min(720, lessonScroll.availableWidth)
            x: Math.max(0, (lessonScroll.availableWidth - width) / 2)
            spacing: Theme.Studio.gap
            RowLayout {
                Layout.fillWidth: true; spacing: 4
                Repeater {
                    model: page.controller.tutorialCount
                    Rectangle {
                        required property int index
                        Layout.fillWidth: true; implicitHeight: 6; radius: 3
                        color: page.controller.tutorialDoneSteps[index] ? Theme.Studio.mintDark
                            : index === page.controller.tutorialIndex ? Theme.Studio.mint : Theme.Studio.line
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                text: page.controller.tutorialFinished ? "준비됐어요" : (page.controller.tutorialIndex + 1) + " / " + page.controller.tutorialCount
                color: Theme.Studio.muted; font.pixelSize: Theme.Studio.body
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: page.controller.tutorialFinished ? "이제 켜고 써 보세요" : page.controller.tutorialTitle
                color: Theme.Studio.ink; font.bold: true; font.pixelSize: Theme.Studio.title
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: page.controller.tutorialFinished ? "메인 화면에서 V 또는 켜기 버튼으로 제어를 시작하세요. 동작 다시 배우기로 언제든 돌아올 수 있습니다."
                    : page.controller.tutorialHow
                color: Theme.Studio.muted; font.pixelSize: Theme.Studio.body
            }
            Rectangle {
                visible: !page.controller.tutorialFinished
                Layout.fillWidth: true; Layout.preferredHeight: Math.max(180, Math.min(280, page.height * .4))
                color: Theme.Studio.stage; radius: Theme.Studio.radius; clip: true
                Image {
                    id: tutorialPreview
                    anchors.fill: parent; source: page.controller.previewUrl; cache: false; fillMode: Image.PreserveAspectFit
                    HandSkeleton { anchors.fill: parent; bridge: page.controller; imageWidth: tutorialPreview.paintedWidth; imageHeight: tutorialPreview.paintedHeight }
                }
                Item {
                    objectName: "tutorialTargets"
                    anchors.fill: parent
                    visible: page.controller.tutorialKey === "sensitivity"
                    Repeater {
                        model: page.controller.tutorialTargets
                        Rectangle {
                            required property var modelData; required property int index
                            width: 44; height: 44; radius: 22
                            x: modelData.x * parent.width - width / 2
                            y: modelData.y * parent.height - height / 2
                            color: modelData.done ? Theme.Studio.mint : Theme.Studio.paper
                            border.color: Theme.Studio.mint; border.width: 2
                            Label { anchors.centerIn: parent; text: parent.modelData.done ? "✓" : String(parent.index + 1); color: parent.modelData.done ? Theme.Studio.mintText : Theme.Studio.ink; font.bold: true }
                        }
                    }
                    Rectangle {
                        width: 14; height: 14; radius: 7; color: Theme.Studio.mint; border.color: Theme.Studio.mintText
                        visible: page.controller.tutorialCursor.length === 2
                        x: (page.controller.tutorialCursor[0] || 0) * parent.width - width / 2
                        y: (page.controller.tutorialCursor[1] || 0) * parent.height - height / 2
                    }
                }
                Label {
                    visible: page.controller.tutorialSucceeded
                    anchors.centerIn: parent; padding: Theme.Studio.pad
                    text: "성공했어요"; color: Theme.Studio.mintText; font.bold: true; font.pixelSize: Theme.Studio.title
                    background: Rectangle { color: Theme.Studio.mint; radius: Theme.Studio.radius }
                }
            }
            QuickSpeedControls {
                Layout.fillWidth: true; visible: page.controller.tutorialKey === "sensitivity"
                controller: page.controller; showDesktopSwitch: false
            }
            ProgressBar { Layout.fillWidth: true; visible: !page.controller.tutorialFinished; value: page.controller.tutorialProgress }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap; visible: !page.controller.tutorialFinished
                text: page.controller.tutorialHelp
                color: page.controller.tutorialFailures >= 3 ? Theme.Studio.amber : Theme.Studio.muted
                font.pixelSize: Theme.Studio.small
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                text: "연습 화면에서만 이동·클릭을 확인합니다. Windows 커서·클릭·키 입력은 보내지 않습니다."
                color: Theme.Studio.muted; font.pixelSize: Theme.Studio.small
            }
        }
        }
            RowLayout {
                Layout.fillWidth: true
                Button { text: "이전"; visible: page.controller.tutorialIndex > 0 && !page.controller.tutorialFinished; onClicked: page.controller.tutorialBack() }
                Button { text: "다시 시도"; visible: page.controller.tutorialFailures > 0 && !page.controller.tutorialFinished; onClicked: page.controller.tutorialRetry() }
                Item { Layout.fillWidth: true }
                Button { text: "나중에 배우기"; visible: !page.controller.tutorialFinished; onClicked: page.controller.finishTutorial() }
                Button {
                    objectName: "tutorialAdvance"
                    text: page.controller.tutorialFinished ? "시작하기" : page.controller.tutorialSucceeded ? "다음" : "이 단계 건너뛰기"
                    enabled: page.controller.tutorialFinished || page.controller.tutorialSucceeded || page.controller.tutorialCanSkip
                    onClicked: page.controller.tutorialFinished ? page.controller.finishTutorial() : page.controller.tutorialNext()
                }
            }
    }
}
