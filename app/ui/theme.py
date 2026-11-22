"""Quiet Studio Slate design tokens, QPainter surfaces, StrokedTextLabel, LED beacon, and VU meter."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QApplication, QFrame, QMenu, QSizePolicy, QWidget

from ..config import BASE_DIR

# Core Quiet Studio Slate Palette
BG_CANVAS = "#202226"
BG_RAIL = "#191B1F"
BG_SURFACE = "#26292F"
BG_CONTROL = "#2C3038"
BG_HOVER = "#353A44"
BG_PRESSED = "#23262C"
BORDER_SUBTLE = "#2D3139"
BORDER_HAIRLINE = "rgba(255, 255, 255, 0.08)"

ACCENT_PRIMARY = "#3B82F6"
STATUS_OK = "#34C77B"
STATUS_WARN = "#E5A93C"
STATUS_ERR = "#E05A5A"

# Compatibility aliases (preserve downstream imports without neon palette bleed)
BG_CARBON = BG_CANVAS
BG_GRAPHITE = BG_RAIL
BG_ELEVATED = BG_CONTROL
BG_DEEP = BG_CANVAS
BG_CARD = BG_SURFACE

ACCENT_VERMILION = ACCENT_PRIMARY
ACCENT_LIME = STATUS_OK
ACCENT_OCHRE = STATUS_WARN
ACCENT_CRIMSON = STATUS_ERR
ACCENT_CYAN = ACCENT_PRIMARY
ACCENT_VIOLET = ACCENT_PRIMARY
ACCENT_AMBER = STATUS_WARN
ACCENT_GREEN = STATUS_OK
ACCENT_RED = STATUS_ERR

TEXT_PRIMARY = "#F1F3F5"
TEXT_SECONDARY = "#9CA3AF"
TEXT_MUTED = "#6B7280"
TEXT_IVORY = TEXT_PRIMARY
TEXT_STONE = TEXT_SECONDARY
TEXT_MAIN = TEXT_PRIMARY
TEXT_SUB = TEXT_SECONDARY
TEXT_ASR_LIME = "#A7F3D0"

_CHECK_INITIALIZED = False


def _ensure_check_png() -> str:
    """Create crisp white checkmark icon for checked checkboxes on primary cobalt blue."""
    global _CHECK_INITIALIZED
    check_path: Path = BASE_DIR / "assets" / "check.png"
    try:
        check_path.parent.mkdir(parents=True, exist_ok=True)
        if not _CHECK_INITIALIZED or not check_path.exists():
            img = QImage(24, 24, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor("#FFFFFF"), 3.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawLine(QPointF(5.0, 12.5), QPointF(10.0, 17.5))
            p.drawLine(QPointF(10.0, 17.5), QPointF(19.0, 6.5))
            p.end()
            img.save(str(check_path))
            _CHECK_INITIALIZED = True
    except Exception:
        pass
    return check_path.as_posix()


def build_app_qss() -> str:
    check_url = _ensure_check_png()
    return f"""
