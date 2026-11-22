"""Minimalist 1.5px geometric vector icons drawn via QPainter (zero Emoji/Unicode soup)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


def draw_vector_icon(
    p: QPainter, name: str, rect: QRectF, color: str = "#F4F0E8", stroke: float = 1.5
) -> None:
    """Paint a crisp 16x16-grid-normalized geometric stroke icon inside rect."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    w = rect.width()
    h = rect.height()
    ox = rect.left()
    oy = rect.top()

    def pt(nx: float, ny: float) -> QPointF:
        return QPointF(ox + nx * w / 16.0, oy + ny * h / 16.0)

    def r(nx: float, ny: float, nw: float, nh: float) -> QRectF:
        return QRectF(ox + nx * w / 16.0, oy + ny * h / 16.0, nw * w / 16.0, nh * h / 16.0)

    if name == "plus":
        p.drawLine(pt(8, 3), pt(8, 13))
        p.drawLine(pt(3, 8), pt(13, 8))
    elif name == "minimize":
        p.drawLine(pt(3.5, 11.5), pt(12.5, 11.5))
    elif name == "close":
        p.drawLine(pt(4, 4), pt(12, 12))
        p.drawLine(pt(12, 4), pt(4, 12))
    elif name == "crop":
        p.drawLine(pt(5, 2), pt(5, 11))
        p.drawLine(pt(5, 11), pt(14, 11))
        p.drawLine(pt(2, 5), pt(11, 5))
        p.drawLine(pt(11, 5), pt(11, 14))
    elif name == "sliders":
        for y, xk in ((4, 6), (8, 11), (12, 5)):
            p.drawLine(pt(2.5, y), pt(13.5, y))
            p.setBrush(QColor(color))
            p.drawEllipse(pt(xk, y), 1.4 * w / 16.0, 1.4 * h / 16.0)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "merge":
        p.drawRect(r(2.5, 2.5, 11, 11))
        p.drawLine(pt(11.5, 4.5), pt(7, 9))
        p.drawLine(pt(7, 9), pt(10, 9))
        p.drawLine(pt(7, 9), pt(7, 6))
    elif name == "detach":
        p.drawRect(r(2.5, 5.5, 8, 8))
        p.drawLine(pt(8, 8), pt(13.5, 2.5))
        p.drawLine(pt(9.5, 2.5), pt(13.5, 2.5))
        p.drawLine(pt(13.5, 2.5), pt(13.5, 6.5))
    elif name == "crosshair":
        p.drawEllipse(r(3.5, 3.5, 9, 9))
        p.drawLine(pt(8, 1.5), pt(8, 4.5))
        p.drawLine(pt(8, 11.5), pt(8, 14.5))
        p.drawLine(pt(1.5, 8), pt(4.5, 8))
        p.drawLine(pt(11.5, 8), pt(14.5, 8))
    elif name == "pause":
        p.drawLine(pt(5.5, 3.5), pt(5.5, 12.5))
        p.drawLine(pt(10.5, 3.5), pt(10.5, 12.5))
    elif name == "play":
        path = QPainterPath()
        path.moveTo(pt(5, 3))
        path.lineTo(pt(13, 8))
        path.lineTo(pt(5, 13))
        path.closeSubpath()
        p.setBrush(QColor(color))
        p.drawPath(path)
    elif name == "copy":
        p.drawRoundedRect(r(5.5, 5.5, 8, 8), 1.5, 1.5)
        p.drawLine(pt(3, 10.5), pt(3, 3.5))
        p.drawLine(pt(3, 3.5), pt(10.5, 3.5))
    elif name == "check":
        p.drawLine(pt(3, 8.5), pt(6.5, 12))
        p.drawLine(pt(6.5, 12), pt(13, 4.5))
    elif name == "lock":
        p.drawRoundedRect(r(3.5, 7, 9, 6.5), 1.5, 1.5)
        p.drawArc(r(5.2, 2.5, 5.6, 6.5), 0, 180 * 16)
    elif name == "unlock":
        p.drawRoundedRect(r(3.5, 7, 9, 6.5), 1.5, 1.5)
        p.drawArc(r(5.2, 2.2, 5.6, 6.5), 25 * 16, 155 * 16)
    elif name == "eye":
        path = QPainterPath()
        path.moveTo(pt(1.5, 8))
        path.quadTo(pt(8, 2.5), pt(14.5, 8))
        path.quadTo(pt(8, 13.5), pt(1.5, 8))
        p.drawPath(path)
        p.drawEllipse(pt(8, 8), 2.0 * w / 16.0, 2.0 * h / 16.0)
    elif name == "eye_off":
        p.drawLine(pt(2, 14), pt(14, 2))
        p.drawArc(r(2.5, 4.5, 11, 7), 15 * 16, 150 * 16)
    elif name == "mic":
        p.drawRoundedRect(r(5.8, 2.2, 4.4, 7.2), 2.2, 2.2)
        p.drawArc(r(3.8, 5, 8.4, 6.4), 180 * 16, 180 * 16)
        p.drawLine(pt(8, 11.4), pt(8, 14))
        p.drawLine(pt(5.5, 14), pt(10.5, 14))
    elif name == "mic_off":
        p.drawRoundedRect(r(5.8, 2.2, 4.4, 7.2), 2.2, 2.2)
        p.drawLine(pt(2.5, 13.5), pt(13.5, 2.5))
    elif name == "send":
        path = QPainterPath()
        path.moveTo(pt(2, 8))
        path.lineTo(pt(14, 2.5))
        path.lineTo(pt(9.5, 13.5))
        path.lineTo(pt(7.2, 9.2))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "layers":
        for dy in (0, 3.8, 7.6):
            p.drawLine(pt(2.5, 4.5 + dy), pt(8, 2 + dy))
            p.drawLine(pt(8, 2 + dy), pt(13.5, 4.5 + dy))
            if dy == 0:
                p.drawLine(pt(2.5, 4.5), pt(8, 7))
                p.drawLine(pt(8, 7), pt(13.5, 4.5))
    elif name == "cpu":
        p.drawRoundedRect(r(4, 4, 8, 8), 1.5, 1.5)
        p.drawRect(r(6.2, 6.2, 3.6, 3.6))
        for k in (5.8, 10.2):
            p.drawLine(pt(k, 2), pt(k, 4))
            p.drawLine(pt(k, 12), pt(k, 14))
            p.drawLine(pt(2, k), pt(4, k))
            p.drawLine(pt(12, k), pt(14, k))
    elif name == "cloud":
        p.drawRoundedRect(r(2.5, 7, 11, 5.2), 2.6, 2.6)
        p.drawArc(r(4.5, 3.8, 6.5, 6), 0, 180 * 16)
    elif name == "bolt":
        path = QPainterPath()
        path.moveTo(pt(9, 2))
        path.lineTo(pt(4, 9))
        path.lineTo(pt(8, 9))
        path.lineTo(pt(7, 14))
        path.lineTo(pt(12, 7))
        path.lineTo(pt(8, 7))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "gamepad":
        p.drawRoundedRect(r(2, 5, 12, 6.5), 3.0, 3.0)
        p.drawLine(pt(5, 7), pt(5, 9.5))
        p.drawLine(pt(3.8, 8.2), pt(6.2, 8.2))
        p.drawPoint(pt(10.5, 7.6))
        p.drawPoint(pt(11.8, 8.8))
    elif name == "keyboard":
        p.drawRoundedRect(r(2, 4, 12, 8), 1.6, 1.6)
        p.drawLine(pt(4.5, 7), pt(5.5, 7))
        p.drawLine(pt(7.5, 7), pt(8.5, 7))
        p.drawLine(pt(10.5, 7), pt(11.5, 7))
        p.drawLine(pt(5, 9.8), pt(11, 9.8))
    elif name == "trash":
        p.drawLine(pt(3, 4.5), pt(13, 4.5))
        p.drawLine(pt(6, 4.5), pt(6, 2.8))
        p.drawLine(pt(6, 2.8), pt(10, 2.8))
        p.drawLine(pt(10, 2.8), pt(10, 4.5))
        p.drawRoundedRect(r(4.2, 4.5, 7.6, 9), 1.2, 1.2)
    elif name == "refresh":
        p.drawArc(r(3, 3, 10, 10), 30 * 16, 270 * 16)
        p.drawLine(pt(11.5, 2.5), pt(13, 5.2))
        p.drawLine(pt(10.2, 5.4), pt(13, 5.2))
    elif name == "download":
        p.drawLine(pt(8, 2.5), pt(8, 10.5))
        p.drawLine(pt(5, 7.8), pt(8, 10.8))
        p.drawLine(pt(11, 7.8), pt(8, 10.8))
        p.drawLine(pt(3, 13.2), pt(13, 13.2))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(pt(2.5, 4))
        path.lineTo(pt(6.8, 4))
        path.lineTo(pt(8.2, 5.8))
        path.lineTo(pt(13.5, 5.8))
        path.lineTo(pt(13.5, 12.5))
        path.lineTo(pt(2.5, 12.5))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "terminal":
        p.drawRoundedRect(r(2.2, 3, 11.6, 10), 1.5, 1.5)
        p.drawLine(pt(4.5, 6), pt(7, 8))
        p.drawLine(pt(7, 8), pt(4.5, 10))
        p.drawLine(pt(8.2, 10.2), pt(11.2, 10.2))
    elif name == "shield":
        path = QPainterPath()
        path.moveTo(pt(8, 2.2))
        path.lineTo(pt(13, 4.2))
        path.lineTo(pt(13, 8.2))
        path.quadTo(pt(13, 12), pt(8, 14))
        path.quadTo(pt(3, 12), pt(3, 8.2))
        path.lineTo(pt(3, 4.2))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "home":
        path = QPainterPath()
        path.moveTo(pt(2, 7.5))
        path.lineTo(pt(8, 2.5))
        path.lineTo(pt(14, 7.5))
        p.drawPath(path)
        p.drawRoundedRect(r(3.8, 7.2, 8.4, 6.4), 1.2, 1.2)
        p.drawRect(r(6.6, 10.0, 2.8, 3.6))
    elif name == "power":
        p.drawArc(r(3.0, 3.6, 10.0, 10.0), 125 * 16, 290 * 16)
        p.drawLine(pt(8, 2.0), pt(8, 7.6))
    elif name == "palette":
        p.drawRoundedRect(r(2.5, 2.5, 4.6, 4.6), 1.0, 1.0)
        p.drawRoundedRect(r(8.9, 2.5, 4.6, 4.6), 1.0, 1.0)
        p.drawRoundedRect(r(2.5, 8.9, 4.6, 4.6), 1.0, 1.0)
        p.setBrush(QColor(color))
        p.drawRoundedRect(r(8.9, 8.9, 4.6, 4.6), 1.0, 1.0)
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "activity":
        p.drawLine(pt(1.5, 8.5), pt(4.2, 8.5))
        p.drawLine(pt(4.2, 8.5), pt(6.2, 3.5))
        p.drawLine(pt(6.2, 3.5), pt(9.5, 13.0))
        p.drawLine(pt(9.5, 13.0), pt(11.6, 7.2))
        p.drawLine(pt(11.6, 7.2), pt(14.5, 8.5))
    elif name == "grip":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        for gx in (5.5, 10.5):
            for gy in (4, 8, 12):
                p.drawEllipse(pt(gx, gy), 0.95 * w / 16.0, 0.95 * h / 16.0)

    p.restore()


def make_icon(name: str, color: str = "#F4F0E8", size: int = 16) -> QIcon:
    """Return a cached crisp vector QIcon drawn at 2x scale for Retina/HiDPI clarity."""
    key = (name, color, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_vector_icon(p, name, QRectF(2, 2, size * 2 - 4, size * 2 - 4), color=color, stroke=3.0)
    p.end()
    pm.setDevicePixelRatio(2.0)

    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon
