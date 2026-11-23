"""Main PySide6 controller: Multi-OCR, Dual ASR, 3 Display Modes, WDA Shield & Bidirectional Output."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

os.environ.setdefault("FOR_DISABLE_CONSOLE_CTRL_HANDLER", "1")

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_LOCAL_TOOLS_DIR = Path(_ROOT) / "tools"
_LOCAL_TMP_DIR = _LOCAL_TOOLS_DIR / "tmp"
_LOCAL_HF_DIR = _LOCAL_TOOLS_DIR / "hf_cache"
try:
    _LOCAL_TMP_DIR.mkdir(parents=True, exist_ok=True)
    _LOCAL_HF_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
os.environ.setdefault("HF_HOME", str(_LOCAL_HF_DIR))
os.environ.setdefault("TMP", str(_LOCAL_TMP_DIR))
os.environ.setdefault("TEMP", str(_LOCAL_TMP_DIR))

from app.dll_setup import has_nvidia_cuda_gpu, setup_cuda_dlls  # noqa: E402

setup_cuda_dlls()

from PySide6.QtCore import QObject, QPoint, QRect, QSignalBlocker, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QAction, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget  # noqa: E402

from app.asr_engine import (  # noqa: E402
    MicrophoneRecorder,
    RealtimeASREngine,
    list_input_microphones,
    list_loopback_devices,
)
from app.config import (  # noqa: E402
    ASR_COLOR,
    DEFAULT_CONFIG,
    ICON_PATH,
    ICON_PNG_PATH,
    INPLACE_STYLES,
    load_config,
    make_default_detached_style,
    make_region_config,
    next_region_number,
    save_config,
)
from app.llm_service import LocalLLMTranslator  # noqa: E402
from app.model_manager import (  # noqa: E402
    VULKAN_RUNTIME_SPEC,
    detect_system_hardware,
    download_asr_model,
    download_llm_model,
    download_vulkan_runtime,
    evaluate_hardware_for_asset,
    get_asr_catalog_entry,
    get_llm_catalog_entry,
    has_vulkan_runtime,
    is_asr_model_installed,
    is_llm_model_installed,
)
from app.ocr_engine import MultiRegionOCRWorker, MultilingualOCRManager  # noqa: E402
from app.process_audio import list_audio_target_apps  # noqa: E402
from app.ui.capture_box import CaptureRegionWindow  # noqa: E402
from app.ui.channel_card import ChannelCardWidget, DetachedCardWindow  # noqa: E402
from app.ui.control_panel import ControlPanelWindow  # noqa: E402
from app.ui.dialogs import (  # noqa: E402
    RESULT_CANCEL,
    RESULT_CONFIRM,
    RESULT_SECONDARY,
    build_model_download_dialog,
    confirm_clickthrough_lock,
    confirm_hide_windows,
    confirm_inplace_mode,
)
from app.ui.dock_window import DockWindow  # noqa: E402
from app.ui.inplace_overlay import InPlaceOverlayWindow  # noqa: E402
from app.ui.quick_capsule import QuickTranslateCapsule  # noqa: E402
from app.ui.snip_overlay import SnipOverlay, WindowPickerOverlay  # noqa: E402
from app.ui.theme import (  # noqa: E402
    ACCENT_CRIMSON,
    ACCENT_LIME,
    ACCENT_OCHRE,
    ACCENT_VERMILION,
    APP_QSS,
)
from app.win32_utils import (  # noqa: E402
    WDA_EXCLUDEFROMCAPTURE,
    GlobalHotkeyManager,
    get_window_display_affinity,
    send_ctrl_v_to_foreground,
)


class _BridgeSignals(QObject):
    status = Signal(str, str)
    ready = Signal()
    boot_progress = Signal(int, str, bool)
    self_check_done = Signal(list)
    ocr_result = Signal(str, str, str, object, str, int, int)
    ocr_motion = Signal(str)
    asr_result = Signal(str, str, str, int, int)
    asr_level = Signal(float)
    asr_source = Signal(str)
    error_msg = Signal(str)
    dl_progress = Signal(int, str, bool)
    api_test_done = Signal(bool, str)
    quick_result = Signal(str, int, str)
    quick_error = Signal(str)
    mic_level = Signal(float)
    telemetry_dirty = Signal()


class TranslatorController(QObject):
    """Coordinates Dock, Rangefinder Boxes, In-Place Overlays, Quick Capsule, Hotkeys & AI Backends."""

    def __init__(self, app: QApplication, self_test: bool = False) -> None:
        super().__init__()
        self.app = app
        self.self_test = self_test
        self.cfg = load_config()
        self._shutting_down = False
        self._models_ready = False
        self._translation_active = False
        self._last_results: dict[str, tuple[Any, ...]] = {}
        self._last_asr_source: str = ""
        self._dl_cancel = threading.Event()
        self._reconfig_lock = threading.Lock()
        self._hotkey_status: dict[str, bool] = {}

        if not bool(self.cfg.get("remember_window_pos", True)):
            self._reset_translation_rects_to_default()

        self.sig = _BridgeSignals()
        self.sig.status.connect(self._on_status)
        self.sig.ready.connect(self._on_ready)
        self.sig.ocr_result.connect(self._on_ocr_result)
        self.sig.ocr_motion.connect(self._on_ocr_motion)
        self.sig.asr_result.connect(self._on_asr_result)
        self.sig.asr_level.connect(self._on_asr_level)
        self.sig.asr_source.connect(self._on_asr_source)
        self.sig.error_msg.connect(self._on_error_msg)
        self.sig.dl_progress.connect(self._on_dl_progress)
        self.sig.api_test_done.connect(self._on_api_test_done)
        self.sig.quick_result.connect(self._on_quick_result)
        self.sig.quick_error.connect(self._on_quick_error)
        self.sig.mic_level.connect(self._on_mic_level)
        self.sig.telemetry_dirty.connect(self._refresh_telemetry_labels)

        # Configure translation backend
        self.llm = LocalLLMTranslator()
        self.llm.configure(
            engine_mode=str(self.cfg.get("llm_engine_mode", "local")),
            llama_backend=str(self.cfg.get("llama_backend", "auto")),
            local_model_id=str(self.cfg.get("local_model_id", "hy_mt_1_8b_q8")),
            custom_gguf_path=str(self.cfg.get("custom_gguf_path", "")),
            game_mode=bool(self.cfg.get("game_mode", False)),
            api_base_url=str(self.cfg.get("api_base_url", "")),
            api_key=str(self.cfg.get("api_key", "")),
            api_model=str(self.cfg.get("api_model", "")),
        )

        self.ocr_mgr = MultilingualOCRManager()
        self.ocr_worker = MultiRegionOCRWorker(
            llm=self.llm,
            ocr_mgr=self.ocr_mgr,
            on_result=lambda rid, en, zh, items, badge, om, lm: self.sig.ocr_result.emit(
                rid, en, zh, items, badge, om, lm
            ),
            on_error=lambda _rid, msg: self.sig.error_msg.emit(msg),
            on_motion=lambda rid: self.sig.ocr_motion.emit(rid),
        )
        self.ocr_worker.set_filter_chinese(bool(self.cfg.get("ocr_filter_chinese", False)))
        self.ocr_worker.set_tuning(
            float(self.cfg.get("ocr_interval_sec", 0.28)),
            float(self.cfg.get("ocr_diff_threshold", 1.6)),
        )

        self.asr_engine = RealtimeASREngine(
            llm=self.llm,
            on_result=lambda en, zh, badge, am, lm: self.sig.asr_result.emit(
                en, zh, badge, am, lm
            ),
            on_level=lambda lvl: self.sig.asr_level.emit(lvl),
            on_status=lambda txt: self.sig.asr_source.emit(txt),
            on_error=lambda msg: self.sig.error_msg.emit(msg),
        )
        self.asr_engine.set_lang(str(self.cfg.get("asr_lang", "auto")))
        self.asr_engine.set_tuning(
            float(self.cfg.get("asr_rms_threshold", 0.008)),
            float(self.cfg.get("asr_silence_flush_sec", 0.45)),
        )
        self.asr_engine.configure_source(
            audio_mode=str(self.cfg.get("audio_mode", "device")),
            target_pid=int(self.cfg.get("target_pid", 0)),
            target_exe=str(self.cfg.get("target_process_name", "")),
            device_name=str(self.cfg.get("audio_device_name", "")),
        )

        self.mic_recorder = MicrophoneRecorder(
            on_level=lambda lvl: self.sig.mic_level.emit(lvl)
        )

        # Windows & overlays registries
        self._capture_boxes: dict[str, CaptureRegionWindow] = {}
        self._merged_cards: dict[str, ChannelCardWidget] = {}
        self._detached_wins: dict[str, DetachedCardWindow] = {}
        self._inplace_overlays: dict[str, InPlaceOverlayWindow] = {}
        self._active_overlay: Optional[Any] = None
        self._dock_manually_minimized: bool = False

        exclude = bool(self.cfg.get("exclude_from_capture", True))
        disp_mode = str(self.cfg.get("display_mode", "floating"))
        stealth_hk = str(self.cfg.get("stealth_hotkey", "Alt+Q"))

        self.dock = DockWindow(
            self.cfg.get("dock_rect", [180, 480, 780, 390]),
            opacity=float(self.cfg.get("opacity", 0.94)),
            bg_color=str(self.cfg.get("panel_bg_color", "#202226")),
            exclude_capture=exclude,
            display_mode=disp_mode,
            stealth_hotkey=stealth_hk,
        )
        self._wire_dock()

        self.panel = ControlPanelWindow(self.cfg)
        self.sig.boot_progress.connect(self.panel.set_boot_progress)
        self.sig.self_check_done.connect(self.panel.set_self_check_report)
        self._wire_panel()

        prect = self.cfg.get("panel_rect", [220, 90, 960, 675])
        if isinstance(prect, list) and len(prect) == 4:
            px, py, pw, ph = [int(v) for v in prect]
            scr = QApplication.screenAt(QPoint(px + 80, py + 40))
            if scr is not None:
                self.panel.set_suppress_geometry_emit(True)
                self.panel.setGeometry(px, py, max(880, pw), max(610, ph))
                QTimer.singleShot(120, lambda: self.panel.set_suppress_geometry_emit(False))

        self.quick_capsule = QuickTranslateCapsule(
            target_lang=str(self.cfg.get("output_target_lang", "en")),
            tone=str(self.cfg.get("output_tone", "natural")),
            exclude_capture=exclude,
            voice_hotkey=str(self.cfg.get("voice_out_hotkey", "Alt+V")),
            voice_hold_mode=bool(self.cfg.get("voice_out_hold_mode", True)),
            auto_copy=bool(self.cfg.get("output_auto_copy", True)),
            minimal_mode=bool(self.cfg.get("capsule_minimal_mode", False)),
            minimal_rect=list(self.cfg.get("capsule_rect", [520, 220, 560, 120])),
            full_pos=list(self.cfg.get("capsule_full_pos", [])),
            capsule_opacity=float(self.cfg.get("capsule_opacity", 0.90)),
            capsule_bg_color=str(self.cfg.get("capsule_bg_color", "#202226")),
            capsule_font_size=int(self.cfg.get("capsule_font_size", 15)),
            capsule_font_color=str(self.cfg.get("capsule_font_color", "#F1F3F5")),
        )
        self.quick_capsule.translate_text_requested.connect(self._on_quick_text_requested)
        self.quick_capsule.voice_toggle_requested.connect(self._on_quick_voice_toggled)
        self.quick_capsule.output_prefs_changed.connect(self._on_capsule_prefs_changed)
        self.quick_capsule.auto_copy_toggled.connect(self._on_capsule_auto_copy_toggled)
        self.quick_capsule.minimal_mode_toggled.connect(self.set_capsule_minimal_mode)
        self.quick_capsule.click_through_requested.connect(self._on_capsule_clickthrough_requested)
        self.quick_capsule.geometry_changed.connect(lambda r: self._update_cfg_key("capsule_rect", r))
        self.quick_capsule.full_pos_changed.connect(lambda p: self._update_cfg_key("capsule_full_pos", p))

        # Global hotkeys
        self.hotkeys = GlobalHotkeyManager(self.app, parent=self)
        self.hotkeys.triggered.connect(self._on_hotkey_triggered)
        self.hotkeys.released.connect(self._on_hotkey_released)
        self._register_all_hotkeys()

        self._build_tray()
        self._rebuild_channels()
        self._hide_all_windows()
        self._update_clickthrough_indicator()
        self._refresh_telemetry_labels()

        # Startup shows ONLY Control Center main window; translation UI opens on 「开始翻译」
        self.panel.set_boot_progress(8, "正在初始化本地或云端模型...", False)
        self.panel.set_translation_active(False)
        self.panel.show()
        self.panel.raise_()

        threading.Thread(target=self._load_models_bg, name="ModelBoot", daemon=True).start()

        if self.self_test:
            QTimer.singleShot(50000, self.shutdown)

    # ---- Wiring ----
    def _wire_dock(self) -> None:
        self.dock.add_region_requested.connect(self.add_region)
        self.dock.snip_requested.connect(self.start_snip)
        self.dock.quick_capsule_requested.connect(lambda: self.open_quick_capsule(voice=False))
        self.dock.control_panel_requested.connect(lambda: self.show_control_panel(tab=0))
        self.dock.merge_all_requested.connect(self.merge_all_channels)
        self.dock.display_mode_changed.connect(lambda m: self.set_display_mode(m, from_user=True))
        self.dock.exclude_capture_toggled.connect(self.set_exclude_from_capture)
        self.dock.unlock_clickthrough_requested.connect(self.unlock_all_clickthrough)
        self.dock.minimize_requested.connect(self._minimize_to_tray)
        self.dock.close_translation_requested.connect(self.close_translation_ui)
        self.dock.geometry_changed.connect(lambda r: self._update_cfg_key("dock_rect", r))

    def _wire_panel(self) -> None:
        self.panel.start_translation_requested.connect(self.start_translation)
        self.panel.close_translation_requested.connect(self.close_translation_ui)
        self.panel.quit_app_requested.connect(self.shutdown)
        self.panel.self_check_requested.connect(self.run_self_check)
        self.panel.remember_window_pos_changed.connect(self.set_remember_window_pos)

        self.panel.add_region_requested.connect(self.add_region)
        self.panel.remove_region_requested.connect(self.remove_region)
        self.panel.region_lang_changed.connect(self.set_region_lang)
        self.panel.region_enabled_changed.connect(self.set_region_enabled)
        self.panel.region_box_visible_changed.connect(self.set_region_box_visible)
        self.panel.region_smart_clean_changed.connect(self.set_region_smart_clean)
        self.panel.region_locate_requested.connect(self.locate_region)
        self.panel.region_detach_toggled.connect(self.toggle_channel_detach)
        self.panel.merge_all_requested.connect(self.merge_all_channels)
        self.panel.detach_all_requested.connect(self.detach_all_channels)
        self.panel.show_all_boxes_requested.connect(self.set_all_boxes_visible)
        self.panel.ocr_tuning_changed.connect(self._on_ocr_tuning)
        self.panel.ocr_filter_chinese_changed.connect(self.set_ocr_filter_chinese)
        self.panel.display_mode_changed.connect(lambda m: self.set_display_mode(m, from_user=True))
        self.panel.exclude_capture_changed.connect(self.set_exclude_from_capture)
        self.panel.stealth_settings_changed.connect(self._on_stealth_settings)
        self.panel.inplace_style_changed.connect(self._on_inplace_style_changed)

        self.panel.tier_preset_requested.connect(self.apply_tier_preset)
        self.panel.game_mode_changed.connect(self.set_game_mode)
        self.panel.local_engine_apply_requested.connect(self._on_apply_local_engine)
        self.panel.api_engine_apply_requested.connect(self._on_apply_api_engine)
        self.panel.api_test_requested.connect(self._on_test_api)
        self.panel.download_asset_requested.connect(self._on_download_asset_prompt)
        self.panel.asr_backend_changed.connect(self._on_asr_backend_changed)

        self.panel.asr_enabled_changed.connect(self.set_asr_enabled)
        self.panel.audio_mode_changed.connect(self.set_audio_mode)
        self.panel.target_process_changed.connect(self.set_target_process)
        self.panel.refresh_apps_requested.connect(self.refresh_apps)
        self.panel.pick_window_requested.connect(self.start_window_picker)
        self.panel.audio_device_changed.connect(self.set_audio_device)
        self.panel.refresh_devices_requested.connect(self.refresh_devices)
        self.panel.mic_device_changed.connect(lambda n: self._update_cfg_key("mic_device_name", n))
        self.panel.refresh_mics_requested.connect(self.refresh_mics)
        self.panel.asr_lang_changed.connect(self.set_asr_lang)
        self.panel.asr_tuning_changed.connect(self._on_asr_tuning)

        self.panel.quick_settings_changed.connect(self._on_quick_settings)
        self.panel.unlock_hotkey_changed.connect(self._on_unlock_hotkey_changed)
        self.panel.capsule_minimal_changed.connect(self.set_capsule_minimal_mode)
        self.panel.capsule_style_changed.connect(self._on_capsule_style_changed)
        self.panel.open_quick_capsule_requested.connect(lambda: self.open_quick_capsule(voice=False))

        self.panel.global_appearance_changed.connect(self._on_global_appearance_changed)
        self.panel.detached_appearance_changed.connect(self._on_detached_appearance_changed)
        self.panel.show_author_changed.connect(self.set_show_comment_author)
        self.panel.unlock_clickthrough_requested.connect(self.unlock_all_clickthrough)
        self.panel.reset_positions_requested.connect(self.reset_positions)
        self.panel.geometry_changed.connect(lambda r: self._update_cfg_key("panel_rect", r))

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        if ICON_PNG_PATH.exists():
            self.tray.setIcon(QIcon(str(ICON_PNG_PATH)))
        elif ICON_PATH.exists():
            self.tray.setIcon(QIcon(str(ICON_PATH)))
        self.tray.setToolTip("Glimpse · 瞬译")

        menu = QMenu()
        act_panel = QAction("打开控制中心", self)
        act_panel.triggered.connect(lambda: self.show_control_panel(tab=0))
        self._act_tray_trans = QAction("开始 / 关闭翻译页面", self)
        self._act_tray_trans.triggered.connect(self.toggle_translation_ui)
        act_quick = QAction("双向快翻胶囊", self)
        act_quick.triggered.connect(lambda: self.open_quick_capsule(voice=False))
        act_unlock = QAction("解除全部穿透锁定", self)
        act_unlock.triggered.connect(self.unlock_all_clickthrough)
        act_quit = QAction("退出软件", self)
        act_quit.triggered.connect(self.shutdown)

        menu.addAction(act_panel)
        menu.addAction(self._act_tray_trans)
        menu.addAction(act_quick)
        menu.addAction(act_unlock)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_control_panel(tab=0)
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def _update_cfg_key(self, key: str, val: Any) -> None:
        self.cfg[key] = val

    def _find_region(self, rid: str) -> Optional[dict[str, Any]]:
        for reg in self.cfg["regions"]:
            if reg["id"] == rid:
                return reg
        return None

    # ---- Hotkey Management ----
    def _register_all_hotkeys(self) -> None:
        s_hk = str(self.cfg.get("stealth_hotkey", "Alt+Q"))
        s_hold = str(self.cfg.get("stealth_trigger_mode", "toggle")) == "hold"
        q_hk = str(self.cfg.get("quick_hotkey", "Alt+T"))
        v_hk = str(self.cfg.get("voice_out_hotkey", "Alt+V"))
        v_hold = bool(self.cfg.get("voice_out_hold_mode", True))
        u_hk = str(self.cfg.get("unlock_clickthrough_hotkey", "Ctrl+Alt+U"))

        self._hotkey_status = {
            "stealth": self.hotkeys.register_slot("stealth", s_hk, hold_mode=s_hold),
            "quick_text": self.hotkeys.register_slot("quick_text", q_hk, hold_mode=False),
            "quick_voice": self.hotkeys.register_slot("quick_voice", v_hk, hold_mode=v_hold),
            "unlock_clickthrough": self.hotkeys.register_slot("unlock_clickthrough", u_hk, hold_mode=False),
        }
        ok_all = all(self._hotkey_status.values())
        summary = (
            f"热键就绪 ({s_hk} / {q_hk} / {v_hk} / 解锁 {u_hk})"
            if ok_all
            else f"部分热键被占用: {[k for k, v in self._hotkey_status.items() if not v]}"
        )
        self.panel.set_hotkey_status_text(summary, ok=ok_all)
        self.quick_capsule.set_voice_hotkey_hint(v_hk, v_hold)
        self._update_clickthrough_indicator()

    def _any_window_visible(self) -> bool:
        if self.dock.isVisible() or self.quick_capsule.isVisible():
            return True
        for b in self._capture_boxes.values():
            if b.isVisible():
                return True
        for dw in self._detached_wins.values():
            if dw.isVisible():
                return True
        for io in self._inplace_overlays.values():
            if io.isVisible():
                return True
        return False

    def _hide_all_windows(self) -> None:
        self.ocr_worker.set_continuous_paused(True)
        for io in self._inplace_overlays.values():
            io.hide()
        for box in self._capture_boxes.values():
            box.hide()
        for dw in self._detached_wins.values():
            dw.hide()
        self.quick_capsule.hide()
        self.dock.hide()

    @staticmethod
    def _snap_window_above_rect(win: QWidget, anchor_rect: list[int]) -> None:
        if not anchor_rect or len(anchor_rect) != 4:
            return
        rx, ry, rw, rh = [int(v) for v in anchor_rect]
        dw, dh = win.width(), win.height()
        scr = QApplication.screenAt(QPoint(rx + rw // 2, ry)) or QApplication.primaryScreen()
        avail: QRect = scr.availableGeometry() if scr else QRect(0, 0, 1920, 1080)
        target_x = max(avail.left() + 8, min(avail.right() - dw - 8, rx + (rw - dw) // 2))
        if ry - avail.top() >= max(90, int(dh * 0.55)):
            target_y = max(avail.top() + 6, ry - dh - 10)
        else:
            target_y = min(avail.bottom() - dh - 8, ry + rh + 10)
        if hasattr(win, "set_suppress_geometry_emit"):
            win.set_suppress_geometry_emit(True)
        win.move(target_x, target_y)
        if hasattr(win, "set_suppress_geometry_emit"):
            QTimer.singleShot(120, lambda w=win: w.set_suppress_geometry_emit(False))

    @staticmethod
    def _restore_saved_pos(win: QWidget, rect: list[int]) -> None:
        if not rect or len(rect) < 2:
            return
        if hasattr(win, "set_suppress_geometry_emit"):
            win.set_suppress_geometry_emit(True)
        win.move(int(rect[0]), int(rect[1]))
        if hasattr(win, "set_suppress_geometry_emit"):
            QTimer.singleShot(120, lambda w=win: w.set_suppress_geometry_emit(False))

    def _on_hotkey_triggered(self, slot: str) -> None:
        if slot == "unlock_clickthrough":
            self.unlock_all_clickthrough()
        elif slot == "quick_text":
            self.open_quick_capsule(voice=False)
        elif slot == "quick_voice":
            v_hold = bool(self.cfg.get("voice_out_hold_mode", True))
            if v_hold:
                if not self.mic_recorder.is_recording:
                    self.open_quick_capsule(voice=True)
            else:
                if self.quick_capsule.isVisible() and self.mic_recorder.is_recording:
                    self._on_quick_voice_toggled(False)
                else:
                    self.open_quick_capsule(voice=True)
        elif slot == "stealth":
            if not self._translation_active:
                if self._models_ready:
                    self.start_translation()
                return
            trig = str(self.cfg.get("stealth_trigger_mode", "toggle"))
            pos_mode = str(self.cfg.get("stealth_position", "above_region"))

            if trig == "toggle" and self._any_window_visible():
                self.cfg["display_mode"] = "stealth"
                self.dock.set_mode_state("stealth", str(self.cfg.get("stealth_hotkey", "Alt+Q")))
                self.panel.set_display_mode_indicator("stealth")
                self._hide_all_windows()
                return

            # Reveal path: respect manually-minimized Dock when detached floating windows exist
            has_detached = bool(self._detached_wins)
            show_dock = not (self._dock_manually_minimized and has_detached)

            if show_dock:
                anchor_rect = None
                # Only anchor Dock to a non-detached enabled region (never steal detached region boxes)
                for reg in self.cfg["regions"]:
                    rid = reg["id"]
                    if (
                        reg.get("enabled", True)
                        and not reg.get("detached", False)
                        and rid in self._capture_boxes
                    ):
                        anchor_rect = self._capture_boxes[rid].get_logical_rect()
                        break
                if pos_mode == "above_region" and anchor_rect:
                    self._snap_window_above_rect(self.dock, anchor_rect)
                else:
                    self._restore_saved_pos(
                        self.dock, self.cfg.get("dock_rect", [180, 480, 780, 390])
                    )
                self.dock._stealth_highlight = True
                self.dock.show()
                self.dock.raise_()
                self.dock.update()

            # Reveal all detached floating HUDs (snap each OCR window above its own capture box if above_region)
            for cid, dw in self._detached_wins.items():
                if cid == "asr":
                    self._restore_saved_pos(
                        dw, self.cfg.get("asr_float_rect", [780, 560, 480, 195])
                    )
                else:
                    reg = self._find_region(cid)
                    if pos_mode == "above_region":
                        anchor = (
                            self._capture_boxes[cid].get_logical_rect()
                            if cid in self._capture_boxes
                            else (reg["box_rect"] if reg is not None else None)
                        )
                        if anchor:
                            self._snap_window_above_rect(dw, anchor)
                    elif reg is not None:
                        self._restore_saved_pos(dw, reg["float_rect"])
                dw.set_stealth_highlight(True)
                dw.show()
                dw.raise_()

            if bool(self.cfg.get("capsule_minimal_mode", False)):
                self.quick_capsule.show()
                self.quick_capsule.raise_()

            if self._models_ready:
                if show_dock:
                    self.dock.set_status("正在热键抓帧识别...", ACCENT_OCHRE)
                threading.Thread(target=self._run_stealth_capture_all, daemon=True).start()

    def _on_hotkey_released(self, slot: str) -> None:
        if slot == "stealth" and str(self.cfg.get("stealth_trigger_mode", "toggle")) == "hold":
            self._hide_all_windows()
        elif slot == "quick_voice" and bool(self.cfg.get("voice_out_hold_mode", True)):
            if self.mic_recorder.is_recording:
                self._on_quick_voice_toggled(False)

    def _run_stealth_capture_all(self) -> None:
        for reg in list(self.cfg.get("regions", [])):
            if reg.get("enabled", True):
                self.ocr_worker.capture_region_once(reg["id"])

    # ---- Display Modes & WDA Anti-Self-Capture ----
    def set_exclude_from_capture(self, enabled: bool) -> None:
        val = bool(enabled)
        self.cfg["exclude_from_capture"] = val
        self.dock.set_exclude_from_capture(val)
        self.panel.set_exclude_from_capture(val)
        self.quick_capsule.set_exclude_from_capture(val)
        for box in self._capture_boxes.values():
            box.set_exclude_from_capture(val)
        for dw in self._detached_wins.values():
            dw.set_exclude_from_capture(val)
        for io in self._inplace_overlays.values():
            io.set_exclude_from_capture(val)
        self._refresh_telemetry_labels()

    def set_display_mode(self, mode: str, from_user: bool = False) -> None:
        if mode not in ("floating", "stealth", "inplace"):
            return
        prev_mode = str(self.cfg.get("display_mode", "floating"))
        exclude = bool(self.cfg.get("exclude_from_capture", True))

        if from_user and mode == "stealth" and not bool(self.cfg.get("suppress_hide_prompt", False)):
            parent_w = self.panel if self.panel.isVisible() else self.dock
            ok, dont_ask = confirm_hide_windows(
                str(self.cfg.get("stealth_hotkey", "Alt+Q")),
                bool(self._hotkey_status.get("stealth", True)),
                str(self.cfg.get("stealth_trigger_mode", "toggle")),
                exclude_capture=exclude,
                parent=parent_w,
            )
            if dont_ask:
                self.cfg["suppress_hide_prompt"] = True
                save_config(self.cfg)
            if not ok:
                self.dock.set_mode_state(prev_mode, str(self.cfg.get("stealth_hotkey", "Alt+Q")))
                self.panel.set_display_mode_indicator(prev_mode)
                return

        if from_user and mode == "inplace" and not bool(self.cfg.get("suppress_inplace_prompt", False)):
            style_key = str(self.cfg.get("inplace_style", "blend"))
            style_lbl = dict(INPLACE_STYLES).get(style_key, style_key)
            parent_w = self.panel if self.panel.isVisible() else self.dock
            ok, dont_ask = confirm_inplace_mode(
                style_lbl,
                exclude_capture=exclude,
                parent=parent_w,
            )
            if dont_ask:
                self.cfg["suppress_inplace_prompt"] = True
                save_config(self.cfg)
            if not ok:
                self.dock.set_mode_state(prev_mode, str(self.cfg.get("stealth_hotkey", "Alt+Q")))
                self.panel.set_display_mode_indicator(prev_mode)
                return

        self.cfg["display_mode"] = mode
        self.dock.set_mode_state(mode, str(self.cfg.get("stealth_hotkey", "Alt+Q")))
        self.panel.set_display_mode_indicator(mode)
        self._apply_display_mode_visibility(initial=False)

    def _apply_display_mode_visibility(self, initial: bool = False) -> None:
        if not self._translation_active:
            self._hide_all_windows()
            return
        mode = str(self.cfg.get("display_mode", "floating"))
        if mode == "floating":
            self.ocr_worker.set_continuous_paused(False)
            for io in self._inplace_overlays.values():
                io.hide()
            for reg in self.cfg["regions"]:
                rid = reg["id"]
                if rid in self._capture_boxes:
                    if reg.get("box_visible", True):
                        self._capture_boxes[rid].show()
                    else:
                        self._capture_boxes[rid].hide()
            for cid, dw in self._detached_wins.items():
                dw.set_stealth_highlight(False)
                if cid == "asr":
                    self._restore_saved_pos(
                        dw, self.cfg.get("asr_float_rect", [780, 560, 480, 195])
                    )
                else:
                    reg = self._find_region(cid)
                    if reg is not None:
                        self._restore_saved_pos(dw, reg["float_rect"])
                dw.show()
            if bool(self.cfg.get("capsule_minimal_mode", False)):
                self.quick_capsule.show()
            show_dock = not (self._dock_manually_minimized and bool(self._detached_wins))
            self.dock._stealth_highlight = False
            self._restore_saved_pos(self.dock, self.cfg.get("dock_rect", [180, 480, 780, 390]))
            if show_dock:
                self.dock.show()
                self.dock.raise_()
                self.dock.update()
            else:
                self.dock.hide()
        elif mode == "stealth":
            if initial and self.self_test:
                return
            self._hide_all_windows()
        elif mode == "inplace":
            self.ocr_worker.set_continuous_paused(False)
            for box in self._capture_boxes.values():
                box.hide()
            style = str(self.cfg.get("inplace_style", "blend"))
            show_author = bool(self.cfg.get("show_comment_author", True))
            for reg in self.cfg["regions"]:
                rid = reg["id"]
                if not reg.get("enabled", True):
                    if rid in self._inplace_overlays:
                        self._inplace_overlays[rid].hide()
                    continue
                if rid in self._inplace_overlays and rid in self._capture_boxes:
                    io = self._inplace_overlays[rid]
                    io.sync_region_rect(self._capture_boxes[rid].get_logical_interior_rect())
                    io.set_overlay_style(style, show_author=show_author)
                    cached = self._last_results.get(rid)
                    if cached is not None and len(cached) >= 6:
                        io.set_blocks(list(cached[5] or []), show_author=show_author)
                    io.show()
            self.dock.show()
            self.dock._stealth_highlight = False
            self.dock.update()
        self._apply_all_appearances()

    def _on_stealth_settings(self, hotkey: str, trigger_mode: str, position: str) -> None:
        self.cfg["stealth_hotkey"] = hotkey
        self.cfg["stealth_trigger_mode"] = trigger_mode
        self.cfg["stealth_position"] = position
        self.dock.set_mode_state(str(self.cfg.get("display_mode", "floating")), hotkey)
        self._register_all_hotkeys()

    def _on_inplace_style_changed(self, style: str) -> None:
        self.cfg["inplace_style"] = style
        show_author = bool(self.cfg.get("show_comment_author", True))
        for io in self._inplace_overlays.values():
            io.set_overlay_style(style, show_author=show_author)

    # ---- Channels & Geometry Rebuild ----
    def _rebuild_channels(self) -> None:
        regions: list[dict[str, Any]] = self.cfg["regions"]
        valid_ids = {r["id"] for r in regions} | {"asr"}
        exclude = bool(self.cfg.get("exclude_from_capture", True))
        disp_mode = str(self.cfg.get("display_mode", "floating"))
        inplace_style = str(self.cfg.get("inplace_style", "blend"))

        for rid in list(self._capture_boxes.keys()):
            if rid not in valid_ids:
                self.ocr_worker.remove_region(rid)
                box = self._capture_boxes.pop(rid)
                box.close()
                box.deleteLater()

        for rid in list(self._inplace_overlays.keys()):
            if rid not in valid_ids:
                io = self._inplace_overlays.pop(rid)
                io.close()
                io.deleteLater()

        for cid in list(self._detached_wins.keys()):
            if cid not in valid_ids:
                dw = self._detached_wins.pop(cid)
                dw.close()
                dw.deleteLater()

        opacity = float(self.cfg.get("opacity", 0.94))
        bg_color = str(self.cfg.get("panel_bg_color", "#202226"))
        show_author = bool(self.cfg.get("show_comment_author", True))

        merged_list: list[ChannelCardWidget] = []

        for idx, reg in enumerate(regions, start=1):
            rid = reg["id"]
            idx_tag = f"{idx:02d}"
            if rid not in self._capture_boxes:
                box = CaptureRegionWindow(
                    rid,
                    reg["name"],
                    reg["color"],
                    reg["box_rect"],
                    index_num=idx,
                    exclude_capture=exclude,
                )
                box.geometry_changed.connect(self._on_box_geometry)
                self._capture_boxes[rid] = box
            box = self._capture_boxes[rid]
            box.update_meta(reg["name"], reg["color"], bool(reg["enabled"]), index_num=idx)
            box.set_exclude_from_capture(exclude)

            if rid not in self._inplace_overlays:
                io = InPlaceOverlayWindow(
                    rid,
                    box.get_logical_interior_rect(),
                    style=inplace_style,
                    exclude_capture=exclude,
                    opacity=opacity,
                )
                self._inplace_overlays[rid] = io
            self._inplace_overlays[rid].sync_region_rect(box.get_logical_interior_rect())
            self._inplace_overlays[rid].set_exclude_from_capture(exclude)
            self._inplace_overlays[rid].set_overlay_style(inplace_style, show_author=show_author)

            if self._translation_active and disp_mode == "floating" and reg.get("box_visible", True):
                box.show()
            else:
                box.hide()

            self.ocr_worker.upsert_region(
                region_id=rid,
                lang=reg.get("lang", "auto"),
                enabled=bool(reg.get("enabled", True)),
                get_rect=box.get_capture_rect,
                smart_clean=bool(reg.get("smart_clean", True)),
            )

            if reg.get("detached", False):
                if rid in self._merged_cards:
                    mc = self._merged_cards.pop(rid)
                    mc.setParent(None)
                    mc.deleteLater()
                st_r = self._resolve_window_appearance(rid)
                if rid not in self._detached_wins:
                    dw = DetachedCardWindow(
                        channel_id=rid,
                        title=reg["name"],
                        color_hex=reg["color"],
                        rect=reg["float_rect"],
                        index_tag=idx_tag,
                        opacity=float(st_r["opacity"]),
                        bg_color=str(st_r["bg_color"]),
                        is_asr=False,
                        exclude_capture=exclude,
                    )
                    self._wire_card_signals(dw.card)
                    dw.geometry_changed.connect(self._on_float_geometry)
                    self._detached_wins[rid] = dw
                dw = self._detached_wins[rid]
                dw.update_meta(reg["name"], reg["color"], bool(reg["enabled"]), index_tag=idx_tag)
                cached = self._last_results.get(rid)
                if cached is not None:
                    dw.card.set_translation(*cached)
                dw.set_exclude_from_capture(exclude)
                dw.set_click_through(bool(reg.get("click_through", False)))
                if self._translation_active and disp_mode != "stealth":
                    dw.show()
                else:
                    dw.hide()
            else:
                if rid in self._detached_wins:
                    dw = self._detached_wins.pop(rid)
                    dw.close()
                    dw.deleteLater()
                if rid not in self._merged_cards:
                    mc = ChannelCardWidget(
                        channel_id=rid,
                        title=reg["name"],
                        color_hex=reg["color"],
                        index_tag=idx_tag,
                        is_asr=False,
                        is_detached=False,
                    )
                    self._wire_card_signals(mc)
                    self._merged_cards[rid] = mc
                mc = self._merged_cards[rid]
                mc.update_meta(reg["name"], reg["color"], bool(reg["enabled"]), index_tag=idx_tag)
                cached = self._last_results.get(rid)
                if cached is not None:
                    mc.set_translation(*cached)
                merged_list.append(mc)

        # ASR channel
        asr_enabled = bool(self.cfg.get("asr_enabled", True))
        if self.cfg.get("asr_detached", False):
            if "asr" in self._merged_cards:
                mc = self._merged_cards.pop("asr")
                mc.setParent(None)
                mc.deleteLater()
            st_a = self._resolve_window_appearance("asr")
            if "asr" not in self._detached_wins:
                dw = DetachedCardWindow(
                    channel_id="asr",
                    title="语音同传",
                    color_hex=ASR_COLOR,
                    rect=self.cfg["asr_float_rect"],
                    index_tag="ASR",
                    opacity=float(st_a["opacity"]),
                    bg_color=str(st_a["bg_color"]),
                    is_asr=True,
                    exclude_capture=exclude,
                )
                self._wire_card_signals(dw.card)
                dw.geometry_changed.connect(self._on_float_geometry)
                self._detached_wins["asr"] = dw
            dw = self._detached_wins["asr"]
            dw.update_meta("语音同传", ASR_COLOR, asr_enabled, index_tag="ASR")
            if self._last_asr_source:
                dw.card.set_source_hint(self._last_asr_source)
            cached_asr = self._last_results.get("asr")
            if cached_asr is not None:
                dw.card.set_translation(*cached_asr)
            dw.set_exclude_from_capture(exclude)
            dw.set_click_through(bool(self.cfg.get("asr_click_through", False)))
            if self._translation_active and disp_mode != "stealth":
                dw.show()
            else:
                dw.hide()
        else:
            if "asr" in self._detached_wins:
                dw = self._detached_wins.pop("asr")
                dw.close()
                dw.deleteLater()
            if "asr" not in self._merged_cards:
                mc = ChannelCardWidget(
                    channel_id="asr",
                    title="语音同传",
                    color_hex=ASR_COLOR,
                    index_tag="ASR",
                    is_asr=True,
                    is_detached=False,
                )
                self._wire_card_signals(mc)
                self._merged_cards["asr"] = mc
            mc = self._merged_cards["asr"]
            mc.update_meta("语音同传", ASR_COLOR, asr_enabled, index_tag="ASR")
            if self._last_asr_source:
                mc.set_source_hint(self._last_asr_source)
            cached_asr = self._last_results.get("asr")
            if cached_asr is not None:
                mc.set_translation(*cached_asr)
            merged_list.append(mc)

        self.dock.set_merged_cards(merged_list)
        self._apply_all_appearances()
        self.panel.refresh_regions(regions)
        det_channels = [(str(r["id"]), str(r.get("name", r["id"]))) for r in regions]
        det_channels.append(("asr", "语音同传"))
        self.panel.refresh_detached_channels(
            det_channels, self.cfg.get("detached_styles", {})
        )

    def _wire_card_signals(self, card: ChannelCardWidget) -> None:
        card.detach_requested.connect(self.detach_channel)
        card.merge_requested.connect(self.merge_channel)
        card.locate_requested.connect(self.locate_region)
        card.toggle_enabled_requested.connect(self.toggle_channel_enabled)
        card.toggle_clickthrough_requested.connect(self.toggle_channel_clickthrough)

    def _get_card(self, cid: str) -> Optional[ChannelCardWidget]:
        if cid in self._merged_cards:
            return self._merged_cards[cid]
        if cid in self._detached_wins:
            return self._detached_wins[cid].card
        return None

    def _on_box_geometry(self, rid: str, rect: list[int]) -> None:
        reg = self._find_region(rid)
        if reg is not None:
            reg["box_rect"] = rect
        if rid in self._capture_boxes and rid in self._inplace_overlays:
            self._inplace_overlays[rid].sync_region_rect(
                self._capture_boxes[rid].get_logical_interior_rect()
            )

    def _on_float_geometry(self, cid: str, rect: list[int]) -> None:
        if cid == "asr":
            self.cfg["asr_float_rect"] = rect
        else:
            reg = self._find_region(cid)
            if reg is not None:
                reg["float_rect"] = rect

    # ---- Channel management actions ----
    def add_region(self) -> None:
        regions = self.cfg["regions"]
        num = next_region_number(regions)
        reg = make_region_config(num)
        regions.append(reg)
        self._rebuild_channels()
        self.locate_region(reg["id"])

    def remove_region(self, rid: str) -> None:
        regions = self.cfg["regions"]
        if len(regions) <= 1:
            return
        self.cfg["regions"] = [r for r in regions if r["id"] != rid]
        self._last_results.pop(rid, None)
        self._rebuild_channels()

    def set_region_lang(self, rid: str, lang: str) -> None:
        reg = self._find_region(rid)
        if reg is not None:
            reg["lang"] = lang
            self._rebuild_channels()

    def set_region_enabled(self, rid: str, enabled: bool) -> None:
        reg = self._find_region(rid)
        if reg is not None:
            reg["enabled"] = enabled
            self._rebuild_channels()

    def set_region_box_visible(self, rid: str, visible: bool) -> None:
        reg = self._find_region(rid)
        if reg is not None:
            reg["box_visible"] = visible
            self._rebuild_channels()

    def set_region_smart_clean(self, rid: str, smart_clean: bool) -> None:
        reg = self._find_region(rid)
        if reg is not None:
            reg["smart_clean"] = smart_clean
            self._rebuild_channels()

    def set_all_boxes_visible(self, visible: bool) -> None:
        for reg in self.cfg["regions"]:
            reg["box_visible"] = visible
        self._rebuild_channels()

    def locate_region(self, rid: str) -> None:
        reg = self._find_region(rid)
        if reg is not None and not reg.get("box_visible", True):
            reg["box_visible"] = True
            self._rebuild_channels()
        if rid in self._capture_boxes:
            self._capture_boxes[rid].show()
            self._capture_boxes[rid].pulse_highlight()

    def detach_channel(self, cid: str) -> None:
        if cid == "asr":
            self.cfg["asr_detached"] = True
        else:
            reg = self._find_region(cid)
            if reg is not None:
                reg["detached"] = True
        self._rebuild_channels()

    def merge_channel(self, cid: str) -> None:
        if cid == "asr":
            self.cfg["asr_detached"] = False
            self.cfg["asr_click_through"] = False
        else:
            reg = self._find_region(cid)
            if reg is not None:
                reg["detached"] = False
                reg["click_through"] = False
        self._rebuild_channels()

    def toggle_channel_detach(self, cid: str) -> None:
        if cid == "asr":
            self.cfg["asr_detached"] = not bool(self.cfg.get("asr_detached", False))
        else:
            reg = self._find_region(cid)
            if reg is not None:
                reg["detached"] = not bool(reg.get("detached", False))
        self._rebuild_channels()

    def merge_all_channels(self) -> None:
        for reg in self.cfg["regions"]:
            reg["detached"] = False
            reg["click_through"] = False
        self.cfg["asr_detached"] = False
        self.cfg["asr_click_through"] = False
        self._rebuild_channels()

    def detach_all_channels(self) -> None:
        for reg in self.cfg["regions"]:
            reg["detached"] = True
        self.cfg["asr_detached"] = True
        self._rebuild_channels()

    def toggle_channel_enabled(self, cid: str) -> None:
        if cid == "asr":
            self.set_asr_enabled(not bool(self.cfg.get("asr_enabled", True)))
            with QSignalBlocker(self.panel._chk_asr_enabled):
                self.panel._chk_asr_enabled.setChecked(bool(self.cfg["asr_enabled"]))
        else:
            reg = self._find_region(cid)
            if reg is not None:
                reg["enabled"] = not bool(reg.get("enabled", True))
                self._rebuild_channels()

    def _may_enable_clickthrough(self, parent_w: Optional[Any] = None) -> bool:
        if bool(self.cfg.get("suppress_clickthrough_prompt", False)):
            return True
        ok, dont_ask = confirm_clickthrough_lock(
            str(self.cfg.get("unlock_clickthrough_hotkey", "Ctrl+Alt+U")),
            exclude_capture=bool(self.cfg.get("exclude_from_capture", True)),
            parent=parent_w or self.dock,
        )
        if dont_ask:
            self.cfg["suppress_clickthrough_prompt"] = True
            save_config(self.cfg)
        return ok

    def _update_clickthrough_indicator(self) -> None:
        any_locked = bool(self.cfg.get("asr_click_through", False)) or bool(
            self.cfg.get("capsule_click_through", False)
        )
        if not any_locked:
            for reg in self.cfg.get("regions", []):
                if bool(reg.get("click_through", False)):
                    any_locked = True
                    break
        self.dock.set_clickthrough_active(
            any_locked, str(self.cfg.get("unlock_clickthrough_hotkey", "Ctrl+Alt+U"))
        )

    def toggle_channel_clickthrough(self, cid: str) -> None:
        if cid == "asr":
            val = not bool(self.cfg.get("asr_click_through", False))
            if val and not self._may_enable_clickthrough(self._detached_wins.get(cid)):
                return
            self.cfg["asr_click_through"] = val
        else:
            reg = self._find_region(cid)
            if reg is None:
                return
            val = not bool(reg.get("click_through", False))
            if val and not self._may_enable_clickthrough(self._detached_wins.get(cid)):
                return
            reg["click_through"] = val
        if cid in self._detached_wins:
            self._detached_wins[cid].set_click_through(val)
        self._update_clickthrough_indicator()

    def _on_capsule_clickthrough_requested(self, enabled: bool) -> None:
        if enabled and not self._may_enable_clickthrough(self.quick_capsule):
            return
        self.cfg["capsule_click_through"] = bool(enabled)
        self.quick_capsule.set_click_through(bool(enabled))
        self._update_clickthrough_indicator()

    def unlock_all_clickthrough(self) -> None:
        for reg in self.cfg["regions"]:
            reg["click_through"] = False
        self.cfg["asr_click_through"] = False
        self.cfg["capsule_click_through"] = False
        for dw in self._detached_wins.values():
            dw.set_click_through(False)
        self.quick_capsule.set_click_through(False)
        self._update_clickthrough_indicator()
        self.dock.set_status("已解除全部穿透锁定", ACCENT_LIME)

    # ---- Model Tiers, Cloud API, Game Mode & Hardware-Gated Downloads ----
    def _prompt_download_confirmation(
        self, kind: str, asset_id: str, allow_fallback_existing: bool = False
    ) -> int:
        if kind == "vulkan":
            asset_id = "llama_vulkan"
            name = VULKAN_RUNTIME_SPEC["name"]
            size_label = VULKAN_RUNTIME_SPEC["size_label"]
        elif kind == "llm":
            entry = get_llm_catalog_entry(asset_id) or {}
            name = entry.get("name", asset_id)
            size_label = entry.get("size_label", "未知")
        else:
            entry = get_asr_catalog_entry(asset_id) or {}
            name = entry.get("name", asset_id)
            size_label = entry.get("size_label", "未知")

        eval_info = evaluate_hardware_for_asset(asset_id)
        parent_w = self.panel if self.panel.isVisible() else self.dock
        dlg = build_model_download_dialog(
            asset_name=name,
            size_label=size_label,
            eval_info=eval_info,
            allow_fallback_existing=allow_fallback_existing,
            exclude_capture=bool(self.cfg.get("exclude_from_capture", True)),
            parent=parent_w,
        )
        dlg.exec()
        return dlg.result_code()

    def _on_download_asset_prompt(self, kind: str, asset_id: str) -> None:
        target_id = "llama_vulkan" if kind == "vulkan" else asset_id
        if kind == "llm" and is_llm_model_installed(target_id):
            self.panel.set_download_progress(100, f"{target_id} 已在本机安装完成", active=False)
            return
        if kind == "vulkan" and has_vulkan_runtime():
            self.panel.set_download_progress(100, "llama.cpp Vulkan 运行时已就绪", active=False)
            return
        if kind == "asr" and is_asr_model_installed(target_id):
            self.panel.set_download_progress(100, f"{target_id} 语音模型已就绪", active=False)
            return

        res = self._prompt_download_confirmation(kind, target_id, allow_fallback_existing=False)
        if res == RESULT_CONFIRM:
            self._on_download_asset(kind, target_id)

    def apply_tier_preset(self, tier_id: str) -> None:
        self.cfg["translator_tier"] = tier_id
        if tier_id == "precision_7b":
            if not is_llm_model_installed("hy_mt_7b_q4"):
                res = self._prompt_download_confirmation("llm", "hy_mt_7b_q4", allow_fallback_existing=False)
                if res != RESULT_CONFIRM:
                    return
                self.cfg["game_mode"] = False
                self.cfg["llm_engine_mode"] = "local"
                self.cfg["llama_backend"] = "cuda" if has_nvidia_cuda_gpu() else "auto"
                self.cfg["local_model_id"] = "hy_mt_7b_q4"
                self.cfg["asr_backend"] = "whisper"
                self.panel.sync_tier_ui(self.cfg)
                self._on_download_asset("llm", "hy_mt_7b_q4", auto_apply_local=True)
                return
            self.cfg["game_mode"] = False
            self.cfg["llm_engine_mode"] = "local"
            self.cfg["llama_backend"] = "cuda" if has_nvidia_cuda_gpu() else "auto"
            self.cfg["local_model_id"] = "hy_mt_7b_q4"
            self.cfg["asr_backend"] = "whisper"
            self.panel.sync_tier_ui(self.cfg)
            self._reconfigure_llm_bg()
        elif tier_id == "balanced":
            self.cfg["game_mode"] = False
            self.cfg["llm_engine_mode"] = "local"
            self.cfg["llama_backend"] = "auto"
            self.cfg["local_model_id"] = "hy_mt_1_8b_q8"
            self.cfg["asr_backend"] = "whisper"
            self.panel.sync_tier_ui(self.cfg)
            self._reconfigure_llm_and_asr_bg()
        elif tier_id == "game_light":
            if not is_asr_model_installed("sensevoice") and not is_llm_model_installed("hy_mt_1_8b_q4"):
                res = self._prompt_download_confirmation("asr", "sensevoice", allow_fallback_existing=True)
                if res == RESULT_CANCEL:
                    return
                if res == RESULT_CONFIRM:
                    self.cfg["game_mode"] = True
                    self.cfg["llm_engine_mode"] = "local"
                    self.cfg["llama_backend"] = "cpu"
                    self.cfg["local_model_id"] = "hy_mt_1_8b_q8"
                    self.cfg["asr_backend"] = "sensevoice"
                    self.panel.sync_tier_ui(self.cfg)
                    self._on_download_asset("asr", "sensevoice", auto_apply_asr=True)
                    self._reconfigure_llm_bg()
                    return

            self.cfg["game_mode"] = True
            self.cfg["llm_engine_mode"] = "local"
            self.cfg["llama_backend"] = "cpu"
            self.cfg["local_model_id"] = (
                "hy_mt_1_8b_q4" if is_llm_model_installed("hy_mt_1_8b_q4") else "hy_mt_1_8b_q8"
            )
            if is_asr_model_installed("sensevoice"):
                self.cfg["asr_backend"] = "sensevoice"
            self.panel.sync_tier_ui(self.cfg)
            self._reconfigure_llm_and_asr_bg()
        elif tier_id == "cloud_api":
            if not str(self.cfg.get("api_key", "")).strip():
                self.show_control_panel(tab=2)
                self.panel.set_api_test_result(False, "请先输入您的 API Key，测试通过后点击「切换至云端 API」")
                return
            self.cfg["llm_engine_mode"] = "api"
            self.panel.sync_tier_ui(self.cfg)
            self._reconfigure_llm_bg()

    def set_game_mode(self, enabled: bool) -> None:
        self.cfg["game_mode"] = bool(enabled)
        self._reconfigure_llm_and_asr_bg()

    def _on_apply_local_engine(self, llama_backend: str, model_id: str, custom_path: str) -> None:
        if model_id != "custom" and not is_llm_model_installed(model_id):
            if self._prompt_download_confirmation("llm", model_id) != RESULT_CONFIRM:
                return
            self.cfg["llm_engine_mode"] = "local"
            self.cfg["llama_backend"] = llama_backend
            self.cfg["local_model_id"] = model_id
            self.cfg["custom_gguf_path"] = custom_path
            self._on_download_asset("llm", model_id, auto_apply_local=True)
            return
        if llama_backend == "vulkan" and not has_vulkan_runtime():
            if self._prompt_download_confirmation("vulkan", "llama_vulkan") != RESULT_CONFIRM:
                return
            self.cfg["llm_engine_mode"] = "local"
            self.cfg["llama_backend"] = llama_backend
            self.cfg["local_model_id"] = model_id
            self.cfg["custom_gguf_path"] = custom_path
            self._on_download_asset("vulkan", "llama_vulkan", auto_apply_local=True)
            return
        self.cfg["llm_engine_mode"] = "local"
        self.cfg["llama_backend"] = llama_backend
        self.cfg["local_model_id"] = model_id
        self.cfg["custom_gguf_path"] = custom_path
        self._reconfigure_llm_bg()

    def _on_apply_api_engine(self, provider: str, base_url: str, api_key: str, model: str) -> None:
        self.cfg["api_provider"] = provider
        self.cfg["api_base_url"] = base_url
        self.cfg["api_key"] = api_key
        self.cfg["api_model"] = model
        if not api_key:
            self.panel.set_api_test_result(False, "API Key 不能为空")
            return
        self.cfg["llm_engine_mode"] = "api"
        self._reconfigure_llm_bg()

    def _on_test_api(self, base_url: str, api_key: str, model: str) -> None:
        self.panel.set_api_test_result(True, "正在发送连通性测试请求...")

        def _worker() -> None:
            ok, msg, _ms = LocalLLMTranslator.test_api_connection(base_url, api_key, model)
            self.sig.api_test_done.emit(ok, msg)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_api_test_done(self, ok: bool, msg: str) -> None:
        self.panel.set_api_test_result(ok, msg)

    def _on_asr_backend_changed(self, backend_id: str) -> None:
        if backend_id != "whisper" and not is_asr_model_installed(backend_id):
            if self._prompt_download_confirmation("asr", backend_id) != RESULT_CONFIRM:
                return
            self.cfg["asr_backend"] = backend_id
            self._on_download_asset("asr", backend_id, auto_apply_asr=True)
            return
        self.cfg["asr_backend"] = backend_id
        self._reconfigure_asr_bg()

    def _on_download_asset(
        self,
        kind: str,
        asset_id: str,
        auto_apply_local: bool = False,
        auto_apply_asr: bool = False,
    ) -> None:
        self._dl_cancel.clear()
        self.sig.dl_progress.emit(1, f"准备下载 {asset_id}...", True)

        def _worker() -> None:
            try:
                cb = lambda pct, msg: self.sig.dl_progress.emit(pct, msg, True)
                if kind == "llm":
                    download_llm_model(asset_id, cb, cancel_event=self._dl_cancel)
                elif kind == "vulkan":
                    download_vulkan_runtime(cb, cancel_event=self._dl_cancel)
                elif kind == "asr":
                    download_asr_model(asset_id, cb, cancel_event=self._dl_cancel)
                self.sig.dl_progress.emit(100, f"{asset_id} 已准备就绪", False)
                if auto_apply_local:
                    self._reconfigure_llm_bg()
                if auto_apply_asr:
                    self._reconfigure_asr_bg()
            except Exception as exc:
                self.sig.dl_progress.emit(0, f"下载失败: {exc}", False)

        threading.Thread(target=_worker, name="AssetDownload", daemon=True).start()

    def _on_dl_progress(self, pct: int, msg: str, active: bool) -> None:
        self.panel.set_download_progress(pct, msg, active=active)
        if active:
            self.dock.set_status(msg[:38], ACCENT_OCHRE)
        elif pct >= 100:
            self.dock.set_status("资源就绪", ACCENT_LIME)

    def _reconfigure_llm_bg(self) -> None:
        def _worker() -> None:
            with self._reconfig_lock:
                self.sig.status.emit("正在切换翻译引擎...", ACCENT_OCHRE)
                try:
                    self.llm.reconfigure(
                        engine_mode=str(self.cfg.get("llm_engine_mode", "local")),
                        llama_backend=str(self.cfg.get("llama_backend", "auto")),
                        local_model_id=str(self.cfg.get("local_model_id", "hy_mt_1_8b_q8")),
                        custom_gguf_path=str(self.cfg.get("custom_gguf_path", "")),
                        game_mode=bool(self.cfg.get("game_mode", False)),
                        api_base_url=str(self.cfg.get("api_base_url", "")),
                        api_key=str(self.cfg.get("api_key", "")),
                        api_model=str(self.cfg.get("api_model", "")),
                    )
                    self.sig.status.emit("翻译引擎已就绪", ACCENT_LIME)
                    self.sig.telemetry_dirty.emit()
                except Exception as exc:
                    self.sig.error_msg.emit(f"切换翻译引擎失败: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _reconfigure_asr_bg(self) -> None:
        def _worker() -> None:
            with self._reconfig_lock:
                self.sig.status.emit("正在切换语音识别引擎...", ACCENT_OCHRE)
                try:
                    self.asr_engine.switch_backend(
                        backend=str(self.cfg.get("asr_backend", "whisper")),
                        force_cpu=bool(self.cfg.get("game_mode", False)),
                    )
                    self.sig.status.emit("语音引擎已就绪", ACCENT_LIME)
                    self.sig.telemetry_dirty.emit()
                except Exception as exc:
                    self.sig.error_msg.emit(f"切换语音引擎失败: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _reconfigure_llm_and_asr_bg(self) -> None:
        def _worker() -> None:
            with self._reconfig_lock:
                self.sig.status.emit("正在应用算力配置...", ACCENT_OCHRE)
                try:
                    self.llm.reconfigure(
                        engine_mode=str(self.cfg.get("llm_engine_mode", "local")),
                        llama_backend=str(self.cfg.get("llama_backend", "auto")),
                        local_model_id=str(self.cfg.get("local_model_id", "hy_mt_1_8b_q8")),
                        custom_gguf_path=str(self.cfg.get("custom_gguf_path", "")),
                        game_mode=bool(self.cfg.get("game_mode", False)),
                        api_base_url=str(self.cfg.get("api_base_url", "")),
                        api_key=str(self.cfg.get("api_key", "")),
                        api_model=str(self.cfg.get("api_model", "")),
                    )
                    self.asr_engine.switch_backend(
                        backend=str(self.cfg.get("asr_backend", "whisper")),
                        force_cpu=bool(self.cfg.get("game_mode", False)),
                    )
                    self.sig.status.emit("算力配置已生效", ACCENT_LIME)
                    self.sig.telemetry_dirty.emit()
                except Exception as exc:
                    self.sig.error_msg.emit(f"应用算力配置失败: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_telemetry_labels(self) -> None:
        llm_sum = self.llm.active_summary()
        asr_sum = self.asr_engine.device_mode.upper()
        self.dock.set_engine_badge(llm_sum, game_mode=bool(self.cfg.get("game_mode", False)))
        self.panel.set_engine_telemetry(llm_sum, asr_sum)

    # ---- Audio & Appearance Controls ----
    def set_asr_enabled(self, enabled: bool) -> None:
        self.cfg["asr_enabled"] = enabled
        self.asr_engine.set_enabled(enabled)
        self._rebuild_channels()

    def set_audio_mode(self, mode: str) -> None:
        self.cfg["audio_mode"] = mode
        self._apply_asr_source()

    def set_target_process(self, pid: int, exe: str) -> None:
        self.cfg["target_pid"] = int(pid)
        self.cfg["target_process_name"] = exe
        self._apply_asr_source()

    def set_audio_device(self, name: str) -> None:
        self.cfg["audio_device_name"] = name
        self._apply_asr_source()

    def set_asr_lang(self, lang: str) -> None:
        self.cfg["asr_lang"] = lang
        self.asr_engine.set_lang(lang)

    def _on_ocr_tuning(self, interval_sec: float, diff_thresh: float) -> None:
        self.cfg["ocr_interval_sec"] = interval_sec
        self.cfg["ocr_diff_threshold"] = diff_thresh
        self.ocr_worker.set_tuning(interval_sec, diff_thresh)

    def _on_asr_tuning(self, rms_thresh: float, sil_flush: float) -> None:
        self.cfg["asr_rms_threshold"] = rms_thresh
        self.cfg["asr_silence_flush_sec"] = sil_flush
        self.asr_engine.set_tuning(rms_thresh, sil_flush)

    def _apply_asr_source(self) -> None:
        self.asr_engine.configure_source(
            audio_mode=str(self.cfg.get("audio_mode", "device")),
            target_pid=int(self.cfg.get("target_pid", 0)),
            target_exe=str(self.cfg.get("target_process_name", "")),
            device_name=str(self.cfg.get("audio_device_name", "")),
        )

    def refresh_apps(self) -> None:
        my_pids = {os.getpid()}
        apps = list_audio_target_apps(exclude_pids=my_pids)
        self.panel.set_apps(
            apps,
            int(self.cfg.get("target_pid", 0)),
            str(self.cfg.get("target_process_name", "")),
        )

    def refresh_devices(self) -> None:
        devs = list_loopback_devices()
        self.panel.set_devices(devs, str(self.cfg.get("audio_device_name", "")))

    def refresh_mics(self) -> None:
        mics = list_input_microphones()
        self.panel.set_mics(mics, str(self.cfg.get("mic_device_name", "")))

    # ---- Lifecycle: Main Window -> Start/Close Translation, Position Memory, Self-Check & OCR Filter ----
    def start_translation(self) -> None:
        if not self._models_ready and not self.self_test:
            self.panel.set_hotkey_status_text("引擎仍在加载中，请稍候...", ok=False)
            return
        self._translation_active = True
        self._dock_manually_minimized = False
        self.panel.set_translation_active(True)
        self.ocr_worker.set_continuous_paused(False)
        self.asr_engine.set_enabled(bool(self.cfg.get("asr_enabled", True)))
        if str(self.cfg.get("display_mode", "floating")) == "stealth":
            self.set_display_mode("floating", from_user=False)
        else:
            self._apply_display_mode_visibility(initial=False)
        self.dock.set_status("Glimpse 瞬译已启动", ACCENT_LIME)

    def close_translation_ui(self) -> None:
        """Close translation overlays & pause live workers while keeping Main Control Center open."""
        self._translation_active = False
        self._dock_manually_minimized = False
        self.panel.set_translation_active(False)
        self.asr_engine.set_enabled(False)
        self._hide_all_windows()
        save_config(self.cfg)

    def toggle_translation_ui(self) -> None:
        if self._translation_active:
            self.close_translation_ui()
        else:
            self.start_translation()

    def set_remember_window_pos(self, enabled: bool) -> None:
        self.cfg["remember_window_pos"] = bool(enabled)
        save_config(self.cfg)

    def set_ocr_filter_chinese(self, enabled: bool) -> None:
        self.cfg["ocr_filter_chinese"] = bool(enabled)
        self.ocr_worker.set_filter_chinese(bool(enabled))

    def _reset_translation_rects_to_default(self) -> None:
        self.cfg["dock_rect"] = list(DEFAULT_CONFIG["dock_rect"])
        self.cfg["asr_float_rect"] = list(DEFAULT_CONFIG["asr_float_rect"])
        self.cfg["capsule_full_pos"] = []
        self.cfg["capsule_rect"] = list(DEFAULT_CONFIG["capsule_rect"])
        for i, reg in enumerate(self.cfg.get("regions", []), start=1):
            dflt = make_region_config(i)
            reg["box_rect"] = list(dflt["box_rect"])
            reg["float_rect"] = list(dflt["float_rect"])

    def run_self_check(self) -> None:
        """Inspect OCR, LLM, ASR, Anti-Capture WDA, and Hotkey subsystems and push report to Tab 0."""
        rows: list[dict[str, Any]] = []
        # 1. OCR Engine
        en_regs = [r for r in self.cfg.get("regions", []) if r.get("enabled", True)]
        filt_txt = "忽略纯中文=开" if self.cfg.get("ocr_filter_chinese", False) else "不过滤中文"
        rows.append(
            {
                "name": "画面 OCR 识别",
                "status": "ok" if en_regs else "warn",
                "detail": f"PP-OCRv5 已配置 {len(en_regs)} 条启用区域 · 阈值={self.cfg.get('ocr_diff_threshold', 1.6)} · {filt_txt}",
            }
        )
        # 2. LLM Translation Engine
        mode = str(self.cfg.get("llm_engine_mode", "local"))
        llm_sum = self.llm.active_summary()
        if mode == "api" and not str(self.cfg.get("api_key", "")).strip():
            rows.append(
                {
                    "name": "翻译推理引擎",
                    "status": "err",
                    "detail": f"{llm_sum} — 云端 API Key 为空，请在「模型与引擎」填写",
                }
            )
        else:
            rows.append(
                {
                    "name": "翻译推理引擎",
                    "status": "ok" if self._models_ready else "warn",
                    "detail": f"{llm_sum}（{'已加载就绪' if self._models_ready else '正在加载中'}）",
                }
            )
        # 3. ASR & Audio Isolation
        asr_on = bool(self.cfg.get("asr_enabled", True))
        aud_mode = str(self.cfg.get("audio_mode", "device"))
        aud_desc = (
            f"进程隔离: {self.cfg.get('target_process_name') or '未选定'}"
            if aud_mode == "process"
            else f"声卡环回: {self.cfg.get('audio_device_name') or '系统默认'}"
        )
        rows.append(
            {
                "name": "语音识别与隔离",
                "status": "ok" if asr_on else "warn",
                "detail": f"{'已启用' if asr_on else '已暂停'} · {self.asr_engine.device_mode.upper()} · {aud_desc}",
            }
        )
        # 4. Anti-Self-Capture WDA
        excl = bool(self.cfg.get("exclude_from_capture", True))
        rows.append(
            {
                "name": "防自播屏幕隔离",
                "status": "ok" if excl else "warn",
                "detail": f"WDA_EXCLUDEFROMCAPTURE={'已启用（取景框自动无视译文）' if excl else '已关闭（慎用，可能自循环）'}",
            }
        )
        # 5. Hotkeys & Hardware
        ok_hk = all(self._hotkey_status.values()) if self._hotkey_status else False
        failed_hk = [k for k, v in self._hotkey_status.items() if not v]
        hw = detect_system_hardware()
        rows.append(
            {
                "name": "热键与硬件环境",
                "status": "ok" if ok_hk else "warn",
                "detail": (
                    f"热键全部生效 · {hw.get('gpu_name', 'CPU')} · 可用内存 {hw.get('ram_avail_gb', '?')} GB"
                    if ok_hk
                    else f"热键冲突或未注册: {failed_hk} · GPU={hw.get('gpu_name', 'CPU')}"
                ),
            }
        )
        self.sig.self_check_done.emit(rows)

    def _global_appearance_dict(self) -> dict[str, Any]:
        return {
            "bg_color": str(self.cfg.get("panel_bg_color", "#202226")),
            "opacity": float(self.cfg.get("opacity", 0.94)),
            "trans_font_size": int(self.cfg.get("trans_font_size", self.cfg.get("font_size", 15))),
            "trans_font_color": str(self.cfg.get("trans_font_color", "#F1F3F5")),
            "trans_stroke_color": str(self.cfg.get("trans_stroke_color", "#000000")),
            "trans_stroke_size": int(self.cfg.get("trans_stroke_size", 0)),
            "show_original": bool(self.cfg.get("show_original", True)),
            "orig_font_size": int(self.cfg.get("orig_font_size", 12)),
            "orig_font_color": str(self.cfg.get("orig_font_color", "#9CA3AF")),
            "orig_stroke_color": str(self.cfg.get("orig_stroke_color", "#000000")),
            "orig_stroke_size": int(self.cfg.get("orig_stroke_size", 0)),
            "show_comment_author": bool(self.cfg.get("show_comment_author", True)),
        }

    def _resolve_window_appearance(self, cid: str) -> dict[str, Any]:
        g = self._global_appearance_dict()
        custom_map = self.cfg.get("detached_styles", {})
        if isinstance(custom_map, dict) and cid in custom_map:
            custom = custom_map[cid]
            if isinstance(custom, dict) and bool(custom.get("use_custom", False)):
                merged = make_default_detached_style(self.cfg)
                merged.update(custom)
                g.update(merged)
        return g

    def _apply_all_appearances(self) -> None:
        g = self._global_appearance_dict()
        show_orig = bool(g["show_original"])
        show_author = bool(g["show_comment_author"])
        self.dock.apply_appearance(g)
        for mc in self._merged_cards.values():
            mc.apply_appearance(g, show_original=show_orig, show_author=show_author)
        for cid, dw in self._detached_wins.items():
            st = self._resolve_window_appearance(cid)
            dw.apply_appearance(st, show_original=show_orig, show_author=show_author)
        for io in self._inplace_overlays.values():
            io.apply_appearance(g)

    def _on_global_appearance_changed(self, style: dict[str, Any]) -> None:
        self.cfg["panel_bg_color"] = str(style.get("bg_color", "#202226"))
        self.cfg["opacity"] = max(0.25, min(1.0, float(style.get("opacity", 0.94))))
        self.cfg["trans_font_size"] = int(style.get("trans_font_size", 15))
        self.cfg["font_size"] = self.cfg["trans_font_size"]
        self.cfg["trans_font_color"] = str(style.get("trans_font_color", "#F1F3F5"))
        self.cfg["trans_stroke_color"] = str(style.get("trans_stroke_color", "#000000"))
        self.cfg["trans_stroke_size"] = max(0, min(4, int(style.get("trans_stroke_size", 0))))
        self.cfg["show_original"] = bool(style.get("show_original", True))
        self.cfg["orig_font_size"] = int(style.get("orig_font_size", 12))
        self.cfg["orig_font_color"] = str(style.get("orig_font_color", "#9CA3AF"))
        self.cfg["orig_stroke_color"] = str(style.get("orig_stroke_color", "#000000"))
        self.cfg["orig_stroke_size"] = max(0, min(4, int(style.get("orig_stroke_size", 0))))
        self.cfg["show_comment_author"] = bool(style.get("show_comment_author", True))
        self._apply_all_appearances()

    def _on_detached_appearance_changed(self, cid: str, style: dict[str, Any]) -> None:
        det_map = self.cfg.setdefault("detached_styles", {})
        if not bool(style.get("use_custom", False)):
            det_map.pop(cid, None)
        else:
            det_map[cid] = dict(style)
        self._apply_all_appearances()

    def set_show_comment_author(self, show_author: bool) -> None:
        self.cfg["show_comment_author"] = bool(show_author)
        self._apply_all_appearances()
        for io in self._inplace_overlays.values():
            io.set_overlay_style(str(self.cfg.get("inplace_style", "blend")), show_author=bool(show_author))

    def reset_positions(self) -> None:
        self._reset_translation_rects_to_default()
        self.cfg["panel_rect"] = list(DEFAULT_CONFIG["panel_rect"])
        self.quick_capsule.reset_saved_positions()
        for reg in self.cfg["regions"]:
            rid = reg["id"]
            if rid in self._capture_boxes:
                x, y, w, h = reg["box_rect"]
                self._capture_boxes[rid].setGeometry(x, y, w, h)
        dx, dy, dw, dh = self.cfg["dock_rect"]
        self.dock.setGeometry(dx, dy, dw, dh)
        px, py, pw, ph = self.cfg["panel_rect"]
        self.panel.setGeometry(px, py, pw, ph)
        self._rebuild_channels()

    # ---- Quick Bidirectional Output Capsule ----
    def _on_quick_settings(
        self,
        q_hk: str,
        v_hk: str,
        v_hold: bool,
        lang: str,
        tone: str,
        auto_copy: bool,
        auto_paste: bool,
    ) -> None:
        self.cfg["quick_hotkey"] = q_hk
        self.cfg["voice_out_hotkey"] = v_hk
        self.cfg["voice_out_hold_mode"] = bool(v_hold)
        self.cfg["output_target_lang"] = lang
        self.cfg["output_tone"] = tone
        self.cfg["output_auto_copy"] = auto_copy
        self.cfg["output_auto_paste"] = auto_paste
        self.quick_capsule.set_prefs(lang, tone)
        self.quick_capsule.set_auto_copy_state(auto_copy)
        self._register_all_hotkeys()

    def _on_unlock_hotkey_changed(self, u_hk: str) -> None:
        self.cfg["unlock_clickthrough_hotkey"] = u_hk
        self._register_all_hotkeys()

    def _on_capsule_auto_copy_toggled(self, enabled: bool) -> None:
        self.cfg["output_auto_copy"] = bool(enabled)
        self.quick_capsule.set_auto_copy_state(bool(enabled))
        self.panel.sync_quick_ui(self.cfg)

    def set_capsule_minimal_mode(self, enabled: bool) -> None:
        val = bool(enabled)
        self.cfg["capsule_minimal_mode"] = val
        if val:
            self.cfg["output_auto_copy"] = True
            self.quick_capsule.set_auto_copy_state(True)
        else:
            if bool(self.cfg.get("capsule_click_through", False)):
                self.cfg["capsule_click_through"] = False
                self.quick_capsule.set_click_through(False)
                self._update_clickthrough_indicator()
        self.quick_capsule.set_minimal_mode(val)
        self.panel.sync_quick_ui(self.cfg)
        self.quick_capsule.show()
        self.quick_capsule.raise_()

    def _on_capsule_style_changed(
        self, opacity: float, bg_color: str, font_size: int, font_color: str
    ) -> None:
        self.cfg["capsule_opacity"] = float(opacity)
        self.cfg["capsule_bg_color"] = str(bg_color)
        self.cfg["capsule_font_size"] = int(font_size)
        self.cfg["capsule_font_color"] = str(font_color)
        self.quick_capsule.set_capsule_style(opacity, bg_color, font_size, font_color)

    def _on_capsule_prefs_changed(self, lang: str, tone: str) -> None:
        self.cfg["output_target_lang"] = lang
        self.cfg["output_tone"] = tone

    def open_quick_capsule(self, voice: bool = False) -> None:
        self.quick_capsule.set_prefs(
            str(self.cfg.get("output_target_lang", "en")),
            str(self.cfg.get("output_tone", "natural")),
        )
        self.quick_capsule.set_auto_copy_state(bool(self.cfg.get("output_auto_copy", True)))
        if voice:
            self.quick_capsule.open_for_voice()
            self._on_quick_voice_toggled(True)
        else:
            self.quick_capsule.open_for_text()

    def _on_quick_text_requested(self, src_text: str, target_lang: str, tone: str) -> None:
        def _worker() -> None:
            try:
                out, ms = self.llm.translate_to_target(src_text, target_lang=target_lang, tone=tone)
                self.sig.quick_result.emit(out, ms, src_text)
            except Exception as exc:
                self.sig.quick_error.emit(f"快翻失败: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_quick_voice_toggled(self, start: bool) -> None:
        if start:
            try:
                mic_name = self.mic_recorder.start(str(self.cfg.get("mic_device_name", "")))
                self.quick_capsule.set_recording_state(True, mic_label=mic_name[:22])
            except Exception as exc:
                self.quick_capsule.set_recording_state(False)
                self.quick_capsule.set_error_status(f"麦克风开启失败: {exc}")
            return

        audio = self.mic_recorder.stop()
        self.quick_capsule.set_recording_state(False)
        if len(audio) < 2400:
            self.quick_capsule.set_error_status("录音过短，请再试一次")
            return

        lang = self.quick_capsule.current_target_lang()
        tone = self.quick_capsule.current_tone()
        self.quick_capsule.set_busy_status("正在识别语音并翻译...")

        def _worker() -> None:
            try:
                src_txt, asr_ms = self.asr_engine.transcribe_audio_16k(audio, lang="auto")
                if not src_txt:
                    self.sig.quick_error.emit("未识别到清晰语音，请靠近麦克风")
                    return
                out, llm_ms = self.llm.translate_to_target(src_txt, target_lang=lang, tone=tone)
                self.sig.quick_result.emit(out, asr_ms + llm_ms, src_txt)
            except Exception as exc:
                self.sig.quick_error.emit(f"语音快翻失败: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_quick_result(self, translated: str, elapsed_ms: int, src_text: str) -> None:
        auto_copy = bool(self.cfg.get("output_auto_copy", True))
        auto_paste = bool(self.cfg.get("output_auto_paste", False))
        if auto_copy and translated:
            QApplication.clipboard().setText(translated)
        self.quick_capsule.set_result(
            translated,
            elapsed_ms,
            src_text=src_text,
            copied=auto_copy,
            pasted=auto_paste,
        )
        if auto_paste and translated:
            QTimer.singleShot(110, self._hide_capsule_and_paste)

    def _hide_capsule_and_paste(self) -> None:
        self.quick_capsule.hide()
        threading.Thread(target=send_ctrl_v_to_foreground, daemon=True).start()

    def _on_quick_error(self, msg: str) -> None:
        self.quick_capsule.set_error_status(msg)

    def _on_mic_level(self, lvl: float) -> None:
        if self.quick_capsule.isVisible():
            self.quick_capsule.mic_wave.set_level(lvl)

    # ---- Overlays ----
    def start_snip(self) -> None:
        if not self._models_ready:
            self._on_status("模型仍在加载中...", ACCENT_OCHRE)
            return
        hidden_boxes: list[CaptureRegionWindow] = []
        for box in self._capture_boxes.values():
            if box.isVisible():
                box.hide()
                hidden_boxes.append(box)

        first_rid = self.cfg["regions"][0]["id"]
        first_lang = self.cfg["regions"][0].get("lang", "auto")
        first_clean = bool(self.cfg["regions"][0].get("smart_clean", True))

        def on_rect(rect: tuple[int, int, int, int]) -> None:
            if self.cfg.get("display_mode") == "floating":
                for b in hidden_boxes:
                    b.show()
            self._on_status("正在识别框选区域...", ACCENT_VERMILION)
            threading.Thread(
                target=self.ocr_worker.snip_translate_rect,
                args=(rect, first_rid, first_lang, first_clean),
                daemon=True,
            ).start()

        self._active_overlay = SnipOverlay(on_rect)

    def start_window_picker(self) -> None:
        self.panel.hide()

        def on_picked(picked: Optional[dict]) -> None:
            self.panel.show()
            self.panel.raise_()
            if picked is not None:
                self.cfg["audio_mode"] = "process"
                self.cfg["target_pid"] = picked["pid"]
                self.cfg["target_process_name"] = picked["exe"]
                self.panel.select_app(picked)
                self._apply_asr_source()

        self._active_overlay = WindowPickerOverlay({os.getpid()}, on_picked)

    def toggle_control_panel(self) -> None:
        if self.panel.isVisible() and not self.panel.isMinimized():
            self.panel.showMinimized()
        else:
            self.show_control_panel()

    def show_control_panel(self, tab: Optional[int] = None) -> None:
        self.refresh_apps()
        self.refresh_mics()
        if tab is not None:
            self.panel._switch_page(tab)
        if self.panel.isMinimized():
            self.panel.showNormal()
        else:
            self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def _minimize_to_tray(self) -> None:
        self._dock_manually_minimized = True
        self.dock.hide()

    def _restore_from_tray(self) -> None:
        self._translation_active = True
        self._dock_manually_minimized = False
        self.panel.set_translation_active(True)
        if self.cfg.get("display_mode") == "stealth":
            self.set_display_mode("floating", from_user=False)
        else:
            self._apply_display_mode_visibility(initial=False)
        self.dock.show()
        self.dock.raise_()
        self.dock.activateWindow()

    # ---- Background Model Boot & Workers ----
    def _load_models_bg(self) -> None:
        try:
            self.sig.status.emit("启动翻译引擎...", ACCENT_OCHRE)
            self.sig.boot_progress.emit(18, "正在加载本地 / 云端翻译引擎...", False)
            self.llm.start()

            self.sig.status.emit("加载 PP-OCRv5 识别引擎...", ACCENT_OCHRE)
            self.sig.boot_progress.emit(48, "正在预加载 PP-OCRv5 多语识别引擎...", False)
            self.ocr_mgr.preload_defaults()

            asr_name = str(self.cfg.get("asr_backend", "whisper"))
            self.sig.status.emit(f"加载语音模型 ({asr_name})...", ACCENT_OCHRE)
            self.sig.boot_progress.emit(78, f"正在预加载语音识别模型 ({asr_name})...", False)
            self.asr_engine.load_model(
                backend=asr_name, force_cpu=bool(self.cfg.get("game_mode", False))
            )

            self.ocr_worker.set_continuous_paused(
                (not self._translation_active) or (self.cfg.get("display_mode") == "stealth")
            )
            self.ocr_worker.start()
            self.asr_engine.set_enabled(
                self._translation_active and bool(self.cfg.get("asr_enabled", True))
            )
            self.asr_engine.start()
            self.sig.boot_progress.emit(100, "全部模型已就绪，点击「开始翻译」即可使用", True)
            self.sig.ready.emit()
        except Exception as exc:
            self.sig.boot_progress.emit(100, f"部分引擎初始化异常（仍可进入页面排查）: {exc}", True)
            self.sig.error_msg.emit(f"模型启动失败: {exc}")

    def _on_status(self, msg: str, color: str) -> None:
        self.dock.set_status(msg, color)

    def _on_ready(self) -> None:
        self._models_ready = True
        self._refresh_telemetry_labels()
        self.dock.set_status("引擎就绪", ACCENT_LIME)
        self.refresh_devices()
        self.refresh_apps()
        self.refresh_mics()
        if self.self_test:
            QTimer.singleShot(600, self._run_self_test)

    def _on_ocr_motion(self, rid: str) -> None:
        if self.cfg.get("display_mode") == "inplace" and rid in self._inplace_overlays:
            self._inplace_overlays[rid].clear_for_motion()

    def _on_ocr_result(
        self,
        rid: str,
        src: str,
        zh: str,
        items: Any,
        badge: str,
        ocr_ms: int,
        llm_ms: int,
    ) -> None:
        item_list = list(items) if items else []
        self._last_results[rid] = (src, zh, badge, ocr_ms, llm_ms, item_list)
        card = self._get_card(rid)
        if card is not None:
            card.set_translation(src, zh, badge, ocr_ms, llm_ms, items=item_list)
        if self.cfg.get("display_mode") == "inplace" and rid in self._inplace_overlays:
            io = self._inplace_overlays[rid]
            show_author = bool(self.cfg.get("show_comment_author", True))
            io.set_blocks(item_list, show_author=show_author)
            if not io.isVisible():
                io.show()
        self.dock.set_status(f"就绪 · 识别 {ocr_ms}ms / 译 {llm_ms}ms", ACCENT_LIME)

    def _on_asr_result(self, src: str, zh: str, badge: str, asr_ms: int, llm_ms: int) -> None:
        self._last_results["asr"] = (src, zh, badge, asr_ms, llm_ms)
        card = self._get_card("asr")
        if card is not None:
            card.set_translation(src, zh, badge, asr_ms, llm_ms)
        self.dock.set_status(f"就绪 · 语音 {asr_ms}ms / 译 {llm_ms}ms", ACCENT_LIME)

    def _on_asr_level(self, lvl: float) -> None:
        card = self._get_card("asr")
        if card is not None and card.wave is not None:
            card.wave.set_level(lvl)
        if self.panel.isVisible():
            self.panel.panel_wave.set_level(lvl)

    def _on_asr_source(self, txt: str) -> None:
        self._last_asr_source = txt
        card = self._get_card("asr")
        if card is not None:
            card.set_source_hint(txt)

    def _on_error_msg(self, msg: str) -> None:
        self.dock.set_status(msg[:42], ACCENT_CRIMSON)

    # ---- Self-test automation hook for v3.0 visual & functional check ----
    def _run_self_test(self) -> None:
        try:
            self.ocr_worker.stop()
        except Exception:
            pass

        import numpy as np
        from app.ocr_engine import is_noise_or_chinese
        from app.ui.control_panel import ApiModelHelpDialog

        # 1. Boot gate & Main Window checks (before start_translation)
        assert self.panel.isVisible(), "Main Control Center must be visible at boot"
        assert not self.dock.isVisible(), "Dock must remain hidden until start_translation()"
        assert self.panel._btn_start_trans.isEnabled(), "Start button must be enabled once ready"

        # Trigger self-check & inspect diagnostic rows
        self.run_self_check()
        QApplication.processEvents()
        assert self.panel._self_check_container.count() >= 5, "Expected >=5 self-check rows"

        # 2. OCR unit checks (filter toggle + tile-max MAD)
        assert is_noise_or_chinese("测试中文内容", filter_chinese=False) is False
        assert is_noise_or_chinese("测试中文内容", filter_chinese=True) is True
        a = np.full((80, 160), 20, dtype=np.uint8)
        b = a.copy()
        b[12:24, 15:95] = 210
        mad_val = MultiRegionOCRWorker._tile_max_mad(a, b)
        assert mad_val > 1.6, f"Expected _tile_max_mad > 1.6, got {mad_val}"

        # 3. API help check (no deprecated deepseek-chat / deepseek-reasoner)
        self.panel._switch_page(2)
        QApplication.processEvents()
        assert self.panel._ed_api_model.text().strip() == "deepseek-flash", (
            f"Expected deepseek-flash, got {self.panel._ed_api_model.text()!r}"
        )
        help_dlg = ApiModelHelpDialog(self.panel)
        help_text = " ".join(
            lbl.text() for lbl in help_dlg.findChildren(type(self.panel._lbl_page_title))
        )
        assert "deepseek-chat" not in help_text and "deepseek-reasoner" not in help_text
        help_dlg.deleteLater()

        # 4. Start translation -> Dock becomes visible with WDA 0x11
        self.set_exclude_from_capture(True)
        self.panel._switch_page(0)
        QApplication.processEvents()
        self.start_translation()
        QApplication.processEvents()
        assert self.dock.isVisible(), "Dock must show after start_translation()"
        assert get_window_display_affinity(self.dock) == 0x11, "Dock WDA must be 0x11"
        assert get_window_display_affinity(self.panel) == 0x11, "Main panel WDA must be 0x11"
        assert "关闭翻译页面" in self.panel._btn_start_trans.text()
        self.dock.set_status("就绪 · 识别 48ms / 译 132ms", ACCENT_LIME)

        self.add_region()
        r1 = self.cfg["regions"][0]
        r2 = self.cfg["regions"][1]
        r1_id, r2_id = r1["id"], r2["id"]
        r2["float_rect"] = [260, 180, 490, 320]

        r1_demo = (
            "спасибо огромное, что снимаете мой тренд",
            "非常感谢大家拍我的热门挑战！",
            "俄→中",
            52,
            135,
        )
        r2_items = [
            {
                "author": "sasa",
                "src": "THE RIGHT PERSON WHO CAN PULL OFF ADA WONG",
                "zh": "就是那个能完美驾驭艾达·王的人",
                "bbox": [14, 14, 460, 46],
                "bg_rgb": [22, 21, 20],
                "fg_rgb": [244, 240, 232],
                "crop_w": 500,
                "crop_h": 240,
            },
            {
                "author": "w1ngedpearl",
                "src": "где хейт, че мне лайкать",
                "zh": "黑粉在哪，我该点赞哪条？",
                "bbox": [14, 66, 420, 98],
                "bg_rgb": [22, 21, 20],
                "fg_rgb": [244, 240, 232],
                "crop_w": 500,
                "crop_h": 240,
            },
        ]
        r2_src = "\n".join(f"{it['author']}: {it['src']}" for it in r2_items)
        r2_zh = "\n".join(f"{it['author']}: {it['zh']}" for it in r2_items)
        r2_demo = (r2_src, r2_zh, "多语→中", 184, 128, r2_items)
        asr_demo = (
            "Hey everyone, welcome back to Miami Beach.",
            "大家好，欢迎回到迈阿密海滩。",
            "英→中",
            96,
            88,
        )
        self._last_results[r1_id] = r1_demo
        self._last_results[r2_id] = r2_demo
        self._last_results["asr"] = asr_demo
        self._last_asr_source = "进程隔离 · msedge.exe"

        for cid, data in ((r1_id, r1_demo), (r2_id, r2_demo), ("asr", asr_demo)):
            c = self._get_card(cid)
            if c:
                c.set_translation(*data)
        asr_c = self._get_card("asr")
        if asr_c:
            asr_c.set_source_hint(self._last_asr_source)
            if asr_c.wave is not None:
                asr_c.wave.set_level(0.65)

        shot_dir = Path(tempfile.gettempdir()) / "glimpse_selftest"
        shot_dir.mkdir(parents=True, exist_ok=True)
        wda_dock = get_window_display_affinity(self.dock)

        def step_a() -> None:
            # Global + per-detached custom styling check
            self._on_global_appearance_changed(
                {
                    "bg_color": "#202226",
                    "opacity": 0.90,
                    "trans_font_size": 16,
                    "trans_font_color": "#F1F3F5",
                    "trans_stroke_color": "#000000",
                    "trans_stroke_size": 1,
                    "show_original": True,
                    "orig_font_size": 12,
                    "orig_font_color": "#9CA3AF",
                    "orig_stroke_color": "#000000",
                    "orig_stroke_size": 0,
                    "show_comment_author": True,
                }
            )
            self.detach_channel(r2_id)
            self._on_detached_appearance_changed(
                r2_id,
                {
                    "use_custom": True,
                    "bg_color": "#1B2838",
                    "opacity": 0.88,
                    "trans_font_size": 17,
                    "trans_font_color": "#FFFFFF",
                    "trans_stroke_color": "#0F172A",
                    "trans_stroke_size": 2,
                    "orig_font_size": 12,
                    "orig_font_color": "#93C5FD",
                    "orig_stroke_color": "#000000",
                    "orig_stroke_size": 1,
                },
            )
            QApplication.processEvents()
            det_r2 = self._detached_wins.get(r2_id)
            assert det_r2 is not None, "Detached region_2 window missing"
            assert get_window_display_affinity(det_r2) == 0x11, "Detached WDA must be 0x11"
            assert det_r2._bg_color.upper() == "#1B2838"
            assert self.dock._bg_color.upper() == "#202226"

            # Grab screenshots cleanly
            self.panel._switch_page(0)
            QApplication.processEvents()
            self.panel.grab().save(str(shot_dir / "v3_home.png"))

            self.panel._switch_page(5)
            QApplication.processEvents()
            self.panel.grab().save(str(shot_dir / "v3_appearance.png"))

            if self.dock.isVisible():
                self.dock.grab().save(str(shot_dir / "v3_dock.png"))
            det_r2.grab().save(str(shot_dir / "v3_detached.png"))

            # Capsule bg_color + opacity + position memory
            self._on_capsule_style_changed(0.85, "#1E293B", 15, "#F8FAFC")
            self.set_capsule_minimal_mode(False)
            self.open_quick_capsule(voice=False)
            self.quick_capsule.set_result(
                "Two snipers 2F left, stay in cover.",
                94,
                src_text="左边二楼有两个狙，别露头",
                copied=True,
                pasted=False,
            )
            QTimer.singleShot(350, step_b)

        def step_b() -> None:
            assert get_window_display_affinity(self.quick_capsule) == 0x11, "Capsule WDA must be 0x11"
            assert self.quick_capsule._capsule_bg_color.upper() == "#1E293B"
            self.quick_capsule.grab().save(str(shot_dir / "v3_capsule.png"))

            self.quick_capsule.move(310, 260)
            QApplication.processEvents()
            self.quick_capsule.hide()
            QApplication.processEvents()
            self.quick_capsule.open_for_voice()
            QApplication.processEvents()
            assert self.quick_capsule.pos() == QPoint(310, 260), "Voice hotkey snapped capsule away!"
            self.quick_capsule.hide()
            QApplication.processEvents()
            self.quick_capsule.open_for_text()
            QApplication.processEvents()
            assert self.quick_capsule.pos() == QPoint(310, 260), "Text hotkey snapped capsule away!"

            self.set_capsule_minimal_mode(True)
            QTimer.singleShot(220, step_c)

        def step_c() -> None:
            assert self.quick_capsule._min_bar.isVisible(), "Minimal bar should be visible before lock"
            self.cfg["capsule_click_through"] = True
            self.quick_capsule.set_click_through(True)
            assert not self.quick_capsule._min_bar.isVisible(), "Minimal bar MUST hide when locked"
            QTimer.singleShot(180, step_d)

        def step_d() -> None:
            self.unlock_all_clickthrough()
            assert self.quick_capsule._min_bar.isVisible(), "Minimal bar MUST restore after unlock_all"
            self.set_capsule_minimal_mode(False)
            self.quick_capsule.hide()

            # Stealth snap + preserved float_rect check
            self.set_display_mode("floating", from_user=False)
            self.detach_channel(r1_id)
            self.detach_channel("asr")
            saved_r1_float = [260, 200, 480, 195]
            self._find_region(r1_id)["float_rect"] = list(saved_r1_float)
            self._capture_boxes[r1_id].setGeometry(300, 420, 480, 140)
            self.cfg["stealth_position"] = "above_region"
            self.cfg["stealth_trigger_mode"] = "toggle"
            QApplication.processEvents()

            self._minimize_to_tray()
            QApplication.processEvents()
            assert not self.dock.isVisible()

            # Toggle stealth hide -> reveal
            self._on_hotkey_triggered("stealth")
            QApplication.processEvents()
            assert not self._any_window_visible(), "ALL translation windows must hide after stealth toggle"

            self._on_hotkey_triggered("stealth")
            QTimer.singleShot(220, lambda: step_e(saved_r1_float))

        def step_e(expected_float: list[int]) -> None:
            assert not self.dock.isVisible(), "Minimized Dock must stay hidden when detached windows reveal"
            det_r1 = self._detached_wins.get(r1_id)
            det_asr = self._detached_wins.get("asr")
            assert det_r1 and det_r1.isVisible(), "Detached r1 must reveal on stealth hotkey"
            assert det_asr and det_asr.isVisible(), "Detached asr must reveal on stealth hotkey"
            assert det_r1.y() + det_r1.height() <= 420, f"r1 must snap above y=420, got {det_r1.geometry()}"
            actual_float = self._find_region(r1_id)["float_rect"]
            assert actual_float[:2] == expected_float[:2], (
                f"Stealth snap overwrote float_rect! expected {expected_float}, got {actual_float}"
            )

            # Close translation UI -> Main Window stays visible with 「开始翻译」 restored
            self.close_translation_ui()
            QApplication.processEvents()
            assert not self._any_window_visible(), "Translation windows must hide on close_translation_ui()"
            assert self.panel.isVisible(), "Main Window must stay visible after close_translation_ui()"
            assert self.panel._btn_start_trans.text().strip() == "开始翻译"

            print(
                f"SELF_TEST_V3_PASS wda_dock=0x{wda_dock:02x} mad={mad_val:.2f} "
                f"hotkeys={self._hotkey_status}",
                flush=True,
            )

            # Restore clean user defaults before exit
            self.cfg["detached_styles"] = {}
            self.set_exclude_from_capture(True)
            self._on_ocr_tuning(0.28, 1.6)
            self._on_global_appearance_changed(
                {
                    "bg_color": "#202226",
                    "opacity": 0.94,
                    "trans_font_size": 15,
                    "trans_font_color": "#F1F3F5",
                    "trans_stroke_color": "#000000",
                    "trans_stroke_size": 0,
                    "show_original": True,
                    "orig_font_size": 12,
                    "orig_font_color": "#9CA3AF",
                    "orig_stroke_color": "#000000",
                    "orig_stroke_size": 0,
                    "show_comment_author": True,
                }
            )
            self._on_capsule_style_changed(0.90, "#202226", 15, "#F1F3F5")
            self.cfg["capsule_full_pos"] = []
            self.quick_capsule.reset_saved_positions()
            self._dock_manually_minimized = False
            self.cfg["ocr_filter_chinese"] = False
            self.cfg["remember_window_pos"] = True
            self.set_capsule_minimal_mode(False)
            self.quick_capsule.hide()
            self.set_display_mode("floating", from_user=False)
            self.merge_all_channels()
            self.remove_region(r2_id)
            self.reset_positions()
            self.panel.sync_from_config(self.cfg)
            QTimer.singleShot(250, self.shutdown)

        QTimer.singleShot(350, step_a)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._dl_cancel.set()
        save_config(self.cfg)
        try:
            self.hotkeys.unregister_all()
        except Exception:
            pass
        try:
            self.mic_recorder.stop()
        except Exception:
            pass
        try:
            self.ocr_worker.stop()
        except Exception:
            pass
        try:
            self.asr_engine.stop()
        except Exception:
            pass
        try:
            self.llm.stop()
        except Exception:
            pass
        try:
            self.tray.hide()
        except Exception:
            pass
        for b in list(self._capture_boxes.values()):
            b.close()
        for io in list(self._inplace_overlays.values()):
            io.close()
        for dw in list(self._detached_wins.values()):
            dw.close()
        self.quick_capsule.close()
        self.panel.close()
        self.dock.close()
        self.app.quit()


def main() -> None:
    self_test = "--self-test" in sys.argv
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(APP_QSS)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    ctrl = TranslatorController(app, self_test=self_test)
    app.aboutToQuit.connect(ctrl.shutdown)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
