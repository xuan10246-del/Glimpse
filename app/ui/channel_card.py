"""Architectural channel cards (merged stack in Dock or detached floating HUD with stroke/color support)."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..win32_utils import set_exclude_from_capture, set_window_click_through
from .icons import make_icon
from .theme import (
    ACCENT_PRIMARY,
    BG_CANVAS,
    STATUS_ERR,
    STATUS_OK,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    AudioWaveWidget,
    StrokedTextLabel,
    apply_rounded_mask,
    paint_panel_surface,
    start_native_window_drag,
)


class _ColorDot(QWidget):
    """Crisp 6px circular channel indicator dot."""

    def __init__(self, color_hex: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 14)
        self._color = QColor(color_hex)

    def set_color(self, color_hex: str) -> None:
        self._color = QColor(color_hex)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(2.0, 4.0, 6.0, 6.0))


class _GripHandleBar(QWidget):
    """Clean top drag handle bar for DetachedCardWindow using vector grip dots."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)

        icon_lbl = QLabel(self)
        icon_lbl.setPixmap(make_icon("grip", TEXT_MUTED, 12).pixmap(12, 12))
        icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(icon_lbl)

        self._title_lbl = QLabel(title, self)
        self._title_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: bold; background: transparent;"
        )
        lay.addWidget(self._title_lbl)
        lay.addStretch(1)

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class ChannelCardWidget(QFrame):
    """Channel unit displaying OCR / ASR output with full font/stroke customization."""

    detach_requested = Signal(str)
    merge_requested = Signal(str)
    locate_requested = Signal(str)
    toggle_enabled_requested = Signal(str)
    toggle_clickthrough_requested = Signal(str)

    def __init__(
        self,
        channel_id: str,
        title: str,
        color_hex: str,
        index_tag: str = "01",
        is_asr: bool = False,
        is_detached: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.channel_id = channel_id
        self._title = title
        self._color = color_hex
        self._index_tag = index_tag
        self._is_asr = is_asr
        self._is_detached = is_detached
        self._enabled = True
        self._click_through = False
        self._show_original = True
        self._show_author = True
        self._items: list[dict[str, Any]] = []
        self._plain_zh = ""
        self._plain_src = ""

        # Current visual style parameters
        self._trans_size = 15
        self._trans_color = TEXT_PRIMARY
        self._trans_stroke_color = "#000000"
        self._trans_stroke_size = 0
        self._orig_size = 12
        self._orig_color = TEXT_SECONDARY
        self._orig_stroke_color = "#000000"
        self._orig_stroke_size = 0

        self.setObjectName("ChannelCard")
        self.setStyleSheet("#ChannelCard { background: transparent; border: none; }")

        content = QVBoxLayout(self)
        content.setContentsMargins(10, 8, 10, 8)
        content.setSpacing(6)

        # Header row
        top_row = QHBoxLayout()
        top_row.setSpacing(7)
        content.addLayout(top_row)

        self._dot = _ColorDot(color_hex, self)
        top_row.addWidget(self._dot)

        self._idx_lbl = QLabel(index_tag, self)
        self._idx_lbl.setStyleSheet(
            f"color: {color_hex}; font-size: 11px; font-weight: bold; background: transparent;"
        )
        top_row.addWidget(self._idx_lbl)

        self._title_lbl = QLabel(title, self)
        self._title_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: bold; background: transparent;"
        )
        top_row.addWidget(self._title_lbl)

        self._badge_lbl = QLabel("待机", self)
        self._badge_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._badge_lbl.setStyleSheet(
            "background: rgba(255, 255, 255, 0.06); color: #9CA3AF; "
            "border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 4px; "
            "padding: 1px 6px; font-size: 10px;"
        )
        top_row.addWidget(self._badge_lbl)

        if self._is_asr:
            self._source_lbl = QLabel("", self)
            self._source_lbl.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 10px; background: transparent;"
            )
            top_row.addWidget(self._source_lbl)
        else:
            self._source_lbl = None

        top_row.addStretch(1)

        self._latency_lbl = QLabel("", self)
        self._latency_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10px; font-family: 'Consolas', monospace; background: transparent;"
        )
        top_row.addWidget(self._latency_lbl)

        if self._is_asr:
            self.wave = AudioWaveWidget(self, height=16)
            self.wave.setFixedWidth(104)
            top_row.addWidget(self.wave)
        else:
            self.wave = None

        default_msg = (
            "等待语音输入（可在控制中心选择独立软件或声卡监听）"
            if is_asr
            else "将取景框拖至需要翻译的画面位置"
        )
        self._default_msg = default_msg

        self._zh_lbl = StrokedTextLabel(
            default_msg,
            self,
            font_size=self._trans_size,
            text_color=self._trans_color,
            stroke_color=self._trans_stroke_color,
            stroke_size=self._trans_stroke_size,
            bold=True,
        )
        self._en_lbl = StrokedTextLabel(
            "",
            self,
            font_size=self._orig_size,
            text_color=self._orig_color,
            stroke_color=self._orig_stroke_color,
            stroke_size=self._orig_stroke_size,
            bold=False,
        )
        self._en_lbl.setVisible(False)

        if self._is_detached:
            text_scroll = QScrollArea(self)
            text_scroll.setWidgetResizable(True)
            text_scroll.setFrameShape(QFrame.Shape.NoFrame)
            text_scroll.viewport().setAutoFillBackground(False)
            text_scroll.viewport().setStyleSheet("#qt_scrollarea_viewport { background: transparent; }")
            text_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            text_host = QWidget()
            text_host.setObjectName("CardScrollHost")
            text_host.setAutoFillBackground(False)
            text_host.setStyleSheet("#CardScrollHost { background: transparent; }")
            tlay = QVBoxLayout(text_host)
            tlay.setContentsMargins(0, 0, 2, 0)
            tlay.setSpacing(4)
            self._zh_lbl.setParent(text_host)
            self._en_lbl.setParent(text_host)
            tlay.addWidget(self._zh_lbl)
            tlay.addWidget(self._en_lbl)
            tlay.addStretch(1)
            text_scroll.setWidget(text_host)
            content.addWidget(text_scroll, 1)
        else:
            content.addWidget(self._zh_lbl)
            content.addWidget(self._en_lbl)
            content.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(5)
        content.addLayout(btn_row)

        if self._is_detached:
            self._btn_split = QPushButton(make_icon("merge", "#FFFFFF", 13), "合并归位", self)
            self._btn_split.setProperty("accent", True)
            self._btn_split.clicked.connect(lambda: self.merge_requested.emit(self.channel_id))
        else:
            self._btn_split = QPushButton(make_icon("detach", TEXT_SECONDARY, 13), "独立浮窗", self)
            self._btn_split.setProperty("ghost", True)
            self._btn_split.clicked.connect(lambda: self.detach_requested.emit(self.channel_id))
        self._btn_split.setProperty("compact", True)
        btn_row.addWidget(self._btn_split)

        if not self._is_asr:
            self._btn_locate = QPushButton(make_icon("crosshair", TEXT_SECONDARY, 13), "定位", self)
            self._btn_locate.setProperty("compact", True)
            self._btn_locate.setProperty("ghost", True)
            self._btn_locate.clicked.connect(lambda: self.locate_requested.emit(self.channel_id))
            btn_row.addWidget(self._btn_locate)
        else:
            self._btn_locate = None

        self._btn_pause = QPushButton(make_icon("pause", TEXT_SECONDARY, 13), "暂停", self)
        self._btn_pause.setProperty("compact", True)
        self._btn_pause.setProperty("ghost", True)
        self._btn_pause.clicked.connect(lambda: self.toggle_enabled_requested.emit(self.channel_id))
        btn_row.addWidget(self._btn_pause)

        self._btn_copy = QPushButton(make_icon("copy", TEXT_SECONDARY, 13), "复制", self)
        self._btn_copy.setProperty("compact", True)
        self._btn_copy.setProperty("ghost", True)
        self._btn_copy.clicked.connect(self._copy_text)
        btn_row.addWidget(self._btn_copy)

        if self._is_detached:
            self._btn_lock = QPushButton(make_icon("unlock", TEXT_SECONDARY, 13), "穿透锁定", self)
            self._btn_lock.setProperty("compact", True)
            self._btn_lock.setProperty("ghost", True)
            self._btn_lock.clicked.connect(
                lambda: self.toggle_clickthrough_requested.emit(self.channel_id)
            )
            btn_row.addWidget(self._btn_lock)
        else:
            self._btn_lock = None

        btn_row.addStretch(1)

        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(self._restore_copy_label)

    def update_meta(
        self, title: str, color_hex: str, enabled: bool, index_tag: Optional[str] = None
    ) -> None:
        self._title = title
        self._color = color_hex
        self._enabled = enabled
        if index_tag is not None:
            self._index_tag = index_tag
        dot_col = color_hex if enabled else TEXT_MUTED
        self._dot.set_color(dot_col)
        self._idx_lbl.setText(self._index_tag)
        self._idx_lbl.setStyleSheet(
            f"color: {dot_col}; font-size: 11px; font-weight: bold; background: transparent;"
        )
        self._title_lbl.setText(title)
        if enabled:
            self._btn_pause.setIcon(make_icon("pause", TEXT_SECONDARY, 13))
            self._btn_pause.setText("暂停")
            if self._badge_lbl.text() == "已暂停":
                self._badge_lbl.setText("待机")
        else:
            self._btn_pause.setIcon(make_icon("play", STATUS_OK, 13))
            self._btn_pause.setText("继续")
            self._badge_lbl.setText("已暂停")
        if self.wave is not None:
            self.wave.set_active(enabled)
        self.update()

    def apply_appearance(self, style: dict[str, Any], show_original: bool, show_author: bool = True) -> None:
        self._trans_size = int(style.get("trans_font_size", 15))
        self._trans_color = str(style.get("trans_font_color", TEXT_PRIMARY))
        self._trans_stroke_color = str(style.get("trans_stroke_color", "#000000"))
        self._trans_stroke_size = int(style.get("trans_stroke_size", 0))

        self._orig_size = int(style.get("orig_font_size", 12))
        self._orig_color = str(style.get("orig_font_color", TEXT_SECONDARY))
        self._orig_stroke_color = str(style.get("orig_stroke_color", "#000000"))
        self._orig_stroke_size = int(style.get("orig_stroke_size", 0))

        self._show_original = bool(show_original)
        self._show_author = bool(show_author)

        self._zh_lbl.set_style_options(
            self._trans_size,
            self._trans_color,
            self._trans_stroke_color,
            self._trans_stroke_size,
            bold=True,
        )
        self._en_lbl.set_style_options(
            self._orig_size,
            self._orig_color,
            self._orig_stroke_color,
            self._orig_stroke_size,
            bold=False,
        )
        self._render_content()

    def update_style(self, font_size: int, show_original: bool, show_author: bool = True) -> None:
        self._trans_size = int(font_size)
        self._show_original = bool(show_original)
        self._show_author = bool(show_author)
        self._zh_lbl.set_style_options(
            self._trans_size,
            self._trans_color,
            self._trans_stroke_color,
            self._trans_stroke_size,
            bold=True,
        )
        self._render_content()

    def _render_content(self) -> None:
        if self._items:
            zh_entries: list[tuple[str, str]] = []
            orig_entries: list[tuple[str, str]] = []
            plain_zh_lines: list[str] = []
            plain_src_lines: list[str] = []
            for it in self._items:
                author = (it.get("author") or "").strip() if self._show_author else ""
                src = (it.get("src") or "").strip()
                zh = (it.get("zh") or "").strip()
                if not zh:
                    continue
                zh_entries.append((author, zh))
                plain_zh_lines.append(f"{author}: {zh}" if author else zh)
                if src:
                    orig_entries.append((author, src))
                    plain_src_lines.append(f"{author}: {src}" if author else src)

            self._plain_zh = "\n".join(plain_zh_lines)
            self._plain_src = "\n".join(plain_src_lines)
            self._zh_lbl.set_entries(zh_entries if zh_entries else [("", self._default_msg)])
            show_src = self._show_original and bool(orig_entries)
            self._en_lbl.set_entries(orig_entries if show_src else [])
            self._en_lbl.setVisible(show_src)
            return

        zh_txt = self._plain_zh if self._plain_zh.strip() else self._default_msg
        self._zh_lbl.set_text(zh_txt)
        show_src = self._show_original and bool(self._plain_src.strip())
        self._en_lbl.set_text(self._plain_src if show_src else "")
        self._en_lbl.setVisible(show_src)

    def set_translation(
        self,
        src_text: str,
        zh_text: str,
        badge: str,
        proc_ms: int,
        llm_ms: int,
        items: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._items = list(items) if items else []
        self._plain_zh = zh_text
        self._plain_src = src_text
        self._render_content()
        if badge:
            self._badge_lbl.setText(badge)
        tag = "听写" if self._is_asr else "识别"
        self._latency_lbl.setText(f"{tag} {proc_ms}ms · 译 {llm_ms}ms")

    def set_source_hint(self, hint: str) -> None:
        if self._source_lbl is not None:
            self._source_lbl.setText(hint)

    def clear_text(self) -> None:
        self._items = []
        self._plain_zh = ""
        self._plain_src = ""
        self._render_content()
        self._latency_lbl.setText("")

    def _copy_text(self) -> None:
        txt = (self._plain_zh or self._zh_lbl.plain_text()).strip()
        if txt:
            QApplication.clipboard().setText(txt)
            self._btn_copy.setIcon(make_icon("check", STATUS_OK, 13))
            self._btn_copy.setText("已复制")
            self._copy_timer.start(1200)

    def _restore_copy_label(self) -> None:
        self._btn_copy.setIcon(make_icon("copy", TEXT_SECONDARY, 13))
        self._btn_copy.setText("复制")


class DetachedCardWindow(QWidget):
    """Standalone single-layer floating HUD hosting a detached channel card (WDA safe)."""

    geometry_changed = Signal(str, list)  # channel_id, [x, y, w, h]

    def __init__(
        self,
        channel_id: str,
        title: str,
        color_hex: str,
        rect: list[int],
        index_tag: str = "01",
        opacity: float = 0.94,
        bg_color: str = BG_CANVAS,
        is_asr: bool = False,
        exclude_capture: bool = True,
    ) -> None:
        super().__init__(None)
        self.channel_id = channel_id
        self._color = color_hex
        self._opacity = max(0.25, min(1.0, float(opacity)))
        self._bg_color = bg_color or BG_CANVAS
        self._click_through = False
        self._exclude_capture = bool(exclude_capture)
        self._stealth_highlight = False
        self._suppress_geom_emit = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowOpacity(self._opacity)
        self.setWindowTitle(f"独立浮窗 - {title}")

        x, y, w, h = rect
        self.setGeometry(int(x), int(y), max(290, int(w)), max(126, int(h)))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(2)

        self._drag_bar = _GripHandleBar(title, self)
        outer.addWidget(self._drag_bar)

        self.card = ChannelCardWidget(
            channel_id=channel_id,
            title=title,
            color_hex=color_hex,
            index_tag=index_tag,
            is_asr=is_asr,
            is_detached=True,
            parent=self,
        )
        outer.addWidget(self.card, 1)

        self._drag_origin: Optional[tuple[QPoint, QPoint]] = None
        self._resize_origin: Optional[tuple[QPoint, int, int]] = None

    def set_suppress_geometry_emit(self, suppress: bool) -> None:
        self._suppress_geom_emit = bool(suppress)

    def set_stealth_highlight(self, active: bool) -> None:
        self._stealth_highlight = bool(active)
        self.update()

    def set_exclude_from_capture(self, enabled: bool) -> None:
        self._exclude_capture = bool(enabled)
        if self.isVisible():
            set_exclude_from_capture(self, self._exclude_capture)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_rounded_mask(self, 10.0)
        set_window_click_through(self, self._click_through)
        set_exclude_from_capture(self, self._exclude_capture)

    def apply_appearance(
        self, style: dict[str, Any], show_original: bool, show_author: bool = True
    ) -> None:
        self._opacity = max(0.25, min(1.0, float(style.get("opacity", self._opacity))))
        self._bg_color = str(style.get("bg_color", self._bg_color))
        self.setWindowOpacity(self._opacity)
        self.card.apply_appearance(style, show_original=show_original, show_author=show_author)
        self.update()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.25, min(1.0, float(opacity)))
        self.setWindowOpacity(self._opacity)
        self.update()

    def set_click_through(self, enabled: bool) -> None:
        self._click_through = bool(enabled)
        set_window_click_through(self, self._click_through)
        if self.card._btn_lock is not None:
            if enabled:
                self.card._btn_lock.setIcon(make_icon("lock", STATUS_ERR, 13))
                self.card._btn_lock.setText("已穿透")
            else:
                self.card._btn_lock.setIcon(make_icon("unlock", TEXT_SECONDARY, 13))
                self.card._btn_lock.setText("穿透锁定")

    def update_meta(
        self, title: str, color_hex: str, enabled: bool, index_tag: Optional[str] = None
    ) -> None:
        self._color = color_hex
        self._drag_bar.set_title(title)
        self.card.update_meta(title, color_hex, enabled, index_tag=index_tag)
        self.update()

    def get_logical_rect(self) -> list[int]:
        g = self.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not self._suppress_geom_emit:
            self.geometry_changed.emit(self.channel_id, self.get_logical_rect())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        apply_rounded_mask(self, 10.0)
        if not self._suppress_geom_emit:
            self.geometry_changed.emit(self.channel_id, self.get_logical_rect())

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
            self.resize(max(290, w0 + delta.x()), max(126, h0 + delta.y()))
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
            border_hex=ACCENT_PRIMARY if self._stealth_highlight else self._color,
            bg_hex=self._bg_color,
        )
        p.setPen(QPen(QColor(TEXT_MUTED), 1.4))
        w, h = self.width(), self.height()
        p.drawLine(w - 13, h - 5, w - 5, h - 13)
        p.drawLine(w - 8, h - 5, w - 5, h - 8)
