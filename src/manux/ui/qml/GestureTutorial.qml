import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "theme" as Theme

Rectangle {
    id: tutorialPage
    color: Theme.Studio.surface

    component TextLabel: Label {
        color: Theme.Studio.ink
        font.pixelSize: 14
    }
    component ActionButton: Button {
        property bool accent: false
        highlighted: accent
    }
    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 64, 720)
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            Repeater {
                model: dashboard.tutorialCount
                delegate: Rectangle {
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: 6
                    radius: 3
                    color: dashboard.tutorialDoneSteps[index] ? Theme.Studio.mint
                         : index === dashboard.tutorialIndex ? "#9FB3BF" : Theme.Studio.line
                }
            }
        }
        TextLabel {
            text: dashboard.tutorialFinished ? "준비됐어요"
                : (dashboard.tutorialIndex + 1) + " / " + dashboard.tutorialCount
            color: Theme.Studio.muted
            font.pixelSize: 13
        }
        TextLabel {
            Layout.fillWidth: true
            text: dashboard.tutorialFinished ? "이제 켜고 써 보세요" : dashboard.tutorialTitle
            font.pixelSize: 32
            font.bold: true
        }
        TextLabel {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.Studio.muted
            font.pixelSize: 16
            text: dashboard.tutorialFinished
                ? "배운 동작은 메인 화면에 늘 보입니다. 메인 화면의 \"동작 다시 배우기\"로 언제든 다시 할 수 있어요."
                : dashboard.tutorialHow
        }
        Rectangle {
            visible: !dashboard.tutorialFinished
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(320, tutorialPage.height * .42)
            radius: 16
            color: Theme.Studio.ink
            clip: true
            Image { anchors.fill: parent; source: dashboard.previewUrl; cache: false; fillMode: Image.PreserveAspectFit }
            Rectangle {
                visible: dashboard.tutorialSucceeded
                anchors.centerIn: parent
                width: doneLabel.implicitWidth + 40
                height: 56
                radius: 28
                color: Theme.Studio.mint
                TextLabel { id: doneLabel; anchors.centerIn: parent; text: "잘했어요"; font.pixelSize: 22; font.bold: true }
            }
        }
        Rectangle {
            visible: !dashboard.tutorialFinished && !dashboard.tutorialSucceeded && dashboard.tutorialProgress > 0
            Layout.fillWidth: true
            implicitHeight: 6
            radius: 3
            color: Theme.Studio.line
            Rectangle { width: parent.width * dashboard.tutorialProgress; height: parent.height; radius: 3; color: Theme.Studio.mint }
        }
        TextLabel {
            visible: !dashboard.tutorialFinished
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.Studio.muted
            font.pixelSize: 12
            text: "연습 중에는 실제 마우스 커서·클릭·키 입력을 전혀 보내지 않습니다. 화면의 표시로만 확인하세요."
        }
        RowLayout {
            Layout.fillWidth: true
            ActionButton {
                visible: !dashboard.tutorialFinished && dashboard.tutorialIndex > 0
                text: "이전"
                onClicked: dashboard.tutorialBack()
            }
            Item { Layout.fillWidth: true }
            ActionButton {
                visible: !dashboard.tutorialFinished
                text: "나중에 하기"
                onClicked: dashboard.finishTutorial()
            }
            ActionButton {
                text: dashboard.tutorialFinished ? "시작하기" : (dashboard.tutorialSucceeded ? "다음" : "건너뛰기")
                accent: dashboard.tutorialFinished || dashboard.tutorialSucceeded
                onClicked: dashboard.tutorialFinished ? dashboard.finishTutorial() : dashboard.tutorialNext()
            }
        }
    }
}
