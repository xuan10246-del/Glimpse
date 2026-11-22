"""Register local CUDA / cuDNN / llama.cpp DLL directories before loading GPU libs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .config import BASE_DIR, LLAMA_DIR

_DLL_HANDLES: list[object] = []
_REGISTERED: list[str] = []
_CUDA_AVAILABLE: Optional[bool] = None


def setup_cuda_dlls() -> list[str]:
    """Add CUDA 12 / cuDNN 9 DLL directories to Windows loader search paths."""
    if _REGISTERED:
        return list(_REGISTERED)

    candidates: list[Path] = [LLAMA_DIR]
    site_roots: list[Path] = [
        Path(sys.prefix) / "Lib" / "site-packages",
        BASE_DIR / ".venv" / "Lib" / "site-packages",
    ]
    seen_roots: set[str] = set()
    for root in site_roots:
        r_str = str(root).lower()
        if r_str in seen_roots or not root.exists():
            continue
        seen_roots.add(r_str)
        nvidia_dir = root / "nvidia"
        if nvidia_dir.exists():
            for sub in ("cublas", "cudnn", "cuda_runtime"):
                bin_dir = nvidia_dir / sub / "bin"
                if bin_dir.exists():
                    candidates.append(bin_dir)
            for bin_dir in nvidia_dir.glob("*/bin"):
                if bin_dir.is_dir():
                    candidates.append(bin_dir)

    seen_dirs: set[str] = set()
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    prepend_parts: list[str] = []

    for d in candidates:
        if not d.exists():
            continue
        d_str = str(d.resolve())
        d_key = d_str.lower()
        if d_key in seen_dirs:
            continue
        seen_dirs.add(d_key)
        if hasattr(os, "add_dll_directory"):
            try:
                _DLL_HANDLES.append(os.add_dll_directory(d_str))
            except OSError:
                pass
        if d_str not in path_parts:
            prepend_parts.append(d_str)
        _REGISTERED.append(d_str)

    if prepend_parts:
        os.environ["PATH"] = os.pathsep.join(prepend_parts + path_parts)

    return list(_REGISTERED)


def has_nvidia_cuda_gpu() -> bool:
    """Detect whether at least one usable NVIDIA CUDA GPU is available."""
    global _CUDA_AVAILABLE
    if _CUDA_AVAILABLE is not None:
        return _CUDA_AVAILABLE

    setup_cuda_dlls()
    try:
        import ctranslate2

        if int(ctranslate2.get_cuda_device_count()) > 0:
            _CUDA_AVAILABLE = True
            return True
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True,
            creationflags=0x08000000,
            timeout=2.0,
        ).strip()
        if bool(out):
            _CUDA_AVAILABLE = True
            return True
    except Exception:
        pass

    _CUDA_AVAILABLE = False
    return False


def get_compute_device_label() -> str:
    """Return human-readable active compute mode label."""
    return "NVIDIA CUDA" if has_nvidia_cuda_gpu() else "CPU 兼容模式"
