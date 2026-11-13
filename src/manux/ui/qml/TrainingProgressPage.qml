import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "theme" as Theme

Popup {
    id: page
    objectName: "trainingProgressPage"
    required property var controller
    readonly property var state: controller.trainingProgressState
    property bool expandedEvents: false
    signal backRequested()
    anchors.centerIn: parent
    width: Math.min(1000, parent.width - 32)
    height: Math.min(800, parent.height - 32)
    modal: true
    focus: true
    padding: 20
    closePolicy: Popup.CloseOnEscape
    onOpened: controller.showTrainingProgress(true)
    onClosed: controller.showTrainingProgress(false)
    background: Rectangle { color: Theme.Studio.paper; radius: 16; border.color: Theme.Studio.line }
    component Copy: Label {
        color: Theme.Studio.ink; font.family: Theme.Studio.font; font.pixelSize: 14; wrapMode: Text.Wrap
    }
    component Metric: ColumnLayout {
        property string caption
        property string value
        Layout.fillWidth: true
        Copy { text: caption; color: Theme.Studio.muted; Layout.fillWidth: true }
        Copy { text: value; font.pixelSize: 20; font.bold: true; Layout.fillWidth: true }
    }
    contentItem: ColumnLayout {
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Copy { text: "학습 진행"; font.pixelSize: 24; font.bold: true; Layout.fillWidth: true }
            StudioButton {
                objectName: "trainingProgressBack"
                text: "학습센터로"; onClicked: { page.close(); page.backRequested() }
            }
            StudioButton { text: "닫기"; onClicked: page.close() }
        }
        ScrollView {
            id: scroll
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth; spacing: 16
                Copy { text: page.state.statusText || "학습 대기"; font.pixelSize: 21; font.bold: true; color: Theme.Studio.mintDark }
                Copy { text: page.state.tasks || "선택한 학습 동작"; Layout.fillWidth: true }
                Copy { objectName: "trainingData"; text: page.state.data || ""; Layout.fillWidth: true; color: Theme.Studio.muted }
                Copy { text: "앱을 닫기 전 마지막 완료·중단 결과입니다."; visible: page.state.restored === true; color: Theme.Studio.muted }
                Copy { text: page.state.stage || "학습센터에서 학습을 시작하세요"; Layout.fillWidth: true }
                RowLayout {
                    Layout.fillWidth: true
                    ProgressBar { Layout.fillWidth: true; value: page.state.progress || 0 }
                    Copy { objectName: "trainingPercent"; text: (page.state.percent || 0) + "%"; font.bold: true }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: page.width < 840 ? 2 : 4
                    columnSpacing: 16; rowSpacing: 16
                    Metric { caption: "경과 시간"; value: page.state.elapsed || "0초" }
                    Metric { objectName: "trainingEta"; caption: "남은 학습 시간"; value: page.state.remaining || "계산 중" }
                    Metric { caption: "완료 예상 시각"; value: page.state.finish || "계산 중" }
                    Metric { caption: "반복 / 분류기"; value: (page.state.epoch || "—") + " · " + (page.state.stageCount || "—") }
                }
                Copy { text: page.state.etaHint || ""; Layout.fillWidth: true; color: Theme.Studio.muted }
                Copy { text: "현재 단계 학습 손실 · 최근 " + (page.state.loss || "—") + " · 최저 " + (page.state.bestLoss || "—"); font.bold: true }
                Canvas {
                    id: graph
                    objectName: "trainingLossGraph"
                    Layout.fillWidth: true; Layout.preferredHeight: 170
                    property var values: page.state.losses || []
                    onValuesChanged: requestPaint()
                    onWidthChanged: requestPaint()
                    Connections { target: Theme.Studio; function onThemeNameChanged() { graph.requestPaint() } }
                    Copy { anchors.centerIn: parent; text: "학습이 진행되면 손실 그래프가 나타납니다"; visible: graph.values.length < 2; color: Theme.Studio.muted }
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        ctx.fillStyle = Theme.Studio.surface; ctx.fillRect(0, 0, width, height)
                        if (values.length < 2) return
                        const low = Math.min.apply(null, values), high = Math.max.apply(null, values)
                        const span = Math.max(0.0001, high - low)
                        ctx.strokeStyle = Theme.Studio.mintDark; ctx.lineWidth = 2; ctx.beginPath()
                        for (let i = 0; i < values.length; i++) {
                            const x = 12 + (width - 24) * i / (values.length - 1)
                            const y = height - 12 - (height - 24) * (values[i] - low) / span
                            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                        }
                        ctx.stroke()
                    }
                }
                Copy { text: page.state.error || ""; visible: text !== ""; color: Theme.Studio.amber; Layout.fillWidth: true }
                Copy {
                    Layout.fillWidth: true
                    text: "이 화면을 닫아도 학습은 계속됩니다. 새로 학습한 동작은 검증 후 시험 모드로 준비됩니다."
                    color: Theme.Studio.muted
                }
                StudioButton { text: "학습 중단"; enabled: page.state.cancellable === true; onClicked: page.controller.stopTraining() }
                RowLayout {
                    Layout.fillWidth: true
                    Copy { text: "진행 기록 · 최신순"; font.bold: true; Layout.fillWidth: true }
                    StudioButton { text: page.expandedEvents ? "접기" : "전체 기록 보기"; onClicked: page.expandedEvents = !page.expandedEvents }
                }
                Copy {
                    objectName: "trainingEvents"
                    Layout.fillWidth: true
                    text: (page.state.events || []).slice(page.expandedEvents ? -120 : -16).reverse().join("\n") || "학습을 시작하면 단계별 기록이 표시됩니다."
                    color: Theme.Studio.muted
                }
            }
        }
    }
}
