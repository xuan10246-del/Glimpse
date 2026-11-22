"""Configuration paths, channel structures, presets, and JSON persistence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BASE_DIR: Path = Path(__file__).resolve().parent.parent
LLAMA_DIR: Path = BASE_DIR / "bin" / "llama"
LLAMA_SERVER_EXE: Path = LLAMA_DIR / "llama-server.exe"
LLAMA_VULKAN_DIR: Path = BASE_DIR / "bin" / "llama-vulkan"
LLAMA_VULKAN_EXE: Path = LLAMA_VULKAN_DIR / "llama-server.exe"

MODELS_DIR: Path = BASE_DIR / "models"
LLM_MODEL_PATH: Path = MODELS_DIR / "HY-MT1.5-1.8B-Q8_0.gguf"
WHISPER_MODEL_DIR: Path = MODELS_DIR / "faster-whisper-large-v3-turbo-ct2"
ASR_MODELS_DIR: Path = MODELS_DIR / "asr"
OCR_MODEL_DIR: Path = MODELS_DIR / "ocr"

ICON_PATH: Path = BASE_DIR / "assets" / "AppIcon.ico"
ICON_PNG_PATH: Path = BASE_DIR / "assets" / "AppIcon.png"
CONFIG_JSON_PATH: Path = BASE_DIR / "config.json"
LLAMA_LOG_PATH: Path = BASE_DIR / "llama_server.log"
APP_LOG_PATH: Path = BASE_DIR / "translator.log"

LLAMA_HOST: str = "127.0.0.1"
LLAMA_PORT: int = 18432

OCR_TEXT_SIM_THRESHOLD: float = 0.88
ASR_MIN_SPEECH_SEC: float = 0.75
ASR_MAX_CHUNK_SEC: float = 3.2
ASR_OVERLAP_SEC: float = 0.25

# Curated Balanced Pigments for Quiet Studio Slate UI
CHANNEL_COLORS: list[str] = [
    "#3B82F6",  # Slate Blue
    "#34C77B",  # Emerald
    "#14B8A6",  # Teal
    "#E5A93C",  # Amber
    "#A855F7",  # Soft Orchid
    "#EC4899",  # Rose
]

ASR_COLOR: str = "#34C77B"

OCR_LANG_CHOICES: list[tuple[str, str]] = [
    ("auto", "自动多语（英/俄/日等）"),
    ("ch", "中 / 英 / 日"),
    ("eslav", "俄语 / 英语"),
    ("en", "纯英语"),
    ("korean", "韩语 / 英语"),
    ("latin", "欧洲拉丁语系"),
]

ASR_LANG_CHOICES: list[tuple[str, str]] = [
    ("auto", "自动识别语种"),
    ("en", "英语 (English)"),
    ("ru", "俄语 (Russian)"),
    ("ja", "日语 (Japanese)"),
    ("ko", "韩语 (Korean)"),
    ("fr", "法语 (French)"),
    ("de", "德语 (German)"),
    ("es", "西班牙语 (Spanish)"),
]

OUTPUT_LANG_CHOICES: list[tuple[str, str]] = [
    ("en", "EN · 英语"),
    ("ru", "RU · 俄语"),
    ("ja", "JA · 日语"),
    ("ko", "KO · 韩语"),
    ("fr", "FR · 法语"),
    ("de", "DE · 德语"),
    ("es", "ES · 西班牙语"),
    ("zh", "ZH · 简体中文"),
]

OUTPUT_TONE_CHOICES: list[tuple[str, str]] = [
    ("natural", "自然口语"),
    ("game", "游戏报点（简短快发）"),
    ("formal", "得体书面"),
]

API_PROVIDER_PRESETS: dict[str, tuple[str, str, str]] = {
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-flash"),
    "dashscope": (
        "阿里云百炼 Qwen",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-mt-turbo",
    ),
    "siliconflow": ("SiliconFlow 硅基流动", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"),
    "gemini": (
        "Google Gemini (OpenAI 兼容)",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.5-flash",
    ),
    "openai": ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "google/gemini-2.5-flash"),
    "custom": ("自定义 OpenAI 兼容接口", "", ""),
}

DISPLAY_MODES: list[tuple[str, str]] = [
    ("floating", "常规浮窗"),
    ("stealth", "隐藏当前窗口"),
    ("inplace", "原位覆盖"),
]

INPLACE_STYLES: list[tuple[str, str]] = [
    ("blend", "无缝背景融合（采样原底色覆盖）"),
    ("strike", "原文红线划除 + 碳黑高反差盖板"),
    ("glass", "暗夜半透磨砂条带"),
]


def make_region_config(index: int, name: str | None = None) -> dict[str, Any]:
    """Create a default region configuration dict for a given 1-based index."""
    color = CHANNEL_COLORS[(index - 1) % len(CHANNEL_COLORS)]
    offset = ((index - 1) % 4) * 36
    return {
        "id": f"region_{index}",
        "name": name or f"画面 {index}",
        "color": color,
        "lang": "auto",
        "enabled": True,
        "box_visible": True,
        "detached": False,
        "click_through": False,
        "smart_clean": True,
        "box_rect": [200 + offset, 310 + offset, 520, 160],
        "float_rect": [260 + offset, 200 + offset, 480, 195],
    }


def next_region_number(regions: list[dict[str, Any]]) -> int:
    """Return the next unused positive integer suffix for region IDs and names."""
    used: set[int] = set()
    for reg in regions:
        for field in (str(reg.get("id", "")), str(reg.get("name", ""))):
            m = re.search(r"(\d+)$", field.strip())
            if m:
                used.add(int(m.group(1)))
    num = 1
    while num in used:
        num += 1
    return num


def make_default_detached_style(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a clean per-detached-window style dict seeded from global appearance settings."""
    src = cfg or DEFAULT_CONFIG
    return {
        "use_custom": False,
        "bg_color": str(src.get("panel_bg_color", "#202226")),
        "opacity": float(src.get("opacity", 0.94)),
        "trans_font_size": int(src.get("trans_font_size", src.get("font_size", 15))),
        "trans_font_color": str(src.get("trans_font_color", "#F1F3F5")),
        "trans_stroke_color": str(src.get("trans_stroke_color", "#000000")),
        "trans_stroke_size": int(src.get("trans_stroke_size", 0)),
        "orig_font_size": int(src.get("orig_font_size", 12)),
        "orig_font_color": str(src.get("orig_font_color", "#9CA3AF")),
        "orig_stroke_color": str(src.get("orig_stroke_color", "#000000")),
        "orig_stroke_size": int(src.get("orig_stroke_size", 0)),
    }


