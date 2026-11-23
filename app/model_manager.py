"""Model & runtime asset catalog and proxy-aware streaming downloader."""

from __future__ import annotations

import bz2
import io
import os
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import psutil
import requests

from .config import (
    ASR_MODELS_DIR,
    LLAMA_VULKAN_DIR,
    LLAMA_VULKAN_EXE,
    LLM_MODEL_PATH,
    MODELS_DIR,
    WHISPER_MODEL_DIR,
)

LOCAL_LLM_CATALOG: list[dict[str, Any]] = [
    {
        "id": "hy_mt_1_8b_q8",
        "name": "HY-MT1.5-1.8B Q8_0 (默认均衡)",
        "filename": "HY-MT1.5-1.8B-Q8_0.gguf",
        "repo": "tencent/HY-MT1.5-1.8B-GGUF",
        "size_label": "1.82 GB",
        "tier": "balanced",
        "desc": "33语高清中译高速度，RTX 显卡约 2.3GB 显存",
        "est_vram_gb": 2.3,
        "est_ram_gb": 2.5,
        "est_disk_gb": 2.0,
        "min_vram_gb": 0.0,
        "min_ram_gb": 8.0,
        "min_cpu_threads": 4,
        "rec_vram_gb": 4.0,
        "rec_ram_gb": 16.0,
        "rec_cpu_threads": 6,
        "usage_text": "GPU 显存 ~2.3 GB（纯CPU模式约 ~2.5 GB 内存），硬盘空间 ~2.0 GB",
        "min_text": "8 GB 内存 · 4 线程及以上 CPU（支持纯 CPU 或 4GB 独显）",
        "rec_text": "4 GB+ 独立显卡（RTX/Vulkan）· 16 GB 内存 · 6 核及以上 CPU",
    },
    {
        "id": "hy_mt_1_8b_q4",
        "name": "HY-MT1.5-1.8B Q4_K_M (游戏轻量化)",
        "filename": "HY-MT1.5-1.8B-Q4_K_M.gguf",
        "repo": "tencent/HY-MT1.5-1.8B-GGUF",
        "size_label": "1.15 GB",
        "tier": "game_light",
        "desc": "CPU / 轻显存游戏极速首选，内存占用极低",
        "est_vram_gb": 1.4,
        "est_ram_gb": 1.6,
        "est_disk_gb": 1.3,
        "min_vram_gb": 0.0,
        "min_ram_gb": 8.0,
        "min_cpu_threads": 4,
        "rec_vram_gb": 0.0,
        "rec_ram_gb": 16.0,
        "rec_cpu_threads": 6,
        "usage_text": "游戏0显存模式：0 MB 显卡显存 · ~1.6 GB 内存 · 限制 4 核 CPU（硬盘 ~1.3 GB）",
        "min_text": "8 GB 内存 · 4 线程 CPU（无需独立显卡，不抢占游戏显存）",
        "rec_text": "16 GB 内存 · 6 核及以上现代桌面处理器",
    },
    {
        "id": "hy_mt_7b_q4",
        "name": "HY-MT1.5-7B Q4_K_M (旗舰高精)",
        "filename": "HY-MT1.5-7B-Q4_K_M.gguf",
        "repo": "tencent/HY-MT1.5-7B-GGUF",
        "size_label": "4.60 GB",
        "tier": "precision_7b",
        "desc": "复杂长句、俚语、双语评论高精旗舰，建议 8GB+ 显存",
        "est_vram_gb": 5.5,
        "est_ram_gb": 6.5,
        "est_disk_gb": 4.8,
        "min_vram_gb": 6.0,
        "min_ram_gb": 16.0,
        "min_cpu_threads": 8,
        "rec_vram_gb": 8.0,
        "rec_ram_gb": 16.0,
        "rec_cpu_threads": 8,
        "usage_text": "GPU 显存 ~5.5 GB（若叠加 Whisper Turbo 语音共占约 ~7.5 GB；硬盘 ~4.8 GB）",
        "min_text": "6 GB 独显（或无独显纯 CPU 下 16 GB 可用内存 + 8 线程 CPU）",
        "rec_text": "8 GB~12 GB 独立显卡（RTX 3060Ti / 4060 / 4070 及以上）· 16 GB+ 内存",
    },
    {
        "id": "translategemma_4b_q4",
        "name": "TranslateGemma-4B Q4_K_M (55语通用)",
        "filename": "translategemma-4b-it.Q4_K_M.gguf",
        "repo": "mradermacher/translategemma-4b-it-GGUF",
        "size_label": "2.50 GB",
        "tier": "balanced",
        "desc": "Google 55语翻译模型，适合长段落与欧洲多语种",
        "est_vram_gb": 3.2,
        "est_ram_gb": 3.8,
        "est_disk_gb": 2.7,
        "min_vram_gb": 4.0,
        "min_ram_gb": 12.0,
        "min_cpu_threads": 6,
        "rec_vram_gb": 6.0,
        "rec_ram_gb": 16.0,
        "rec_cpu_threads": 8,
        "usage_text": "GPU 显存 ~3.2 GB（纯CPU内存 ~3.8 GB），硬盘 ~2.7 GB",
        "min_text": "4 GB 独显 或 12 GB 内存 + 6 线程 CPU",
        "rec_text": "6 GB+ 独立显卡 · 16 GB 内存 · 8 线程 CPU",
    },
]