QWidget {{
    color: #F1F3F5;
    font-family: "Microsoft YaHei UI", "Noto Sans SC", sans-serif;
}}
QPushButton {{
    background-color: #2C3038;
    color: #F1F3F5;
    border: 1px solid rgba(255, 255, 255, 0.11);
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: #353A44;
    border: 1px solid rgba(255, 255, 255, 0.22);
    color: #FFFFFF;
}}
QPushButton:pressed {{
    background-color: #23262C;
}}
QPushButton:disabled {{
    background-color: #23262C;
    color: #6B7280;
    border: 1px solid rgba(255, 255, 255, 0.05);
}}
QPushButton[accent="true"] {{
    background-color: #3B82F6;
    border: 1px solid #60A5FA;
    color: #FFFFFF;
    font-weight: bold;
}}
QPushButton[accent="true"]:hover {{
    background-color: #4F8DF8;
    border: 1px solid #93C5FD;
    color: #FFFFFF;
}}
QPushButton[accent="true"]:pressed {{
    background-color: #2563EB;
}}
QPushButton[accent="true"]:disabled {{
    background-color: #283244;
    border: 1px solid rgba(59, 130, 246, 0.22);
    color: #7D8B9E;
}}
QPushButton[lime="true"] {{
    background-color: rgba(52, 199, 123, 0.14);
    border: 1px solid rgba(52, 199, 123, 0.45);
    color: #34C77B;
    font-weight: bold;
}}
QPushButton[lime="true"]:hover {{
    background-color: rgba(52, 199, 123, 0.22);
}}
QPushButton[amber="true"] {{
    background-color: rgba(229, 169, 60, 0.14);
    border: 1px solid rgba(229, 169, 60, 0.45);
    color: #E5A93C;
    font-weight: bold;
}}
QPushButton[amber="true"]:hover {{
    background-color: rgba(229, 169, 60, 0.22);
}}
QPushButton[ghost="true"] {{
    background-color: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.09);
    color: #9CA3AF;
}}
QPushButton[ghost="true"]:hover {{
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.20);
    color: #F1F3F5;
}}
QPushButton[seg="true"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px 10px;
    font-size: 11px;
    color: #9CA3AF;
}}
QPushButton[seg="true"]:hover {{
    background-color: rgba(255, 255, 255, 0.06);
    color: #F1F3F5;
}}
QPushButton[seg_active="true"] {{
    background-color: rgba(59, 130, 246, 0.18);
    border: 1px solid rgba(59, 130, 246, 0.68);
    border-radius: 5px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: bold;
    color: #FFFFFF;
}}
QPushButton[danger="true"]:hover {{
    background-color: rgba(224, 90, 90, 0.22);
    border: 1px solid #E05A5A;
    color: #FFE4E4;
}}
QPushButton[compact="true"] {{
    padding: 3px 9px;
    font-size: 11px;
    border-radius: 5px;
}}
QCheckBox, QRadioButton {{
    color: #F1F3F5;
    background: transparent;
    spacing: 7px;
    font-size: 12px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 3px;
    border: 1px solid rgba(255, 255, 255, 0.24);
    background-color: #191B1F;
}}
QCheckBox::indicator:hover {{
    border: 1px solid #3B82F6;
}}
QCheckBox::indicator:checked {{
    background-color: #3B82F6;
    border: 1px solid #60A5FA;
    image: url({check_url});
}}
QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.24);
    background-color: #191B1F;
}}
QRadioButton::indicator:hover {{
    border: 1px solid #3B82F6;
}}
QRadioButton::indicator:checked {{
    background-color: #3B82F6;
    border: 3px solid #191B1F;
}}
QComboBox {{
    background-color: #191B1F;
    color: #F1F3F5;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    min-height: 22px;
}}
QComboBox:hover {{
    border: 1px solid rgba(255, 255, 255, 0.25);
}}
QComboBox:focus {{
    border: 1px solid #3B82F6;
}}
QComboBox:disabled {{
    background-color: #1C1E22;
    color: #6B7280;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: #191B1F;
    color: #F1F3F5;
    border: 1px solid rgba(255, 255, 255, 0.18);
    selection-background-color: #3B82F6;
    selection-color: #FFFFFF;
    outline: 0;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: #2E323B;
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: #3B82F6;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: #F1F3F5;
    border: 2px solid #3B82F6;
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:disabled {{
    background: #4B5563;
    border: 2px solid #374151;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea QWidget#qt_scrollarea_viewport {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 5px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 0.16);
    border-radius: 2px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(255, 255, 255, 0.30);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QLineEdit, QPlainTextEdit {{
    background-color: #191B1F;
    color: #F1F3F5;
    border: 1px solid rgba(255, 255, 255, 0.13);
    border-radius: 6px;
    padding: 5px 9px;
    selection-background-color: #3B82F6;
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid #3B82F6;
}}
QProgressBar {{
    background: #191B1F;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 4px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: #3B82F6;
    border-radius: 3px;
}}
QMenu {{
    background-color: #191B1F;
    color: #F1F3F5;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 7px;
    padding: 5px 0px;
}}
QMenu::item {{
    background-color: transparent;
    color: #F1F3F5;
    padding: 6px 22px;
    font-size: 12px;
}}
QMenu::item:selected {{
    background-color: #3B82F6;
    color: #FFFFFF;
    font-weight: bold;
}}
QMenu::separator {{
    height: 1px;
    background: rgba(255, 255, 255, 0.09);
    margin: 4px 10px;
}}
QToolTip {{
    background-color: #191B1F;
    color: #F1F3F5;
    border: 1px solid rgba(255, 255, 255, 0.20);
    border-radius: 5px;
    padding: 4px 8px;
    font-size: 11px;
}}
"""


APP_QSS: str = build_app_qss()


def start_native_window_drag(widget: QWidget) -> bool:
    """Delegate dragging to Windows native window move so cross-monitor mixed-DPI transitions never hang."""
    try:
        wh = widget.windowHandle()
        if wh is not None:
            return bool(wh.startSystemMove())
    except Exception:
        pass
    return False


def apply_rounded_mask(widget: QWidget, radius: float = 10.0) -> None:
    """Apply rounded corner QRegion mask (cached by size) so setWindowOpacity works cleanly with WDA."""
    w, h = widget.width(), widget.height()
    if w <= 2 or h <= 2:
        return
    key = (w, h, float(radius))
    if getattr(widget, "_cached_mask_key", None) == key:
        return
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, float(w), float(h)), radius, radius)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))
    setattr(widget, "_cached_mask_key", key)


def paint_panel_surface(
    painter: QPainter,
    rect: QRectF,
    radius: float = 10.0,
    opacity: float = 0.94,
    active_border: bool = False,
    border_hex: str = "",
    bg_hex: str = BG_CANVAS,
) -> None:
    """Render clean single-window slate surface (opacity via setWindowOpacity preserves Win32 WDA)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    bg_col = QColor(bg_hex or BG_CANVAS)
    if not bg_col.isValid():
        bg_col = QColor(BG_CANVAS)
    painter.fillRect(rect, bg_col)

    body_rect = rect.adjusted(0.75, 0.75, -0.75, -0.75)
    path = QPainterPath()
    path.addRoundedRect(body_rect, radius, radius)

    # Subtle top specular hairline for quiet layered craft
    painter.save()
    painter.setClipPath(path)
    painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
    painter.drawLine(
        int(body_rect.left() + radius * 0.5),
        int(body_rect.top() + 1),
        int(body_rect.right() - radius * 0.5),
        int(body_rect.top() + 1),
    )
    painter.restore()

    if active_border:
        bc = QColor(border_hex or ACCENT_PRIMARY)
        bc.setAlpha(210)
    elif border_hex:
        bc = QColor(border_hex)
        bc.setAlpha(115)
    else:
        bc = QColor(255, 255, 255, 32)
    painter.setPen(QPen(bc, 1.25))
    painter.drawPath(path)
    painter.restore()


paint_monolith_surface = paint_panel_surface
paint_obsidian_glass = paint_panel_surface


class StrokedTextLabel(QWidget):
    """Multi-line word-wrapped text label supporting smooth QPainterPath outline stroke and author badges."""

    def __init__(
        self,
        text: str = "",
        parent: Optional[QWidget] = None,
        font_size: int = 15,
        text_color: str = TEXT_PRIMARY,
        stroke_color: str = "#000000",
        stroke_size: int = 0,
        bold: bool = True,
    ) -> None:
        super().__init__(parent)
        self._entries: list[tuple[str, str]] = [("", text)] if text else []
        self._font_size = int(font_size)
        self._text_color = text_color
        self._stroke_color = stroke_color
        self._stroke_size = max(0, min(4, int(stroke_size)))
        self._bold = bool(bold)
        self._cached_height_w = -1
        self._cached_height = 24
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_copy_menu)
        self._recalc_height()

    def _make_font(self) -> QFont:
        f = QFont("Microsoft YaHei UI", 10)
        f.setFamilies(["Microsoft YaHei UI", "Noto Sans SC", "sans-serif"])
        f.setPixelSize(max(9, self._font_size))
        f.setBold(self._bold)
        return f

    def set_entries(self, entries: list[tuple[str, str]]) -> None:
        cleaned = [(str(a or ""), str(b or "")) for a, b in entries if str(b or "").strip()]
        if cleaned != self._entries:
            self._entries = cleaned
            self._cached_height_w = -1
            self._recalc_height()
            self.update()

    def set_text(self, text: str) -> None:
        self.set_entries([("", text)] if (text or "").strip() else [])

    def plain_text(self) -> str:
        return "\n".join(f"{a}: {b}" if a else b for a, b in self._entries)

    def set_style_options(
        self,
        font_size: int,
        text_color: str,
        stroke_color: str,
        stroke_size: int,
        bold: Optional[bool] = None,
    ) -> None:
        self._font_size = max(9, min(36, int(font_size)))
        self._text_color = text_color or TEXT_PRIMARY
        self._stroke_color = stroke_color or "#000000"
        self._stroke_size = max(0, min(4, int(stroke_size)))
        if bold is not None:
            self._bold = bool(bold)
        self._cached_height_w = -1
        self._recalc_height()
        self.update()

    def _layout_entries(self, width: int) -> tuple[list[tuple[QTextLayout, str, int]], int]:
        pad = max(2, self._stroke_size + 1)
        avail_w = max(48.0, float(width - pad * 2))
        font = self._make_font()
        fm = QFontMetricsF(font)
        line_gap = max(1.0, fm.leading())
        entry_gap = max(4.0, self._font_size * 0.32)

        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

        y = float(pad)
        layouts: list[tuple[QTextLayout, str, int]] = []
        for idx, (author, body) in enumerate(self._entries):
            if idx > 0:
                y += entry_gap
            prefix_sep = f"{author} · " if author else ""
            full_str = f"{prefix_sep}{body}" if prefix_sep else body
            tl = QTextLayout(full_str, font)
            tl.setTextOption(opt)
            tl.setCacheEnabled(True)
            tl.beginLayout()
            while True:
                ln = tl.createLine()
                if not ln.isValid():
                    break
                ln.setLineWidth(avail_w)
                ln.setPosition(QPointF(float(pad), y))
                y += ln.height() + line_gap
            tl.endLayout()
            layouts.append((tl, full_str, len(prefix_sep)))

        total_h = max(int(math.ceil(fm.height() + pad * 2)), int(math.ceil(y + pad)))
        return layouts, total_h

    def _recalc_height(self) -> None:
        w = self.width() if self.width() > 30 else 360
        if w == self._cached_height_w:
            return
        if not self._entries:
            pad = max(2, self._stroke_size + 1)
            fm = QFontMetricsF(self._make_font())
            h = max(18, int(math.ceil(fm.height() + pad * 2)))
        else:
            _, h = self._layout_entries(w)
        self._cached_height_w = w
        self._cached_height = h
        self.setFixedHeight(h)
        self.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._cached_height_w = -1
        self._recalc_height()

    def sizeHint(self) -> QSize:
        return QSize(max(120, self.width()), self._cached_height)

    def minimumSizeHint(self) -> QSize:
        return QSize(60, self._cached_height)

    def _show_copy_menu(self, pos) -> None:
        txt = self.plain_text().strip()
        if not txt:
            return
        menu = QMenu(self)
        act = menu.addAction("复制文本")
        act.triggered.connect(lambda: QApplication.clipboard().setText(txt))
        menu.exec(self.mapToGlobal(pos))

    def paintEvent(self, _event) -> None:
        if not self._entries:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        font = self._make_font()
        fm = QFontMetricsF(font)
        p.setFont(font)

        layouts, _ = self._layout_entries(self.width())
        fg_col = QColor(self._text_color)
        if not fg_col.isValid():
            fg_col = QColor(TEXT_PRIMARY)
        prefix_col = QColor(TEXT_SECONDARY)

        use_stroke = self._stroke_size > 0
        stroke_pen: Optional[QPen] = None
        if use_stroke:
            sc = QColor(self._stroke_color)
            if not sc.isValid():
                sc = QColor("#000000")
            stroke_pen = QPen(sc, float(self._stroke_size) * 2.0)
            stroke_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroke_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        for tl, full_str, prefix_len in layouts:
            for i in range(tl.lineCount()):
                ln = tl.lineAt(i)
                start = ln.textStart()
                length = ln.textLength()
                seg_text = full_str[start : start + length]
                if not seg_text:
                    continue
                pos = ln.position()
                bx = pos.x()
                by = pos.y() + ln.ascent()

                # Split line into muted author prefix + primary text if prefix overlaps this line
                p_end = max(0, min(length, prefix_len - start))
                segments: list[tuple[float, str, QColor]] = []
                if p_end > 0:
                    part_pre = seg_text[:p_end]
                    part_rest = seg_text[p_end:]
                    segments.append((bx, part_pre, prefix_col))
                    if part_rest:
                        bx2 = bx + fm.horizontalAdvance(part_pre)
                        segments.append((bx2, part_rest, fg_col))
                else:
                    segments.append((bx, seg_text, fg_col))

                if use_stroke and stroke_pen is not None:
                    for sx, stxt, _col in segments:
                        path = QPainterPath()
                        path.addText(QPointF(sx, by), font, stxt)
                        p.setPen(stroke_pen)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawPath(path)

                for sx, stxt, col in segments:
                    if use_stroke:
                        path = QPainterPath()
                        path.addText(QPointF(sx, by), font, stxt)
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QBrush(col))
                        p.drawPath(path)
                    else:
                        p.setPen(col)
                        p.drawText(QPointF(sx, by), stxt)


class HorizontalHairline(QFrame):
    """Crisp 1px structural divider seam."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); border: none;")


class StatusBeacon(QWidget):
    """Precision status LED indicator with subtle bezel."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = QColor(STATUS_WARN)
        self._pulse = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)

    def set_color(self, hex_color: str) -> None:
        self._color = QColor(hex_color)
        self._pulse = hex_color.upper() == STATUS_WARN.upper()
        self.update()

    def _tick(self) -> None:
        if self._pulse:
            self._phase = (self._phase + 0.22) % (math.pi * 2)
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = self.width() / 2.0, self.height() / 2.0

        p.setPen(QPen(QColor(255, 255, 255, 36), 1.0))
        p.setBrush(QColor("#141619"))
        p.drawEllipse(QRectF(cx - 5.2, cy - 5.2, 10.4, 10.4))

        col = QColor(self._color)
        if self._pulse:
            col.setAlpha(int(145 + 110 * (0.5 + 0.5 * math.sin(self._phase))))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QRectF(cx - 3.1, cy - 3.1, 6.2, 6.2))


