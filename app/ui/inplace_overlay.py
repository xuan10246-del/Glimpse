"""Mode 2 In-Place Overlay Window: single-layer QRegion-masked, click-through, stroke & WDA safe."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QWidget

from ..win32_utils import set_exclude_from_capture, set_window_click_through
from .theme import STATUS_ERR, TEXT_PRIMARY


class InPlaceOverlayWindow(QWidget):
    """Overlays translated text blocks directly onto OCR coordinates (invisible to mss & mouse)."""

    def __init__(
        self,
        region_id: str,
        rect: list[int],
        style: str = "blend",
        exclude_capture: bool = True,
        opacity: float = 0.94,
    ) -> None:
        super().__init__(None)
        self.region_id = region_id
        self._style = style if style in ("blend", "strike", "glass") else "blend"
        self._exclude_capture = bool(exclude_capture)
        self._opacity = max(0.25, min(1.0, float(opacity)))
        self._show_author = True
        self._trans_font_color = TEXT_PRIMARY
        self._trans_stroke_color = "#000000"
        self._trans_stroke_size = 0
        self._items: list[dict[str, Any]] = []
        self._layout_blocks: list[tuple[QRectF, QFont, str, dict[str, Any]]] = []

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowOpacity(self._opacity)

        x, y, w, h = rect
        self.setGeometry(int(x), int(y), max(40, int(w)), max(30, int(h)))
        self.setMask(QRegion(0, 0, 1, 1))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_window_click_through(self, True)
        set_exclude_from_capture(self, self._exclude_capture)
        self._recompute_layout_and_mask()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_layout_and_mask()

    def set_exclude_from_capture(self, enabled: bool) -> None:
        self._exclude_capture = bool(enabled)
        if self.isVisible():
            set_exclude_from_capture(self, self._exclude_capture)

    def apply_appearance(self, style: dict[str, Any]) -> None:
        self._opacity = max(0.25, min(1.0, float(style.get("opacity", self._opacity))))
        self._trans_font_color = str(style.get("trans_font_color", TEXT_PRIMARY))
        self._trans_stroke_color = str(style.get("trans_stroke_color", "#000000"))
        self._trans_stroke_size = max(0, min(4, int(style.get("trans_stroke_size", 0))))
        self.setWindowOpacity(self._opacity)
        self.update()

    def set_overlay_style(self, style: str, show_author: Optional[bool] = None) -> None:
        if style in ("blend", "strike", "glass"):
            self._style = style
        if show_author is not None:
            self._show_author = bool(show_author)
        self._recompute_layout_and_mask()
        self.update()

    def sync_region_rect(self, interior_rect: list[int]) -> None:
        x, y, w, h = interior_rect
        self.setGeometry(int(x), int(y), max(40, int(w)), max(30, int(h)))

    def set_blocks(self, items: list[dict[str, Any]], show_author: bool = True) -> None:
        self._items = [it for it in items if (it.get("zh") or "").strip()]
        self._show_author = bool(show_author)
        self._recompute_layout_and_mask()
        self.update()

    def clear_for_motion(self) -> None:
        """Immediately erase in-place overlays on fast scrolling/scene transition to stop ghosting."""
        if self._items:
            self._items = []
            self._layout_blocks = []
            self.setMask(QRegion(0, 0, 1, 1))
            self.update()

    def _fit_font_and_rect(
        self, box_rect: QRectF, text: str, max_w: float, max_h: float
    ) -> tuple[QFont, QRectF]:
        font = QFont("Microsoft YaHei UI", 13, QFont.Weight.Bold)
        rect = QRectF(box_rect)
        pad_x = 6.0
        pad_y = 3.0

        for sz in (14, 13, 12, 11, 10, 9):
            font.setPointSize(sz)
            fm = QFontMetrics(font)
            avail_w = max(48.0, rect.width() - pad_x * 2)
            br = fm.boundingRect(
                0,
                0,
                int(avail_w),
                int(max_h * 0.9),
                Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            needed_h = br.height() + pad_y * 2
            if needed_h <= rect.height() + 2.0 or sz == 9:
                final_h = max(rect.height(), min(max_h - rect.top(), needed_h + 2.0))
                final_w = max(rect.width(), min(max_w - rect.left(), br.width() + pad_x * 2 + 4.0))
                rect.setHeight(final_h)
                rect.setWidth(final_w)
                return font, rect
        return font, rect

    def _recompute_layout_and_mask(self) -> None:
        self._layout_blocks = []
        if not self._items:
            self.setMask(QRegion(0, 0, 1, 1))
            return

        w = float(self.width())
        h = float(self.height())
        combined_region = QRegion()

        for idx, it in enumerate(self._items):
            zh = (it.get("zh") or "").strip()
            if not zh:
                continue
            author = (it.get("author") or "").strip()
            display_txt = f"{author}: {zh}" if (self._show_author and author) else zh

            bbox = it.get("bbox") or [0, 0, 0, 0]
            cw = float(it.get("crop_w") or 0)
            ch = float(it.get("crop_h") or 0)

            if cw > 10 and ch > 10 and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                sx = w / cw
                sy = h / ch
                x0 = max(2.0, bbox[0] * sx - 3.0)
                y0 = max(2.0, bbox[1] * sy - 2.0)
                bw = min(w - x0 - 2.0, max(60.0, (bbox[2] - bbox[0]) * sx + 8.0))
                bh = min(h - y0 - 2.0, max(20.0, (bbox[3] - bbox[1]) * sy + 6.0))
                raw_rect = QRectF(x0, y0, bw, bh)
            else:
                line_h = min(36.0, max(22.0, (h - 8.0) / max(1, len(self._items))))
                raw_rect = QRectF(6.0, 4.0 + idx * (line_h + 4.0), max(80.0, w - 12.0), line_h)

            font, draw_rect = self._fit_font_and_rect(raw_rect, display_txt, w - 2.0, h - 2.0)
            self._layout_blocks.append((draw_rect, font, display_txt, it))

            pill_path = QPainterPath()
            pill_path.addRoundedRect(draw_rect.adjusted(-1, -1, 1, 1), 5.0, 5.0)
            combined_region = combined_region.united(QRegion(pill_path.toFillPolygon().toPolygon()))

        if combined_region.isEmpty():
            self.setMask(QRegion(QRect(0, 0, 1, 1)))
        else:
            self.setMask(combined_region)

    def paintEvent(self, _event) -> None:
        if not self._layout_blocks:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        for draw_rect, font, display_txt, it in self._layout_blocks:
            fill_bounds = draw_rect.adjusted(-2.0, -2.0, 2.0, 2.0)
            if self._style == "blend":
                rgb = it.get("bg_rgb") or [32, 34, 38]
                fg = it.get("fg_rgb") or [241, 243, 245]
                bg_col = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
                fg_col = QColor(int(fg[0]), int(fg[1]), int(fg[2]))
                p.fillRect(fill_bounds, bg_col)
            elif self._style == "strike":
                p.fillRect(fill_bounds, QColor(28, 30, 35))
                fg_col = QColor(self._trans_font_color)
                p.setPen(QPen(QColor(224, 90, 90, 195), 1.1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(draw_rect, 4.0, 4.0)
                strike_y = draw_rect.top() + min(3.5, draw_rect.height() * 0.14)
                p.setPen(QPen(QColor(STATUS_ERR), 2.0))
                p.drawLine(
                    int(draw_rect.left() + 4),
                    int(strike_y),
                    int(draw_rect.right() - 4),
                    int(strike_y),
                )
            else:
                p.fillRect(fill_bounds, QColor(32, 34, 38))
                fg_col = QColor(self._trans_font_color)
                p.setPen(QPen(QColor(255, 255, 255, 55), 1.1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(draw_rect, 5.0, 5.0)

            p.setFont(font)
            text_box = draw_rect.adjusted(5.0, 3.0 if self._style == "strike" else 2.0, -5.0, -2.0)
            if self._trans_stroke_size > 0 and self._style != "blend":
                tl = QTextLayout(display_txt, font)
                opt = QTextOption()
                opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
                tl.setTextOption(opt)
                tl.beginLayout()
                y = text_box.top()
                lines_to_draw: list[tuple[float, float, str]] = []
                while True:
                    ln = tl.createLine()
                    if not ln.isValid():
                        break
                    ln.setLineWidth(max(30.0, text_box.width()))
                    sub = display_txt[ln.textStart() : ln.textStart() + ln.textLength()]
                    lines_to_draw.append((text_box.left(), y + ln.ascent(), sub))
                    y += ln.height()
                tl.endLayout()
                pen = QPen(QColor(self._trans_stroke_color), float(self._trans_stroke_size) * 2.0)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                for lx, ly, sub in lines_to_draw:
                    if not sub:
                        continue
                    path = QPainterPath()
                    path.addText(QPointF(lx, ly), font, sub)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(fg_col))
                    p.drawPath(path)
            else:
                p.setPen(fg_col)
                p.drawText(
                    text_box,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                    display_txt,
                )
