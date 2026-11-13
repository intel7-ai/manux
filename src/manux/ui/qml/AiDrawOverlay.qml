import QtQuick
import QtQuick.Controls
import "ai_theme" as Theme

// Drawing mode on the monitor (src/draw_mode.py): the pen, the stroke being drawn, the last shape and its command.
// Covers the primary monitor, click-through and always on top; it only shows dashboard.drawView (judges nothing).
Window {
    id: drawOverlay
    required property var dashboard
    objectName: "drawOverlay"
    title: "MANUX 그리기 모드"
    transientParent: null
    readonly property var view: dashboard.drawView
    readonly property var area: dashboard.drawArea
    readonly property bool showLast: view.last !== undefined
    visible: view.on === true || showLast
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
           | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
    x: area[0]; y: area[1]; width: area[2]; height: area[3]
    onViewChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var v = drawOverlay.view, w = width, h = height
            function path(pts) {
                ctx.beginPath()
                for (var i = 0; i < pts.length; i++) {
                    if (i === 0) ctx.moveTo(pts[i][0] * w, pts[i][1] * h); else ctx.lineTo(pts[i][0] * w, pts[i][1] * h)
                }
            }
            ctx.lineCap = "round"; ctx.lineJoin = "round"
            // the last stroke, fading out, in green (recognised) or red (not)
            if (v.last && v.last.points && v.last.points.length > 1) {
                ctx.globalAlpha = Math.max(0, 1 - v.last.age / 1.5)
                path(v.last.points); ctx.strokeStyle = "rgba(17,35,49,0.5)"; ctx.lineWidth = 12; ctx.stroke()
                path(v.last.points); ctx.strokeStyle = v.last.ok ? "#2FBF8F" : "#E5484D"; ctx.lineWidth = 6; ctx.stroke()
                ctx.globalAlpha = 1
            }
            // the stroke being drawn: dark outline under a mint line (shows on light and dark windows)
            if (v.stroke && v.stroke.length > 1) {
                path(v.stroke); ctx.strokeStyle = "rgba(17,35,49,0.55)"; ctx.lineWidth = 12; ctx.stroke()
                path(v.stroke); ctx.strokeStyle = "#46E5BD"; ctx.lineWidth = 6; ctx.stroke()
            }
            // the pen: filled while down (the index pointing), a ring while up
            if (v.on && v.pen && v.pen.length === 2) {
                var px = v.pen[0] * w, py = v.pen[1] * h
                ctx.beginPath(); ctx.arc(px, py, v.down ? 9 : 12, 0, 2 * Math.PI)
                if (v.down) { ctx.fillStyle = "#46E5BD"; ctx.fill() }
                ctx.lineWidth = 3; ctx.strokeStyle = "#112331"; ctx.stroke()
            }
        }
    }
    // the shape's name and what it did (1.5 s)
    Rectangle {
        visible: drawOverlay.showLast
        anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: 80 }
        width: lastLabel.implicitWidth + 32; height: 44; radius: 22
        color: "#E6112331"
        opacity: drawOverlay.showLast ? Math.max(0, 1 - drawOverlay.view.last.age / 1.5) : 0
        Label {
            id: lastLabel
            anchors.centerIn: parent
            text: !drawOverlay.showLast ? ""
                  : drawOverlay.view.last.ok ? (drawOverlay.view.last.name + "  →  " + (drawOverlay.view.last.action || ""))
                  : ("알아보지 못했어요 · " + drawOverlay.view.last.why)
            color: drawOverlay.showLast && drawOverlay.view.last.ok ? "#46E5BD" : "#FF8A8F"
            font.pixelSize: 18; font.bold: true; font.family: Theme.Studio.font
        }
    }
    // the mode badge
    Rectangle {
        visible: drawOverlay.view.on === true
        anchors { left: parent.left; bottom: parent.bottom; margins: 24 }
        width: badge.implicitWidth + 28; height: 36; radius: 18
        color: "#E6112331"
        Label {
            id: badge
            anchors.centerIn: parent
            text: "✏ 그리기 모드 · 검지를 펴서 그리기 · 손을 펴면 끝 · 설정에서 끄기"
            color: "#F5F7F8"; font.pixelSize: 14; font.family: Theme.Studio.font
        }
    }
}