DEFAULT_CONFIG: dict[str, Any] = {
    "regions": [make_region_config(1)],
    "asr_enabled": True,
    "asr_detached": False,
    "asr_click_through": False,
    "asr_float_rect": [780, 560, 480, 195],
    "asr_lang": "auto",
    "audio_mode": "device",
    "target_process_name": "",
    "target_pid": 0,
    "audio_device_name": "",
    "mic_device_name": "",
    "asr_rms_threshold": 0.008,
    "asr_silence_flush_sec": 0.45,
    "asr_backend": "whisper",  # whisper | sensevoice | moonshine | parakeet
    "font_size": 15,
    "opacity": 0.94,
    "text_opacity": 1.0,
    "panel_bg_color": "#202226",
    "trans_font_size": 15,
    "trans_font_color": "#F1F3F5",
    "trans_stroke_color": "#000000",
    "trans_stroke_size": 0,
    "orig_font_size": 12,
    "orig_font_color": "#9CA3AF",
    "orig_stroke_color": "#000000",
    "orig_stroke_size": 0,
    "detached_styles": {},
    "remember_window_pos": True,
    "ocr_filter_chinese": False,
    "show_original": True,
    "show_comment_author": True,
    "ocr_interval_sec": 0.28,
    "ocr_diff_threshold": 1.6,
    "dock_rect": [180, 480, 780, 390],
    "panel_rect": [220, 90, 960, 670],
    # Display Modes & Anti-Self-Capture
    "exclude_from_capture": True,
    "display_mode": "floating",  # floating | stealth | inplace
    "stealth_hotkey": "Alt+Q",
    "stealth_trigger_mode": "toggle",  # toggle | hold
    "stealth_position": "above_region",  # above_region | dock
    "inplace_style": "blend",  # blend | strike | glass
    # Translation Tiers, Backends & Game Mode
    "translator_tier": "balanced",  # precision_7b | balanced | game_light | cloud_api
    "llm_engine_mode": "local",  # local | api
    "llama_backend": "auto",  # auto | cuda | vulkan | cpu
    "local_model_id": "hy_mt_1_8b_q8",
    "custom_gguf_path": "",
    "game_mode": False,
    # Cloud API Settings
    "api_provider": "deepseek",
    "api_base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "api_model": "deepseek-flash",
    # Bidirectional Quick Output & Minimal Mode / Unlock Hotkey
    "quick_hotkey": "Alt+T",
    "voice_out_hotkey": "Alt+V",
    "voice_out_hold_mode": True,
    "output_target_lang": "en",
    "output_tone": "natural",
    "output_auto_copy": True,
    "output_auto_paste": False,
    "unlock_clickthrough_hotkey": "Ctrl+Alt+U",
    "suppress_hide_prompt": False,
    "suppress_inplace_prompt": False,
    "suppress_clickthrough_prompt": False,
    "capsule_minimal_mode": False,
    "capsule_click_through": False,
    "capsule_opacity": 0.90,
    "capsule_text_opacity": 1.0,
    "capsule_bg_color": "#202226",
    "capsule_font_size": 15,
    "capsule_font_color": "#F1F3F5",
    "capsule_full_pos": [],
    "capsule_rect": [520, 220, 560, 120],
}


