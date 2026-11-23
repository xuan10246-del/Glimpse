# -*- coding: utf-8 -*-
"""Glimpse (瞬译) v3.1 Main Application Window (Quiet Studio Slate 6-Tab Studio UI)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QPoint, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    API_PROVIDER_PRESETS,
    ASR_LANG_CHOICES,
    DISPLAY_MODES,
    ICON_PNG_PATH,
    INPLACE_STYLES,
    OCR_LANG_CHOICES,
    OUTPUT_LANG_CHOICES,
    OUTPUT_TONE_CHOICES,
    make_default_detached_style,
)
from ..model_manager import (
    ASR_BACKEND_CATALOG,
    LOCAL_LLM_CATALOG,
    has_vulkan_runtime,
    is_asr_model_installed,
    is_llm_model_installed,
)
from ..win32_utils import set_exclude_from_capture
from .icons import make_icon
from .theme import (
    ACCENT_PRIMARY,
    BG_CANVAS,
    BG_CONTROL,
    BG_HOVER,
    BG_RAIL,
    BG_SURFACE,
    BORDER_HAIRLINE,
    BORDER_SUBTLE,
    STATUS_ERR,
    STATUS_OK,
    STATUS_WARN,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    AudioWaveWidget,
    HorizontalHairline,
    StatusBeacon,
    apply_rounded_mask,
    paint_panel_surface,
    start_native_window_drag,
)

# Scoped structural styles only — let global APP_QSS style controls, inputs, and checkbox ticks
_SCOPED_PANEL_QSS = f"""
#StudioScroll, #StudioScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
#StudioRail {{
    background-color: {BG_RAIL};
    border-right: 1px solid {BORDER_SUBTLE};
}}
#StudioHeader {{
    background: transparent;
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
#StudioCard {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 10px;
}}
#StudioSubRow {{
    background-color: {BG_CONTROL};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: 8px;
}}
QProgressBar {{
    background-color: {BG_CONTROL};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {ACCENT_PRIMARY};
    border-radius: 3px;
}}
"""


class NoWheelSlider(QSlider):
    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: Optional[QWidget] = None):
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelComboBox(QComboBox):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:
        event.ignore()


class HotkeyCaptureEdit(QLineEdit):
    """Focus and press modifier+key combination to record global shortcut."""

    hotkey_captured = Signal(str)

    def __init__(self, value: str = "", parent: Optional[QWidget] = None):
        super().__init__(value, parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPlaceholderText("点击后按下组合键")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (
            Qt.Key.Key_unknown,
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            return
        if key == Qt.Key.Key_Escape:
            self.clearFocus()
            return

        mods = event.modifiers()
        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")

        key_name = ""
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            key_name = f"F{key - Qt.Key.Key_F1 + 1}"
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            key_name = chr(key)
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            key_name = chr(key)
        elif key == Qt.Key.Key_Space:
            key_name = "Space"
        else:
            txt = (event.text() or "").strip().upper()
            if txt and len(txt) == 1 and txt.isalnum():
                key_name = txt

        if not parts or not key_name:
            return

        combo = "+".join(parts + [key_name])
        self.setText(combo)
        self.hotkey_captured.emit(combo)
        self.clearFocus()


class ColorSwatchButton(QPushButton):
    """Compact hex color picker button showing rounded swatch + hex text."""

    color_changed = Signal(str)

    def __init__(self, hex_color: str = "#F1F3F5", title: str = "选择颜色", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._hex = str(hex_color or "#F1F3F5").upper()
        self._dialog_title = title
        self.setFixedHeight(30)
        self.setMinimumWidth(98)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_visual()
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._hex

    def setColor(self, hex_color: str) -> None:
        clean = str(hex_color or "#F1F3F5").strip()
        if not clean.startswith("#"):
            clean = "#" + clean
        qc = QColor(clean)
        if qc.isValid():
            self._hex = qc.name().upper()
            self._refresh_visual()

    def _refresh_visual(self) -> None:
        pm = QPixmap(14, 14)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(self._hex))
        p.setPen(QColor(255, 255, 255, 72))
        p.drawRoundedRect(1, 1, 12, 12, 3, 3)
        p.end()
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(14, 14))
        self.setText(self._hex)

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._hex), self, self._dialog_title)
        if chosen.isValid():
            self._hex = chosen.name().upper()
            self._refresh_visual()
            self.color_changed.emit(self._hex)


class StudioCard(QFrame):
    """Quiet Studio Slate card container."""

    def __init__(self, title: str = "", subtitle: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("StudioCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(10)

        if title:
            hdr = QVBoxLayout()
            hdr.setSpacing(2)
            lbl_t = QLabel(title, self)
            lbl_t.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 13.5px; font-weight: bold; background: transparent;")
            hdr.addWidget(lbl_t)
            if subtitle:
                lbl_s = QLabel(subtitle, self)
                lbl_s.setWordWrap(True)
                lbl_s.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11.5px; background: transparent;")
                hdr.addWidget(lbl_s)
            self.body.addLayout(hdr)


class NavButton(QPushButton):
    """Left rail navigation button."""

    def __init__(self, icon_name: str, label: str, parent: Optional[QWidget] = None):
        super().__init__(label, parent)
        self._icon_name = icon_name
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(37)
        self.setIconSize(QSize(16, 16))
        self._sync_style()
        self.toggled.connect(lambda _c: self._sync_style())

    def _sync_style(self) -> None:
        active = self.isChecked()
        ic_color = "#FFFFFF" if active else TEXT_SECONDARY
        self.setIcon(make_icon(self._icon_name, ic_color, 16))
        if active:
            self.setStyleSheet(
                f"QPushButton {{"
                f"text-align: left; padding: 0 12px; border-radius: 7px;"
                f"background-color: rgba(59, 130, 246, 0.20);"
                f"color: #FFFFFF; font-weight: bold;"
                f"border: 1px solid rgba(59, 130, 246, 0.48);"
                f"}}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{"
                f"text-align: left; padding: 0 12px; border-radius: 7px;"
                f"background-color: transparent; color: {TEXT_SECONDARY}; font-weight: normal;"
                f"border: 1px solid transparent;"
                f"}}"
                f"QPushButton:hover {{"
                f"background-color: {BG_HOVER}; color: {TEXT_PRIMARY};"
                f"}}"
            )


class ApiModelHelpDialog(QDialog):
    """Clean Frameless dialog explaining how to fill Cloud API model names."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setFixedSize(490, 340)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("云端 API 模型名称填写提示", self)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: bold; background: transparent;")
        btn_close = QPushButton(make_icon("close", TEXT_SECONDARY, 14), "", self)
        btn_close.setFixedSize(26, 26)
        btn_close.setProperty("ghost", True)
        btn_close.clicked.connect(self.accept)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(btn_close)
        root.addLayout(top)

        root.addWidget(HorizontalHairline(self))

        info = QLabel(
            f"<div style='color:{TEXT_PRIMARY}; line-height:1.65; font-size:12px;'>"
            "<p style='margin:0 0 8px 0;'>请在「模型名」输入框填写服务商开放平台提供的正式 <b>Model ID</b>：</p>"
            f"<p style='margin:0 0 5px 0;'>• <b>DeepSeek：</b><code style='color:#60A5FA;'>deepseek-flash</code>（高速低延迟）或平台当前提供的正式模型 ID</p>"
            f"<p style='margin:0 0 5px 0;'>• <b>阿里云百炼：</b><code style='color:#60A5FA;'>qwen-mt-turbo</code> / <code>qwen-plus</code></p>"
            f"<p style='margin:0 0 5px 0;'>• <b>硅基流动：</b><code style='color:#60A5FA;'>Qwen/Qwen2.5-7B-Instruct</code></p>"
            f"<p style='margin:0 0 5px 0;'>• <b>Google Gemini：</b><code style='color:#60A5FA;'>gemini-2.5-flash</code></p>"
            f"<p style='margin:0 0 10px 0;'>• <b>OpenAI：</b><code style='color:#60A5FA;'>gpt-4o-mini</code></p>"
            f"<p style='margin:0; color:{TEXT_SECONDARY};'>填写 Base URL、API Key 与模型名后，可点击「测试连通性」立即验证是否可用。</p>"
            "</div>",
            self,
        )
        info.setWordWrap(True)
        root.addWidget(info, 1)

        btn_ok = QPushButton("我知道了", self)
        btn_ok.setProperty("accent", True)
        btn_ok.setFixedWidth(108)
        btn_ok.clicked.connect(self.accept)
        bot = QHBoxLayout()
        bot.addStretch(1)
        bot.addWidget(btn_ok)
        root.addLayout(bot)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint_panel_surface(
            p, QRectF(self.rect()), radius=12.0, active_border=True, bg_hex=BG_CANVAS
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        apply_rounded_mask(self, 12.0)


def _scroll_page() -> tuple[QScrollArea, QVBoxLayout]:
    sa = QScrollArea()
    sa.setObjectName("StudioScroll")
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(20, 14, 20, 22)
    lay.setSpacing(14)
    sa.setWidget(inner)
    return sa, lay


def _make_slider_row(
    label: str,
    minimum: int,
    maximum: int,
    value: int,
    fmt_fn,
) -> tuple[QHBoxLayout, NoWheelSlider, QLabel]:
    row = QHBoxLayout()
    row.setSpacing(10)
    lbl_name = QLabel(label)
    lbl_name.setFixedWidth(128)
    lbl_name.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent;")
    slider = NoWheelSlider(Qt.Orientation.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(value)
    lbl_val = QLabel(fmt_fn(value))
    lbl_val.setFixedWidth(68)
    lbl_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl_val.setStyleSheet(f"color: {TEXT_PRIMARY}; font-family: Consolas, monospace; font-size: 12px; background: transparent;")
    slider.valueChanged.connect(lambda v: lbl_val.setText(fmt_fn(v)))
    row.addWidget(lbl_name)
    row.addWidget(slider, 1)
    row.addWidget(lbl_val)
    return row, slider, lbl_val


class ControlPanelWindow(QWidget):
    """True Desktop Main Application Window for Glimpse (瞬译) v3.1."""

    # Main Window / Lifecycle
    start_translation_requested = Signal()
    close_translation_requested = Signal()
    quit_app_requested = Signal()
    self_check_requested = Signal()
    remember_window_pos_changed = Signal(bool)

    # Page 1: Regions, Modes & OCR
    add_region_requested = Signal()
    remove_region_requested = Signal(str)
    region_lang_changed = Signal(str, str)
    region_enabled_changed = Signal(str, bool)
    region_box_visible_changed = Signal(str, bool)
    region_smart_clean_changed = Signal(str, bool)
    region_locate_requested = Signal(str)
    region_detach_toggled = Signal(str)
    merge_all_requested = Signal()
    detach_all_requested = Signal()
    show_all_boxes_requested = Signal(bool)
    ocr_tuning_changed = Signal(float, float)
    ocr_filter_chinese_changed = Signal(bool)
    display_mode_changed = Signal(str)
    exclude_capture_changed = Signal(bool)
    stealth_settings_changed = Signal(str, str, str)  # hotkey, trigger_mode, position
    inplace_style_changed = Signal(str)

    # Page 2: Models & Engine
    tier_preset_requested = Signal(str)
    game_mode_changed = Signal(bool)
    local_engine_apply_requested = Signal(str, str, str)  # llama_backend, model_id, custom_path
    api_engine_apply_requested = Signal(str, str, str, str)  # provider, base_url, api_key, model
    api_test_requested = Signal(str, str, str)  # base_url, api_key, model
    download_asset_requested = Signal(str, str)  # kind, asset_id
    asr_backend_changed = Signal(str)

    # Page 3: Audio & ASR
    asr_enabled_changed = Signal(bool)
    audio_mode_changed = Signal(str)
    target_process_changed = Signal(int, str)
    refresh_apps_requested = Signal()
    pick_window_requested = Signal()
    audio_device_changed = Signal(str)
    refresh_devices_requested = Signal()
    mic_device_changed = Signal(str)
    refresh_mics_requested = Signal()
    asr_lang_changed = Signal(str)
    asr_tuning_changed = Signal(float, float)

    # Page 4: Outbound Quick Capsule
    quick_settings_changed = Signal(str, str, bool, str, str, bool, bool)
    unlock_hotkey_changed = Signal(str)
    capsule_minimal_changed = Signal(bool)
    capsule_style_changed = Signal(float, str, int, str)  # opacity, bg_color, font_size, font_color
    open_quick_capsule_requested = Signal()

    # Page 5: Global & Per-Detached Appearance
    global_appearance_changed = Signal(dict)
    detached_appearance_changed = Signal(str, dict)
    show_author_changed = Signal(bool)
    unlock_clickthrough_requested = Signal()
    reset_positions_requested = Signal()
    geometry_changed = Signal(list)

    _TAB_META = [
        ("home", "控制中心", "启动控制、模型预加载进度、状态自检与操作指引"),
        ("layers", "画面与模式", "取景区域管理、隐藏/原位覆盖模式与屏幕文字检测灵敏度"),
        ("cpu", "模型与引擎", "档位预设、本地离线模型下载与云端 OpenAI 兼容 API"),
        ("mic", "语音与隔离", "系统声卡监听、独立软件进程音频隔离与实时语音识别"),
        ("keyboard", "双向快翻", "游戏快捷语音/文字输出转译、自动复制与胶囊独立外观"),
        ("palette", "外观与窗口", "全局面板/译文/原文描边以及各独立浮窗专属外观设定"),
    ]

    def __init__(self, cfg: dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cfg = copy.deepcopy(cfg)
        self._exclude_capture = bool(cfg.get("exclude_from_capture", True))
        self._boot_ready = False
        self._trans_active = False
        self._suppress_geom_emit = False
        self._resizing = False
        self._resize_origin_pos = QPoint()
        self._resize_origin_size = QSize()
        self._detached_styles_cache: dict[str, dict[str, Any]] = {}
        self._tier_btns: dict[str, QPushButton] = {}
        self._mode_btns: dict[str, QPushButton] = {}

        self.setWindowTitle("Glimpse · 瞬译")
        if ICON_PNG_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PNG_PATH)))

        # Real taskbar window: NO Tool, NO WindowStaysOnTopHint, NO WA_TranslucentBackground
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setMinimumSize(880, 610)
        self.resize(960, 675)
        self.setStyleSheet(_SCOPED_PANEL_QSS)

        self._build_shell()
        self._build_page_home()
        self._build_page_screen()
        self._build_page_models()
        self._build_page_audio()
        self._build_page_capsule()
        self._build_page_appearance()

        self.sync_from_config(self._cfg)
        self._switch_page(0)

        self._vram_timer = QTimer(self)
        self._vram_timer.setInterval(2000)
        self._vram_timer.timeout.connect(self._poll_vram)
        self._vram_timer.start()

    # ------------------------------------------------------------------ Shell
    def _build_shell(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._rail = QWidget(self)
        self._rail.setObjectName("StudioRail")
        self._rail.setFixedWidth(196)
        rail_lay = QVBoxLayout(self._rail)
        rail_lay.setContentsMargins(14, 16, 14, 14)
        rail_lay.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        lbl_ico = QLabel(self._rail)
        lbl_ico.setFixedSize(28, 28)
        if ICON_PNG_PATH.exists():
            pm = QPixmap(str(ICON_PNG_PATH)).scaled(
                28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            lbl_ico.setPixmap(pm)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        lbl_brand = QLabel("Glimpse 瞬译", self._rail)
        lbl_brand.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: bold; background: transparent;")
        lbl_ver = QLabel("桌面工作台 v3.1", self._rail)
        lbl_ver.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        title_col.addWidget(lbl_brand)
        title_col.addWidget(lbl_ver)
        brand.addWidget(lbl_ico)
        brand.addLayout(title_col, 1)
        rail_lay.addLayout(brand)
        rail_lay.addSpacing(10)

        self._nav_buttons: list[NavButton] = []
        for idx, (ic_name, title, _sub) in enumerate(self._TAB_META):
            btn = NavButton(ic_name, f"  {title}", self._rail)
            btn.clicked.connect(lambda _c, i=idx: self._switch_page(i))
            rail_lay.addWidget(btn)
            self._nav_buttons.append(btn)

        rail_lay.addStretch(1)

        tel_box = QFrame(self._rail)
        tel_box.setObjectName("StudioSubRow")
        tlay = QVBoxLayout(tel_box)
        tlay.setContentsMargins(10, 8, 10, 8)
        tlay.setSpacing(4)
        st_row = QHBoxLayout()
        st_row.setSpacing(6)
        self._rail_beacon = StatusBeacon(tel_box)
        self._lbl_rail_status = QLabel("正在初始化", tel_box)
        self._lbl_rail_status.setStyleSheet(f"color: {STATUS_WARN}; font-size: 11.5px; font-weight: bold; background: transparent;")
        st_row.addWidget(self._rail_beacon)
        st_row.addWidget(self._lbl_rail_status, 1)
        tlay.addLayout(st_row)
        self._lbl_rail_engines = QLabel("LLM: 初始化中\nASR: 初始化中", tel_box)
        self._lbl_rail_engines.setWordWrap(True)
        self._lbl_rail_engines.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10.5px; background: transparent;")
        tlay.addWidget(self._lbl_rail_engines)
        rail_lay.addWidget(tel_box)
        outer.addWidget(self._rail)

        # Right Main Column
        right = QWidget(self)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        self._header = QWidget(right)
        self._header.setObjectName("StudioHeader")
        self._header.setFixedHeight(58)
        hdr_lay = QHBoxLayout(self._header)
        hdr_lay.setContentsMargins(20, 8, 16, 8)
        hdr_lay.setSpacing(10)

        hdr_text = QVBoxLayout()
        hdr_text.setSpacing(1)
        self._lbl_page_title = QLabel("控制中心", self._header)
        self._lbl_page_title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 15px; font-weight: bold; background: transparent;")
        self._lbl_page_sub = QLabel("", self._header)
        self._lbl_page_sub.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11.5px; background: transparent;")
        hdr_text.addWidget(self._lbl_page_title)
        hdr_text.addWidget(self._lbl_page_sub)
        hdr_lay.addLayout(hdr_text, 1)

        btn_min = QPushButton(make_icon("minimize", TEXT_SECONDARY, 14), " 最小化到任务栏", self._header)
        btn_min.setProperty("compact", True)
        btn_min.clicked.connect(self.showMinimized)

        btn_quit = QPushButton(make_icon("power", "#F48387", 14), " 退出软件", self._header)
        btn_quit.setProperty("compact", True)
        btn_quit.setProperty("danger", True)
        btn_quit.clicked.connect(self.quit_app_requested.emit)

        hdr_lay.addWidget(btn_min)
        hdr_lay.addWidget(btn_quit)
        right_lay.addWidget(self._header)

        self._pages = QStackedWidget(right)
        right_lay.addWidget(self._pages, 1)
        outer.addWidget(right, 1)

    def _switch_page(self, index: int) -> None:
        idx = max(0, min(index, len(self._TAB_META) - 1))
        self._pages.setCurrentIndex(idx)
        for i, b in enumerate(self._nav_buttons):
            with QSignalBlocker(b):
                b.setChecked(i == idx)
            b._sync_style()
        _ic, title, sub = self._TAB_META[idx]
        self._lbl_page_title.setText(title)
        self._lbl_page_sub.setText(sub)

    def switch_tab(self, index: int) -> None:
        self._switch_page(index)

    # ------------------------------------------------------------------ Tab 0: 控制中心
    def _build_page_home(self) -> None:
        sa, lay = _scroll_page()

        hero = StudioCard(
            "翻译服务控制台",
            "模型预加载完成后即可点击「开始翻译」打开实时译文与取景窗口；译文窗右上角 × 仅关闭翻译界面，可随时在此重新开启。",
        )
        self._lbl_boot_status = QLabel("正在初始化本地组件与引擎...", hero)
        self._lbl_boot_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        hero.body.addWidget(self._lbl_boot_status)

        self._boot_bar = QProgressBar(hero)
        self._boot_bar.setRange(0, 100)
        self._boot_bar.setValue(0)
        hero.body.addWidget(self._boot_bar)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._btn_start_trans = QPushButton(make_icon("play", "#FFFFFF", 14), " 开始翻译（模型加载中...）", hero)
        self._btn_start_trans.setProperty("accent", True)
        self._btn_start_trans.setFixedHeight(38)
        self._btn_start_trans.setMinimumWidth(205)
        self._btn_start_trans.setEnabled(False)
        self._btn_start_trans.clicked.connect(self._on_start_or_close_clicked)

        self._btn_home_capsule = QPushButton(make_icon("bolt", STATUS_OK, 14), " 打开双向快翻", hero)
        self._btn_home_capsule.setFixedHeight(38)
        self._btn_home_capsule.clicked.connect(self.open_quick_capsule_requested.emit)

        self._btn_self_check = QPushButton(make_icon("activity", TEXT_PRIMARY, 14), " 功能自检", hero)
        self._btn_self_check.setFixedHeight(38)
        self._btn_self_check.clicked.connect(self.self_check_requested.emit)

        btn_row.addWidget(self._btn_start_trans)
        btn_row.addWidget(self._btn_home_capsule)
        btn_row.addWidget(self._btn_self_check)
        btn_row.addStretch(1)
        hero.body.addLayout(btn_row)

        self._lbl_home_engines = QLabel("当前引擎：准备中", hero)
        self._lbl_home_engines.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11.5px; background: transparent;")
        hero.body.addWidget(self._lbl_home_engines)

        self._chk_remember_pos = QCheckBox("记忆上次翻译窗口与取景框位置（关闭或重启后自动恢复上次摆放位置）", hero)
        self._chk_remember_pos.toggled.connect(self.remember_window_pos_changed.emit)
        hero.body.addWidget(self._chk_remember_pos)
        lay.addWidget(hero)

        check_card = StudioCard("当前功能自检状态", "一键核对画面 OCR、翻译模型、语音通道、防自播隔离与热键状态。")
        self._self_check_container = QVBoxLayout()
        self._self_check_container.setSpacing(6)
        hint = QLabel("点击上方「功能自检」可立即检查各核心模块运行状态。", check_card)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;")
        self._self_check_container.addWidget(hint)
        check_card.body.addLayout(self._self_check_container)
        lay.addWidget(check_card)

        guide = StudioCard("快速上手说明")
        guide_lbl = QLabel(
            f"<div style='color:{TEXT_SECONDARY}; line-height:1.68; font-size:12px;'>"
            f"1. <b style='color:{TEXT_PRIMARY};'>开始使用：</b>等待上方进度条完成并点击「开始翻译」，将取景框拖至需要翻译的外语画面上方。<br>"
            f"2. <b style='color:{TEXT_PRIMARY};'>隐身与原位覆盖：</b>可在「画面与模式」切换「隐藏当前窗口」（热键 <code>Alt+Q</code>）或「原位覆盖」。<br>"
            f"3. <b style='color:{TEXT_PRIMARY};'>独立浮窗专属外观：</b>拆出任意「独立浮窗」后，可在「外观与窗口」分别为每个浮窗单独调整底色、不透明度及文字描边。<br>"
            f"4. <b style='color:{TEXT_PRIMARY};'>双向快翻与最小化：</b><code>Alt+T</code> 打开快翻、<code>Alt+V</code> 语音输入；主界面右上角可最小化到任务栏或彻底退出软件。"
            "</div>",
            guide,
        )
        guide_lbl.setWordWrap(True)
        guide.body.addWidget(guide_lbl)
        lay.addWidget(guide)

        lay.addStretch(1)
        self._pages.addWidget(sa)

    def _on_start_or_close_clicked(self) -> None:
        if not self._boot_ready:
            return
        if self._trans_active:
            self.close_translation_requested.emit()
        else:
            self.start_translation_requested.emit()

    # ------------------------------------------------------------------ Tab 1: 画面与模式
    def _build_page_screen(self) -> None:
        sa, lay = _scroll_page()

        c_reg = StudioCard("屏幕取景区域与频道", "每个取景框对应一条翻译通道，支持独立源语言、拆分为独立浮窗或显隐切换。")
        top_btns = QHBoxLayout()
        btn_add = QPushButton(make_icon("plus", "#FFFFFF", 13), " 新增取景框", c_reg)
        btn_add.setProperty("accent", True)
        btn_add.clicked.connect(self.add_region_requested.emit)
        btn_show_boxes = QPushButton("显示全部框", c_reg)
        btn_show_boxes.clicked.connect(lambda: self.show_all_boxes_requested.emit(True))
        btn_hide_boxes = QPushButton("隐藏全部框", c_reg)
        btn_hide_boxes.clicked.connect(lambda: self.show_all_boxes_requested.emit(False))
        btn_merge_all = QPushButton("全部归位", c_reg)
        btn_merge_all.clicked.connect(self.merge_all_requested.emit)
        btn_detach_all = QPushButton("全部拆出", c_reg)
        btn_detach_all.clicked.connect(self.detach_all_requested.emit)
        top_btns.addWidget(btn_add)
        top_btns.addWidget(btn_show_boxes)
        top_btns.addWidget(btn_hide_boxes)
        top_btns.addWidget(btn_merge_all)
        top_btns.addWidget(btn_detach_all)
        top_btns.addStretch(1)
        c_reg.body.addLayout(top_btns)

        self._regions_box = QVBoxLayout()
        self._regions_box.setSpacing(7)
        c_reg.body.addLayout(self._regions_box)
        lay.addWidget(c_reg)

        c_mode = StudioCard("显示模式、隐身热键与防自播")
        seg_row = QHBoxLayout()
        seg_row.setSpacing(6)
        for mk, mlabel in DISPLAY_MODES:
            mb = QPushButton(mlabel, c_mode)
            mb.clicked.connect(lambda _c, k=mk: self.display_mode_changed.emit(k))
            self._mode_btns[mk] = mb
            seg_row.addWidget(mb)
        seg_row.addStretch(1)
        btn_unlock = QPushButton("一键解除穿透锁定", c_mode)
        btn_unlock.clicked.connect(self.unlock_clickthrough_requested.emit)
        seg_row.addWidget(btn_unlock)
        c_mode.body.addLayout(seg_row)

        self._chk_exclude_cap = QCheckBox("开启防自播捕获（OCR 取词框与录屏软件自动无视浮窗内容，推荐开启）", c_mode)
        self._chk_exclude_cap.toggled.connect(self.exclude_capture_changed.emit)
        c_mode.body.addWidget(self._chk_exclude_cap)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)

        grid.addWidget(QLabel("隐藏/呼出热键：", c_mode), 0, 0)
        self._hk_stealth = HotkeyCaptureEdit("", c_mode)
        self._hk_stealth.hotkey_captured.connect(lambda _k: self._emit_stealth_settings())
        grid.addWidget(self._hk_stealth, 0, 1)

        grid.addWidget(QLabel("热键触发方式：", c_mode), 1, 0)
        self._cb_stealth_trig = NoWheelComboBox(c_mode)
        self._cb_stealth_trig.addItem("按键切换显示 / 隐藏", "toggle")
        self._cb_stealth_trig.addItem("仅按住热键时临时显示", "hold")
        self._cb_stealth_trig.currentIndexChanged.connect(lambda _i: self._emit_stealth_settings())
        grid.addWidget(self._cb_stealth_trig, 1, 1)

        grid.addWidget(QLabel("呼出出现位置：", c_mode), 2, 0)
        self._cb_stealth_pos = NoWheelComboBox(c_mode)
        self._cb_stealth_pos.addItem("自动吸附在取景框上方", "above_region")
        self._cb_stealth_pos.addItem("保持上次记忆位置", "dock")
        self._cb_stealth_pos.currentIndexChanged.connect(lambda _i: self._emit_stealth_settings())
        grid.addWidget(self._cb_stealth_pos, 2, 1)

        grid.addWidget(QLabel("原位覆盖风格：", c_mode), 3, 0)
        self._cb_inplace_style = NoWheelComboBox(c_mode)
        for key, label in INPLACE_STYLES:
            self._cb_inplace_style.addItem(label, key)
        self._cb_inplace_style.currentIndexChanged.connect(
            lambda _i: self.inplace_style_changed.emit(str(self._cb_inplace_style.currentData() or "blend"))
        )
        grid.addWidget(self._cb_inplace_style, 3, 1)
        c_mode.body.addLayout(grid)

        self._lbl_hotkey_status = QLabel("", c_mode)
        self._lbl_hotkey_status.setWordWrap(True)
        self._lbl_hotkey_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11.5px; background: transparent;")
        c_mode.body.addWidget(self._lbl_hotkey_status)
        lay.addWidget(c_mode)

        c_ocr = StudioCard("屏幕文字识别（OCR）灵敏度")
        r1, self._sl_ocr_int, _ = _make_slider_row("轮询间隔", 15, 150, 28, lambda v: f"{v / 100.0:.2f}s")
        self._sl_ocr_int.valueChanged.connect(lambda _v: self._emit_ocr_tuning())
        c_ocr.body.addLayout(r1)

        r2, self._sl_ocr_diff, _ = _make_slider_row("画面变化阈值", 5, 80, 16, lambda v: f"{v / 10.0:.1f}")
        self._sl_ocr_diff.valueChanged.connect(lambda _v: self._emit_ocr_tuning())
        c_ocr.body.addLayout(r2)

        self._chk_ocr_filter_zh = QCheckBox("忽略纯中文画面（开启后检测到纯中文内容将跳过翻译）", c_ocr)
        self._chk_ocr_filter_zh.toggled.connect(self.ocr_filter_chinese_changed.emit)
        c_ocr.body.addWidget(self._chk_ocr_filter_zh)
        lay.addWidget(c_ocr)

        lay.addStretch(1)
        self._pages.addWidget(sa)

    # ------------------------------------------------------------------ Tab 2: 模型与引擎
    def _build_page_models(self) -> None:
        sa, lay = _scroll_page()

        c_tier = StudioCard("快速方案档位", "按本地硬件配置一键搭配最适合的翻译与语音引擎。")
        tier_row = QHBoxLayout()
        tier_row.setSpacing(8)
        for tid, tlabel in (
            ("precision_7b", "大显存高精度 (7B)"),
            ("balanced", "通用均衡 (1.8B Q8)"),
            ("game_light", "游戏低占极速"),
            ("cloud_api", "云端 API 零显存"),
        ):
            tb = QPushButton(tlabel, c_tier)
            tb.clicked.connect(lambda _c, k=tid: self.tier_preset_requested.emit(k))
            self._tier_btns[tid] = tb
            tier_row.addWidget(tb)
        c_tier.body.addLayout(tier_row)

        self._chk_game_mode = QCheckBox("开启游戏极速低占模式（限制线程并降低显存占用，适合高负载游戏同开）", c_tier)
        self._chk_game_mode.toggled.connect(self.game_mode_changed.emit)
        c_tier.body.addWidget(self._chk_game_mode)
        lay.addWidget(c_tier)

        c_loc = StudioCard("本地离线大模型（llama.cpp GGUF）")
        lgrid = QGridLayout()
        lgrid.setHorizontalSpacing(10)
        lgrid.setVerticalSpacing(9)

        lgrid.addWidget(QLabel("算力后端：", c_loc), 0, 0)
        self._cb_llama_backend = NoWheelComboBox(c_loc)
        for lbl, key in (
            ("自动探测 (CUDA → Vulkan → CPU)", "auto"),
            ("NVIDIA CUDA 显卡加速", "cuda"),
            ("Vulkan 通用显卡加速", "vulkan"),
            ("纯 CPU 推理", "cpu"),
        ):
            self._cb_llama_backend.addItem(lbl, key)
        btn_dl_vulkan = QPushButton("下载 Vulkan 运行时", c_loc)
        btn_dl_vulkan.clicked.connect(lambda: self.download_asset_requested.emit("vulkan", "llama_vulkan"))
        lgrid.addWidget(self._cb_llama_backend, 0, 1)
        lgrid.addWidget(btn_dl_vulkan, 0, 2)

        lgrid.addWidget(QLabel("本地模型：", c_loc), 1, 0)
        self._cb_local_model = NoWheelComboBox(c_loc)
        self._cb_local_model.currentIndexChanged.connect(lambda _i: self._update_local_model_hint())
        btn_dl_llm = QPushButton("下载选中模型", c_loc)
        btn_dl_llm.clicked.connect(
            lambda: self.download_asset_requested.emit("llm", str(self._cb_local_model.currentData() or "hy_mt_1_8b_q8"))
        )
        lgrid.addWidget(self._cb_local_model, 1, 1)
        lgrid.addWidget(btn_dl_llm, 1, 2)

        lgrid.addWidget(QLabel("自定义 GGUF：", c_loc), 2, 0)
        self._ed_custom_gguf = QLineEdit(c_loc)
        self._ed_custom_gguf.setPlaceholderText("可选：指定本地 .gguf 模型文件路径")
        btn_browse = QPushButton("浏览...", c_loc)
        btn_browse.clicked.connect(self._browse_gguf)
        lgrid.addWidget(self._ed_custom_gguf, 2, 1)
        lgrid.addWidget(btn_browse, 2, 2)
        c_loc.body.addLayout(lgrid)

        self._lbl_local_hint = QLabel("", c_loc)
        self._lbl_local_hint.setWordWrap(True)
        self._lbl_local_hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11.5px; background: transparent;")
        c_loc.body.addWidget(self._lbl_local_hint)

        apply_loc_row = QHBoxLayout()
        btn_apply_local = QPushButton("应用并切换为本地离线引擎", c_loc)
        btn_apply_local.setProperty("accent", True)
        btn_apply_local.clicked.connect(
            lambda: self.local_engine_apply_requested.emit(
                str(self._cb_llama_backend.currentData() or "auto"),
                str(self._cb_local_model.currentData() or "hy_mt_1_8b_q8"),
                self._ed_custom_gguf.text().strip(),
            )
        )
        apply_loc_row.addWidget(btn_apply_local)
        apply_loc_row.addStretch(1)
        c_loc.body.addLayout(apply_loc_row)
        lay.addWidget(c_loc)

        c_api = StudioCard("云端 OpenAI 兼容 API（无显卡 / 低内存首选）")
        agrid = QGridLayout()
        agrid.setHorizontalSpacing(10)
        agrid.setVerticalSpacing(9)

        agrid.addWidget(QLabel("预设服务商：", c_api), 0, 0)
        self._cb_api_provider = NoWheelComboBox(c_api)
        for key, (label, _u, _m) in API_PROVIDER_PRESETS.items():
            self._cb_api_provider.addItem(label, key)
        self._cb_api_provider.currentIndexChanged.connect(self._on_api_provider_changed)
        agrid.addWidget(self._cb_api_provider, 0, 1, 1, 2)

        agrid.addWidget(QLabel("Base URL：", c_api), 1, 0)
        self._ed_api_url = QLineEdit(c_api)
        agrid.addWidget(self._ed_api_url, 1, 1, 1, 2)

        agrid.addWidget(QLabel("API Key：", c_api), 2, 0)
        self._ed_api_key = QLineEdit(c_api)
        self._ed_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ed_api_key.setPlaceholderText("sk-...")
        agrid.addWidget(self._ed_api_key, 2, 1, 1, 2)

        agrid.addWidget(QLabel("模型名：", c_api), 3, 0)
        self._ed_api_model = QLineEdit(c_api)
        self._ed_api_model.setPlaceholderText("填写对应平台正式 Model ID，如 deepseek-flash")
        btn_help = QPushButton("?", c_api)
        btn_help.setFixedSize(28, 28)
        btn_help.setToolTip("查看常用云端服务商 Model ID 填写提示")
        btn_help.clicked.connect(lambda: ApiModelHelpDialog(self).exec())
        agrid.addWidget(self._ed_api_model, 3, 1)
        agrid.addWidget(btn_help, 3, 2)
        c_api.body.addLayout(agrid)

        api_btn_row = QHBoxLayout()
        btn_test_api = QPushButton("测试连通性", c_api)
        btn_test_api.clicked.connect(
            lambda: self.api_test_requested.emit(
                self._ed_api_url.text().strip(),
                self._ed_api_key.text().strip(),
                self._ed_api_model.text().strip(),
            )
        )
        btn_use_api = QPushButton("切换至云端 API", c_api)
        btn_use_api.setProperty("accent", True)
        btn_use_api.clicked.connect(
            lambda: self.api_engine_apply_requested.emit(
                str(self._cb_api_provider.currentData() or "custom"),
                self._ed_api_url.text().strip(),
                self._ed_api_key.text().strip(),
                self._ed_api_model.text().strip(),
            )
        )
        self._lbl_api_test = QLabel("", c_api)
        self._lbl_api_test.setWordWrap(True)
        self._lbl_api_test.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11.5px; background: transparent;")
        api_btn_row.addWidget(btn_test_api)
        api_btn_row.addWidget(btn_use_api)
        api_btn_row.addWidget(self._lbl_api_test, 1)
        c_api.body.addLayout(api_btn_row)
        lay.addWidget(c_api)

        c_asr_eng = StudioCard("语音识别引擎选择与资源下载")
        arow = QHBoxLayout()
        self._cb_asr_backend = NoWheelComboBox(c_asr_eng)
        btn_dl_asr = QPushButton("下载当前语音模型", c_asr_eng)
        btn_dl_asr.clicked.connect(
            lambda: self.download_asset_requested.emit("asr", str(self._cb_asr_backend.currentData() or "sensevoice"))
        )
        btn_apply_asr = QPushButton("应用语音引擎", c_asr_eng)
        btn_apply_asr.clicked.connect(
            lambda: self.asr_backend_changed.emit(str(self._cb_asr_backend.currentData() or "whisper"))
        )
        arow.addWidget(self._cb_asr_backend, 1)
        arow.addWidget(btn_dl_asr)
        arow.addWidget(btn_apply_asr)
        c_asr_eng.body.addLayout(arow)

        self._dl_bar = QProgressBar(c_asr_eng)
        self._dl_bar.setRange(0, 100)
        self._dl_bar.setVisible(False)
        self._lbl_dl_msg = QLabel("", c_asr_eng)
        self._lbl_dl_msg.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11.5px; background: transparent;")
        c_asr_eng.body.addWidget(self._dl_bar)
        c_asr_eng.body.addWidget(self._lbl_dl_msg)
        lay.addWidget(c_asr_eng)

        self._rebuild_catalog_combos()
        lay.addStretch(1)
        self._pages.addWidget(sa)

    # ------------------------------------------------------------------ Tab 3: 语音与隔离
    def _build_page_audio(self) -> None:
        sa, lay = _scroll_page()

        c_aud = StudioCard("音频监听与独立软件进程隔离", "锁定单个软件或游戏窗口音频，过滤掉其余系统通知与无关背景音。")
        top_row = QHBoxLayout()
        self._chk_asr_enabled = QCheckBox("启用实时语音同传字幕", c_aud)
        self._chk_asr_enabled.toggled.connect(self.asr_enabled_changed.emit)
        self.panel_wave = AudioWaveWidget(c_aud, height=20)
        top_row.addWidget(self._chk_asr_enabled)
        top_row.addStretch(1)
        top_row.addWidget(self.panel_wave)
        c_aud.body.addLayout(top_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)

        grid.addWidget(QLabel("采集方式：", c_aud), 0, 0)
        self._cb_audio_mode = NoWheelComboBox(c_aud)
        self._cb_audio_mode.addItem("独立软件进程隔离（仅抓选定程序音频）", "process")
        self._cb_audio_mode.addItem("声卡硬件环回输出（WASAPI Loopback）", "device")
        self._cb_audio_mode.currentIndexChanged.connect(
            lambda _i: self.audio_mode_changed.emit(str(self._cb_audio_mode.currentData() or "device"))
        )
        grid.addWidget(self._cb_audio_mode, 0, 1, 1, 3)

        grid.addWidget(QLabel("目标软件：", c_aud), 1, 0)
        self._cb_apps = NoWheelComboBox(c_aud)
        self._cb_apps.currentIndexChanged.connect(self._on_app_combo_changed)
        btn_ref_apps = QPushButton("刷新列表", c_aud)
        btn_ref_apps.clicked.connect(self.refresh_apps_requested.emit)
        btn_pick_win = QPushButton("点选屏幕窗口", c_aud)
        btn_pick_win.setProperty("accent", True)
        btn_pick_win.clicked.connect(self.pick_window_requested.emit)
        grid.addWidget(self._cb_apps, 1, 1)
        grid.addWidget(btn_ref_apps, 1, 2)
        grid.addWidget(btn_pick_win, 1, 3)

        grid.addWidget(QLabel("环回声卡：", c_aud), 2, 0)
        self._cb_devices = NoWheelComboBox(c_aud)
        self._cb_devices.currentIndexChanged.connect(
            lambda _i: self.audio_device_changed.emit(str(self._cb_devices.currentData() or ""))
        )
        btn_ref_dev = QPushButton("刷新声卡", c_aud)
        btn_ref_dev.clicked.connect(self.refresh_devices_requested.emit)
        grid.addWidget(self._cb_devices, 2, 1, 1, 2)
        grid.addWidget(btn_ref_dev, 2, 3)

        grid.addWidget(QLabel("源语言：", c_aud), 3, 0)
        self._cb_asr_lang = NoWheelComboBox(c_aud)
        for code, lbl in ASR_LANG_CHOICES:
            self._cb_asr_lang.addItem(lbl, code)
        self._cb_asr_lang.currentIndexChanged.connect(
            lambda _i: self.asr_lang_changed.emit(str(self._cb_asr_lang.currentData() or "auto"))
        )
        grid.addWidget(self._cb_asr_lang, 3, 1, 1, 3)
        c_aud.body.addLayout(grid)

        r_rms, self._sl_asr_rms, _ = _make_slider_row("触发音量阈值", 2, 40, 8, lambda v: f"{v / 1000.0:.3f}")
        self._sl_asr_rms.valueChanged.connect(lambda _v: self._emit_asr_tuning())
        c_aud.body.addLayout(r_rms)

        r_sil, self._sl_asr_sil, _ = _make_slider_row("静音断句时长", 20, 150, 45, lambda v: f"{v / 100.0:.2f}s")
        self._sl_asr_sil.valueChanged.connect(lambda _v: self._emit_asr_tuning())
        c_aud.body.addLayout(r_sil)

        lay.addWidget(c_aud)
        lay.addStretch(1)
        self._pages.addWidget(sa)

    # ------------------------------------------------------------------ Tab 4: 双向快翻
    def _build_page_capsule(self) -> None:
        sa, lay = _scroll_page()

        c_cap = StudioCard("双向快翻与游戏快捷输出", "按下唤出或按住说话热键，快速将你的中文想法译成外语并自动复制到剪贴板。")
        btn_open = QPushButton(make_icon("bolt", "#FFFFFF", 14), " 立即打开双向快翻胶囊", c_cap)
        btn_open.setProperty("accent", True)
        btn_open.clicked.connect(self.open_quick_capsule_requested.emit)
        c_cap.body.addWidget(btn_open)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)

        grid.addWidget(QLabel("文字快翻热键：", c_cap), 0, 0)
        self._hk_quick = HotkeyCaptureEdit("", c_cap)
        self._hk_quick.hotkey_captured.connect(lambda _k: self._emit_quick_settings())
        grid.addWidget(self._hk_quick, 0, 1, 1, 2)

        grid.addWidget(QLabel("语音输入热键：", c_cap), 1, 0)
        self._hk_voice_out = HotkeyCaptureEdit("", c_cap)
        self._hk_voice_out.hotkey_captured.connect(lambda _k: self._emit_quick_settings())
        grid.addWidget(self._hk_voice_out, 1, 1, 1, 2)

        grid.addWidget(QLabel("目标语言：", c_cap), 2, 0)
        self._cb_out_lang = NoWheelComboBox(c_cap)
        for code, lbl in OUTPUT_LANG_CHOICES:
            self._cb_out_lang.addItem(lbl, code)
        self._cb_out_lang.currentIndexChanged.connect(lambda _i: self._emit_quick_settings())
        grid.addWidget(self._cb_out_lang, 2, 1, 1, 2)

        grid.addWidget(QLabel("表达语气：", c_cap), 3, 0)
        self._cb_out_tone = NoWheelComboBox(c_cap)
        for code, lbl in OUTPUT_TONE_CHOICES:
            self._cb_out_tone.addItem(lbl, code)
        self._cb_out_tone.currentIndexChanged.connect(lambda _i: self._emit_quick_settings())
        grid.addWidget(self._cb_out_tone, 3, 1, 1, 2)

        grid.addWidget(QLabel("麦克风设备：", c_cap), 4, 0)
        self._cb_mics = NoWheelComboBox(c_cap)
        self._cb_mics.currentIndexChanged.connect(
            lambda _i: self.mic_device_changed.emit(str(self._cb_mics.currentData() or ""))
        )
        btn_ref_mic = QPushButton("刷新", c_cap)
        btn_ref_mic.clicked.connect(self.refresh_mics_requested.emit)
        grid.addWidget(self._cb_mics, 4, 1)
        grid.addWidget(btn_ref_mic, 4, 2)

        grid.addWidget(QLabel("一键解锁穿透热键：", c_cap), 5, 0)
        self._hk_unlock = HotkeyCaptureEdit("", c_cap)
        self._hk_unlock.hotkey_captured.connect(self.unlock_hotkey_changed.emit)
        grid.addWidget(self._hk_unlock, 5, 1, 1, 2)
        c_cap.body.addLayout(grid)

        self._chk_voice_hold = QCheckBox("按住语音热键期间录音（松开立即识别并翻译）", c_cap)
        self._chk_voice_hold.toggled.connect(lambda _b: self._emit_quick_settings())
        self._chk_out_autocopy = QCheckBox("译出后自动复制到系统剪贴板", c_cap)
        self._chk_out_autocopy.toggled.connect(lambda _b: self._emit_quick_settings())
        self._chk_out_autopaste = QCheckBox("译出后自动模拟 Ctrl+V 粘贴到当前焦点窗口", c_cap)
        self._chk_out_autopaste.toggled.connect(lambda _b: self._emit_quick_settings())
        self._chk_cap_minimal = QCheckBox("启用极简双语语音悬浮条（仅保留双语输出框并支持鼠标穿透）", c_cap)
        self._chk_cap_minimal.toggled.connect(self.capsule_minimal_changed.emit)
        c_cap.body.addWidget(self._chk_voice_hold)
        c_cap.body.addWidget(self._chk_out_autocopy)
        c_cap.body.addWidget(self._chk_out_autopaste)
        c_cap.body.addWidget(self._chk_cap_minimal)
        lay.addWidget(c_cap)

        # Capsule Appearance (keeps opacity + adds window bg_color + font size + font color)
        c_style = StudioCard("双向快翻窗口独立外观", "仅对双向快翻胶囊生效：支持设置窗口底色、窗口不透明度与字体样式。")
        crow = QHBoxLayout()
        crow.addWidget(QLabel("窗口底色：", c_style))
        self._btn_cap_bg = ColorSwatchButton(BG_CANVAS, "选择双向快翻窗口底色", c_style)
        self._btn_cap_bg.color_changed.connect(lambda _c: self._emit_capsule_style())
        crow.addWidget(self._btn_cap_bg)
        crow.addSpacing(16)
        crow.addWidget(QLabel("文字颜色：", c_style))
        self._btn_cap_fg = ColorSwatchButton(TEXT_PRIMARY, "选择双向快翻文字颜色", c_style)
        self._btn_cap_fg.color_changed.connect(lambda _c: self._emit_capsule_style())
        crow.addWidget(self._btn_cap_fg)
        crow.addStretch(1)
        c_style.body.addLayout(crow)

        rc1, self._sl_cap_op, _ = _make_slider_row("窗口不透明度", 20, 100, 90, lambda v: f"{v}%")
        self._sl_cap_op.valueChanged.connect(lambda _v: self._emit_capsule_style())
        c_style.body.addLayout(rc1)

        rc2, self._sl_cap_font, _ = _make_slider_row("字体大小", 11, 32, 15, lambda v: f"{v} px")
        self._sl_cap_font.valueChanged.connect(lambda _v: self._emit_capsule_style())
        c_style.body.addLayout(rc2)
        lay.addWidget(c_style)

        lay.addStretch(1)
        self._pages.addWidget(sa)

    # ------------------------------------------------------------------ Tab 5: 外观与窗口
    def _build_page_appearance(self) -> None:
        sa, lay = _scroll_page()

        c_glob = StudioCard(
            "全局译文浮窗外观",
            "统一控制底部翻译面板及所有“未开启单独设置”的独立浮窗，使用平滑路径描边保证高对比可读性。",
        )
        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("面板底色：", c_glob))
        self._btn_gl_bg = ColorSwatchButton(BG_CANVAS, "选择全局面板底色", c_glob)
        self._btn_gl_bg.color_changed.connect(lambda _c: self._emit_global_appearance())
        bg_row.addWidget(self._btn_gl_bg)
        bg_row.addStretch(1)
        c_glob.body.addLayout(bg_row)

        rg_op, self._sl_gl_op, _ = _make_slider_row("窗口不透明度", 25, 100, 94, lambda v: f"{v}%")
        self._sl_gl_op.valueChanged.connect(lambda _v: self._emit_global_appearance())
        c_glob.body.addLayout(rg_op)

        c_glob.body.addWidget(self._section_tag("译文文字与描边", c_glob))
        tr_row = QHBoxLayout()
        tr_row.addWidget(QLabel("译文颜色：", c_glob))
        self._btn_gl_tr_fg = ColorSwatchButton(TEXT_PRIMARY, "选择译文颜色", c_glob)
        self._btn_gl_tr_fg.color_changed.connect(lambda _c: self._emit_global_appearance())
        tr_row.addWidget(self._btn_gl_tr_fg)
        tr_row.addSpacing(14)
        tr_row.addWidget(QLabel("描边颜色：", c_glob))
        self._btn_gl_tr_sc = ColorSwatchButton("#000000", "选择译文描边颜色", c_glob)
        self._btn_gl_tr_sc.color_changed.connect(lambda _c: self._emit_global_appearance())
        tr_row.addWidget(self._btn_gl_tr_sc)
        tr_row.addStretch(1)
        c_glob.body.addLayout(tr_row)

        rg_ts, self._sl_gl_tr_sz, _ = _make_slider_row("译文字号", 11, 32, 15, lambda v: f"{v} px")
        self._sl_gl_tr_sz.valueChanged.connect(lambda _v: self._emit_global_appearance())
        c_glob.body.addLayout(rg_ts)

        rg_tst, self._sl_gl_tr_st, _ = _make_slider_row("译文描边", 0, 4, 0, lambda v: f"{v} px")
        self._sl_gl_tr_st.valueChanged.connect(lambda _v: self._emit_global_appearance())
        c_glob.body.addLayout(rg_tst)

        c_glob.body.addWidget(self._section_tag("原文对照与描边", c_glob))
        self._chk_show_orig = QCheckBox("在译文下方保留外语原文对照", c_glob)
        self._chk_show_orig.toggled.connect(lambda _b: self._emit_global_appearance())
        c_glob.body.addWidget(self._chk_show_orig)

        or_row = QHBoxLayout()
        or_row.addWidget(QLabel("原文颜色：", c_glob))
        self._btn_gl_or_fg = ColorSwatchButton(TEXT_SECONDARY, "选择原文颜色", c_glob)
        self._btn_gl_or_fg.color_changed.connect(lambda _c: self._emit_global_appearance())
        or_row.addWidget(self._btn_gl_or_fg)
        or_row.addSpacing(14)
        or_row.addWidget(QLabel("描边颜色：", c_glob))
        self._btn_gl_or_sc = ColorSwatchButton("#000000", "选择原文描边颜色", c_glob)
        self._btn_gl_or_sc.color_changed.connect(lambda _c: self._emit_global_appearance())
        or_row.addWidget(self._btn_gl_or_sc)
        or_row.addStretch(1)
        c_glob.body.addLayout(or_row)

        rg_os, self._sl_gl_or_sz, _ = _make_slider_row("原文字号", 10, 24, 12, lambda v: f"{v} px")
        self._sl_gl_or_sz.valueChanged.connect(lambda _v: self._emit_global_appearance())
        c_glob.body.addLayout(rg_os)

        rg_ost, self._sl_gl_or_st, _ = _make_slider_row("原文描边", 0, 4, 0, lambda v: f"{v} px")
        self._sl_gl_or_st.valueChanged.connect(lambda _v: self._emit_global_appearance())
        c_glob.body.addLayout(rg_ost)

        self._chk_show_author = QCheckBox("在弹幕/评论翻译中保留发言人用户名", c_glob)
        self._chk_show_author.toggled.connect(self.show_author_changed.emit)
        c_glob.body.addWidget(self._chk_show_author)

        util_row = QHBoxLayout()
        btn_unlock_all = QPushButton("一键解除全部穿透锁定", c_glob)
        btn_unlock_all.clicked.connect(self.unlock_clickthrough_requested.emit)
        btn_reset_pos = QPushButton("重置所有窗口与取景框位置", c_glob)
        btn_reset_pos.clicked.connect(self.reset_positions_requested.emit)
        util_row.addWidget(btn_unlock_all)
        util_row.addWidget(btn_reset_pos)
        util_row.addStretch(1)
        c_glob.body.addLayout(util_row)
        lay.addWidget(c_glob)

        # Per-Detached Window Customization ("use_custom" schema matching config.py)
        c_det = StudioCard("独立浮窗单独外观设置", "可为进入「独立浮窗」状态的每个通道单独设置底色、透明度和字体描边。")
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("选择独立浮窗：", c_det))
        self._cb_det_target = NoWheelComboBox(c_det)
        self._cb_det_target.currentIndexChanged.connect(lambda _i: self._load_detached_form())
        sel_row.addWidget(self._cb_det_target, 1)
        c_det.body.addLayout(sel_row)

        self._chk_det_custom = QCheckBox("为当前选中的独立浮窗启用专属外观（关闭则跟随全局）", c_det)
        self._chk_det_custom.toggled.connect(self._on_det_custom_toggled)
        c_det.body.addWidget(self._chk_det_custom)

        self._det_wrap = QWidget(c_det)
        det_lay = QVBoxLayout(self._det_wrap)
        det_lay.setContentsMargins(0, 4, 0, 0)
        det_lay.setSpacing(8)

        dbg_row = QHBoxLayout()
        dbg_row.addWidget(QLabel("窗口底色：", self._det_wrap))
        self._btn_det_bg = ColorSwatchButton(BG_CANVAS, "选择独立浮窗底色", self._det_wrap)
        self._btn_det_bg.color_changed.connect(lambda _c: self._emit_detached_appearance())
        dbg_row.addWidget(self._btn_det_bg)
        btn_det_reset = QPushButton("恢复跟随全局", self._det_wrap)
        btn_det_reset.clicked.connect(self._reset_current_detached_style)
        dbg_row.addWidget(btn_det_reset)
        dbg_row.addStretch(1)
        det_lay.addLayout(dbg_row)

        rd_op, self._sl_det_op, _ = _make_slider_row("面板不透明度", 25, 100, 94, lambda v: f"{v}%")
        self._sl_det_op.valueChanged.connect(lambda _v: self._emit_detached_appearance())
        det_lay.addLayout(rd_op)

        dt_row = QHBoxLayout()
        dt_row.addWidget(QLabel("译文颜色：", self._det_wrap))
        self._btn_det_tr_fg = ColorSwatchButton(TEXT_PRIMARY, "选择译文颜色", self._det_wrap)
        self._btn_det_tr_fg.color_changed.connect(lambda _c: self._emit_detached_appearance())
        dt_row.addWidget(self._btn_det_tr_fg)
        dt_row.addSpacing(12)
        dt_row.addWidget(QLabel("描边颜色：", self._det_wrap))
        self._btn_det_tr_sc = ColorSwatchButton("#000000", "选择译文描边颜色", self._det_wrap)
        self._btn_det_tr_sc.color_changed.connect(lambda _c: self._emit_detached_appearance())
        dt_row.addWidget(self._btn_det_tr_sc)
        dt_row.addStretch(1)
        det_lay.addLayout(dt_row)

        rd_ts, self._sl_det_tr_sz, _ = _make_slider_row("译文字号", 11, 32, 15, lambda v: f"{v} px")
        self._sl_det_tr_sz.valueChanged.connect(lambda _v: self._emit_detached_appearance())
        det_lay.addLayout(rd_ts)

        rd_tst, self._sl_det_tr_st, _ = _make_slider_row("译文描边", 0, 4, 0, lambda v: f"{v} px")
        self._sl_det_tr_st.valueChanged.connect(lambda _v: self._emit_detached_appearance())
        det_lay.addLayout(rd_tst)

        do_row = QHBoxLayout()
        do_row.addWidget(QLabel("原文颜色：", self._det_wrap))
        self._btn_det_or_fg = ColorSwatchButton(TEXT_SECONDARY, "选择原文颜色", self._det_wrap)
        self._btn_det_or_fg.color_changed.connect(lambda _c: self._emit_detached_appearance())
        do_row.addWidget(self._btn_det_or_fg)
        do_row.addSpacing(12)
        do_row.addWidget(QLabel("描边颜色：", self._det_wrap))
        self._btn_det_or_sc = ColorSwatchButton("#000000", "选择原文描边颜色", self._det_wrap)
        self._btn_det_or_sc.color_changed.connect(lambda _c: self._emit_detached_appearance())
        do_row.addWidget(self._btn_det_or_sc)
        do_row.addStretch(1)
        det_lay.addLayout(do_row)

        rd_os, self._sl_det_or_sz, _ = _make_slider_row("原文字号", 10, 24, 12, lambda v: f"{v} px")
        self._sl_det_or_sz.valueChanged.connect(lambda _v: self._emit_detached_appearance())
        det_lay.addLayout(rd_os)

        rd_ost, self._sl_det_or_st, _ = _make_slider_row("原文描边", 0, 4, 0, lambda v: f"{v} px")
        self._sl_det_or_st.valueChanged.connect(lambda _v: self._emit_detached_appearance())
        det_lay.addLayout(rd_ost)

        c_det.body.addWidget(self._det_wrap)
        lay.addWidget(c_det)

        c_tel = StudioCard("系统硬件与资源占用")
        self._lbl_vram = QLabel("正在读取内存与磁盘状态...", c_tel)
        self._lbl_vram.setWordWrap(True)
        self._lbl_vram.setStyleSheet(f"color: {TEXT_SECONDARY}; font-family: Consolas, monospace; font-size: 12px; background: transparent;")
        c_tel.body.addWidget(self._lbl_vram)
        lay.addWidget(c_tel)

        lay.addStretch(1)
        self._pages.addWidget(sa)

    @staticmethod
    def _section_tag(text: str, parent: QWidget) -> QLabel:
        lbl = QLabel(text, parent)
        lbl.setStyleSheet(
            f"color: #60A5FA; font-size: 12px; font-weight: bold;"
            f"padding-top: 6px; border-top: 1px solid {BORDER_HAIRLINE}; background: transparent;"
        )
        return lbl

    # ------------------------------------------------------------------ Public APIs
    def sync_from_config(self, cfg: dict[str, Any]) -> None:
        self._cfg = copy.deepcopy(cfg)
        self._detached_styles_cache = copy.deepcopy(cfg.get("detached_styles", {}) or {})

        with QSignalBlocker(self._chk_remember_pos):
            self._chk_remember_pos.setChecked(bool(cfg.get("remember_window_pos", True)))

        self.refresh_regions(cfg.get("regions", []))
        self.set_display_mode_indicator(str(cfg.get("display_mode", "floating")))
        self.set_exclude_from_capture(bool(cfg.get("exclude_from_capture", True)))

        with QSignalBlocker(self._hk_stealth):
            self._hk_stealth.setText(str(cfg.get("stealth_hotkey", "Alt+Q")))
        with QSignalBlocker(self._cb_stealth_trig):
            i = self._cb_stealth_trig.findData(cfg.get("stealth_trigger_mode", "toggle"))
            if i >= 0:
                self._cb_stealth_trig.setCurrentIndex(i)
        with QSignalBlocker(self._cb_stealth_pos):
            i = self._cb_stealth_pos.findData(cfg.get("stealth_position", "above_region"))
            if i >= 0:
                self._cb_stealth_pos.setCurrentIndex(i)
        with QSignalBlocker(self._cb_inplace_style):
            i = self._cb_inplace_style.findData(cfg.get("inplace_style", "blend"))
            if i >= 0:
                self._cb_inplace_style.setCurrentIndex(i)

        with QSignalBlocker(self._sl_ocr_int):
            self._sl_ocr_int.setValue(int(round(float(cfg.get("ocr_interval_sec", 0.28)) * 100)))
        with QSignalBlocker(self._sl_ocr_diff):
            self._sl_ocr_diff.setValue(int(round(float(cfg.get("ocr_diff_threshold", 1.6)) * 10)))
        with QSignalBlocker(self._chk_ocr_filter_zh):
            self._chk_ocr_filter_zh.setChecked(bool(cfg.get("ocr_filter_chinese", False)))

        self.sync_tier_ui(cfg)

        with QSignalBlocker(self._cb_api_provider):
            i = self._cb_api_provider.findData(cfg.get("api_provider", "deepseek"))
            if i >= 0:
                self._cb_api_provider.setCurrentIndex(i)
        self._ed_api_url.setText(str(cfg.get("api_base_url", "https://api.deepseek.com/v1")))
        self._ed_api_key.setText(str(cfg.get("api_key", "")))
        self._ed_api_model.setText(str(cfg.get("api_model", "deepseek-flash")))

        with QSignalBlocker(self._chk_asr_enabled):
            self._chk_asr_enabled.setChecked(bool(cfg.get("asr_enabled", True)))
        with QSignalBlocker(self._cb_audio_mode):
            i = self._cb_audio_mode.findData(cfg.get("audio_mode", "device"))
            if i >= 0:
                self._cb_audio_mode.setCurrentIndex(i)
        with QSignalBlocker(self._cb_asr_lang):
            i = self._cb_asr_lang.findData(cfg.get("asr_lang", "auto"))
            if i >= 0:
                self._cb_asr_lang.setCurrentIndex(i)
        with QSignalBlocker(self._sl_asr_rms):
            self._sl_asr_rms.setValue(int(round(float(cfg.get("asr_rms_threshold", 0.008)) * 1000)))
        with QSignalBlocker(self._sl_asr_sil):
            self._sl_asr_sil.setValue(int(round(float(cfg.get("asr_silence_flush_sec", 0.45)) * 100)))

        self.sync_quick_ui(cfg)

        # Global Appearance
        self._btn_gl_bg.setColor(str(cfg.get("panel_bg_color", BG_CANVAS)))
        with QSignalBlocker(self._sl_gl_op):
            self._sl_gl_op.setValue(int(round(float(cfg.get("opacity", 0.94)) * 100)))
        self._btn_gl_tr_fg.setColor(str(cfg.get("trans_font_color", TEXT_PRIMARY)))
        self._btn_gl_tr_sc.setColor(str(cfg.get("trans_stroke_color", "#000000")))
        with QSignalBlocker(self._sl_gl_tr_sz):
            self._sl_gl_tr_sz.setValue(int(cfg.get("trans_font_size", cfg.get("font_size", 15))))
        with QSignalBlocker(self._sl_gl_tr_st):
            self._sl_gl_tr_st.setValue(max(0, min(4, int(cfg.get("trans_stroke_size", 0)))))

        with QSignalBlocker(self._chk_show_orig):
            self._chk_show_orig.setChecked(bool(cfg.get("show_original", True)))
        self._btn_gl_or_fg.setColor(str(cfg.get("orig_font_color", TEXT_SECONDARY)))
        self._btn_gl_or_sc.setColor(str(cfg.get("orig_stroke_color", "#000000")))
        with QSignalBlocker(self._sl_gl_or_sz):
            self._sl_gl_or_sz.setValue(int(cfg.get("orig_font_size", 12)))
        with QSignalBlocker(self._sl_gl_or_st):
            self._sl_gl_or_st.setValue(max(0, min(4, int(cfg.get("orig_stroke_size", 0)))))
        with QSignalBlocker(self._chk_show_author):
            self._chk_show_author.setChecked(bool(cfg.get("show_comment_author", True)))

        channels = [(str(r["id"]), str(r.get("name", r["id"]))) for r in cfg.get("regions", [])]
        channels.append(("asr", "语音同传"))
        self.refresh_detached_channels(channels, self._detached_styles_cache)

    def sync_tier_ui(self, cfg: dict[str, Any]) -> None:
        self._cfg.update({
            "translator_tier": cfg.get("translator_tier", "balanced"),
            "game_mode": cfg.get("game_mode", False),
            "llama_backend": cfg.get("llama_backend", "auto"),
            "local_model_id": cfg.get("local_model_id", "hy_mt_1_8b_q8"),
            "custom_gguf_path": cfg.get("custom_gguf_path", ""),
            "asr_backend": cfg.get("asr_backend", "whisper"),
        })
        tier = str(cfg.get("translator_tier", "balanced"))
        for k, btn in self._tier_btns.items():
            active = k == tier
            btn.setProperty("seg", not active)
            btn.setProperty("seg_active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        with QSignalBlocker(self._chk_game_mode):
            self._chk_game_mode.setChecked(bool(cfg.get("game_mode", False)))
        with QSignalBlocker(self._cb_llama_backend):
            i = self._cb_llama_backend.findData(cfg.get("llama_backend", "auto"))
            if i >= 0:
                self._cb_llama_backend.setCurrentIndex(i)
        with QSignalBlocker(self._cb_local_model):
            i = self._cb_local_model.findData(cfg.get("local_model_id", "hy_mt_1_8b_q8"))
            if i >= 0:
                self._cb_local_model.setCurrentIndex(i)
        self._ed_custom_gguf.setText(str(cfg.get("custom_gguf_path", "")))
        with QSignalBlocker(self._cb_asr_backend):
            i = self._cb_asr_backend.findData(cfg.get("asr_backend", "whisper"))
            if i >= 0:
                self._cb_asr_backend.setCurrentIndex(i)
        self._update_local_model_hint()

    def sync_quick_ui(self, cfg: dict[str, Any]) -> None:
        with QSignalBlocker(self._hk_quick):
            self._hk_quick.setText(str(cfg.get("quick_hotkey", "Alt+T")))
        with QSignalBlocker(self._hk_voice_out):
            self._hk_voice_out.setText(str(cfg.get("voice_out_hotkey", "Alt+V")))
        with QSignalBlocker(self._chk_voice_hold):
            self._chk_voice_hold.setChecked(bool(cfg.get("voice_out_hold_mode", True)))
        with QSignalBlocker(self._cb_out_lang):
            i = self._cb_out_lang.findData(cfg.get("output_target_lang", "en"))
            if i >= 0:
                self._cb_out_lang.setCurrentIndex(i)
        with QSignalBlocker(self._cb_out_tone):
            i = self._cb_out_tone.findData(cfg.get("output_tone", "natural"))
            if i >= 0:
                self._cb_out_tone.setCurrentIndex(i)
        with QSignalBlocker(self._chk_out_autocopy):
            self._chk_out_autocopy.setChecked(bool(cfg.get("output_auto_copy", True)))
        with QSignalBlocker(self._chk_out_autopaste):
            self._chk_out_autopaste.setChecked(bool(cfg.get("output_auto_paste", False)))
        with QSignalBlocker(self._hk_unlock):
            self._hk_unlock.setText(str(cfg.get("unlock_clickthrough_hotkey", "Ctrl+Alt+U")))
        with QSignalBlocker(self._chk_cap_minimal):
            self._chk_cap_minimal.setChecked(bool(cfg.get("capsule_minimal_mode", False)))
        self._btn_cap_bg.setColor(str(cfg.get("capsule_bg_color", BG_CANVAS)))
        self._btn_cap_fg.setColor(str(cfg.get("capsule_font_color", TEXT_PRIMARY)))
        with QSignalBlocker(self._sl_cap_op):
            self._sl_cap_op.setValue(int(round(float(cfg.get("capsule_opacity", 0.90)) * 100)))
        with QSignalBlocker(self._sl_cap_font):
            self._sl_cap_font.setValue(int(cfg.get("capsule_font_size", 15)))

    def set_boot_progress(self, pct: int, msg: str, ready: bool) -> None:
        self._boot_ready = bool(ready)
        self._boot_bar.setValue(max(0, min(100, int(pct))))
        self._lbl_boot_status.setText(msg)
        self._btn_start_trans.setEnabled(self._boot_ready)
        self._refresh_start_btn()
        if self._boot_ready:
            self._lbl_boot_status.setStyleSheet(f"color: {STATUS_OK}; font-size: 12px; background: transparent;")
            self._rail_beacon.set_color(STATUS_OK)
            self._lbl_rail_status.setText("系统已就绪")
            self._lbl_rail_status.setStyleSheet(f"color: {STATUS_OK}; font-size: 11.5px; font-weight: bold; background: transparent;")
        else:
            self._lbl_boot_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px; background: transparent;")
            self._rail_beacon.set_color(STATUS_WARN)
            self._lbl_rail_status.setText("模型加载中")
            self._lbl_rail_status.setStyleSheet(f"color: {STATUS_WARN}; font-size: 11.5px; font-weight: bold; background: transparent;")

    def set_translation_active(self, active: bool) -> None:
        self._trans_active = bool(active)
        self._refresh_start_btn()

    def _refresh_start_btn(self) -> None:
        if not self._boot_ready:
            self._btn_start_trans.setIcon(make_icon("play", TEXT_MUTED, 14))
            self._btn_start_trans.setText(" 开始翻译（模型加载中...）")
        elif self._trans_active:
            self._btn_start_trans.setIcon(make_icon("close", "#FFFFFF", 14))
            self._btn_start_trans.setText(" 关闭翻译页面")
        else:
            self._btn_start_trans.setIcon(make_icon("play", "#FFFFFF", 14))
            self._btn_start_trans.setText(" 开始翻译")

    def set_self_check_report(self, rows: list[dict[str, Any]]) -> None:
        while self._self_check_container.count():
            it = self._self_check_container.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        if not rows:
            lbl = QLabel("暂无检测条目。", self)
            lbl.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent;")
            self._self_check_container.addWidget(lbl)
            return

        color_map = {"ok": STATUS_OK, "warn": STATUS_WARN, "err": STATUS_ERR}
        tag_map = {"ok": "正常", "warn": "提示", "err": "异常"}
        for item in rows:
            st = str(item.get("status", "ok")).lower()
            clr = color_map.get(st, TEXT_SECONDARY)
            tag = tag_map.get(st, "信息")
            row_w = QFrame()
            row_w.setObjectName("StudioSubRow")
            hl = QHBoxLayout(row_w)
            hl.setContentsMargins(10, 7, 10, 7)
            hl.setSpacing(10)

            badge = QLabel(tag, row_w)
            badge.setFixedWidth(44)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                f"color: {clr}; border: 1px solid {clr}; border-radius: 4px;"
                f"font-size: 11px; font-weight: bold; padding: 2px 0; background: transparent;"
            )
            name = QLabel(str(item.get("name", "")), row_w)
            name.setFixedWidth(124)
            name.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: bold; background: transparent;")
            detail = QLabel(str(item.get("detail", "")), row_w)
            detail.setWordWrap(True)
            detail.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px; background: transparent;")

            hl.addWidget(badge)
            hl.addWidget(name)
            hl.addWidget(detail, 1)
            self._self_check_container.addWidget(row_w)

    def set_engine_telemetry(self, llm_sum: str, asr_sum: str) -> None:
        self._lbl_rail_engines.setText(f"LLM: {llm_sum}\nASR: {asr_sum}")
        self._lbl_home_engines.setText(f"当前引擎：翻译 [{llm_sum}]  ·  语音 [{asr_sum}]")

    def set_hotkey_status_text(self, summary: str, ok: bool = True) -> None:
        clr = STATUS_OK if ok else STATUS_WARN
        self._lbl_hotkey_status.setStyleSheet(f"color: {clr}; font-size: 11.5px; background: transparent;")
        self._lbl_hotkey_status.setText(summary)

    def set_display_mode_indicator(self, mode: str) -> None:
        for mk, btn in self._mode_btns.items():
            active = mk == mode
            btn.setProperty("seg", not active)
            btn.setProperty("seg_active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_exclude_from_capture(self, enabled: bool) -> None:
        self._exclude_capture = bool(enabled)
        with QSignalBlocker(self._chk_exclude_cap):
            self._chk_exclude_cap.setChecked(self._exclude_capture)
        if self.isVisible():
            set_exclude_from_capture(self, self._exclude_capture)

    def set_download_progress(self, pct: int, msg: str, active: bool = False) -> None:
        self._dl_bar.setVisible(active or (0 < pct < 100))
        self._dl_bar.setValue(max(0, min(100, int(pct))))
        self._lbl_dl_msg.setText(msg)
        if not active and pct >= 100:
            self._rebuild_catalog_combos()

    def set_api_test_result(self, ok: bool, msg: str) -> None:
        clr = STATUS_OK if ok else STATUS_ERR
        self._lbl_api_test.setStyleSheet(f"color: {clr}; font-size: 11.5px; background: transparent;")
        self._lbl_api_test.setText(msg)

    def refresh_regions(self, regions: list[dict[str, Any]]) -> None:
        while self._regions_box.count():
            it = self._regions_box.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()

        for reg in regions:
            rid = str(reg.get("id", ""))
            row = QFrame()
            row.setObjectName("StudioSubRow")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(10, 7, 10, 7)
            hl.setSpacing(7)

            dot = QLabel("●", row)
            dot.setStyleSheet(f"color: {reg.get('color', ACCENT_PRIMARY)}; background: transparent;")
            name = QLabel(str(reg.get("name", rid)), row)
            name.setMinimumWidth(68)
            name.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: bold; background: transparent;")

            cb_lang = NoWheelComboBox(row)
            for code, lbl in OCR_LANG_CHOICES:
                cb_lang.addItem(lbl, code)
            li = cb_lang.findData(reg.get("lang", "auto"))
            if li >= 0:
                cb_lang.setCurrentIndex(li)
            cb_lang.currentIndexChanged.connect(
                lambda _i, r=rid, cb=cb_lang: self.region_lang_changed.emit(r, str(cb.currentData() or "auto"))
            )

            chk_en = QCheckBox("启用", row)
            chk_en.setChecked(bool(reg.get("enabled", True)))
            chk_en.toggled.connect(lambda ch, r=rid: self.region_enabled_changed.emit(r, ch))

            chk_vis = QCheckBox("显示框", row)
            chk_vis.setChecked(bool(reg.get("box_visible", True)))
            chk_vis.toggled.connect(lambda ch, r=rid: self.region_box_visible_changed.emit(r, ch))

            chk_clean = QCheckBox("过滤噪声", row)
            chk_clean.setChecked(bool(reg.get("smart_clean", True)))
            chk_clean.toggled.connect(lambda ch, r=rid: self.region_smart_clean_changed.emit(r, ch))

            btn_det = QPushButton("归位" if reg.get("detached", False) else "拆出", row)
            btn_det.setProperty("compact", True)
            btn_det.clicked.connect(lambda _c, r=rid: self.region_detach_toggled.emit(r))

            btn_loc = QPushButton("定位", row)
            btn_loc.setProperty("compact", True)
            btn_loc.clicked.connect(lambda _c, r=rid: self.region_locate_requested.emit(r))

            btn_del = QPushButton("删", row)
            btn_del.setProperty("compact", True)
            btn_del.setProperty("danger", True)
            btn_del.setEnabled(len(regions) > 1)
            btn_del.clicked.connect(lambda _c, r=rid: self.remove_region_requested.emit(r))

            hl.addWidget(dot)
            hl.addWidget(name)
            hl.addWidget(cb_lang)
            hl.addWidget(chk_en)
            hl.addWidget(chk_vis)
            hl.addWidget(chk_clean)
            hl.addStretch(1)
            hl.addWidget(btn_det)
            hl.addWidget(btn_loc)
            hl.addWidget(btn_del)
            self._regions_box.addWidget(row)

    def refresh_detached_channels(
        self, channels: list[tuple[str, str]], detached_styles: dict[str, Any]
    ) -> None:
        self._detached_styles_cache = copy.deepcopy(detached_styles or {})
        prev = self._cb_det_target.currentData()
        with QSignalBlocker(self._cb_det_target):
            self._cb_det_target.clear()
            for cid, label in channels:
                self._cb_det_target.addItem(f"{label} ({cid})", cid)
            if prev:
                idx = self._cb_det_target.findData(prev)
                if idx >= 0:
                    self._cb_det_target.setCurrentIndex(idx)
        self._load_detached_form()

    def set_apps(self, apps: list[dict[str, Any]], cur_pid: int = 0, cur_exe: str = "") -> None:
        with QSignalBlocker(self._cb_apps):
            self._cb_apps.clear()
            self._cb_apps.addItem("请选择目标软件进程...", (0, ""))
            match_idx = 0
            for idx, item in enumerate(apps, start=1):
                pid = int(item.get("pid", 0))
                exe = str(item.get("exe", ""))
                lbl = str(item.get("label", f"{exe} [{pid}]"))
                self._cb_apps.addItem(lbl, (pid, exe))
                if (cur_pid and pid == cur_pid) or (cur_exe and exe.lower() == cur_exe.lower() and match_idx == 0):
                    match_idx = idx
            self._cb_apps.setCurrentIndex(match_idx)

    def select_app(self, picked: dict[str, Any]) -> None:
        pid = int(picked.get("pid", 0))
        exe = str(picked.get("exe", ""))
        label = str(picked.get("label", f"{exe} [{pid}]"))
        with QSignalBlocker(self._cb_apps):
            for i in range(self._cb_apps.count()):
                data = self._cb_apps.itemData(i)
                if isinstance(data, tuple) and data[0] == pid:
                    self._cb_apps.setCurrentIndex(i)
                    return
            self._cb_apps.addItem(label, (pid, exe))
            self._cb_apps.setCurrentIndex(self._cb_apps.count() - 1)

    def set_devices(self, devs: list[dict[str, Any]], cur_name: str = "") -> None:
        with QSignalBlocker(self._cb_devices):
            self._cb_devices.clear()
            self._cb_devices.addItem("系统默认输出声卡", "")
            match_idx = 0
            for idx, d in enumerate(devs, start=1):
                name = str(d.get("name", ""))
                tag = " [默认]" if d.get("is_default") else ""
                self._cb_devices.addItem(f"{name}{tag}", name)
                if cur_name and name == cur_name:
                    match_idx = idx
            self._cb_devices.setCurrentIndex(match_idx)

    def set_mics(self, mics: list[dict[str, Any]], cur_name: str = "") -> None:
        with QSignalBlocker(self._cb_mics):
            self._cb_mics.clear()
            self._cb_mics.addItem("系统默认麦克风", "")
            match_idx = 0
            for idx, m in enumerate(mics, start=1):
                name = str(m.get("name", ""))
                tag = " [默认]" if m.get("is_default") else ""
                self._cb_mics.addItem(f"{name}{tag}", name)
                if cur_name and name == cur_name:
                    match_idx = idx
            self._cb_mics.setCurrentIndex(match_idx)

    def set_suppress_geometry_emit(self, suppress: bool) -> None:
        self._suppress_geom_emit = bool(suppress)

    def get_logical_rect(self) -> list[int]:
        g = self.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    # ------------------------------------------------------------------ Internal Emitters
    def _rebuild_catalog_combos(self) -> None:
        cur_llm = self._cb_local_model.currentData() or self._cfg.get("local_model_id", "hy_mt_1_8b_q8")
        with QSignalBlocker(self._cb_local_model):
            self._cb_local_model.clear()
            for entry in LOCAL_LLM_CATALOG:
                eid = entry["id"]
                inst = "已下载" if is_llm_model_installed(eid) else "未下载"
                self._cb_local_model.addItem(f"{entry['name']} [{inst}]", eid)
            self._cb_local_model.addItem("自定义本地 .gguf 文件", "custom")
            pos = self._cb_local_model.findData(cur_llm)
            if pos >= 0:
                self._cb_local_model.setCurrentIndex(pos)

        cur_asr = self._cb_asr_backend.currentData() or self._cfg.get("asr_backend", "whisper")
        with QSignalBlocker(self._cb_asr_backend):
            self._cb_asr_backend.clear()
            for entry in ASR_BACKEND_CATALOG:
                aid = entry["id"]
                inst = "已就绪" if is_asr_model_installed(aid) else "未下载"
                self._cb_asr_backend.addItem(f"{entry['name']} [{inst}]", aid)
            pos = self._cb_asr_backend.findData(cur_asr)
            if pos >= 0:
                self._cb_asr_backend.setCurrentIndex(pos)

        self._update_local_model_hint()

    def _update_local_model_hint(self) -> None:
        mid = str(self._cb_local_model.currentData() or "")
        for entry in LOCAL_LLM_CATALOG:
            if entry["id"] == mid:
                vk = "就绪" if has_vulkan_runtime() else "未下载"
                self._lbl_local_hint.setText(
                    f"说明：{entry['usage_text']} | 推荐配置：{entry['rec_text']}（Vulkan 运行时：{vk}）"
                )
                return
        self._lbl_local_hint.setText("自定义模型模式：请指定本地 .gguf 权重路径后点击「应用并切换为本地离线引擎」。")

    def _browse_gguf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择本地 GGUF 模型", "", "GGUF Models (*.gguf)")
        if path:
            self._ed_custom_gguf.setText(path)
            idx = self._cb_local_model.findData("custom")
            if idx >= 0:
                self._cb_local_model.setCurrentIndex(idx)

    def _on_api_provider_changed(self) -> None:
        key = str(self._cb_api_provider.currentData() or "")
        if key in API_PROVIDER_PRESETS:
            _lbl, url, model = API_PROVIDER_PRESETS[key]
            if url:
                self._ed_api_url.setText(url)
            if model:
                self._ed_api_model.setText(model)

    def _on_app_combo_changed(self) -> None:
        data = self._cb_apps.currentData()
        if isinstance(data, tuple) and len(data) == 2 and int(data[0]) > 0:
            self.target_process_changed.emit(int(data[0]), str(data[1]))

    def _emit_stealth_settings(self) -> None:
        self.stealth_settings_changed.emit(
            self._hk_stealth.text().strip() or "Alt+Q",
            str(self._cb_stealth_trig.currentData() or "toggle"),
            str(self._cb_stealth_pos.currentData() or "above_region"),
        )

    def _emit_ocr_tuning(self) -> None:
        self.ocr_tuning_changed.emit(
            self._sl_ocr_int.value() / 100.0,
            self._sl_ocr_diff.value() / 10.0,
        )

    def _emit_asr_tuning(self) -> None:
        self.asr_tuning_changed.emit(
            self._sl_asr_rms.value() / 1000.0,
            self._sl_asr_sil.value() / 100.0,
        )

    def _emit_quick_settings(self) -> None:
        self.quick_settings_changed.emit(
            self._hk_quick.text().strip() or "Alt+T",
            self._hk_voice_out.text().strip() or "Alt+V",
            self._chk_voice_hold.isChecked(),
            str(self._cb_out_lang.currentData() or "en"),
            str(self._cb_out_tone.currentData() or "natural"),
            self._chk_out_autocopy.isChecked(),
            self._chk_out_autopaste.isChecked(),
        )

    def _emit_capsule_style(self) -> None:
        self.capsule_style_changed.emit(
            self._sl_cap_op.value() / 100.0,
            self._btn_cap_bg.color(),
            self._sl_cap_font.value(),
            self._btn_cap_fg.color(),
        )

    def _current_global_style_dict(self) -> dict[str, Any]:
        return {
            "bg_color": self._btn_gl_bg.color(),
            "opacity": self._sl_gl_op.value() / 100.0,
            "trans_font_size": self._sl_gl_tr_sz.value(),
            "trans_font_color": self._btn_gl_tr_fg.color(),
            "trans_stroke_color": self._btn_gl_tr_sc.color(),
            "trans_stroke_size": int(self._sl_gl_tr_st.value()),
            "show_original": self._chk_show_orig.isChecked(),
            "orig_font_size": self._sl_gl_or_sz.value(),
            "orig_font_color": self._btn_gl_or_fg.color(),
            "orig_stroke_color": self._btn_gl_or_sc.color(),
            "orig_stroke_size": int(self._sl_gl_or_st.value()),
            "show_comment_author": self._chk_show_author.isChecked(),
        }

    def _emit_global_appearance(self) -> None:
        self.global_appearance_changed.emit(self._current_global_style_dict())

    def _load_detached_form(self) -> None:
        cid = str(self._cb_det_target.currentData() or "")
        st = self._detached_styles_cache.get(cid, {}) if cid else {}
        use_custom = bool(st.get("use_custom", False))
        base = make_default_detached_style(
            {**self._current_global_style_dict(), "panel_bg_color": self._btn_gl_bg.color()}
        )
        if use_custom:
            base.update(st)

        with QSignalBlocker(self._chk_det_custom):
            self._chk_det_custom.setChecked(use_custom)
        self._det_wrap.setEnabled(use_custom)

        self._btn_det_bg.setColor(str(base["bg_color"]))
        with QSignalBlocker(self._sl_det_op):
            self._sl_det_op.setValue(int(round(float(base["opacity"]) * 100)))
        self._btn_det_tr_fg.setColor(str(base["trans_font_color"]))
        self._btn_det_tr_sc.setColor(str(base["trans_stroke_color"]))
        with QSignalBlocker(self._sl_det_tr_sz):
            self._sl_det_tr_sz.setValue(int(base["trans_font_size"]))
        with QSignalBlocker(self._sl_det_tr_st):
            self._sl_det_tr_st.setValue(max(0, min(4, int(base["trans_stroke_size"]))))

        self._btn_det_or_fg.setColor(str(base["orig_font_color"]))
        self._btn_det_or_sc.setColor(str(base["orig_stroke_color"]))
        with QSignalBlocker(self._sl_det_or_sz):
            self._sl_det_or_sz.setValue(int(base["orig_font_size"]))
        with QSignalBlocker(self._sl_det_or_st):
            self._sl_det_or_st.setValue(max(0, min(4, int(base["orig_stroke_size"]))))

    def _on_det_custom_toggled(self, checked: bool) -> None:
        self._det_wrap.setEnabled(checked)
        self._emit_detached_appearance()

    def _reset_current_detached_style(self) -> None:
        with QSignalBlocker(self._chk_det_custom):
            self._chk_det_custom.setChecked(False)
        self._det_wrap.setEnabled(False)
        cid = str(self._cb_det_target.currentData() or "")
        if cid:
            self._detached_styles_cache.pop(cid, None)
            self._load_detached_form()
            self.detached_appearance_changed.emit(cid, {"use_custom": False})

    def _emit_detached_appearance(self) -> None:
        cid = str(self._cb_det_target.currentData() or "")
        if not cid:
            return
        style = {
            "use_custom": self._chk_det_custom.isChecked(),
            "bg_color": self._btn_det_bg.color(),
            "opacity": self._sl_det_op.value() / 100.0,
            "trans_font_size": self._sl_det_tr_sz.value(),
            "trans_font_color": self._btn_det_tr_fg.color(),
            "trans_stroke_color": self._btn_det_tr_sc.color(),
            "trans_stroke_size": int(self._sl_det_tr_st.value()),
            "orig_font_size": self._sl_det_or_sz.value(),
            "orig_font_color": self._btn_det_or_fg.color(),
            "orig_stroke_color": self._btn_det_or_sc.color(),
            "orig_stroke_size": int(self._sl_det_or_st.value()),
        }
        self._detached_styles_cache[cid] = copy.deepcopy(style)
        self.detached_appearance_changed.emit(cid, style)

    def _poll_vram(self) -> None:
        if not self.isVisible() or self._pages.currentIndex() != 5:
            return
        try:
            import shutil
            import psutil

            vm = psutil.virtual_memory()
            used_gb = (vm.total - vm.available) / (1024 ** 3)
            tot_gb = vm.total / (1024 ** 3)
            disk_free = shutil.disk_usage(str(Path.home())).free / (1024 ** 3)
            vram_txt = ""
            try:
                import subprocess

                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                    text=True,
                    creationflags=0x08000000,
                    timeout=1.0,
                ).strip()
                parts = [p.strip() for p in out.split(",")]
                if len(parts) >= 2:
                    vram_txt = f" | 显存：{parts[0]} / {parts[1]} MiB"
            except Exception:
                pass
            self._lbl_vram.setText(
                f"系统内存：{used_gb:.1f} / {tot_gb:.1f} GB ({vm.percent}%){vram_txt} | "
                f"系统盘可用：{disk_free:.1f} GB | 后端：{self._cfg.get('llama_backend', 'auto').upper()}"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ Window Paint, Drag, Resize
    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_rounded_mask(self, 12.0)
        set_exclude_from_capture(self, self._exclude_capture)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint_panel_surface(
            p, QRectF(self.rect()), radius=12.0, active_border=False, bg_hex=BG_CANVAS
        )
        p.setPen(QPen(QColor(TEXT_MUTED), 1.4))
        w, h = self.width(), self.height()
        p.drawLine(w - 13, h - 5, w - 5, h - 13)
        p.drawLine(w - 8, h - 5, w - 5, h - 8)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if not self._suppress_geom_emit:
            self.geometry_changed.emit(self.get_logical_rect())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        apply_rounded_mask(self, 12.0)
        if not self._suppress_geom_emit:
            self.geometry_changed.emit(self.get_logical_rect())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if pos.x() >= self.width() - 20 and pos.y() >= self.height() - 20:
                self._resizing = True
                self._resize_origin_pos = event.globalPosition().toPoint()
                self._resize_origin_size = self.size()
                event.accept()
                return
            child = self.childAt(pos)
            is_interactive = isinstance(child, (QPushButton, QLineEdit, QComboBox, QSlider, QCheckBox))
            if not is_interactive and (pos.y() <= self._header.height() or pos.x() <= self._rail.width()):
                if start_native_window_drag(self):
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_origin_pos
            self.resize(
                max(self.minimumWidth(), self._resize_origin_size.width() + delta.x()),
                max(self.minimumHeight(), self._resize_origin_size.height() + delta.y()),
            )
            event.accept()
            return
        pos = event.position().toPoint()
        if pos.x() >= self.width() - 20 and pos.y() >= self.height() - 20:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            self._resizing = False
            event.accept()
            return
        super().mouseReleaseEvent(event)
