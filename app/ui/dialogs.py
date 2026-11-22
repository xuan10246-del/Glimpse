"""Cohesive MONOLITH dark modal confirmation & hardware readiness dialogs."""

from __future__ import annotations

import html
from typing import Any, Optional

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..win32_utils import set_exclude_from_capture
from .icons import make_icon
from .theme import (
    ACCENT_PRIMARY,
    STATUS_OK,
    STATUS_WARN,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    HorizontalHairline,
    apply_rounded_mask,
    paint_panel_surface,
    start_native_window_drag,
)

# Backwards aliases
ACCENT_LIME = STATUS_OK
ACCENT_OCHRE = STATUS_WARN
ACCENT_VERMILION = ACCENT_PRIMARY
TEXT_IVORY = TEXT_PRIMARY
TEXT_STONE = TEXT_SECONDARY
paint_monolith_surface = paint_panel_surface

RESULT_CANCEL = 0
RESULT_CONFIRM = 1
RESULT_SECONDARY = 2


class MonolithConfirmDialog(QDialog):
    """Frameless dark matte architectural dialog (replaces harsh native light QMessageBox)."""

    def __init__(
        self,
        title: str,
        body_html: str,
        confirm_text: str = "确认",
        cancel_text: str = "取消",
        icon_name: str = "shield",
        accent_color: str = ACCENT_VERMILION,
        show_dont_ask: bool = False,
        secondary_text: str = "",
        exclude_capture: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._result_code = RESULT_CANCEL
        self._exclude_capture = bool(exclude_capture)
        self._accent = accent_color
        self._drag_origin: Optional[tuple[QPoint, QPoint]] = None

        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowOpacity(0.98)
        self.setModal(True)
        self.setFixedWidth(500)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        root.addLayout(hdr)

        ico_lbl = QLabel(self)
        ico_lbl.setPixmap(make_icon(icon_name, accent_color, 16).pixmap(16, 16))
        ico_lbl.setStyleSheet("background: transparent;")
        hdr.addWidget(ico_lbl)

        ttl = QLabel(title, self)
        ttl.setStyleSheet(
            f"color: {TEXT_IVORY}; font-size: 14px; font-weight: bold; background: transparent;"
        )
        hdr.addWidget(ttl, 1)

        btn_x = QPushButton(make_icon("close", "#9E988E", 13), "", self)
        btn_x.setFixedSize(24, 24)
        btn_x.setProperty("ghost", True)
        btn_x.clicked.connect(self._on_cancel)
        hdr.addWidget(btn_x)

        root.addWidget(HorizontalHairline(self))

        body = QLabel(self)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setText(body_html)
        body.setStyleSheet(
            f"color: {TEXT_IVORY}; font-size: 12px; line-height: 1.55; background: transparent;"
        )
        root.addWidget(body)

        self._chk_dont_ask: Optional[QCheckBox] = None
        if show_dont_ask:
            self._chk_dont_ask = QCheckBox("以后不再显示此提示", self)
            self._chk_dont_ask.setStyleSheet(f"color: {TEXT_STONE}; font-size: 11px;")
            root.addWidget(self._chk_dont_ask)

        root.addWidget(HorizontalHairline(self))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        root.addLayout(btn_row)
        btn_row.addStretch(1)

        if cancel_text:
            btn_c = QPushButton(cancel_text, self)
            btn_c.clicked.connect(self._on_cancel)
            btn_row.addWidget(btn_c)

        if secondary_text:
            btn_sec = QPushButton(secondary_text, self)
            btn_sec.setProperty("lime", True)
            btn_sec.clicked.connect(self._on_secondary)
            btn_row.addWidget(btn_sec)

        btn_ok = QPushButton(confirm_text, self)
        btn_ok.setProperty("accent", True)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_confirm)
        btn_row.addWidget(btn_ok)

    def _on_cancel(self) -> None:
        self._result_code = RESULT_CANCEL
        self.reject()

    def _on_confirm(self) -> None:
        self._result_code = RESULT_CONFIRM
        self.accept()

    def _on_secondary(self) -> None:
        self._result_code = RESULT_SECONDARY
        self.accept()

    def result_code(self) -> int:
        return self._result_code

    def dont_show_again(self) -> bool:
        return bool(self._chk_dont_ask is not None and self._chk_dont_ask.isChecked())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_rounded_mask(self, 10.0)
        set_exclude_from_capture(self, self._exclude_capture)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        apply_rounded_mask(self, 10.0)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 44:
            if not start_native_window_drag(self):
                self._drag_origin = (event.globalPosition().toPoint(), self.pos())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None:
            g0, p0 = self._drag_origin
            self.move(p0 + event.globalPosition().toPoint() - g0)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_origin = None

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint_monolith_surface(
            p,
            QRectF(self.rect()),
            radius=10.0,
            opacity=0.98,
            active_border=False,
            border_hex=self._accent,
        )