def _sanitize_hex(val: Any, fallback: str) -> str:
    s = str(val or "").strip()
    return s.upper() if re.match(r"^#[0-9A-Fa-f]{6}$", s) else fallback


def _sanitize_detached_style(raw: Any, global_cfg: dict[str, Any]) -> dict[str, Any]:
    base = make_default_detached_style(global_cfg)
    if not isinstance(raw, dict):
        return base
    if isinstance(raw.get("use_custom"), bool):
        base["use_custom"] = raw["use_custom"]
    base["bg_color"] = _sanitize_hex(raw.get("bg_color"), base["bg_color"])
    if isinstance(raw.get("opacity"), (int, float)) and not isinstance(raw.get("opacity"), bool):
        base["opacity"] = max(0.25, min(1.0, float(raw["opacity"])))
    if isinstance(raw.get("trans_font_size"), int) and not isinstance(raw.get("trans_font_size"), bool):
        base["trans_font_size"] = max(11, min(32, int(raw["trans_font_size"])))
    base["trans_font_color"] = _sanitize_hex(raw.get("trans_font_color"), base["trans_font_color"])
    base["trans_stroke_color"] = _sanitize_hex(raw.get("trans_stroke_color"), base["trans_stroke_color"])
    if isinstance(raw.get("trans_stroke_size"), int) and not isinstance(raw.get("trans_stroke_size"), bool):
        base["trans_stroke_size"] = max(0, min(4, int(raw["trans_stroke_size"])))
    if isinstance(raw.get("orig_font_size"), int) and not isinstance(raw.get("orig_font_size"), bool):
        base["orig_font_size"] = max(10, min(24, int(raw["orig_font_size"])))
    base["orig_font_color"] = _sanitize_hex(raw.get("orig_font_color"), base["orig_font_color"])
    base["orig_stroke_color"] = _sanitize_hex(raw.get("orig_stroke_color"), base["orig_stroke_color"])
    if isinstance(raw.get("orig_stroke_size"), int) and not isinstance(raw.get("orig_stroke_size"), bool):
        base["orig_stroke_size"] = max(0, min(4, int(raw["orig_stroke_size"])))
    return base