ASR_BACKEND_CATALOG: list[dict[str, Any]] = [
    {
        "id": "whisper",
        "name": "Faster-Whisper Large-v3-Turbo (默认全语种)",
        "archive": "",
        "size_label": "1.54 GB",
        "desc": "99+ 语种最高听写精度，支持 CUDA GPU / CPU",
        "est_vram_gb": 1.9,
        "est_ram_gb": 1.8,
        "est_disk_gb": 1.6,
        "min_vram_gb": 0.0,
        "min_ram_gb": 8.0,
        "min_cpu_threads": 4,
        "rec_vram_gb": 4.0,
        "rec_ram_gb": 16.0,
        "rec_cpu_threads": 6,
        "usage_text": "CUDA 显存 ~1.9 GB（游戏模式下切为纯 CPU：0 显存 + ~1.8 GB 内存）",
        "min_text": "8 GB 内存 · 4 线程 CPU",
        "rec_text": "4 GB+ RTX 显卡 或 8 核以上桌面 CPU",
    },
    {
        "id": "sensevoice",
        "name": "SenseVoice-Small INT8 (纯CPU 0显存 · 中英日韩粤)",
        "archive": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2",
        "size_label": "230 MB",
        "desc": "sherpa-onnx 纯 CPU 推理 (~70ms/句)，游戏开黑零显存占满首选",
        "est_vram_gb": 0.0,
        "est_ram_gb": 0.45,
        "est_disk_gb": 0.25,
        "min_vram_gb": 0.0,
        "min_ram_gb": 4.0,
        "min_cpu_threads": 4,
        "rec_vram_gb": 0.0,
        "rec_ram_gb": 8.0,
        "rec_cpu_threads": 6,
        "usage_text": "0 MB 显卡显存 · 仅 ~450 MB 内存 · CPU 占用 <8%（硬盘 ~250 MB）",
        "min_text": "4 GB 内存 · 4 线程 CPU（无需显卡）",
        "rec_text": "8 GB+ 内存 · 6 核及以上 CPU",
    },
    {
        "id": "moonshine",
        "name": "Moonshine-Base EN INT8 (纯CPU 0显存 · 英语极速)",
        "archive": "sherpa-onnx-moonshine-base-en-int8.tar.bz2",
        "size_label": "120 MB",
        "desc": "专为低算力 CPU 优化的轻量化英语语音识别",
        "est_vram_gb": 0.0,
        "est_ram_gb": 0.35,
        "est_disk_gb": 0.15,
        "min_vram_gb": 0.0,
        "min_ram_gb": 4.0,
        "min_cpu_threads": 2,
        "rec_vram_gb": 0.0,
        "rec_ram_gb": 8.0,
        "rec_cpu_threads": 4,
        "usage_text": "0 MB 显存 · 仅 ~350 MB 内存 · CPU 极低负载（硬盘 ~150 MB）",
        "min_text": "4 GB 内存 · 双核以上 CPU",
        "rec_text": "8 GB 内存 · 4 核以上 CPU",
    },
    {
        "id": "parakeet",
        "name": "NVIDIA Parakeet-TDT 110M EN INT8 (纯CPU · 英语高精)",
        "archive": "sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8.tar.bz2",
        "size_label": "130 MB",
        "desc": "高精度英文游戏语音与口语断句模型，纯 CPU 0 显存",
        "est_vram_gb": 0.0,
        "est_ram_gb": 0.4,
        "est_disk_gb": 0.16,
        "min_vram_gb": 0.0,
        "min_ram_gb": 4.0,
        "min_cpu_threads": 4,
        "rec_vram_gb": 0.0,
        "rec_ram_gb": 8.0,
        "rec_cpu_threads": 6,
        "usage_text": "0 MB 显存 · 仅 ~400 MB 内存（硬盘 ~160 MB）",
        "min_text": "4 GB 内存 · 4 线程 CPU",
        "rec_text": "8 GB 内存 · 6 核及以上 CPU",
    },
]

