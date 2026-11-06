"""Paint the original live-camera skeleton and motion ring; no gesture decisions here."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen


CONNECTIONS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
               (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))
COLORS = {"near": "#ffdc00", "pinched": "#00ff00", "action": "#ff3c00"}


def draw_hand_preview(image, view):
    if view.landmarks is None or view.preview_anchor is None:
        return image
    result = image.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = result.width(), result.height()
    scale = w / view.image.shape[1] if view.image is not None else 1.
    points = [QPointF(x*w, y*h) for x, y, _ in view.landmarks]
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    def line(a, b, color, width=2):
        painter.setPen(QPen(QColor(color), width*scale))
        painter.drawLine(a, b)
    def circle(point, radius, color, filled=True):
        painter.setPen(QPen(QColor(color), 2*scale))
        painter.setBrush(QColor(color) if filled else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, radius*scale, radius*scale)
    try:
        color = "#76e600" if any(state != "near" for _, state in view.preview_guides) else "#dcdcdc"
        for a, b in CONNECTIONS:
            line(points[a], points[b], color)
        for point in points:
            circle(point, 4, "#00b4ff")
        for tip, state in view.preview_guides:
            color = COLORS[state]
            line(points[4], points[tip], color, 2 if state == "near" else 3)
            circle(points[4], 8, color)
            circle(points[tip], 8, color)
            if state != "near":
                circle((points[4]+points[tip])/2, 11, color, False)
        color = "#76e600" if view.preview_moving else "#ff8c00"
        line(points[0], points[9], color, 3)
        point = QPointF(view.preview_anchor[0]*w, view.preview_anchor[1]*h)
        circle(point, 10, color, False)
        circle(point, 4, color)
    finally:
        painter.end()
    return result
