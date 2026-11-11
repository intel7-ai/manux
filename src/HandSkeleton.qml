import QtQuick
import "theme" as Theme

// The hand skeleton over the camera preview (from manux-merged-20261110 `src/manux/ui/qml/HandSkeleton.qml`).
// The runtime no longer draws it into the frame (QtRuntime.qml_skeleton): it sends dashboard.previewHand together with
// the preview image it belongs to, and this draws it on the image's painted area. Drawing only; it judges nothing.
// Place it over a PreserveAspectFit Image and pass the painted size (imageWidth/imageHeight).
Item {
    id: root
    objectName: "liveHandSkeleton"
    property real imageWidth: width
    property real imageHeight: height
    readonly property var hand: dashboard.previewHand
    Canvas {
        id: canvas
        objectName: "liveHandSkeletonCanvas"
        anchors.centerIn: parent
        width: root.imageWidth
        height: root.imageHeight
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onAvailableChanged: if (available) requestPaint()
        Connections { target: dashboard; function onPreviewChanged() { canvas.requestPaint() } }
        onPaint: {
            if (!available) return
            let c = getContext("2d"); c.reset()
            let h = root.hand
            let p = h ? h.lm : undefined
            if (!p || p.length !== 21) return
            c.save(); c.beginPath(); c.rect(0, 0, width, height); c.clip()
            // marks follow the hand's size in the frame: at fixed sizes they cover a far hand of 30-50 px completely
            let xs = p.map(v => v[0] * width), ys = p.map(v => v[1] * height)
            let k = Math.max(.45, Math.min(1.0, Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 160))
            let pinched = (h.guides || []).some(g => g[1] !== "near")
            let chains = [[0,1,2,3,4],[0,5,6,7,8],[0,9,10,11,12],[0,13,14,15,16],[0,17,18,19,20],[5,9,13,17]]
            c.lineCap = "round"; c.lineJoin = "round"
            // a dark outline under the line: visible on bright and dark backgrounds alike
            for (let pass = 0; pass < 2; pass++) {
                c.strokeStyle = pass === 0 ? "rgba(17,35,49,0.6)" : (pinched ? "#2FBF8F" : Theme.Studio.mint)
                c.lineWidth = (pass === 0 ? 4.5 : 2.2) * k
                for (let chain of chains) {
                    c.beginPath()
                    for (let i = 0; i < chain.length; i++) {
                        let v = p[chain[i]]
                        if (i === 0) c.moveTo(v[0] * width, v[1] * height); else c.lineTo(v[0] * width, v[1] * height)
                    }
                    c.stroke()
                }
            }
            c.fillStyle = Theme.Studio.stageText
            for (let i = 0; i < 21; i++) {
                let v = p[i]
                c.beginPath(); c.arc(v[0] * width, v[1] * height, (i % 4 === 0 && i > 0 ? 3.5 : 2.6) * k, 0, Math.PI * 2); c.fill()
            }
            // engine-provided pinch proximity: near (amber), pinched (green), action (red)
            let colors = {"near": "#F2B544", "pinched": "#2FBF8F", "action": "#E5484D"}
            for (let g of (h.guides || [])) {
                let a = p[4], b = p[g[0]]
                let col = colors[g[1]] || "#F2B544"
                c.strokeStyle = col; c.lineWidth = (g[1] === "near" ? 2 : 3) * k
                c.beginPath(); c.moveTo(a[0] * width, a[1] * height); c.lineTo(b[0] * width, b[1] * height); c.stroke()
                c.fillStyle = col
                for (let q of [a, b]) { c.beginPath(); c.arc(q[0] * width, q[1] * height, 6 * k, 0, Math.PI * 2); c.fill() }
                if (g[1] !== "near") {
                    c.beginPath(); c.arc((a[0] + b[0]) / 2 * width, (a[1] + b[1]) / 2 * height, 10 * k, 0, Math.PI * 2); c.stroke()
                }
            }
            // wrist -> palm line and the cursor anchor: green while the cursor moves, amber while it holds
            let moveCol = h.moving ? "#2FBF8F" : "#F2B544"
            c.strokeStyle = moveCol; c.lineWidth = 3 * k
            c.beginPath(); c.moveTo(p[0][0] * width, p[0][1] * height); c.lineTo(p[9][0] * width, p[9][1] * height); c.stroke()
            let anchor = h.anchor || []
            if (anchor.length === 2) {
                c.lineWidth = 2
                c.beginPath(); c.arc(anchor[0] * width, anchor[1] * height, 10, 0, Math.PI * 2); c.stroke()
                c.fillStyle = moveCol
                c.beginPath(); c.arc(anchor[0] * width, anchor[1] * height, 4, 0, Math.PI * 2); c.fill()
            }
            c.restore()
        }
    }
}