VULKAN_RUNTIME_SPEC: dict[str, Any] = {
    "id": "llama_vulkan",
    "name": "llama.cpp Vulkan 跨显卡通用加速后端",
    "size_label": "~35 MB",
    "est_vram_gb": 0.0,
    "est_ram_gb": 0.2,
    "est_disk_gb": 0.08,
    "min_vram_gb": 2.0,
    "min_ram_gb": 8.0,
    "min_cpu_threads": 4,
    "rec_vram_gb": 4.0,
    "rec_ram_gb": 16.0,
    "rec_cpu_threads": 6,
    "usage_text": "硬盘 ~80 MB；为 AMD / Intel Arc / 核显或跨卡 Vulkan 设备启用 GPU 加速",
    "min_text": "支持 Vulkan 1.2 的显卡（AMD/Intel/NVIDIA）· 8 GB 内存",
    "rec_text": "4 GB+ 显存或统一内存的 Vulkan 显卡",
}


def detect_system_hardware() -> dict[str, Any]:
    """Detect local CPU, RAM, and NVIDIA GPU specs cleanly for model readiness checks."""
    cpu_name = platform.processor() or "未知处理器"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "ProcessorNameString")
            if str(val).strip():
                cpu_name = str(val).strip()
    except Exception:
        pass

    cpu_cores = int(psutil.cpu_count(logical=False) or 0)
    cpu_threads = int(psutil.cpu_count(logical=True) or os.cpu_count() or 4)

    vm = psutil.virtual_memory()
    ram_total_gb = round(float(vm.total) / (1024 ** 3), 1)
    ram_avail_gb = round(float(vm.available) / (1024 ** 3), 1)

    gpu_name = "未检测到独立 NVIDIA 显卡 (仅核显/CPU)"
    vram_total_gb = 0.0
    vram_free_gb = 0.0
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            creationflags=0x08000000,
            timeout=2.0,
        ).strip()
        first = out.splitlines()[0] if out else ""
        parts = [p.strip() for p in first.split(",")]
        if len(parts) >= 3:
            gpu_name = parts[0]
            vram_total_gb = round(float(parts[1]) / 1024.0, 1)
            vram_free_gb = round(float(parts[2]) / 1024.0, 1)
    except Exception:
        pass

    return {
        "cpu_name": cpu_name,
        "cpu_cores": cpu_cores or max(2, cpu_threads // 2),
        "cpu_threads": cpu_threads,
        "ram_total_gb": ram_total_gb,
        "ram_avail_gb": ram_avail_gb,
        "gpu_name": gpu_name,
        "vram_total_gb": vram_total_gb,
        "vram_free_gb": vram_free_gb,
    }


def evaluate_hardware_for_asset(
    asset_id: str, hw: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Compare local hardware against model/runtime specs and return user-facing assessment."""
    hw_info = hw or detect_system_hardware()
    spec: Optional[dict[str, Any]] = None
    if asset_id == "llama_vulkan":
        spec = VULKAN_RUNTIME_SPEC
    else:
        spec = get_llm_catalog_entry(asset_id) or get_asr_catalog_entry(asset_id)
    if spec is None:
        spec = {
            "id": asset_id,
            "name": asset_id,
            "size_label": "未知",
            "min_vram_gb": 0.0,
            "min_ram_gb": 8.0,
            "min_cpu_threads": 4,
            "rec_vram_gb": 4.0,
            "rec_ram_gb": 16.0,
            "rec_cpu_threads": 6,
            "usage_text": "未知",
            "min_text": "8 GB 内存 · 4 线程 CPU",
            "rec_text": "16 GB 内存",
        }

    vram = float(hw_info["vram_total_gb"])
    ram = float(hw_info["ram_total_gb"])
    threads = int(hw_info["cpu_threads"])

    min_vram = float(spec["min_vram_gb"])
    min_ram = float(spec["min_ram_gb"])
    min_thr = int(spec["min_cpu_threads"])
    rec_vram = float(spec["rec_vram_gb"])
    rec_ram = float(spec["rec_ram_gb"])
    rec_thr = int(spec["rec_cpu_threads"])

    # Local 7B / 4B can fall back to CPU+RAM if RAM>=16GB & threads>=8, though GPU is strongly advised
    meets_rec = (
        ram >= rec_ram - 0.5
        and threads >= rec_thr
        and (rec_vram <= 0.0 or vram >= rec_vram - 0.4)
    )
    if min_vram > 0.0 and vram < min_vram - 0.4:
        # Check CPU+RAM fallback feasibility
        meets_min = ram >= max(min_ram, 15.5) and threads >= min_thr
        if meets_min:
            status = "minimum"
            verdict = "符合最低 CPU/内存运行要求（显存不足，将回退 CPU 推理，速度偏慢）"
            color = "#F2B84B"
        else:
            status = "insufficient"
            verdict = "低于最低配置要求（显存或内存/CPU 不足，可能卡顿或无法加载）"
            color = "#FF3B30"
    elif ram < min_ram - 0.5 or threads < min_thr:
        status = "insufficient"
        verdict = "低于最低内存或 CPU 线程要求，不建议直接加载"
        color = "#FF3B30"
    elif meets_rec:
        status = "recommended"
        verdict = "完全符合推荐配置（可流畅满速运行）"
        color = "#B8FF4A"
    else:
        status = "minimum"
        verdict = "符合最低运行要求（可正常运行，略低于推荐高配）"
        color = "#F2B84B"

    return {
        "spec": spec,
        "hw": hw_info,
        "status": status,
        "verdict": verdict,
        "color": color,
    }


def get_llm_catalog_entry(model_id: str) -> Optional[dict[str, str]]:
    for entry in LOCAL_LLM_CATALOG:
        if entry["id"] == model_id:
            return entry
    return None


def get_asr_catalog_entry(backend_id: str) -> Optional[dict[str, str]]:
    for entry in ASR_BACKEND_CATALOG:
        if entry["id"] == backend_id:
            return entry
    return None


def resolve_local_model_path(model_id: str, custom_path: str = "") -> Optional[Path]:
    """Resolve existing local GGUF file for selected preset or custom path."""
    if model_id == "custom" and custom_path:
        p = Path(custom_path)
        return p if p.exists() and p.is_file() else None
    entry = get_llm_catalog_entry(model_id)
    if entry is not None:
        candidate = MODELS_DIR / entry["filename"]
        if candidate.exists() and candidate.stat().st_size > 10_000_000:
            return candidate
    if LLM_MODEL_PATH.exists():
        return LLM_MODEL_PATH
    if custom_path:
        p = Path(custom_path)
        if p.exists() and p.is_file():
            return p
    return None


def is_llm_model_installed(model_id: str, custom_path: str = "") -> bool:
    if model_id == "custom":
        p = Path(custom_path) if custom_path else None
        return bool(p and p.exists() and p.is_file())
    entry = get_llm_catalog_entry(model_id)
    if entry is None:
        return False
    p = MODELS_DIR / entry["filename"]
    return p.exists() and p.stat().st_size > 10_000_000


def has_vulkan_runtime() -> bool:
    return LLAMA_VULKAN_EXE.exists()


def get_asr_model_dir(backend_id: str) -> Path:
    if backend_id == "whisper":
        return WHISPER_MODEL_DIR
    return ASR_MODELS_DIR / backend_id


def is_asr_model_installed(backend_id: str) -> bool:
    d = get_asr_model_dir(backend_id)
    if backend_id == "whisper":
        return d.exists() and (d / "model.bin").exists()
    if not d.exists():
        return False
    onnx_files = list(d.rglob("*.onnx"))
    tokens_files = list(d.rglob("tokens.txt"))
    return bool(onnx_files and tokens_files)


def _stream_download(
    urls: list[str],
    dest_path: Path,
    label: str,
    on_progress: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """Download from candidate URLs in order with resume-safe .part temporary file."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")
    last_err: Optional[Exception] = None

    sess = requests.Session()
    sess.trust_env = True  # Respect user Windows proxy for external HuggingFace / GitHub downloads
    headers = {"User-Agent": "Glimpse/3.1"}

    for idx, url in enumerate(urls):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("下载已取消")
        src_tag = "主源" if idx == 0 else f"镜像 {idx}"
        on_progress(1, f"正在连接 {label} ({src_tag})...")
        try:
            with sess.get(url, stream=True, timeout=(15, 60), headers=headers, allow_redirects=True) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                last_pct = -1
                with open(part_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=512 * 1024):
                        if cancel_event is not None and cancel_event.is_set():
                            raise InterruptedError("下载已取消")
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(98, int(downloaded * 100 / total))
                            if pct != last_pct:
                                last_pct = pct
                                mb_done = downloaded / (1024 * 1024)
                                mb_tot = total / (1024 * 1024)
                                on_progress(pct, f"下载 {label}: {mb_done:.0f} / {mb_tot:.0f} MB ({pct}%)")
                        else:
                            mb_done = downloaded / (1024 * 1024)
                            on_progress(25, f"下载 {label}: {mb_done:.0f} MB...")
            if part_path.exists() and part_path.stat().st_size > 1024:
                if dest_path.exists():
                    dest_path.unlink()
                part_path.replace(dest_path)
                return dest_path
        except InterruptedError:
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass
            raise
        except Exception as exc:
            last_err = exc
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass

    raise RuntimeError(f"下载 {label} 失败: {last_err}")


def download_llm_model(
    model_id: str,
    on_progress: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    entry = get_llm_catalog_entry(model_id)
    if entry is None:
        raise ValueError(f"未知翻译模型预设: {model_id}")
    dest = MODELS_DIR / entry["filename"]
    if is_llm_model_installed(model_id):
        on_progress(100, f"{entry['filename']} 已就绪")
        return dest
    repo = entry["repo"]
    fn = entry["filename"]
    urls = [
        f"https://huggingface.co/{repo}/resolve/main/{fn}",
        f"https://hf-mirror.com/{repo}/resolve/main/{fn}",
    ]
    out = _stream_download(urls, dest, entry["name"], on_progress, cancel_event)
    on_progress(100, f"{entry['name']} 安装完成")
    return out


def download_vulkan_runtime(
    on_progress: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """Fetch newest ggml-org/llama.cpp b* Windows Vulkan x64 build into bin/llama-vulkan/."""
    if has_vulkan_runtime():
        on_progress(100, "llama.cpp Vulkan 运行时已存在")
        return LLAMA_VULKAN_EXE

    on_progress(2, "正在查询最新 llama.cpp Vulkan 发布版本...")
    sess = requests.Session()
    sess.trust_env = True
    resp = sess.get(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10",
        timeout=20,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Glimpse/3.1"},
    )
    resp.raise_for_status()
    releases = resp.json()

    dl_url = ""
    asset_name = ""
    for rel in releases:
        tag = str(rel.get("tag_name") or "")
        if not tag.startswith("b"):
            continue
        for asset in rel.get("assets") or []:
            nm = str(asset.get("name") or "")
            if re.match(r"^llama-b\d+-bin-win-vulkan-x64\.zip$", nm, re.I):
                dl_url = str(asset.get("browser_download_url") or "")
                asset_name = nm
                break
        if dl_url:
            break

    if not dl_url:
        raise RuntimeError("未在最近 llama.cpp Release 中找到 win-vulkan-x64 资产")

    tmp_zip = MODELS_DIR / "_tmp_vulkan.zip"
    urls = [dl_url, f"https://ghfast.top/{dl_url}"]
    _stream_download(urls, tmp_zip, asset_name, on_progress, cancel_event)

    on_progress(99, "正在解压 Vulkan 推理引擎...")
    if LLAMA_VULKAN_DIR.exists():
        shutil.rmtree(LLAMA_VULKAN_DIR, ignore_errors=True)
    LLAMA_VULKAN_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tmp_zip, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            fname = Path(member.filename).name
            if not fname:
                continue
            with zf.open(member, "r") as src, open(LLAMA_VULKAN_DIR / fname, "wb") as dst:
                shutil.copyfileobj(src, dst)
    try:
        tmp_zip.unlink()
    except Exception:
        pass

    if not LLAMA_VULKAN_EXE.exists():
        raise RuntimeError("解压完成但未发现 llama-server.exe")
    on_progress(100, "llama.cpp Vulkan 运行时就绪 (AMD/Intel/NVIDIA 通用)")
    return LLAMA_VULKAN_EXE


def download_asr_model(
    backend_id: str,
    on_progress: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    entry = get_asr_catalog_entry(backend_id)
    if entry is None or not entry["archive"]:
        raise ValueError(f"不支持自动下载此语音引擎: {backend_id}")
    dest_dir = get_asr_model_dir(backend_id)
    if is_asr_model_installed(backend_id):
        on_progress(100, f"{entry['name']} 已就绪")
        return dest_dir

    arch = entry["archive"]
    primary = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{arch}"
    urls = [primary, f"https://ghfast.top/{primary}"]
    tmp_arch = ASR_MODELS_DIR / f"_{backend_id}.tar.bz2"
    _stream_download(urls, tmp_arch, entry["name"], on_progress, cancel_event)

    on_progress(99, f"正在解压 {entry['name']}...")
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tmp_arch, "r:bz2") as tf:
        tf.extractall(dest_dir)
    try:
        tmp_arch.unlink()
    except Exception:
        pass

    on_progress(100, f"{entry['name']} 安装完成")
    return dest_dir
