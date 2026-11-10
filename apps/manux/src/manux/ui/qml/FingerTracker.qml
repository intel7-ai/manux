import QtQuick

// Display port of virtualHandWindow from Dan's hand-mouse@49dfaea.
Window {
    id: tracker
    objectName: "fingerTracker"
    required property var controller
    title: "MANUX 가상 손"
    transientParent: null
    visible: controller.fingerOverlay && shown !== null
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
           | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
    width: 640
    height: 640
    readonly property var frame: controller.fingerFrame
    property var shown: null
    property bool settling: false
    readonly property var bones: [
        [0,1],[1,2],[2,3],[3,4], [0,5],[5,6],[6,7],[7,8], [5,9],[9,10],[10,11],[11,12],
        [9,13],[13,14],[14,15],[15,16], [13,17],[17,18],[18,19],[19,20], [0,17]
    ]
    readonly property var guideColors: ({near: "#F2B544", pinched: "#2FBF8F", action: "#E5484D"})

    onFrameChanged: {
        if (frame.pts !== undefined) {
            handLoss.stop()
            // Center the drawing on the existing OS cursor; never relocate input.
            x = frame.cursor[0] - width / 2
            y = frame.cursor[1] - height / 2
            if (shown === null)
                shown = frame.pts.slice()
            settling = true
        } else {
            settling = false
            if (frame.lost && shown !== null) {
                handLoss.start()
            } else {
                handLoss.stop()
                shown = null
            }
        }
        handCanvas.requestPaint()
    }
    Timer {
        id: handLoss
        interval: 400
        onTriggered: tracker.shown = null
    }
    FrameAnimation {
        running: tracker.visible && tracker.settling
        onTriggered: {
            const target = tracker.frame.pts
            const points = tracker.shown
            if (!target || points === null)
                return
            // Dan's 40 ms glide between uneven camera frames, at the display refresh rate.
            const blend = 1 - Math.exp(-Math.min(frameTime, 0.1) / 0.04)
            let unsettled = false
            for (let i = 0; i < points.length; ++i) {
                const delta = target[i] - points[i]
                if (Math.abs(delta) > 0.1) {
                    points[i] += delta * blend
                    unsettled = true
                } else {
                    points[i] = target[i]
                }
            }
            tracker.settling = unsettled
            handCanvas.requestPaint()
        }
    }
    Canvas {
        id: handCanvas
        objectName: "virtualHandCanvas"
        anchors.fill: parent
        opacity: tracker.frame.lost ? 0.35 : 1
        onAvailableChanged: if (available) requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const points = tracker.shown
            if (points === null) return
            const cx = width / 2, cy = height / 2
            function at(i) { return [cx + points[2 * i], cy + points[2 * i + 1]] }
            ctx.lineCap = "round"
            // The outline keeps the joints legible on both light and dark apps.
            for (let pass = 0; pass < 2; ++pass) {
                ctx.strokeStyle = pass === 0 ? "rgba(17,35,49,0.55)" : "rgba(245,247,248,0.95)"
                ctx.lineWidth = pass === 0 ? 7 : 3.5
                ctx.beginPath()
                for (const bone of tracker.bones) {
                    const a = at(bone[0]), b = at(bone[1])
                    ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1])
                }
                ctx.stroke()
            }
            ctx.fillStyle = "#46E5BD"
            for (let joint = 0; joint < 21; ++joint) {
                const p = at(joint)
                ctx.beginPath()
                ctx.arc(p[0], p[1], joint > 0 && joint % 4 === 0 ? 5 : 3.5, 0, 2 * Math.PI)
                ctx.fill()
            }
            for (const guide of tracker.frame.guides || []) {
                const thumb = at(4), tip = at(guide[0])
                ctx.strokeStyle = tracker.guideColors[guide[1]] || tracker.guideColors.near
                ctx.lineWidth = guide[1] === "near" ? 3 : 5
                ctx.beginPath(); ctx.moveTo(thumb[0], thumb[1]); ctx.lineTo(tip[0], tip[1]); ctx.stroke()
            }
            ctx.lineWidth = 2
            ctx.strokeStyle = "rgba(17,35,49,0.55)"
            ctx.beginPath(); ctx.arc(cx, cy, 5, 0, 2 * Math.PI); ctx.stroke()
        }
    }
}
