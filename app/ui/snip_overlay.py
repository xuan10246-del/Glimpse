"""Virtual-desktop SnipOverlay and WindowPickerOverlay styled in MONOLITH OPTICAL HUD."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Callable, Optional

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from ..process_audio import pick_process_at_point
from .theme import ACCENT_OCHRE, ACCENT_VERMILION, TEXT_IVORY


def _virtual_desktop_rect() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    vx = int(user32.GetSystemMetrics(76))
    vy = int(user32.GetSystemMetrics(77))
    vw = int(user32.GetSystemMetrics(78))
    vh = int(user32.GetSystemMetrics(79))
    if vw <= 0 or vh <= 0:
        return 0, 0, 1920, 1080
    return vx, vy, vw, vh


def _cursor_phys_pos() -> tuple[int, int]:
    pt = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


class SnipOverlay(QWidget):
    """Full virtual-desktop optical rangefinder region selector."""

    def __init__(self, on_selected: Callable[[tuple[int, int, int, int]], None]) -> None:
        super().__init__(None)
        self._on_selected = on_selected
        self._vx, self._vy, self._vw, self._vh = _virtual_desktop_rect()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.Crosshair)
        self.setGeometry(self._vx, self._vy, self._vw, self._vh)

        self._local_start: Optional[tuple[int, int]] = None
        self._local_cur: Optional[tuple[int, int]] = None
        self._phys_start: Optional[tuple[int, int]] = None

        self.show()
        self.activateWindow()
        self.raise_()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pt = event.position().toPoint()
            self._local_start = (pt.x(), pt.y())
            self._local_cur = (pt.x(), pt.y())
            self._phys_start = _cursor_phys_pos()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._local_start is not None:
            pt = event.position().toPoint()
            self._local_cur = (pt.x(), pt.y())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._phys_start is not None:
            x0, y0 = self._phys_start
            x1, y1 = _cursor_phys_pos()
            self.hide()
            QApplication.processEvents()
            self.close()
            left, top = min(x0, x1), min(y0, y1)
            width, height = abs(x1 - x0), abs(y1 - y0)
            if width >= 15 and height >= 15:
                self._on_selected((left, top, width, height))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(12, 12, 11, 108))

        banner_x = max(20, -self._vx + 560)
        banner = QRect(banner_x, 28, 460, 38)
        p.setBrush(QColor(18, 18, 17, 238))
        p.setPen(QPen(QColor(ACCENT_VERMILION), 1.2))
        p.drawRoundedRect(banner, 7, 7)
        p.setPen(QColor(TEXT_IVORY))
        p.setFont(QFont("Noto Sans SC", 10, QFont.Weight.Bold))
        p.drawText(banner, Qt.AlignmentFlag.AlignCenter, "拖拽框选要识别翻译的画面区域（右键或 Esc 取消）")

        if self._local_start and self._local_cur:
            x0, y0 = self._local_start
            x1, y1 = self._local_cur
            sel = QRect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            p.setBrush(QColor(255, 77, 46, 26))
            p.setPen(QPen(QColor(ACCENT_VERMILION), 1.8))
            p.drawRect(sel)

            # Corner Leica brackets
            arm = min(14, sel.width() // 3, sel.height() // 3)
            p.setPen(QPen(QColor(TEXT_IVORY), 2.5))
            l, t, r, b = sel.left(), sel.top(), sel.right(), sel.bottom()
            for cx, cy, dx, dy in (
                (l, t, arm, arm),
                (r, t, -arm, arm),
                (l, b, arm, -arm),
                (r, b, -arm, -arm),
            ):
                p.drawLine(QPoint(cx, cy), QPoint(cx + dx, cy))
                p.drawLine(QPoint(cx, cy), QPoint(cx, cy + dy))


class WindowPickerOverlay(QWidget):
    """Full virtual-desktop overlay allowing user to click any app window to isolate its audio."""

    def __init__(
        self,
        exclude_pids: set[int],
        on_picked: Callable[[Optional[dict]], None],
    ) -> None:
        super().__init__(None)
        self._exclude_pids = exclude_pids
        self._on_picked = on_picked
        self._vx, self._vy, self._vw, self._vh = _virtual_desktop_rect()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.Crosshair)
        self.setGeometry(self._vx, self._vy, self._vw, self._vh)

        self.show()
        self.activateWindow()
        self.raise_()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            self._on_picked(None)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
            self._on_picked(None)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            px, py = _cursor_phys_pos()
            self.hide()
            QApplication.processEvents()
            picked = pick_process_at_point(px, py, exclude_pids=self._exclude_pids)
            self.close()
            self._on_picked(picked)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(12, 12, 11, 88))
        banner_x = max(20, -self._vx + 520)
        banner = QRect(banner_x, 32, 520, 42)
        p.setBrush(QColor(18, 18, 17, 240))
        p.setPen(QPen(QColor(ACCENT_OCHRE), 1.4))
        p.drawRoundedRect(banner, 8, 8)
        p.setPen(QColor(TEXT_IVORY))
        p.setFont(QFont("Noto Sans SC", 10, QFont.Weight.Bold))
        p.drawText(
            banner,
            Qt.AlignmentFlag.AlignCenter,
            "直接点击需要单独隔离捕获音频的目标软件窗口（右键或 Esc 取消）",
        )
