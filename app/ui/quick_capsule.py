"""Bidirectional Quick Output Capsule + Minimal Bilingual Voice HUD (single-layer WDA safe)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import OUTPUT_LANG_CHOICES, OUTPUT_TONE_CHOICES
from ..win32_utils import set_exclude_from_capture, set_window_click_through
from .icons import make_icon
from .theme import (
    ACCENT_PRIMARY,
    BG_CANVAS,
    STATUS_ERR,
    STATUS_OK,
    STATUS_WARN,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    AudioWaveWidget,
    HorizontalHairline,
    apply_rounded_mask,
    paint_panel_surface,
    start_native_window_drag,
)


class _SubmitTextEdit(QPlainTextEdit):
    """Multi-line text input where Enter triggers translation and Shift+Enter inserts newline."""

    submit_requested = Signal()
    escape_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.escape_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.submit_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class QuickTranslateCapsule(QWidget):
    """Dual-mode command bar & Minimal Bilingual HUD for outbound text/voice translation."""

    translate_text_requested = Signal(str, str, str)  # src_text, target_lang, tone
    voice_toggle_requested = Signal(bool)  # start=True / stop=False
    output_prefs_changed = Signal(str, str)  # target_lang, tone
    auto_copy_toggled = Signal(bool)
    minimal_mode_toggled = Signal(bool)
    click_through_requested = Signal(bool)
    geometry_changed = Signal(list)  # minimal mode rect
    full_pos_changed = Signal(list)  # full mode [x, y]

    def __init__(
        self,
        target_lang: str = "en",
        tone: str = "natural",
        exclude_capture: bool = True,
        voice_hotkey: str = "Alt+V",
        voice_hold_mode: bool = True,
        auto_copy: bool = True,
        minimal_mode: bool = False,
        minimal_rect: Optional[list[int]] = None,
        full_pos: Optional[list[int]] = None,
        capsule_opacity: float = 0.90,
        capsule_bg_color: str = BG_CANVAS,
        capsule_font_size: int = 15,
        capsule_font_color: str = TEXT_PRIMARY,
    ) -> None:
        super().__init__(None)
        self._exclude_capture = bool(exclude_capture)
        self._recording = False
        self._voice_hotkey = voice_hotkey
        self._voice_hold_mode = bool(voice_hold_mode)
        self._auto_copy = bool(auto_copy)
        self._minimal_mode = bool(minimal_mode)
        self._click_through = False
        self._capsule_opacity = max(0.20, min(1.0, float(capsule_opacity)))
        self._capsule_bg_color = capsule_bg_color or BG_CANVAS
        self._capsule_font_size = int(capsule_font_size)
        self._capsule_font_color = capsule_font_color or TEXT_PRIMARY
        self._minimal_rect = list(minimal_rect or [520, 220, 560, 120])
        self._full_pos: list[int] = list(full_pos) if (full_pos and len(full_pos) == 2) else []
        self._suppress_pos_save = False
        self._drag_origin: Optional[tuple[QPoint, QPoint]] = None
        self._resize_origin: Optional[tuple[QPoint, int, int]] = None
        self._last_src_text = ""
        self._last_out_text = ""

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowOpacity(self._capsule_opacity)
        self.setWindowTitle("双向快翻")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(8)

        # ---- Full Mode Widgets ----
        self._full_host = QWidget(self)
        self._full_host.setObjectName("CapsuleFullHost")
        self._full_host.setStyleSheet("#CapsuleFullHost { background: transparent; }")
        full_lay = QVBoxLayout(self._full_host)
        full_lay.setContentsMargins(0, 0, 0, 0)
        full_lay.setSpacing(8)
        root.addWidget(self._full_host)

        hdr = QHBoxLayout()
        hdr.setSpacing(7)
        full_lay.addLayout(hdr)

        title = QLabel("双向快翻", self._full_host)
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: bold; background: transparent;"
        )
        hdr.addWidget(title)

        tag_dir = QLabel("输出", self._full_host)
        tag_dir.setStyleSheet(
            "color: #60A5FA; font-size: 10px; font-weight: bold; "
            "background: rgba(59, 130, 246, 0.14); border: 1px solid rgba(59, 130, 246, 0.35); "
            "border-radius: 4px; padding: 1px 6px;"
        )
        hdr.addWidget(tag_dir)

        hdr.addStretch(1)

        self._lang_combo = QComboBox(self._full_host)
        self._lang_combo.setMinimumWidth(110)
        for code, label in OUTPUT_LANG_CHOICES:
            self._lang_combo.addItem(label, code)
        self._set_combo_value(self._lang_combo, target_lang)
        self._lang_combo.currentIndexChanged.connect(self._emit_prefs)
        hdr.addWidget(self._lang_combo)

        self._tone_combo = QComboBox(self._full_host)
        self._tone_combo.setMinimumWidth(128)
        for code, label in OUTPUT_TONE_CHOICES:
            self._tone_combo.addItem(label, code)
        self._set_combo_value(self._tone_combo, tone)
        self._tone_combo.currentIndexChanged.connect(self._emit_prefs)
        hdr.addWidget(self._tone_combo)

        self._btn_minimal = QPushButton(make_icon("minimize", TEXT_PRIMARY, 13), "极简", self._full_host)
        self._btn_minimal.setProperty("compact", True)
        self._btn_minimal.setToolTip("进入极简模式（仅保留双语文本框 + 快捷键语音输入）")
        self._btn_minimal.clicked.connect(lambda: self.minimal_mode_toggled.emit(True))
        hdr.addWidget(self._btn_minimal)

        btn_close = QPushButton(make_icon("close", TEXT_SECONDARY, 14), "", self._full_host)
        btn_close.setFixedSize(26, 26)
        btn_close.setProperty("ghost", True)
        btn_close.setToolTip("关闭 (Esc)")
        btn_close.clicked.connect(self.hide)
        hdr.addWidget(btn_close)

        full_lay.addWidget(HorizontalHairline(self._full_host))

        self._input_edit = _SubmitTextEdit(self._full_host)
        self._input_edit.setObjectName("CapsuleInputEdit")
        self._input_edit.setPlaceholderText("输入中文或外语（Enter 立即翻译，Shift+Enter 换行）...")
        self._input_edit.setFixedHeight(64)
        self._input_edit.setStyleSheet(
            "#CapsuleInputEdit { background-color: rgba(0, 0, 0, 0.24); color: #F1F3F5; "
            "border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 6px; "
            "padding: 5px 9px; font-size: 13px; } "
            "#CapsuleInputEdit:focus { border: 1px solid #3B82F6; }"
        )
        self._input_edit.submit_requested.connect(self._trigger_text_translate)
        self._input_edit.escape_requested.connect(self.hide)
        full_lay.addWidget(self._input_edit)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        full_lay.addLayout(ctrl)

        self._btn_mic = QPushButton(make_icon("mic", TEXT_PRIMARY, 14), "", self._full_host)
        self._btn_mic.clicked.connect(self._on_mic_clicked)
        ctrl.addWidget(self._btn_mic)

        self.mic_wave = AudioWaveWidget(self._full_host, height=20)
        self.mic_wave.setFixedWidth(96)
        self.mic_wave.set_active(False)
        ctrl.addWidget(self.mic_wave)

        self._btn_auto_copy = QPushButton(self._full_host)
        self._btn_auto_copy.setProperty("compact", True)
        self._btn_auto_copy.clicked.connect(lambda: self.auto_copy_toggled.emit(not self._auto_copy))
        ctrl.addWidget(self._btn_auto_copy)

        self._status_lbl = QLabel("就绪", self._full_host)
        self._status_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; background: transparent;")
        ctrl.addWidget(self._status_lbl, 1)

        self._btn_go = QPushButton(make_icon("send", "#FFFFFF", 14), "翻译", self._full_host)
        self._btn_go.setProperty("accent", True)
        self._btn_go.clicked.connect(self._trigger_text_translate)
        ctrl.addWidget(self._btn_go)

        self._sep_out = HorizontalHairline(self._full_host)
        full_lay.addWidget(self._sep_out)

        out_box = QHBoxLayout()
        out_box.setSpacing(10)
        full_lay.addLayout(out_box)

        self._out_lbl = QLabel("", self._full_host)
        self._out_lbl.setWordWrap(True)
        self._out_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        out_box.addWidget(self._out_lbl, 1)

        out_right = QVBoxLayout()
        out_right.setSpacing(4)
        out_box.addLayout(out_right)

        self._btn_copy = QPushButton(make_icon("copy", TEXT_PRIMARY, 13), "复制", self._full_host)
        self._btn_copy.setProperty("compact", True)
        self._btn_copy.clicked.connect(self._copy_output)
        out_right.addWidget(self._btn_copy)

        self._latency_lbl = QLabel("", self._full_host)
        self._latency_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._latency_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-family: 'Consolas', monospace; font-size: 10px; background: transparent;"
        )
        out_right.addWidget(self._latency_lbl)

        self._set_output_visible(False)

        # ---- Minimal Bilingual Voice HUD ----
        self._min_host = QWidget(self)
        self._min_host.setObjectName("CapsuleMinHost")
        self._min_host.setStyleSheet("#CapsuleMinHost { background: transparent; }")
        min_lay = QVBoxLayout(self._min_host)
        min_lay.setContentsMargins(0, 0, 0, 0)
        min_lay.setSpacing(6)
        root.addWidget(self._min_host)

        self._min_bar = QWidget(self._min_host)
        self._min_bar.setObjectName("CapsuleMinBar")
        self._min_bar.setStyleSheet("#CapsuleMinBar { background: transparent; }")
        bar_lay = QHBoxLayout(self._min_bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(6)

        grip_ico = QLabel(self._min_bar)
        grip_ico.setPixmap(make_icon("grip", TEXT_MUTED, 12).pixmap(12, 12))
        grip_ico.setStyleSheet("background: transparent;")
        bar_lay.addWidget(grip_ico)

        self._min_hint_lbl = QLabel("", self._min_bar)
        self._min_hint_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; background: transparent;"
        )
        bar_lay.addWidget(self._min_hint_lbl)
        bar_lay.addStretch(1)

        self._btn_min_lock = QPushButton(make_icon("lock", TEXT_PRIMARY, 12), "穿透模式", self._min_bar)
        self._btn_min_lock.setProperty("compact", True)
        self._btn_min_lock.setProperty("amber", True)
        self._btn_min_lock.setToolTip("锁定鼠标穿透并隐藏此工具条，仅留双语文本框")
        self._btn_min_lock.clicked.connect(lambda: self.click_through_requested.emit(True))
        bar_lay.addWidget(self._btn_min_lock)

        self._btn_min_exit = QPushButton(make_icon("detach", TEXT_SECONDARY, 12), "退出极简", self._min_bar)
        self._btn_min_exit.setProperty("compact", True)
        self._btn_min_exit.setProperty("ghost", True)
        self._btn_min_exit.clicked.connect(lambda: self.minimal_mode_toggled.emit(False))
        bar_lay.addWidget(self._btn_min_exit)

        min_lay.addWidget(self._min_bar)

        self._min_out_lbl = QLabel("等待语音输入...", self._min_host)
        self._min_out_lbl.setWordWrap(True)
        self._min_out_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        min_lay.addWidget(self._min_out_lbl)

        self._min_src_lbl = QLabel("", self._min_host)
        self._min_src_lbl.setWordWrap(True)
        self._min_src_lbl.setVisible(False)
        min_lay.addWidget(self._min_src_lbl)
        min_lay.addStretch(1)

        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(self._restore_copy_btn)

        self._refresh_voice_hint_labels()
        self._refresh_auto_copy_btn()
        self._apply_capsule_style_to_labels()
        self._sync_mode_layout()

    @staticmethod
    def _set_combo_value(cb: QComboBox, code: str) -> None:
        idx = next((i for i in range(cb.count()) if cb.itemData(i) == code), 0)
        cb.setCurrentIndex(idx)

    def set_prefs(self, target_lang: str, tone: str) -> None:
        self._lang_combo.blockSignals(True)
        self._tone_combo.blockSignals(True)
        self._set_combo_value(self._lang_combo, target_lang)
        self._set_combo_value(self._tone_combo, tone)
        self._lang_combo.blockSignals(False)
        self._tone_combo.blockSignals(False)

    def set_voice_hotkey_hint(self, hk: str, hold_mode: bool) -> None:
        self._voice_hotkey = hk
        self._voice_hold_mode = bool(hold_mode)
        self._refresh_voice_hint_labels()

    def _refresh_voice_hint_labels(self) -> None:
        if not self._recording:
            if self._voice_hold_mode:
                self._btn_mic.setText(f"按住 {self._voice_hotkey} 语音")
            else:
                self._btn_mic.setText(f"语音 ({self._voice_hotkey})")
        if self._voice_hold_mode:
            self._min_hint_lbl.setText(f"极简 · 按住 {self._voice_hotkey} 说话 · 自动复制")
        else:
            self._min_hint_lbl.setText(f"极简 · 按 {self._voice_hotkey} 语音 · 自动复制")
        if not self._last_out_text:
            self._min_out_lbl.setText(
                f"按住 {self._voice_hotkey} 开始语音输入..."
                if self._voice_hold_mode
                else f"按下 {self._voice_hotkey} 开始语音输入..."
            )

    def set_auto_copy_state(self, enabled: bool) -> None:
        self._auto_copy = bool(enabled)
        self._refresh_auto_copy_btn()

    def _refresh_auto_copy_btn(self) -> None:
        if self._auto_copy:
            self._btn_auto_copy.setIcon(make_icon("check", STATUS_OK, 12))
            self._btn_auto_copy.setText("自动复制 ON")
            self._btn_auto_copy.setProperty("lime", True)
            self._btn_auto_copy.setProperty("ghost", False)
        else:
            self._btn_auto_copy.setIcon(make_icon("copy", TEXT_SECONDARY, 12))
            self._btn_auto_copy.setText("自动复制 OFF")
            self._btn_auto_copy.setProperty("lime", False)
            self._btn_auto_copy.setProperty("ghost", True)
        self._btn_auto_copy.style().unpolish(self._btn_auto_copy)
        self._btn_auto_copy.style().polish(self._btn_auto_copy)

    def set_minimal_mode(self, enabled: bool) -> None:
        self._minimal_mode = bool(enabled)
        self._sync_mode_layout()

    def _sync_mode_layout(self) -> None:
        self._suppress_pos_save = True
        try:
            self._full_host.setVisible(not self._minimal_mode)
            self._min_host.setVisible(self._minimal_mode)
            self._min_bar.setVisible(self._minimal_mode and (not self._click_through))
            if self._minimal_mode:
                self.setMinimumSize(260, 72)
                self.setMaximumSize(1280, 520)
                x, y, w, h = self._minimal_rect
                self.setGeometry(int(x), int(y), max(260, int(w)), max(72, int(h)))
            else:
                self.setMaximumSize(640, 640)
                self.setFixedWidth(640)
                self.setMinimumHeight(210)
                self.adjustSize()
                if len(self._full_pos) == 2 and self._is_pos_on_any_screen(
                    self._full_pos[0], self._full_pos[1]
                ):
                    self.move(int(self._full_pos[0]), int(self._full_pos[1]))
            self.setWindowOpacity(self._capsule_opacity)
            apply_rounded_mask(self, 11.0)
            self.update()
        finally:
            self._suppress_pos_save = False

    def set_capsule_style(
        self, opacity: float, bg_color: str, font_size: int, font_color: str
    ) -> None:
        self._capsule_opacity = max(0.20, min(1.0, float(opacity)))
        self._capsule_bg_color = bg_color or BG_CANVAS
        self._capsule_font_size = max(11, min(32, int(font_size)))
        self._capsule_font_color = font_color or TEXT_PRIMARY
        self.setWindowOpacity(self._capsule_opacity)
        self._apply_capsule_style_to_labels()
        self.update()

    def _apply_capsule_style_to_labels(self) -> None:
        sz = self._capsule_font_size
        col = self._capsule_font_color
        self._out_lbl.setStyleSheet(
            f"color: {col}; font-size: {max(sz, 14)}px; font-weight: bold; background: transparent;"
        )
        self._min_out_lbl.setStyleSheet(
            f"color: {col}; font-size: {sz}px; font-weight: bold; background: transparent;"
        )
        src_sz = max(10, sz - 3)
        self._min_src_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: {src_sz}px; background: transparent;"
        )

    def set_click_through(self, enabled: bool) -> None:
        self._click_through = bool(enabled)
        set_window_click_through(self, self._click_through)
        self._min_bar.setVisible(self._minimal_mode and (not self._click_through))
        self.update()

    def current_target_lang(self) -> str:
        return str(self._lang_combo.currentData() or "en")

    def current_tone(self) -> str:
        return str(self._tone_combo.currentData() or "natural")

    def _emit_prefs(self) -> None:
        self.output_prefs_changed.emit(self.current_target_lang(), self.current_tone())

    def set_exclude_from_capture(self, enabled: bool) -> None:
        self._exclude_capture = bool(enabled)
        if self.isVisible():
            set_exclude_from_capture(self, self._exclude_capture)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_rounded_mask(self, 11.0)
        set_window_click_through(self, self._click_through)
        set_exclude_from_capture(self, self._exclude_capture)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        apply_rounded_mask(self, 11.0)
        if self._minimal_mode and not self._suppress_pos_save:
            g = self.geometry()
            self._minimal_rect = [g.x(), g.y(), g.width(), g.height()]
            self.geometry_changed.emit(list(self._minimal_rect))

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self._suppress_pos_save:
            return
        if self._minimal_mode:
            g = self.geometry()
            self._minimal_rect = [g.x(), g.y(), g.width(), g.height()]
            self.geometry_changed.emit(list(self._minimal_rect))
        else:
            p = self.pos()
            self._full_pos = [int(p.x()), int(p.y())]
            self.full_pos_changed.emit(list(self._full_pos))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and not self._click_through:
            self.hide()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _is_pos_on_any_screen(x: int, y: int) -> bool:
        pt = QPoint(int(x) + 40, int(y) + 20)
        for scr in QApplication.screens():
            if scr.availableGeometry().contains(pt):
                return True
        return False

    def reset_saved_positions(self) -> None:
        self._full_pos = []
        self._minimal_rect = [520, 220, 560, 120]
        if self._minimal_mode:
            self._suppress_pos_save = True
            x, y, w, h = self._minimal_rect
            self.setGeometry(x, y, w, h)
            self._suppress_pos_save = False
        elif self.isVisible():
            self._suppress_pos_save = True
            self._center_on_active_screen()
            self._suppress_pos_save = False

    def _center_on_active_screen(self) -> None:
        if self._minimal_mode:
            return
        scr = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.adjustSize()
            cx = g.center().x() - self.width() // 2
            cy = g.top() + int(g.height() * 0.24)
            self._suppress_pos_save = True
            self.move(cx, cy)
            self._suppress_pos_save = False

    def _position_full_mode_if_needed(self) -> None:
        """Preserve dragged location in BOTH modes; only auto-center on first use."""
        if self._minimal_mode or self.isVisible():
            return
        if len(self._full_pos) == 2 and self._is_pos_on_any_screen(self._full_pos[0], self._full_pos[1]):
            self._suppress_pos_save = True
            self.adjustSize()
            self.move(int(self._full_pos[0]), int(self._full_pos[1]))
            self._suppress_pos_save = False
            return
        self._center_on_active_screen()

    def open_for_text(self) -> None:
        if self._minimal_mode:
            self.show()
            self.raise_()
            return
        self._position_full_mode_if_needed()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input_edit.setFocus()
        self._input_edit.selectAll()

    def open_for_voice(self) -> None:
        self._position_full_mode_if_needed()
        self.show()
        self.raise_()
        if not self._click_through and not self._minimal_mode:
            self.activateWindow()

    def set_recording_state(self, recording: bool, mic_label: str = "") -> None:
        self._recording = bool(recording)
        self.mic_wave.set_active(recording)
        if recording:
            self._btn_mic.setIcon(make_icon("mic_off", "#FFFFFF", 14))
            self._btn_mic.setText("松开/停止翻译" if self._voice_hold_mode else "结束并翻译")
            self._btn_mic.setProperty("accent", True)
            self._status_lbl.setText(f"聆听中 ({mic_label or '麦克风'})")
            self._status_lbl.setStyleSheet(
                f"color: {ACCENT_PRIMARY}; font-size: 11px; font-weight: bold; background: transparent;"
            )
            self._min_hint_lbl.setText("正在聆听... 松开自动翻译")
            self._min_hint_lbl.setStyleSheet(
                f"color: {ACCENT_PRIMARY}; font-size: 11px; font-weight: bold; background: transparent;"
            )
        else:
            self._btn_mic.setIcon(make_icon("mic", TEXT_PRIMARY, 14))
            self._btn_mic.setProperty("accent", False)
            self.mic_wave.set_level(0.0)
            self._min_hint_lbl.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 11px; background: transparent;"
            )
            self._refresh_voice_hint_labels()
        self._btn_mic.style().unpolish(self._btn_mic)
        self._btn_mic.style().polish(self._btn_mic)
        self.update()

    def set_busy_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet(
            f"color: {STATUS_WARN}; font-size: 11px; background: transparent;"
        )
        if self._minimal_mode:
            self._min_hint_lbl.setText(msg)

    def set_error_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet(
            f"color: {STATUS_ERR}; font-size: 11px; background: transparent;"
        )
        if self._minimal_mode:
            self._min_hint_lbl.setText(msg)

    def set_result(
        self,
        translated: str,
        elapsed_ms: int,
        src_text: Optional[str] = None,
        copied: bool = True,
        pasted: bool = False,
    ) -> None:
        if src_text is not None:
            self._last_src_text = src_text
            if not self._minimal_mode:
                self._input_edit.setPlainText(src_text)
        self._last_out_text = translated
        self._out_lbl.setText(translated)
        self._latency_lbl.setText(f"{elapsed_ms} ms")
        self._set_output_visible(bool(translated))

        self._min_out_lbl.setText(translated or self._min_out_lbl.text())
        self._min_src_lbl.setText(self._last_src_text)
        self._min_src_lbl.setVisible(bool(self._last_src_text.strip()))

        if not self._minimal_mode:
            self.adjustSize()
        self._refresh_voice_hint_labels()
        if pasted:
            self._status_lbl.setText("已复制并自动粘贴回原窗口")
            self._status_lbl.setStyleSheet(f"color: {STATUS_OK}; font-size: 11px; background: transparent;")
        elif copied:
            self._status_lbl.setText("已自动复制到剪贴板 · 可直接 Ctrl+V")
            self._status_lbl.setStyleSheet(f"color: {STATUS_OK}; font-size: 11px; background: transparent;")
        else:
            self._status_lbl.setText("翻译完成")
            self._status_lbl.setStyleSheet(f"color: {STATUS_OK}; font-size: 11px; background: transparent;")

    def _set_output_visible(self, visible: bool) -> None:
        self._sep_out.setVisible(visible)
        self._out_lbl.setVisible(visible)
        self._btn_copy.setVisible(visible)
        self._latency_lbl.setVisible(visible)

    def _on_mic_clicked(self) -> None:
        self.voice_toggle_requested.emit(not self._recording)

    def _trigger_text_translate(self) -> None:
        src = self._input_edit.toPlainText().strip()
        if not src:
            self.set_error_status("请输入需要翻译的内容或使用语音输入")
            return
        self.set_busy_status("正在翻译...")
        self.translate_text_requested.emit(src, self.current_target_lang(), self.current_tone())

    def _copy_output(self) -> None:
        txt = self._out_lbl.text().strip()
        if txt:
            QApplication.clipboard().setText(txt)
            self._btn_copy.setIcon(make_icon("check", STATUS_OK, 13))
            self._btn_copy.setText("已复制")
            self._copy_timer.start(1200)

    def _restore_copy_btn(self) -> None:
        self._btn_copy.setIcon(make_icon("copy", TEXT_PRIMARY, 13))
        self._btn_copy.setText("复制")

    def mousePressEvent(self, event) -> None:
        if self._click_through:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self._minimal_mode and pos.x() >= self.width() - 22 and pos.y() >= self.height() - 22:
                self._resize_origin = (event.globalPosition().toPoint(), self.width(), self.height())
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif self._minimal_mode or pos.y() <= 48:
                if not start_native_window_drag(self):
                    self._drag_origin = (event.globalPosition().toPoint(), self.pos())
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._click_through:
            return
        pos = event.position().toPoint()
        if self._resize_origin is not None:
            g0, w0, h0 = self._resize_origin
            delta = event.globalPosition().toPoint() - g0
            self.resize(max(260, w0 + delta.x()), max(72, h0 + delta.y()))
        elif self._drag_origin is not None:
            g0, p0 = self._drag_origin
            self.move(p0 + event.globalPosition().toPoint() - g0)
        elif self._minimal_mode and pos.x() >= self.width() - 22 and pos.y() >= self.height() - 22:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_origin = None
        self._resize_origin = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint_panel_surface(
            p,
            QRectF(self.rect()),
            radius=11.0,
            active_border=self._recording,
            border_hex=ACCENT_PRIMARY if self._recording else "",
            bg_hex=self._capsule_bg_color,
        )
        if self._minimal_mode and not self._click_through:
            p.setPen(QPen(QColor(TEXT_MUTED), 1.4))
            w, h = self.width(), self.height()
            p.drawLine(w - 13, h - 5, w - 5, h - 13)
            p.drawLine(w - 8, h - 5, w - 5, h - 8)