def confirm_hide_windows(
    stealth_hotkey: str,
    hotkey_ok: bool,
    trigger_mode: str,
    exclude_capture: bool = True,
    parent: Optional[QWidget] = None,
) -> tuple[bool, bool]:
    """Point 1 modal prompt before hiding all windows."""
    mode_hint = "按住期间显示、松开再次隐藏" if trigger_mode == "hold" else "再次按下立即恢复/抓取翻译"
    hk_badge = (
        f"<b style='color:#3B82F6;'>{html.escape(stealth_hotkey)}</b>"
        if hotkey_ok
        else f"<b style='color:#E5A93C;'>{html.escape(stealth_hotkey)}（当前热键注册冲突，请先在控制中心改键或从右下角托盘图标恢复）</b>"
    )
    body = (
        "<div style='color:#F1F3F5;'>"
        "<p style='margin:0 0 8px 0;'>点击确认后，<b>翻译底座、所有取词框与独立浮窗</b>将全部隐藏，后台屏幕扫描也会暂停（0% 空闲占卡）。</p>"
        f"<p style='margin:0 0 6px 0;'>• 唤起/显隐快捷键：{hk_badge}</p>"
        f"<p style='margin:0 0 6px 0;'>• 触发方式：<b>{mode_hint}</b>（可在「画面与模式」调整为自动吸附取词区上方）</p>"
        "<p style='margin:0; color:#9CA3AF;'>• 提示：随时也可右键点击 Windows 右下角系统托盘图标或在控制中心恢复显示。</p>"
        "</div>"
    )
    dlg = MonolithConfirmDialog(
        title="隐藏当前所有窗口",
        body_html=body,
        confirm_text="确认并全部隐藏",
        cancel_text="取消",
        icon_name="eye_off",
        accent_color=ACCENT_VERMILION,
        show_dont_ask=True,
        exclude_capture=exclude_capture,
        parent=parent,
    )
    dlg.exec()
    return dlg.result_code() == RESULT_CONFIRM, dlg.dont_show_again()


def confirm_inplace_mode(
    style_label: str,
    exclude_capture: bool = True,
    parent: Optional[QWidget] = None,
) -> tuple[bool, bool]:
    """Point 2 modal prompt explaining real effect of In-Place Overlay mode."""
    body = (
        "<div style='color:#F1F3F5;'>"
        "<p style='margin:0 0 8px 0;'>切换为<b>原位覆盖模式</b>后，实际显示与交互效果如下：</p>"
        f"<p style='margin:0 0 5px 0;'>• <b>隐藏取景框，原位替换</b>：四周定位框隐藏，自动在识别到的外语原文正上方绘制中文译文（当前样式：<b style='color:#34C77B;'>{html.escape(style_label)}</b>）。</p>"
        "<p style='margin:0 0 5px 0;'>• <b>完整穿透 + 防自捕</b>：原位译文默认鼠标完全穿透（不影响点击下方视频/界面），且截图引擎会自动无视覆盖层。</p>"
        "<p style='margin:0 0 5px 0;'>• <b>滚动防残影</b>：当画面大幅度滚动或切屏时，覆盖层会立即瞬清并在文本稳定后重绘。</p>"
        "<p style='margin:0; color:#9CA3AF;'>• 提示：若在全屏游戏内使用，请先将游戏设为<b>「无边框窗口化」</b>模式。</p>"
        "</div>"
    )
    dlg = MonolithConfirmDialog(
        title="切换至原位覆盖模式",
        body_html=body,
        confirm_text="启用原位覆盖",
        cancel_text="取消",
        icon_name="layers",
        accent_color=ACCENT_LIME,
        show_dont_ask=True,
        exclude_capture=exclude_capture,
        parent=parent,
    )
    dlg.exec()
    return dlg.result_code() == RESULT_CONFIRM, dlg.dont_show_again()


