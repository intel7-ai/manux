import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: page
    objectName: "trainingProgressPage"
    required property var controller
    readonly property var details: controller.trainingDetails
    signal backRequested()
    anchors.centerIn: parent
    width: Math.min(1080, parent.width - 32)
    height: Math.min(820, parent.height - 32)
    modal: true
    focus: true
    padding: 22
    closePolicy: Popup.CloseOnEscape
    onOpened: controller.openTrainingProgress()
    onClosed: controller.closeTrainingProgress()
    background: Rectangle { radius: 16; color: "#FFFFFF"; border.color: "#DEE5EA" }

    component Caption: Label {
        color: "#112331"
        font.pixelSize: 13
        wrapMode: Text.Wrap
    }
    component Action: Button {
        id: action
        property bool accent: false
        implicitHeight: 40
        leftPadding: 16; rightPadding: 16
        contentItem: Caption {
            text: action.text; font.bold: true
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 9
            color: action.down ? "#BCEFE1" : action.accent ? "#46E5BD" : "#F0F3F6"
            border.color: action.activeFocus ? "#16846E" : "transparent"
        }
    }
    component Stat: Rectangle {
        id: stat
        property string label
        property string value
        property bool accent: false
        Layout.fillWidth: true
        implicitHeight: 91
        radius: 12
        color: accent ? "#E5F8F2" : "#F4F6F8"
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 14; spacing: 7
            Caption { text: stat.label; color: "#526575"; font.pixelSize: 12 }
            Caption {
                text: stat.value
                font.pixelSize: 24; font.bold: true
                Layout.fillWidth: true
                fontSizeMode: Text.Fit; minimumPixelSize: 16
                maximumLineCount: 1
            }
        }
    }

    contentItem: ColumnLayout {
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true; spacing: 3
                Caption { text: "학습 진행"; font.pixelSize: 24; font.bold: true; Layout.fillWidth: true }
                Caption { text: "선택한 동작의 학습부터 모델 적용까지"; color: "#526575"; font.pixelSize: 12; Layout.fillWidth: true }
            }
            Action {
                objectName: "progress-back"
                text: "학습센터로"
                onClicked: page.backRequested()
            }
            Action { text: "닫기"; onClicked: page.close() }
        }
        ScrollView {
            id: scroll
            objectName: "progress-scroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 14
                RowLayout {
                    Layout.fillWidth: true
                    Rectangle {
                        implicitWidth: statusLabel.implicitWidth + 22
                        implicitHeight: 30; radius: 15
                        color: page.details.status === "error" ? "#FFF0EA" : "#E5F8F2"
                        Caption {
                            id: statusLabel
                            anchors.centerIn: parent
                            text: page.details.statusText
                            font.bold: true
                            color: page.details.status === "error" ? "#AD3B20" : "#167C64"
                        }
                    }
                    Caption {
                        text: page.details.stage
                        Layout.fillWidth: true; font.pixelSize: 16; font.bold: true
                    }
                }
                ProgressBar {
                    objectName: "training-overall-progress"
                    Layout.fillWidth: true
                    from: 0; to: 1
                    value: page.details.progress
                    indeterminate: page.details.status === "running" && page.details.phase === "loading"
                    Accessible.name: "전체 학습 진행률"
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: page.width >= 850 ? 4 : 2
                    columnSpacing: 10; rowSpacing: 10
                    Stat { label: "전체 진행률"; value: page.details.percent + "%"; accent: true }
                    Stat { label: "남은 예상 시간"; value: page.details.remaining }
                    Stat { label: page.details.status === "success" ? "완료 시각" : "완료 예상 시각"; value: page.details.finish }
                    Stat { label: "경과 시간"; value: page.details.elapsed }
                }
                Caption { text: page.details.etaHint; color: "#526575"; font.pixelSize: 12; Layout.fillWidth: true }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: targetBody.implicitHeight + 28
                    color: "#F7FAFB"; radius: 12; border.color: "#DEE5EA"
                    ColumnLayout {
                        id: targetBody
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 14; spacing: 7
                        Caption { text: "이번 학습 동작"; font.bold: true }
                        Caption { text: page.details.tasks; Layout.fillWidth: true }
                        Caption { text: page.details.data; color: "#526575"; Layout.fillWidth: true; font.pixelSize: 12 }
                        Caption {
                            visible: page.details.skipped > 0
                            text: "완료되지 않았거나 읽을 수 없어 제외한 녹화 " + page.details.skipped + "개"
                            color: "#8A5A00"; Layout.fillWidth: true; font.pixelSize: 12
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: graphBody.implicitHeight + 28
                    radius: 12; color: "#FFFFFF"; border.color: "#DEE5EA"
                    ColumnLayout {
                        id: graphBody
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 14; spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Caption { text: "현재 단계 학습 손실"; font.bold: true; Layout.fillWidth: true }
                            Caption { text: "학습 단계 " + page.details.stageCount + "  ·  반복 " + page.details.epoch; color: "#526575" }
                        }
                        Caption {
                            text: "최근 " + page.details.loss + "   ·   최저 " + page.details.bestLoss + "   ·   낮을수록 학습 데이터에 더 잘 맞습니다"
                            color: "#526575"; font.pixelSize: 12; Layout.fillWidth: true
                        }
                        Canvas {
                            id: lossChart
                            objectName: "progress-loss-chart"
                            Layout.fillWidth: true; Layout.preferredHeight: 150
                            property var values: page.details.losses
                            onValuesChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.reset()
                                var points = values || []
                                if (points.length < 2) return
                                var hi = Math.max.apply(null, points), lo = Math.min.apply(null, points)
                                var span = Math.max(0.0001, hi - lo)
                                ctx.strokeStyle = "#E5EBEF"
                                ctx.lineWidth = 1
                                for (var line = 0; line < 4; line++) {
                                    var yy = 10 + line / 3 * (height - 20)
                                    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(width, yy); ctx.stroke()
                                }
                                ctx.strokeStyle = "#16846E"; ctx.lineWidth = 2.5
                                ctx.beginPath()
                                for (var i = 0; i < points.length; i++) {
                                    var x = 4 + i / (points.length - 1) * (width - 8)
                                    var y = 10 + (1 - (points[i] - lo) / span) * (height - 20)
                                    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                                }
                                ctx.stroke()
                            }
                            Caption {
                                anchors.centerIn: parent
                                visible: lossChart.values.length < 2
                                text: "학습이 진행되면 손실 그래프가 나타납니다"
                                color: "#526575"
                            }
                        }
                    }
                }
                Caption {
                    visible: page.details.error !== ""
                    text: page.details.error
                    Layout.fillWidth: true; color: "#AD3B20"; font.bold: true
                }
                Caption { text: "진행 기록 · 최신순"; font.bold: true }
                Caption {
                    visible: page.details.events.length === 0
                    text: "학습을 시작하면 단계별 진행 기록이 표시됩니다."
                    color: "#526575"
                }
                Repeater {
                    model: page.details.events.slice().reverse()
                    Caption {
                        required property string modelData
                        text: modelData
                        Layout.fillWidth: true; font.pixelSize: 12
                    }
                }
                CheckBox {
                    id: showLogs
                    visible: page.details.log !== ""
                    text: "실행 로그 자세히 보기"
                }
                TextArea {
                    visible: showLogs.checked && page.details.log !== ""
                    Layout.fillWidth: true
                    text: page.details.log
                    readOnly: true; selectByMouse: true
                    wrapMode: TextEdit.WrapAnywhere
                    font.pixelSize: 11; color: "#526575"
                    background: Rectangle { color: "#F4F6F8"; radius: 8 }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#DEE5EA" }
        RowLayout {
            Layout.fillWidth: true
            Caption {
                text: "이 페이지를 닫거나 학습센터로 돌아가도 진행 중인 학습은 계속됩니다."
                color: "#526575"; font.pixelSize: 12; Layout.fillWidth: true
            }
            Action {
                objectName: "progress-stop"
                visible: controller.trainingRunning
                enabled: page.details.cancellable
                text: page.details.status === "stopping" ? "중단 중…" : "학습 멈춤"
                onClicked: controller.stopTraining()
            }
        }
    }
}