class AudioWaveWidget(QWidget):
    """Compact emerald VU audio level meter."""

    BAR_COUNT = 20

    def __init__(self, parent: Optional[QWidget] = None, height: int = 20) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setMinimumWidth(110)
        self._target_level = 0.0
        self._smooth_level = 0.0
        self._bars = [0.08] * self.BAR_COUNT
        self._phase = 0.0
        self._active = True

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(45)

    def set_level(self, level: float) -> None:
        self._target_level = max(0.0, min(1.0, float(level)))

    def set_active(self, active: bool) -> None:
        self._active = active
        if not active:
            self._target_level = 0.0
        self.update()

    def _tick(self) -> None:
        self._phase += 0.28
        self._smooth_level = self._smooth_level * 0.62 + self._target_level * 0.38
        self._target_level *= 0.86

        for i in range(self.BAR_COUNT):
            wave = 0.5 + 0.5 * math.sin(self._phase + i * 0.6)
            if not self._active:
                target = 0.08
            elif self._smooth_level < 0.02:
                target = 0.09 + 0.05 * wave
            else:
                env = max(0.12, 1.0 - abs((i / (self.BAR_COUNT - 1)) - self._smooth_level) * 0.7)
                target = min(1.0, 0.14 + self._smooth_level * (0.55 + 0.45 * wave) * env)
            self._bars[i] = self._bars[i] * 0.5 + target * 0.5
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        if w <= 10 or h <= 4:
            return

        slot = w / self.BAR_COUNT
        bar_w = max(2.0, slot * 0.54)
        p.setPen(Qt.PenStyle.NoPen)

        for i, val in enumerate(self._bars):
            bh = max(3.0, val * (h - 4.0))
            bx = i * slot + (slot - bar_w) * 0.5
            by = (h - bh) * 0.5
            if not self._active:
                p.setBrush(QColor(107, 114, 128, 85))
            elif val >= 0.82 and i >= self.BAR_COUNT - 4:
                p.setBrush(QColor(STATUS_ERR))
            elif val >= 0.68 and i >= self.BAR_COUNT - 6:
                p.setBrush(QColor(STATUS_WARN))
            else:
                p.setBrush(QColor(STATUS_OK))
            p.drawRoundedRect(QRectF(bx, by, bar_w, bh), 1.0, 1.0)