def _sanitize_rect(raw: Any, fallback: list[int], min_w: int = 120, min_h: int = 80) -> list[int]:
    if isinstance(raw, list) and len(raw) == 4:
        try:
            x, y, w, h = [int(v) for v in raw]
            return [x, y, max(min_w, w), max(min_h, h)]
        except Exception:
            pass
    return list(fallback)


def load_config() -> dict[str, Any]:
    """Load persisted settings safely merged with defaults."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not CONFIG_JSON_PATH.exists():
        return cfg
    try:
        raw = json.loads(CONFIG_JSON_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return cfg
    except Exception:
        return cfg

    scalar_keys = (
        "asr_enabled",
        "asr_detached",
        "asr_click_through",
        "asr_lang",
        "audio_mode",
        "target_process_name",
        "target_pid",
        "audio_device_name",
        "mic_device_name",
        "asr_rms_threshold",
        "asr_silence_flush_sec",
        "asr_backend",
        "font_size",
        "opacity",
        "text_opacity",
        "panel_bg_color",
        "trans_font_size",
        "trans_font_color",
        "trans_stroke_color",
        "trans_stroke_size",
        "orig_font_size",
        "orig_font_color",
        "orig_stroke_color",
        "orig_stroke_size",
        "remember_window_pos",
        "ocr_filter_chinese",
        "show_original",
        "show_comment_author",
        "ocr_interval_sec",
        "ocr_diff_threshold",
        "exclude_from_capture",
        "display_mode",
        "stealth_hotkey",
        "stealth_trigger_mode",
        "stealth_position",
        "inplace_style",
        "translator_tier",
        "llm_engine_mode",
        "llama_backend",
        "local_model_id",
        "custom_gguf_path",
        "game_mode",
        "api_provider",
        "api_base_url",
        "api_key",
        "api_model",
        "quick_hotkey",
        "voice_out_hotkey",
        "voice_out_hold_mode",
        "output_target_lang",
        "output_tone",
        "output_auto_copy",
        "output_auto_paste",
        "unlock_clickthrough_hotkey",
        "suppress_hide_prompt",
        "suppress_inplace_prompt",
        "suppress_clickthrough_prompt",
        "capsule_minimal_mode",
        "capsule_click_through",
        "capsule_opacity",
        "capsule_text_opacity",
        "capsule_bg_color",
        "capsule_font_size",
        "capsule_font_color",
    )
    for key in scalar_keys:
        if key in raw:
            dval = DEFAULT_CONFIG[key]
            rval = raw[key]
            if isinstance(dval, float) and isinstance(rval, (int, float)) and not isinstance(rval, bool):
                cfg[key] = float(rval)
            elif isinstance(rval, type(dval)):
                cfg[key] = rval

    if "trans_font_size" not in raw and isinstance(raw.get("font_size"), int):
        cfg["trans_font_size"] = int(raw["font_size"])
    cfg["trans_font_size"] = max(11, min(32, int(cfg.get("trans_font_size", 15))))
    cfg["font_size"] = cfg["trans_font_size"]
    cfg["orig_font_size"] = max(10, min(24, int(cfg.get("orig_font_size", 12))))
    cfg["trans_stroke_size"] = max(0, min(4, int(cfg.get("trans_stroke_size", 0))))
    cfg["orig_stroke_size"] = max(0, min(4, int(cfg.get("orig_stroke_size", 0))))

    cfg["panel_bg_color"] = _sanitize_hex(cfg.get("panel_bg_color"), "#202226")
    cfg["trans_font_color"] = _sanitize_hex(cfg.get("trans_font_color"), "#F1F3F5")
    cfg["trans_stroke_color"] = _sanitize_hex(cfg.get("trans_stroke_color"), "#000000")
    cfg["orig_font_color"] = _sanitize_hex(cfg.get("orig_font_color"), "#9CA3AF")
    cfg["orig_stroke_color"] = _sanitize_hex(cfg.get("orig_stroke_color"), "#000000")

    cfg["opacity"] = max(0.25, min(1.0, float(cfg.get("opacity", 0.94))))
    cfg["text_opacity"] = 1.0
    cfg["capsule_opacity"] = max(0.20, min(1.0, float(cfg.get("capsule_opacity", 0.90))))
    cfg["capsule_text_opacity"] = 1.0
    cfg["capsule_bg_color"] = _sanitize_hex(cfg.get("capsule_bg_color"), "#202226")
    cfg["capsule_font_size"] = max(11, min(32, int(cfg.get("capsule_font_size", 15))))
    cfg["capsule_font_color"] = _sanitize_hex(cfg.get("capsule_font_color"), "#F1F3F5")

    raw_det = raw.get("detached_styles")
    det_cleaned: dict[str, dict[str, Any]] = {}
    if isinstance(raw_det, dict):
        for cid, st_val in raw_det.items():
            if isinstance(cid, str) and cid.strip():
                det_cleaned[cid.strip()] = _sanitize_detached_style(st_val, cfg)
    cfg["detached_styles"] = det_cleaned

    raw_full_pos = raw.get("capsule_full_pos")
    if isinstance(raw_full_pos, list) and len(raw_full_pos) == 2:
        try:
            cfg["capsule_full_pos"] = [int(raw_full_pos[0]), int(raw_full_pos[1])]
        except Exception:
            cfg["capsule_full_pos"] = []
    else:
        cfg["capsule_full_pos"] = []

    if cfg.get("api_provider") == "deepseek" and cfg.get("api_model") in ("deepseek-chat", "deepseek-reasoner"):
        cfg["api_model"] = "deepseek-flash"

    # Clamp enums safely
    if cfg["display_mode"] not in ("floating", "stealth", "inplace"):
        cfg["display_mode"] = "floating"
    if cfg["stealth_trigger_mode"] not in ("toggle", "hold"):
        cfg["stealth_trigger_mode"] = "toggle"
    if cfg["stealth_position"] not in ("above_region", "dock"):
        cfg["stealth_position"] = "above_region"
    if cfg["inplace_style"] not in ("blend", "strike", "glass"):
        cfg["inplace_style"] = "blend"
    if cfg["llm_engine_mode"] not in ("local", "api"):
        cfg["llm_engine_mode"] = "local"
    if cfg["llama_backend"] not in ("auto", "cuda", "vulkan", "cpu"):
        cfg["llama_backend"] = "auto"
    if cfg["asr_backend"] not in ("whisper", "sensevoice", "moonshine", "parakeet"):
        cfg["asr_backend"] = "whisper"

    cfg["asr_float_rect"] = _sanitize_rect(raw.get("asr_float_rect"), DEFAULT_CONFIG["asr_float_rect"], 280, 120)
    cfg["dock_rect"] = _sanitize_rect(raw.get("dock_rect"), DEFAULT_CONFIG["dock_rect"], 320, 80)
    cfg["panel_rect"] = _sanitize_rect(raw.get("panel_rect"), DEFAULT_CONFIG["panel_rect"], 780, 520)
    cfg["capsule_rect"] = _sanitize_rect(raw.get("capsule_rect"), DEFAULT_CONFIG["capsule_rect"], 260, 72)

    raw_regions = raw.get("regions")
    if isinstance(raw_regions, list) and raw_regions:
        cleaned: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_regions, start=1):
            if not isinstance(item, dict):
                continue
            base = make_region_config(idx)
            for k in ("id", "name", "color", "lang", "enabled", "box_visible", "detached", "click_through", "smart_clean"):
                if k in item and isinstance(item[k], type(base[k])):
                    base[k] = item[k]
            base["box_rect"] = _sanitize_rect(item.get("box_rect"), base["box_rect"], 160, 70)
            base["float_rect"] = _sanitize_rect(item.get("float_rect"), base["float_rect"], 280, 120)
            cleaned.append(base)
        if cleaned:
            cfg["regions"] = cleaned

    return cfg


def save_config(data: dict[str, Any]) -> None:
    """Persist settings cleanly to config.json."""
    try:
        CONFIG_JSON_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
