"""Clean single-layer floating translation dock hosting mode switcher and channel stack."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import ICON_PNG_PATH
from ..win32_utils import set_exclude_from_capture
from .channel_card import ChannelCardWidget
from .icons import make_icon
from .theme import (
    ACCENT_PRIMARY,
    BG_CANVAS,
    STATUS_OK,
    STATUS_WARN,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    HorizontalHairline,
    StatusBeacon,
    apply_rounded_mask,
    paint_panel_surface,
    start_native_window_drag,
)


class DockWindow(QWidget):
    """Floating translation dock window (closing hides translation UI without quitting app)."""

    add_region_requested = Signal()
    snip_requested = Signal()
    quick_capsule_requested = Signal()
    control_panel_requested = Signal()
    merge_all_requested = Signal()
    display_mode_changed = Signal(str)  # floating | stealth | inplace
    exclude_capture_toggled = Signal(bool)
    unlock_clickthrough_requested = Signal()
    minimize_requested = Signal()
    close_translation_requested = Signal()
    geometry_changed = Signal(list)  # [x, y, w, h]

    def __init__(
        self,
        rect: list[int],
        opacity: float = 0.94,
        bg_color: str = BG_CANVAS,
        exclude_capture: bool = True,
        display_mode: str = "floating",
        stealth_hotkey: str = "Alt+Q",
    ) -> None:
        super().__init__(None)
        self._opacity = max(0.25, min(1.0, float(opacity)))
        self._bg_color = bg_color or BG_CANVAS
        self._exclude_capture = bool(exclude_capture)
        self._display_mode = display_mode
        self._stealth_highlight = False
        self._suppress_geom_emit = False
        self._expanded_height = max(250, int(rect[3]))

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowOpacity(self._opacity)
        self.setWindowTitle("Glimpse 瞬译 · 翻译浮窗")

        x, y, w, h = rect
        self.setGeometry(int(x), int(y), max(580, int(w)), max(170, int(h)))

        self._drag_origin: Optional[tuple[QPoint, QPoint]] = None
        self._resize_origin: Optional[tuple[QPoint, int, int]] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # Row 1: App icon + Title + Engine badge + Status + Quick Actions
        header = QHBoxLayout()
        header.setSpacing(6)
        root.addLayout(header)

        self._beacon = StatusBeacon(self)
        header.addWidget(self._beacon)

        if ICON_PNG_PATH.exists():
            ico_lbl = QLabel(self)
            pm = QPixmap(str(ICON_PNG_PATH)).scaled(
                18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            ico_lbl.setPixmap(pm)
            ico_lbl.setStyleSheet("background: transparent;")
            header.addWidget(ico_lbl)

        self._brand_lbl = QLabel("Glimpse 瞬译", self)
        self._brand_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: bold; background: transparent;"
        )
        header.addWidget(self._brand_lbl)

        self._engine_pill = QLabel("LOCAL", self)
        self._engine_pill.setStyleSheet(
            "background: rgba(59, 130, 246, 0.14); color: #60A5FA; "
            "border: 1px solid rgba(59, 130, 246, 0.35); border-radius: 4px; "
            "padding: 1px 6px; font-size: 10px; font-weight: bold;"
        )
        header.addWidget(self._engine_pill)

        self._status_lbl = QLabel("引擎待命", self)
        self._status_lbl.setStyleSheet(
            f"color: {STATUS_WARN}; font-size: 11px; background: transparent;"
        )
        header.addWidget(self._status_lbl)

        header.addStretch(1)

        self._btn_add = QPushButton(make_icon("plus", "#FFFFFF", 13), "画面", self)
        self._btn_add.setProperty("accent", True)
        self._btn_add.setProperty("compact", True)
        self._btn_add.clicked.connect(self.add_region_requested.emit)
        header.addWidget(self._btn_add)

        self._btn_snip = QPushButton(make_icon("crop", TEXT_PRIMARY, 13), "框选", self)
        self._btn_snip.setProperty("compact", True)
        self._btn_snip.clicked.connect(self.snip_requested.emit)
        header.addWidget(self._btn_snip)

        self._btn_quick = QPushButton(make_icon("bolt", STATUS_OK, 13), "快翻", self)
        self._btn_quick.setProperty("compact", True)
        self._btn_quick.setProperty("lime", True)
        self._btn_quick.setToolTip("打开双向快翻输出胶囊")
        self._btn_quick.clicked.connect(self.quick_capsule_requested.emit)
        header.addWidget(self._btn_quick)

        self._btn_panel = QPushButton(make_icon("home", TEXT_PRIMARY, 13), "控制中心", self)
        self._btn_panel.setProperty("compact", True)
        self._btn_panel.clicked.connect(self.control_panel_requested.emit)
        header.addWidget(self._btn_panel)

        self._btn_merge_all = QPushButton(make_icon("merge", TEXT_SECONDARY, 13), "归位", self)
        self._btn_merge_all.setProperty("compact", True)
        self._btn_merge_all.setProperty("ghost", True)
        self._btn_merge_all.setToolTip("将全部独立浮窗合并回底座")
        self._btn_merge_all.clicked.connect(self.merge_all_requested.emit)
        header.addWidget(self._btn_merge_all)

        self._btn_min = QPushButton(make_icon("minimize", TEXT_SECONDARY, 13), "", self)
        self._btn_min.setFixedSize(26, 24)
        self._btn_min.setProperty("ghost", True)
        self._btn_min.setToolTip("最小化底座（不影响独立浮窗或控制中心）")
        self._btn_min.clicked.connect(self.minimize_requested.emit)
        header.addWidget(self._btn_min)

        self._btn_close = QPushButton(make_icon("close", TEXT_SECONDARY, 13), "", self)
        self._btn_close.setFixedSize(26, 24)
        self._btn_close.setProperty("ghost", True)
        self._btn_close.setProperty("danger", True)
        self._btn_close.setToolTip("关闭翻译页面（软件保持运行，可在控制中心重新「开始翻译」）")
        self._btn_close.clicked.connect(self.close_translation_requested.emit)
        header.addWidget(self._btn_close)

        root.addWidget(HorizontalHairline(self))

        # Row 2: 3-Mode Switcher + Click-through Unlock + Anti-Self-Capture
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        root.addLayout(mode_row)

        mode_cap = QLabel("模式", self)
        mode_cap.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        mode_row.addWidget(mode_cap)

        self._mode_btns: dict[str, QPushButton] = {}
        for m_key, m_label in (
            ("floating", "常规浮窗"),
            ("stealth", f"隐藏当前窗口 ({stealth_hotkey})"),
            ("inplace", "原位覆盖"),
        ):
            mb = QPushButton(m_label, self)
            mb.clicked.connect(lambda _c, mk=m_key: self.display_mode_changed.emit(mk))
            self._mode_btns[m_key] = mb
            mode_row.addWidget(mb)

        mode_row.addStretch(1)

        self._btn_unlock = QPushButton(make_icon("unlock", TEXT_SECONDARY, 12), "解除穿透", self)
        self._btn_unlock.setProperty("compact", True)
        self._btn_unlock.setProperty("ghost", True)
        self._btn_unlock.setToolTip("一键解除全部浮窗与胶囊的鼠标穿透锁定")
        self._btn_unlock.clicked.connect(self.unlock_clickthrough_requested.emit)
        mode_row.addWidget(self._btn_unlock)

        self._btn_shield = QPushButton(make_icon("shield", STATUS_OK, 12), "防自捕 ON", self)
        self._btn_shield.setProperty("compact", True)
        self._btn_shield.setToolTip("点击切换防自捕（译文直接叠放原文上且取词框自动无视）")
        self._btn_shield.clicked.connect(lambda: self.exclude_capture_toggled.emit(not self._exclude_capture))
        mode_row.addWidget(self._btn_shield)

        self._game_pill = QLabel("游戏低占", self)
        self._game_pill.setStyleSheet(
            "color: #E5A93C; background: rgba(229, 169, 60, 0.12); "
            "border: 1px solid rgba(229, 169, 60, 0.35); border-radius: 4px; "
            "padding: 1px 6px; font-size: 10px; font-weight: bold;"
        )
        self._game_pill.setVisible(False)
        mode_row.addWidget(self._game_pill)

        root.addWidget(HorizontalHairline(self))

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.viewport().setStyleSheet("#qt_scrollarea_viewport { background: transparent; }")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)

        self._cards_host = QWidget()
        self._cards_host.setObjectName("CardsHost")
        self._cards_host.setAutoFillBackground(False)
        self._cards_host.setStyleSheet("#CardsHost { background: transparent; }")
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 2, 0)
        self._cards_layout.setSpacing(2)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._cards_host)

        self._empty_hint = QLabel("全部通道已拆分为独立浮窗 · 点击上方「归位」可重新合并", self)
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 6px;")
        self._empty_hint.setVisible(False)
        root.addWidget(self._empty_hint)

        self._refresh_mode_buttons()
        self._refresh_shield_pill()

    def _refresh_mode_buttons(self) -> None:
        for mk, btn in self._mode_btns.items():
            active = mk == self._display_mode
            btn.setProperty("seg", not active)
            btn.setProperty("seg_active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _refresh_shield_pill(self) -> None:
        if self._exclude_capture:
            self._btn_shield.setIcon(make_icon("shield", STATUS_OK, 12))
            self._btn_shield.setText("防自捕 ON")
            self._btn_shield.setProperty("lime", True)
            self._btn_shield.setProperty("ghost", False)
        else:
            self._btn_shield.setIcon(make_icon("shield", TEXT_SECONDARY, 12))
            self._btn_shield.setText("防自捕 OFF")
            self._btn_shield.setProperty("lime", False)
            self._btn_shield.setProperty("ghost", True)
        self._btn_shield.style().unpolish(self._btn_shield)
        self._btn_shield.style().polish(self._btn_shield)

    def set_clickthrough_active(self, any_locked: bool, unlock_hotkey: str = "Ctrl+Alt+U") -> None:
        self._btn_unlock.setToolTip(f"一键解除全部穿透锁定（快捷键 {unlock_hotkey}）")
        if any_locked:
            self._btn_unlock.setIcon(make_icon("lock", STATUS_WARN, 12))
            self._btn_unlock.setText(f"解除穿透 ({unlock_hotkey})")
            self._btn_unlock.setProperty("amber", True)
            self._btn_unlock.setProperty("ghost", False)
        else:
            self._btn_unlock.setIcon(make_icon("unlock", TEXT_SECONDARY, 12))
            self._btn_unlock.setText("解除穿透")
            self._btn_unlock.setProperty("amber", False)
            self._btn_unlock.setProperty("ghost", True)
        self._btn_unlock.style().unpolish(self._btn_unlock)
        self._btn_unlock.style().polish(self._btn_unlock)

    def set_exclude_from_capture(self, enabled: bool) -> None:
        self._exclude_capture = bool(enabled)
        self._refresh_shield_pill()
        if self.isVisible():
            set_exclude_from_capture(self, self._exclude_capture)

    def set_mode_state(self, display_mode: str, stealth_hotkey: str) -> None:
        self._display_mode = display_mode
        if "stealth" in self._mode_btns:
            self._mode_btns["stealth"].setText(f"隐藏当前窗口 ({stealth_hotkey})")
        self._refresh_mode_buttons()

    def set_engine_badge(self, summary: str, game_mode: bool = False) -> None:
        self._engine_pill.setText(summary)
        self._game_pill.setVisible(bool(game_mode))

    def set_suppress_geometry_emit(self, suppress: bool) -> None:
        self._suppress_geom_emit = bool(suppress)

    def set_stealth_peek_anchor(
        self, visible: bool, anchor_rect: Optional[list[int]] = None, position: str = "above_region"
    ) -> None:
        """Show/hide dock cleanly when triggered in Stealth mode (anchored above region if set)."""
        self._stealth_highlight = bool(visible) and position != "dock"
        if not visible:
            self.hide()
            return
        if position == "above_region" and anchor_rect and len(anchor_rect) == 4:
            rx, ry, rw, rh = [int(v) for v in anchor_rect]
            dw, dh = self.width(), self.height()
            scr = QApplication.screenAt(QPoint(rx + rw // 2, ry)) or QApplication.primaryScreen()
            avail: QRect = scr.availableGeometry() if scr else QRect(0, 0, 1920, 1080)
            target_x = max(avail.left() + 8, min(avail.right() - dw - 8, rx + (rw - dw) // 2))
            if ry - avail.top() >= max(90, int(dh * 0.55)):
                target_y = max(avail.top() + 6, ry - dh - 10)
            else:
                target_y = min(avail.bottom() - dh - 8, ry + rh + 10)
            self._suppress_geom_emit = True
            self.move(target_x, target_y)
            QTimer.singleShot(120, lambda: self.set_suppress_geometry_emit(False))
        self.show()
        self.raise_()
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_rounded_mask(self, 10.0)
        set_exclude_from_capture(self, self._exclude_capture)

    def set_status(self, text: str, color_hex: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color: {color_hex}; font-size: 11px; background: transparent;"
        )
        self._beacon.set_color(color_hex)

    def apply_appearance(self, style: dict[str, Any]) -> None:
        self._opacity = max(0.25, min(1.0, float(style.get("opacity", self._opacity))))
        self._bg_color = str(style.get("bg_color", self._bg_color))
        self.setWindowOpacity(self._opacity)
        self.update()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.25, min(1.0, float(opacity)))
        self.setWindowOpacity(self._opacity)
        self.update()

    def set_merged_cards(self, cards: list[ChannelCardWidget]) -> None:
        """Attach ChannelCardWidgets into vertical stack separated by clean 1px hairlines."""
        while self._cards_layout.count() > 0:
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                if not isinstance(w, ChannelCardWidget):
                    w.deleteLater()

        for idx, card in enumerate(cards):
            if idx > 0:
                self._cards_layout.addWidget(HorizontalHairline(self._cards_host))
            card.setParent(self._cards_host)
            card.show()
            self._cards_layout.addWidget(card)
        self._cards_layout.addStretch(1)

        has_cards = len(cards) > 0
        self._scroll.setVisible(has_cards)
        self._empty_hint.setVisible(not has_cards)
        if not has_cards and self.height() > 140:
            self._expanded_height = self.height()
            self.resize(self.width(), 122)
        elif has_cards and self.height() < 170:
            self.resize(self.width(), max(250, self._expanded_height))

    def get_logical_rect(self) -> list[int]:
        g = self.geometry()
        h = self._expanded_height if not self._scroll.isVisible() else g.height()
        return [g.x(), g.y(), g.width(), h]

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not self._suppress_geom_emit:
            self.geometry_changed.emit(self.get_logical_rect())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        apply_rounded_mask(self, 10.0)
        if self._scroll.isVisible() and self.height() >= 170:
            self._expanded_height = self.height()
        if not self._suppress_geom_emit:
            self.geometry_changed.emit(self.get_logical_rect())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if pos.x() >= self.width() - 22 and pos.y() >= self.height() - 22:
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
            self.resize(max(580, w0 + delta.x()), max(122, h0 + delta.y()))
            event.accept()
        elif self._drag_origin is not None:
            g0, p0 = self._drag_origin
            delta = event.globalPosition().toPoint() - g0
            self.move(p0 + delta)
            event.accept()
        else:
            if pos.x() >= self.width() - 22 and pos.y() >= self.height() - 22:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._resize_origin = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        event.accept()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint_panel_surface(
            p,
            QRectF(self.rect()),
            radius=10.0,
            active_border=self._stealth_highlight,
            border_hex=ACCENT_PRIMARY if self._stealth_highlight else "",
            bg_hex=self._bg_color,
        )
        p.setPen(QPen(QColor(TEXT_MUTED), 1.4))
        w, h = self.width(), self.height()
        p.drawLine(w - 13, h - 5, w - 5, h - 13)
        p.drawLine(w - 8, h - 5, w - 5, h - 8)
