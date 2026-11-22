"""Optical Rangefinder capture frame with Leica corner reticles & WDA_EXCLUDEFROMCAPTURE."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from ..win32_utils import set_exclude_from_capture
from .theme import start_native_window_drag


class CaptureRegionWindow(QWidget):
    """Frameless hollow Optical Rangefinder OCR capture frame."""

    geometry_changed = Signal(str, list)  # region_id, [x, y, w, h]

    TAB_H = 24
    BORDER_W = 2
    RETICLE_ARM = 14
    GRIP_SZ = 18

    def __init__(
        self,
        region_id: str,
        name: str,
        color_hex: str,
        rect: list[int],
        index_num: int = 1,
        exclude_capture: bool = True,
    ) -> None:
        super().__init__(None)
        self.region_id = region_id
        self._name = name
        self._index_num = index_num
        self._color = QColor(color_hex)
        self._enabled = True
        self._exclude_capture = bool(exclude_capture)
        self._pulse_alpha = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowOpacity(0.95)
        self.setWindowTitle(f"取景框 - {name}")

        self._rect_lock = threading.Lock()
        self._phys_capture_rect: Optional[tuple[int, int, int, int]] = None

        self._drag_origin: Optional[tuple[QPoint, QPoint]] = None
        self._resize_origin: Optional[tuple[QPoint, int, int]] = None

        x, y, w, h = rect
        self.setGeometry(int(x), int(y), max(180, int(w)), max(80, int(h)))
        self._update_mask_and_phys_rect()

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

    def set_exclude_from_capture(self, enabled: bool) -> None:
        self._exclude_capture = bool(enabled)
        if self.isVisible():
            set_exclude_from_capture(self, self._exclude_capture)

    def update_meta(
        self, name: str, color_hex: str, enabled: bool, index_num: Optional[int] = None
    ) -> None:
        self._name = name
        self._color = QColor(color_hex)
        self._enabled = enabled
        if index_num is not None:
            self._index_num = index_num
        self.update()

    def pulse_highlight(self) -> None:
        self._pulse_alpha = 255
        if not self._pulse_timer.isActive():
            self._pulse_timer.start(40)
        self.raise_()
        self.update()

    def _on_pulse_tick(self) -> None:
        self._pulse_alpha = max(0, self._pulse_alpha - 24)
        if self._pulse_alpha <= 0:
            self._pulse_timer.stop()
        self.update()

    def _tab_width(self) -> int:
        return min(self.width(), 196)

    def _update_mask_and_phys_rect(self) -> None:
        w = self.width()
        h = self.height()
        if w < 60 or h < 40:
            return

        tab_w = self._tab_width()
        tab_rect = QRect(0, 0, tab_w, self.TAB_H)

        top_y = self.TAB_H - self.BORDER_W
        outer = QRegion(QRect(0, top_y, w, h - top_y))
        inner = QRegion(
            QRect(
                self.BORDER_W + 2,
                self.TAB_H + 2,
                max(1, w - (self.BORDER_W + 2) * 2),
                max(1, h - self.TAB_H - self.BORDER_W - 4),
            )
        )
        frame = outer.subtracted(inner)
        grip = QRegion(QRect(w - self.GRIP_SZ, h - self.GRIP_SZ, self.GRIP_SZ, self.GRIP_SZ))
        self.setMask(QRegion(tab_rect).united(frame).united(grip))

        # Compute exact Win32 physical pixels even when window is hidden (Stealth / In-Place modes)
        try:
            hwnd = int(self.winId())
            wr = wt.RECT()
            if hwnd and ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(wr)):
                dpr = max(1.0, float(self.devicePixelRatioF()))
                pad_x = int(round((self.BORDER_W + 2) * dpr))
                pad_top = int(round((self.TAB_H + 2) * dpr))
                pad_bot = int(round((self.BORDER_W + 2) * dpr))
                cw = (wr.right - wr.left) - pad_x * 2
                ch = (wr.bottom - wr.top) - pad_top - pad_bot
                if cw >= 20 and ch >= 20:
                    with self._rect_lock:
                        self._phys_capture_rect = (wr.left + pad_x, wr.top + pad_top, cw, ch)
                    return
        except Exception:
            pass

        # Fallback using Qt screen DPR
        g = self.geometry()
        scr = QApplication.screenAt(g.center()) or QApplication.primaryScreen()
        dpr = max(1.0, float(scr.devicePixelRatio())) if scr else 1.0
        pad_x = int(round((self.BORDER_W + 2) * dpr))
        pad_top = int(round((self.TAB_H + 2) * dpr))
        pad_bot = int(round((self.BORDER_W + 2) * dpr))
        cw = int(round(g.width() * dpr)) - pad_x * 2
        ch = int(round(g.height() * dpr)) - pad_top - pad_bot
        if cw >= 20 and ch >= 20:
            with self._rect_lock:
                self._phys_capture_rect = (
                    int(round(g.x() * dpr)) + pad_x,
                    int(round(g.y() * dpr)) + pad_top,
                    cw,
                    ch,
                )

    def get_capture_rect(self) -> Optional[tuple[int, int, int, int]]:
        """Physical Win32 rect for mss.grab (always valid even if box is hidden)."""
        with self._rect_lock:
            return self._phys_capture_rect

    def get_logical_interior_rect(self) -> list[int]:
        """Logical Qt rect of the capture interior (used by InPlaceOverlayWindow)."""
        g = self.geometry()
        pad_x = self.BORDER_W + 2
        pad_top = self.TAB_H + 2
        pad_bot = self.BORDER_W + 2
        return [
            g.x() + pad_x,
            g.y() + pad_top,
            max(40, g.width() - pad_x * 2),
            max(30, g.height() - pad_top - pad_bot),
        ]

    def get_logical_rect(self) -> list[int]:
        g = self.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_exclude_from_capture(self, self._exclude_capture)
        self._update_mask_and_phys_rect()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._update_mask_and_phys_rect()
        self.geometry_changed.emit(self.region_id, self.get_logical_rect())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_mask_and_phys_rect()
        self.geometry_changed.emit(self.region_id, self.get_logical_rect())

    def _tab_rect(self) -> QRect:
        return QRect(0, 0, self._tab_width(), self.TAB_H)

    def _grip_rect(self) -> QRect:
        return QRect(
            self.width() - self.GRIP_SZ,
            self.height() - self.GRIP_SZ,
            self.GRIP_SZ,
            self.GRIP_SZ,
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self._grip_rect().contains(pos):
                self._resize_origin = (event.globalPosition().toPoint(), self.width(), self.height())
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif not start_native_window_drag(self):
                self._drag_origin = (event.globalPosition().toPoint(), self.pos())
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._resize_origin is not None:
            g0, w0, h0 = self._resize_origin
            delta = event.globalPosition().toPoint() - g0
            self.resize(max(180, w0 + delta.x()), max(80, h0 + delta.y()))
            event.accept()
        elif self._drag_origin is not None:
            g0, p0 = self._drag_origin
            delta = event.globalPosition().toPoint() - g0
            self.move(p0 + delta)
            event.accept()
        else:
            if self._grip_rect().contains(pos):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif self._tab_rect().contains(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._resize_origin = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        event.accept()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        base_col = QColor(self._color if self._enabled else "#6D6860")

        # Rangefinder top tab in Slate Dark
        tab_w = float(self._tab_width())
        tab_rect = QRectF(0.5, 0.5, tab_w - 1.0, self.TAB_H - 1.5)
        tab_bg = QColor("#1E2025")
        tab_bg.setAlpha(242)
        p.setBrush(QBrush(tab_bg))
        border_col = QColor(base_col)
        border_col.setAlpha(185)
        p.setPen(QPen(border_col, 1.1))
        p.drawRoundedRect(tab_rect, 4.0, 4.0)

        # Circular indicator dot inside tab
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(base_col))
        p.drawEllipse(QRectF(8.0, 8.5, 7.0, 7.0))

        # Index number + name + tabular size telemetry
        p.setPen(QColor("#F1F3F5"))
        p.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold))
        idx_str = f"{self._index_num:02d}"
        p.drawText(
            QRectF(20.0, 0.0, 22.0, self.TAB_H),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            idx_str,
        )

        p.setPen(QColor("#9CA3AF"))
        p.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.Normal))
        suffix = "" if self._enabled else " 暂停"
        p.drawText(
            QRectF(39.0, 0.0, 78.0, self.TAB_H),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"{self._name}{suffix}",
        )

        p.setPen(QColor("#6D6860"))
        p.setFont(QFont("Consolas", 8))
        p.drawText(
            QRectF(114.0, 0.0, tab_w - 120.0, self.TAB_H),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"{int(w)}x{int(h)}",
        )

        # Thin hairline perimeter box
        top_y = float(self.TAB_H - 1)
        hair_col = QColor(base_col)
        hair_col.setAlpha(140)
        p.setPen(QPen(hair_col, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(1.0, top_y, w - 2.0, h - top_y - 1.0))

        # Leica L-shaped corner rangefinder brackets
        arm = min(float(self.RETICLE_ARM), w * 0.25, (h - top_y) * 0.25)
        p.setPen(QPen(base_col, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        # Top-left
        p.drawLine(QPoint(1, int(top_y)), QPoint(int(arm), int(top_y)))
        p.drawLine(QPoint(1, int(top_y)), QPoint(1, int(top_y + arm)))
        # Top-right
        p.drawLine(QPoint(int(w - 1), int(top_y)), QPoint(int(w - arm), int(top_y)))
        p.drawLine(QPoint(int(w - 1), int(top_y)), QPoint(int(w - 1), int(top_y + arm)))
        # Bottom-left
        p.drawLine(QPoint(1, int(h - 1)), QPoint(int(arm), int(h - 1)))
        p.drawLine(QPoint(1, int(h - 1)), QPoint(1, int(h - arm)))
        # Bottom-right
        p.drawLine(QPoint(int(w - 1), int(h - 1)), QPoint(int(w - arm), int(h - 1)))
        p.drawLine(QPoint(int(w - 1), int(h - 1)), QPoint(int(w - 1), int(h - arm)))

        # Locate flash highlight
        if self._pulse_alpha > 0:
            pc = QColor("#F4F0E8")
            pc.setAlpha(self._pulse_alpha)
            p.setPen(QPen(pc, 2.5))
            p.drawRect(QRectF(1.5, top_y, w - 3.0, h - top_y - 1.5))

        # Compact bottom-right mechanical resize corner
        gx, gy = w - self.GRIP_SZ, h - self.GRIP_SZ
        p.setPen(QPen(base_col, 1.4))
        p.drawLine(int(gx + 6), int(h - 4), int(w - 4), int(gy + 6))
        p.drawLine(int(gx + 11), int(h - 4), int(w - 4), int(gy + 11))
