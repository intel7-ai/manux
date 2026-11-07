import QtQuick
import QtQuick.Controls

Window {
    id: fingerTracker
    objectName: "fingerTracker"
    required property var controller
    title: "MANUX 손가락 표시"
    transientParent: null
    visible: controller.fingerOverlay
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
           | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
    width: 320
    height: 320
    readonly property var frame: controller.fingerFrame
    readonly property bool hasHand: frame.tips !== undefined
    readonly property real palmPx: 46
    readonly property var colors: ({ idle: "#94A3B8", move: "#46E5BD", left: "#10B981", right: "#3B82F6", scroll: "#C084FC", other: "#94A3B8" })
    readonly property var labels: ({ idle: "쉬는 중", move: "커서 이동", left: "좌클릭 · 드래그", right: "우클릭", scroll: "스크롤", other: "손 인식 중" })
    onFrameChanged: {
        if (hasHand) {
            x = frame.cursor[0] - width / 2
            y = frame.cursor[1] - height / 2
        }
    }
    Item {
        anchors.fill: parent
        opacity: fingerTracker.hasHand ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: fingerTracker.hasHand ? 80 : 500 } }
        Canvas {
            id: fingerCanvas
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var f = fingerTracker.frame
                if (!fingerTracker.hasHand) return
                var color = fingerTracker.colors[f.mode] || fingerTracker.colors.other
                var cx = width / 2, cy = height / 2, s = fingerTracker.palmPx
                function at(i) { return [cx + f.tips[i][0] * s, cy + f.tips[i][1] * s] }
                ctx.lineWidth = 3
                ctx.strokeStyle = color
                if (f.contact > 0) {
                    var a = at(0), b = at(f.contact)
                    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke()
                }
                for (var i = 0; i < 5; i++) {
                    var p = at(i)
                    var active = i === 0 || i === f.contact
                    ctx.beginPath()
                    ctx.arc(p[0], p[1], i === 1 ? 7 : 5, 0, Math.PI * 2)
                    ctx.fillStyle = active ? color : "rgba(255,255,255,0.85)"
                    ctx.fill()
                    ctx.lineWidth = 2
                    ctx.stroke()
                }
            }
            Connections {
                target: fingerTracker.controller
                function onFingerFrameChanged() { fingerCanvas.requestPaint() }
            }
        }
        Rectangle {
            x: parent.width / 2 + 18
            y: parent.height / 2 + 26
            width: trackerLabel.implicitWidth + 20
            height: 24
            radius: 12
            color: "#E0112331"
            Label {
                id: trackerLabel
                anchors.centerIn: parent
                text: fingerTracker.hasHand ? (fingerTracker.labels[fingerTracker.frame.mode] || fingerTracker.labels.other) : ""
                color: fingerTracker.hasHand ? (fingerTracker.colors[fingerTracker.frame.mode] || fingerTracker.colors.other) : "#94A3B8"
                font.pixelSize: 13
                font.bold: true
                font.family: "Malgun Gothic"
            }
        }
    }
}
