"""Auto-download runtime binaries (llama.cpp) and local AI models when missing."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import requests

BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
BIN_LLAMA_DIR = BASE_DIR / "bin" / "llama"
OCR_DIR = MODELS_DIR / "ocr"
WHISPER_DIR = MODELS_DIR / "faster-whisper-large-v3-turbo-ct2"
LLM_FILE = MODELS_DIR / "HY-MT1.5-1.8B-Q8_0.gguf"


def detect_nvidia_gpu() -> bool:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True,
            creationflags=0x08000000,
            timeout=3.0,
        ).strip()
        return bool(out)
    except Exception:
        return False


def pick_hf_endpoint(sess: requests.Session) -> str:
    for endpoint in ("https://huggingface.co", "https://hf-mirror.com"):
        try:
            r = sess.get(
                f"{endpoint}/api/models/tencent/HY-MT1.5-1.8B-GGUF",
                timeout=4.0,
            )
            if r.status_code == 200:
                print(f"[网络] 使用模型源: {endpoint}", flush=True)
                return endpoint
        except Exception:
            continue
    print("[网络] 默认切换国内镜像源: https://hf-mirror.com", flush=True)
    return "https://hf-mirror.com"


def download_file(sess: requests.Session, url: str, dest: Path, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    print(f"[下载] {label} -> {dest.name}", flush=True)
    with sess.get(url, stream=True, timeout=60.0) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", "0") or "0")
        done = 0
        last_pct = -1
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if total > 0:
                    pct = int(done * 100 / total)
                    if pct >= last_pct + 5 or pct == 100:
                        last_pct = pct
                        mb_done = done / (1024 * 1024)
                        mb_tot = total / (1024 * 1024)
                        print(f"  进度: {pct:3d}% ({mb_done:.1f} / {mb_tot:.1f} MB)", flush=True)
    if dest.exists():
        dest.unlink()
    part.replace(dest)


def download_with_gh_fallback(sess: requests.Session, url: str, dest: Path, label: str) -> None:
    try:
        download_file(sess, url, dest, label)
    except Exception as exc:
        print(f"[提示] 直连 GitHub 受阻 ({exc})，尝试镜像加速...", flush=True)
        mirror_url = f"https://ghfast.top/{url}"
        download_file(sess, mirror_url, dest, label)


def ensure_llm_model(sess: requests.Session, hf_endpoint: str) -> None:
    if LLM_FILE.exists() and LLM_FILE.stat().st_size > 500 * 1024 * 1024:
        print(f"[跳过] 翻译模型已就绪: {LLM_FILE.name}", flush=True)
        return
    url = f"{hf_endpoint}/tencent/HY-MT1.5-1.8B-GGUF/resolve/main/HY-MT1.5-1.8B-Q8_0.gguf"
    download_file(sess, url, LLM_FILE, "HY-MT1.5-1.8B-Q8_0 翻译模型 (~1.82 GB)")


def ensure_whisper_model(hf_endpoint: str) -> None:
    model_bin = WHISPER_DIR / "model.bin"
    if model_bin.exists() and model_bin.stat().st_size > 500 * 1024 * 1024:
        print("[跳过] Whisper Turbo 语音模型已就绪", flush=True)
        return
    print("[下载] faster-whisper large-v3-turbo 语音模型 (~1.54 GB)...", flush=True)
    os.environ["HF_ENDPOINT"] = hf_endpoint
    from huggingface_hub import snapshot_download

    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="deepdml/faster-whisper-large-v3-turbo-ct2",
        local_dir=str(WHISPER_DIR),
        endpoint=hf_endpoint,
    )


def _extract_llama_zip(zip_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            fname = Path(info.filename).name
            if not fname:
                continue
            low = fname.lower()
            if low.endswith((".exe", ".dll")) or low.startswith("license"):
                out_path = target_dir / fname
                with zf.open(info) as src, open(out_path, "wb") as dst:
                    while True:
                        buf = src.read(1024 * 1024)
                        if not buf:
                            break
                        dst.write(buf)


def ensure_llama_binaries(sess: requests.Session, has_gpu: bool) -> None:
    server_exe = BIN_LLAMA_DIR / "llama-server.exe"
    if server_exe.exists() and server_exe.stat().st_size > 0:
        if not has_gpu or (BIN_LLAMA_DIR / "ggml-cuda.dll").exists():
            print("[跳过] llama.cpp 推理引擎已就绪", flush=True)
            return

    print("[检查] 查询最新版 llama.cpp Windows 发行包...", flush=True)
    releases = sess.get(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10",
        timeout=15.0,
    ).json()

    chosen_assets: list[tuple[str, str]] = []
    for rel in releases:
        tag = str(rel.get("tag_name", ""))
        if not tag.startswith("b"):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        if has_gpu:
            cuda_main: Optional[str] = next(
                (n for n in assets if n.startswith(f"llama-{tag}-bin-win-cuda-12.4-x64") and n.endswith(".zip")),
                None,
            )
            cuda_rt: Optional[str] = next(
                (n for n in assets if n == "cudart-llama-bin-win-cuda-12.4-x64.zip"),
                None,
            )
            if cuda_main:
                chosen_assets.append((cuda_main, assets[cuda_main]))
                if cuda_rt:
                    chosen_assets.append((cuda_rt, assets[cuda_rt]))
                break
        else:
            cpu_main: Optional[str] = next(
                (n for n in assets if n.startswith(f"llama-{tag}-bin-win-cpu-x64") and n.endswith(".zip")),
                None,
            )
            if cpu_main:
                chosen_assets.append((cpu_main, assets[cpu_main]))
                break

    if not chosen_assets:
        raise RuntimeError("未找到适配当前系统架构的 llama.cpp 发行包")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, url in chosen_assets:
            zpath = tmp_dir / name
            download_with_gh_fallback(sess, url, zpath, f"llama.cpp ({name})")
            print(f"[解压] {name} -> bin/llama/", flush=True)
            _extract_llama_zip(zpath, BIN_LLAMA_DIR)


def ensure_ocr_models() -> None:
    det = OCR_DIR / "ch_PP-OCRv5_det_mobile.onnx"
    ch_rec = OCR_DIR / "ch_PP-OCRv5_rec_mobile.onnx"
    ru_rec = OCR_DIR / "eslav_PP-OCRv5_rec_mobile.onnx"
    if det.exists() and ch_rec.exists() and ru_rec.exists():
        print("[跳过] PP-OCRv5 画面识别模型已就绪", flush=True)
        return
    print("[下载] 预热 PP-OCRv5 多语识别模型...", flush=True)
    sys.path.insert(0, str(BASE_DIR))
    from app.ocr_engine import MultilingualOCRManager

    MultilingualOCRManager().preload_defaults()


def main() -> int:
    has_gpu = detect_nvidia_gpu()
    print(
        f"[硬件] 检测到 NVIDIA 显卡: {'是 (启用 CUDA 加速)' if has_gpu else '否 (将配置 CPU 兼容模式)'}",
        flush=True,
    )
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Glimpse-Setup/3.0"})
    hf_endpoint = pick_hf_endpoint(sess)
    ensure_ocr_models()
    ensure_llama_binaries(sess, has_gpu)
    ensure_llm_model(sess, hf_endpoint)
    ensure_whisper_model(hf_endpoint)
    print("[完成] 所有本地引擎与模型均已就绪！", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