def confirm_clickthrough_lock(
    unlock_hotkey: str,
    exclude_capture: bool = True,
    parent: Optional[QWidget] = None,
) -> tuple[bool, bool]:
    """Point 4 modal prompt explaining click-through lock & all unlock methods."""
    body = (
        "<div style='color:#F1F3F5;'>"
        "<p style='margin:0 0 8px 0;'>开启<b>穿透锁定</b>后，鼠标所有左/右键点击与滚轮操作都会<b>直接穿透该浮窗</b>作用到底层软件或游戏，您将无法直接拖动或点击该窗口。</p>"
        "<p style='margin:0 0 5px 0; color:#34C77B;'><b>快速解除穿透锁定的三种方法：</b></p>"
        f"<p style='margin:0 0 4px 0;'>1. 按下全局快捷键：<b style='color:#3B82F6;'>{html.escape(unlock_hotkey)}</b>（无需切窗，随时一键解除全部锁定）</p>"
        "<p style='margin:0 0 4px 0;'>2. 点击翻译浮窗顶部模式栏上的 <b>「解除穿透」</b> 按钮</p>"
        "<p style='margin:0; color:#9CA3AF;'>3. 右键点击右下角系统托盘图标 → <b>「解除全部穿透锁定」</b></p>"
        "</div>"
    )
    dlg = MonolithConfirmDialog(
        title="开启鼠标穿透锁定提示",
        body_html=body,
        confirm_text="确认开启穿透锁定",
        cancel_text="取消",
        icon_name="lock",
        accent_color=ACCENT_OCHRE,
        show_dont_ask=True,
        exclude_capture=exclude_capture,
        parent=parent,
    )
    dlg.exec()
    return dlg.result_code() == RESULT_CONFIRM, dlg.dont_show_again()


def build_model_download_dialog(
    asset_name: str,
    size_label: str,
    eval_info: dict[str, Any],
    allow_fallback_existing: bool = False,
    exclude_capture: bool = True,
    parent: Optional[QWidget] = None,
) -> MonolithConfirmDialog:
    """Point 3 hardware-checked download confirmation dialog."""
    spec = eval_info["spec"]
    hw = eval_info["hw"]
    verdict = eval_info["verdict"]
    v_col = eval_info["color"]

    gpu_str = (
        f"{html.escape(str(hw['gpu_name']))}（总显存 {hw['vram_total_gb']} GB / 空闲 {hw['vram_free_gb']} GB）"
        if hw["vram_total_gb"] > 0
        else html.escape(str(hw["gpu_name"]))
    )

    body = (
        "<div style='color:#F1F3F5;'>"
        f"<p style='margin:0 0 8px 0;'>检测到所选资源 <b style='color:#3B82F6;'>{html.escape(asset_name)}</b>（安装包约 {html.escape(size_label)}）尚未下载，是否确认立即下载安装？</p>"
        "<p style='margin:0 0 3px 0; color:#9CA3AF;'><b>资源要求与预计占用：</b></p>"
        f"<p style='margin:0 0 3px 0;'>• 预计运行占用：{html.escape(str(spec['usage_text']))}</p>"
        f"<p style='margin:0 0 3px 0;'>• 最低配置要求：{html.escape(str(spec['min_text']))}</p>"
        f"<p style='margin:0 0 8px 0;'>• 推荐配置要求：{html.escape(str(spec['rec_text']))}</p>"
        "<p style='margin:0 0 3px 0; color:#9CA3AF;'><b>本机硬件检测结果：</b></p>"
        f"<p style='margin:0 0 2px 0;'>• CPU：{html.escape(str(hw['cpu_name']))}（{hw['cpu_cores']}核 {hw['cpu_threads']}线程）</p>"
        f"<p style='margin:0 0 2px 0;'>• 显卡：{gpu_str}</p>"
        f"<p style='margin:0 0 8px 0;'>• 内存：总计 {hw['ram_total_gb']} GB（当前可用 {hw['ram_avail_gb']} GB）</p>"
        f"<p style='margin:0; padding:6px 8px; background:rgba(255,255,255,0.05); border-radius:5px;'><b>兼容性评估：</b><span style='color:{v_col}; font-weight:bold;'>{html.escape(verdict)}</span></p>"
        "</div>"
    )
    return MonolithConfirmDialog(
        title="确认下载模型 / 加速引擎",
        body_html=body,
        confirm_text=f"确认下载 ({size_label})",
        cancel_text="暂不下载",
        icon_name="download",
        accent_color=v_col,
        show_dont_ask=False,
        secondary_text="先用现有模型 0 显存进入游戏模式" if allow_fallback_existing else "",
        exclude_capture=exclude_capture,
        parent=parent,
    )
