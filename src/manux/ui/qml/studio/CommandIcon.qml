import QtQuick
import "../theme" as Theme

Canvas {
    id: icon
    property string name: "control"
    property color color: Theme.Studio.ink
    implicitWidth: 20
    implicitHeight: 20
    onNameChanged: requestPaint()
    onColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onAvailableChanged: if (available) requestPaint()
    onPaint: {
        if (!available) return
        const c = getContext("2d")
        c.reset()
        c.scale(width / 24, height / 24)
        c.strokeStyle = color
        c.fillStyle = color
        c.lineWidth = 1.6
        c.lineCap = "round"
        c.lineJoin = "round"
        function line(points) {
            c.beginPath()
            c.moveTo(points[0], points[1])
            for (let i = 2; i < points.length; i += 2) c.lineTo(points[i], points[i + 1])
            c.stroke()
        }
        function box(x, y, w, h) { c.strokeRect(x, y, w, h) }
        if (name === "control") {
            line([5,3,5,18,9,14,13,21,16,19,12,12,18,11,5,3])
        } else if (name === "actions") {
            box(3,3,6,6); box(15,15,6,6)
            line([9,6,18,6,18,15]); line([3,16,8,16,8,21])
        } else if (name === "recordings") {
            box(4,5,16,15); line([8,2,16,2]); line([10,9,15,12.5,10,16,10,9])
        } else if (name === "learning") {
            line([2,8,12,3,22,8,12,13,2,8]); line([6,11,6,17,12,20,18,17,18,11]); line([22,8,22,16])
        } else if (name === "device") {
            box(3,5,18,12); line([8,21,16,21]); line([12,17,12,21]); line([9,9,15,9,15,13,9,13,9,9])
        } else if (name === "settings") {
            line([5,3,5,7]); line([5,12,5,21]); line([12,3,12,14]); line([12,19,12,21]); line([19,3,19,6]); line([19,11,19,21])
            box(2,7,6,5); box(9,14,6,5); box(16,6,6,5)
        } else if (name === "arrow") {
            line([4,12,20,12]); line([15,7,20,12,15,17])
        } else if (name === "hand") {
            line([8,13,8,5,10,5,10,11,10,3,12,3,12,11,12,4,14,4,14,12,14,7,16,7,16,15,14,20,8,20,4,14,5,12,8,15])
        }
    }
}
