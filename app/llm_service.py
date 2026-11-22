"""Multi-tier translation client: Local llama.cpp (CUDA/Vulkan/CPU) + OpenAI-Compatible Cloud API."""

from __future__ import annotations

import atexit
import concurrent.futures
import ctypes
import ctypes.wintypes as wt
import re
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import requests

from .config import (
    LLAMA_DIR,
    LLAMA_HOST,
    LLAMA_LOG_PATH,
    LLAMA_PORT,
    LLAMA_SERVER_EXE,
    LLAMA_VULKAN_DIR,
    LLAMA_VULKAN_EXE,
    LLM_MODEL_PATH,
    OUTPUT_LANG_CHOICES,
)
from .dll_setup import has_nvidia_cuda_gpu
from .model_manager import resolve_local_model_path

CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_KANA_RE = re.compile(r"[\u3040-\u30FF]")
_HANGUL_RE = re.compile(r"[\uAC00-\uD7AF]")
_LATIN_RE = re.compile(r"[A-Za-z\u00C0-\u024F]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_LANG_FULL_NAMES: dict[str, str] = {
    "en": "English",
    "ru": "Russian (Русский)",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "fr": "French (Français)",
    "de": "German (Deutsch)",
    "es": "Spanish (Español)",
    "zh": "Simplified Chinese (简体中文)",
}


def detect_lang_badge(text: str) -> str:
    """Return a concise language direction badge based on Unicode script counts."""
    cyr = len(_CYRILLIC_RE.findall(text))
    kana = len(_KANA_RE.findall(text))
    hangul = len(_HANGUL_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))

    present = [
        (cyr, "俄→中"),
        (kana, "日→中"),
        (hangul, "韩→中"),
        (lat, "英→中"),
    ]
    active = [(cnt, tag) for cnt, tag in present if cnt >= 2]
    if len(active) >= 2:
        active.sort(reverse=True)
        if active[0][0] > active[1][0] * 3:
            return active[0][1]
        return "多语→中"
    if active:
        return active[0][1]
    if cyr > 0:
        return "俄→中"
    if kana > 0:
        return "日→中"
    if hangul > 0:
        return "韩→中"
    return "英→中"


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _create_kill_on_close_job(proc_handle: int) -> Optional[int]:
    """Bind child process to a Windows Job Object that kills it on parent exit."""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wt.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        if not k32.AssignProcessToJobObject(job, wt.HANDLE(int(proc_handle))):
            k32.CloseHandle(job)
            return None
        return int(job)
    except Exception:
        return None


class LocalLLMTranslator:
    """Manages local llama-server.exe OR OpenAI-compatible Cloud API translation."""

    def __init__(
        self,
        host: str = LLAMA_HOST,
        port: int = LLAMA_PORT,
        cache_size: int = 2048,
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.cache_size = cache_size

        self.engine_mode: str = "local"  # "local" | "api"
        self.llama_backend: str = "auto"  # "auto" | "cuda" | "vulkan" | "cpu"
        self.local_model_id: str = "hy_mt_1_8b_q8"
        self.custom_gguf_path: str = ""
        self.game_mode: bool = False
        self.api_base_url: str = "https://api.deepseek.com/v1"
        self.api_key: str = ""
        self.api_model: str = "deepseek-flash"

        self._active_model_path: Optional[Path] = LLM_MODEL_PATH
        self._active_exe_path: Path = LLAMA_SERVER_EXE
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._job_handle: Optional[int] = None
        self._log_file = None
        self._owns_process = False
        self.device_mode: str = "cuda"

        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, ...], str] = OrderedDict()
        self._tls = threading.local()
        atexit.register(self.stop)

    def _get_local_session(self) -> requests.Session:
        sess = getattr(self._tls, "local_session", None)
        if sess is None:
            sess = requests.Session()
            sess.trust_env = False
            self._tls.local_session = sess
        return sess

    def _get_cloud_session(self) -> requests.Session:
        sess = getattr(self._tls, "cloud_session", None)
        if sess is None:
            sess = requests.Session()
            sess.trust_env = True
            self._tls.cloud_session = sess
        return sess

    def configure(
        self,
        *,
        engine_mode: str = "local",
        llama_backend: str = "auto",
        local_model_id: str = "hy_mt_1_8b_q8",
        custom_gguf_path: str = "",
        game_mode: bool = False,
        api_base_url: str = "",
        api_key: str = "",
        api_model: str = "",
    ) -> None:
        self.engine_mode = "api" if engine_mode == "api" else "local"
        self.llama_backend = llama_backend if llama_backend in ("auto", "cuda", "vulkan", "cpu") else "auto"
        self.local_model_id = local_model_id or "hy_mt_1_8b_q8"
        self.custom_gguf_path = custom_gguf_path or ""
        self.game_mode = bool(game_mode)
        if api_base_url:
            self.api_base_url = api_base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        if api_model:
            self.api_model = api_model.strip()

    def reconfigure(self, **kwargs) -> None:
        """Apply updated config and restart or stop local server cleanly if backend/model changed."""
        old_sig = (
            self.engine_mode,
            self.llama_backend,
            str(self._active_model_path or ""),
            self.game_mode,
        )
        self.configure(**kwargs)
        resolved = resolve_local_model_path(self.local_model_id, self.custom_gguf_path)
        new_sig = (
            self.engine_mode,
            self.llama_backend,
            str(resolved or ""),
            self.game_mode,
        )

        with self._lock:
            self._cache.clear()

        if self.engine_mode == "api":
            if self.api_key:
                self.stop()
                self.device_mode = "api"
                return
            # Fallback to local if no API key configured yet
            self.engine_mode = "local"

        if old_sig != new_sig or not self._is_healthy(timeout=0.8):
            self.stop()
            self.start()

    def active_summary(self) -> str:
        if self.engine_mode == "api" and self.api_key:
            return f"API · {self.api_model or 'Cloud'}"
        model_label = (self._active_model_path.stem if self._active_model_path else "HY-MT1.5")
        if len(model_label) > 18:
            model_label = model_label[:16] + ".."
        if self.game_mode and self.device_mode == "cpu-game":
            return f"GAME · 0显存 · {model_label}"
        return f"{self.device_mode.upper()} · {model_label}"

    def _is_healthy(self, timeout: float = 1.0) -> bool:
        try:
            resp = self._get_local_session().get(f"{self.base_url}/health", timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self, timeout_sec: float = 60.0) -> None:
        """Start selected local runtime (or mark API ready if Cloud API is configured)."""
        if self.engine_mode == "api" and self.api_key:
            self.stop()
            self.device_mode = "api"
            return

        if self._is_healthy(timeout=1.0):
            return

        model_path = resolve_local_model_path(self.local_model_id, self.custom_gguf_path)
        if model_path is None or not model_path.exists():
            raise FileNotFoundError(f"找不到本地翻译模型文件 ({self.local_model_id})")
        self._active_model_path = model_path

        # Build backend preference sequence
        attempts: list[tuple[Path, Path, str, str]] = []  # (exe, workdir, accel, mode_label)
        if self.game_mode and self.llama_backend not in ("vulkan", "cuda"):
            if LLAMA_SERVER_EXE.exists():
                attempts.append((LLAMA_SERVER_EXE, LLAMA_DIR, "cpu-game", "cpu-game"))
        elif self.llama_backend == "vulkan":
            if LLAMA_VULKAN_EXE.exists():
                attempts.append((LLAMA_VULKAN_EXE, LLAMA_VULKAN_DIR, "vulkan", "vulkan"))
            if LLAMA_SERVER_EXE.exists():
                attempts.append((LLAMA_SERVER_EXE, LLAMA_DIR, "cpu", "cpu"))
        elif self.llama_backend == "cpu":
            if LLAMA_SERVER_EXE.exists():
                attempts.append((LLAMA_SERVER_EXE, LLAMA_DIR, "cpu", "cpu"))
        else:
            # auto or cuda
            if has_nvidia_cuda_gpu() and LLAMA_SERVER_EXE.exists():
                attempts.append((LLAMA_SERVER_EXE, LLAMA_DIR, "cuda", "cuda"))
            if self.llama_backend == "auto" and LLAMA_VULKAN_EXE.exists():
                attempts.append((LLAMA_VULKAN_EXE, LLAMA_VULKAN_DIR, "vulkan", "vulkan"))
            if LLAMA_SERVER_EXE.exists():
                attempts.append((LLAMA_SERVER_EXE, LLAMA_DIR, "cpu", "cpu"))

        if not attempts:
            raise FileNotFoundError(f"llama-server.exe not found: {LLAMA_SERVER_EXE}")

        last_err: Optional[Exception] = None
        for exe, wdir, accel, mode_label in attempts:
            try:
                self._launch_and_wait(
                    exe_path=exe,
                    work_dir=wdir,
                    model_path=model_path,
                    accel=accel,
                    mode_label=mode_label,
                    timeout_sec=timeout_sec,
                )
                return
            except Exception as exc:
                last_err = exc
                self.stop()

        if last_err is not None:
            raise last_err

    def _launch_and_wait(
        self,
        exe_path: Path,
        work_dir: Path,
        model_path: Path,
        accel: str,
        mode_label: str,
        timeout_sec: float,
    ) -> None:
        self.device_mode = mode_label
        self._active_exe_path = exe_path
        self._log_file = open(LLAMA_LOG_PATH, "w", encoding="utf-8", errors="replace")

        ngl = "99" if accel in ("cuda", "vulkan") else "0"
        ctx = "1024" if accel == "cpu-game" else "2048"
        cmd = [
            str(exe_path),
            "-m",
            str(model_path),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "-ngl",
            ngl,
            "-c",
            ctx,
            "-np",
            "2",
        ]
        if accel == "cpu-game":
            cmd.extend(["-t", "4"])
        if accel in ("cuda", "vulkan"):
            cmd.extend(["-fa", "on"])
        cmd.append("--no-warmup")

        self._proc = subprocess.Popen(
            cmd,
            cwd=str(work_dir),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        self._owns_process = True
        self._job_handle = _create_kill_on_close_job(int(self._proc._handle))

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._close_log()
                tail = ""
                try:
                    tail = LLAMA_LOG_PATH.read_text(encoding="utf-8", errors="replace")[-1500:]
                except Exception:
                    pass
                raise RuntimeError(
                    f"llama-server exited prematurely (code={self._proc.returncode}):\n{tail}"
                )
            if self._is_healthy(timeout=1.0):
                return
            time.sleep(0.35)

        self.stop()
        raise TimeoutError(f"llama-server did not become healthy within {timeout_sec}s")

    def _close_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.flush()
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def stop(self) -> None:
        """Gracefully terminate the managed llama-server.exe process."""
        if self._proc is not None and self._owns_process:
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5.0)
                except Exception:
                    try:
                        self._proc.kill()
                        self._proc.wait(timeout=2.0)
                    except Exception:
                        pass
            self._proc = None
            self._owns_process = False
        if self._job_handle is not None:
            try:
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                    wt.HANDLE(self._job_handle)
                )
            except Exception:
                pass
            self._job_handle = None
        self._close_log()

    @staticmethod
    def _clean_output(text: str) -> str:
        out = text.strip()
        if len(out) >= 2 and (
            (out[0] == '"' and out[-1] == '"')
            or (out[0] == "“" and out[-1] == "”")
        ):
            inner = out[1:-1].strip()
            if inner:
                out = inner

        parts = re.split(r"(?<=[。！？!?])\s*", out)
        deduped: list[str] = []
        for p in parts:
            p_clean = p.strip()
            if not p_clean:
                continue
            if deduped and p_clean == deduped[-1]:
                continue
            deduped.append(p_clean)
        if deduped:
            out = "".join(deduped)
        return out

    @staticmethod
    def _normalize_cache_text(text: str) -> str:
        s = " ".join(text.strip().split())
        s = s.replace("…", "...")
        return re.sub(r"\.{3,}", "...", s)

    def _chat_complete(self, prompt: str, max_tokens: int = 256) -> tuple[str, int]:
        t0 = time.perf_counter()
        if self.engine_mode == "api" and self.api_key and self.api_base_url:
            endpoint = self.api_base_url.rstrip("/")
            if not endpoint.endswith("/chat/completions"):
                endpoint = f"{endpoint}/chat/completions"
            payload = {
                "model": self.api_model or "deepseek-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "stream": False,
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            resp = self._get_cloud_session().post(
                endpoint, json=payload, headers=headers, timeout=20.0
            )
            resp.raise_for_status()
            data = resp.json()
        else:
            payload = {
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "top_p": 0.6,
                "top_k": 20,
                "repeat_penalty": 1.05,
                "max_tokens": max_tokens,
                "stream": False,
            }
            resp = self._get_local_session().post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        choices = data.get("choices") or []
        content = ""
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
        return self._clean_output(content), elapsed_ms

    @staticmethod
    def test_api_connection(base_url: str, api_key: str, model: str) -> tuple[bool, str, int]:
        """Verify an OpenAI-compatible API endpoint + key with a minimal live request."""
        url = (base_url or "").strip().rstrip("/")
        key = (api_key or "").strip()
        mdl = (model or "").strip()
        if not url or not key or not mdl:
            return False, "请填写完整的 Base URL、API Key 与模型名称", 0
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        sess = requests.Session()
        sess.trust_env = True
        t0 = time.perf_counter()
        try:
            resp = sess.post(
                url,
                json={
                    "model": mdl,
                    "messages": [{"role": "user", "content": "Translate 'Ready' to Simplified Chinese only:"}],
                    "max_tokens": 24,
                    "temperature": 0.2,
                },
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=12.0,
            )
            ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}: {resp.text[:160]}", ms
            data = resp.json()
            txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            return True, f"连接成功 ({ms}ms) · 示例响应: {txt[:32]}", ms
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            return False, f"连接失败: {exc}", ms

    def translate(self, text: str, mode: str = "ocr") -> tuple[str, str, int]:
        """Translate inbound foreign text into Simplified Chinese."""
        src = " ".join(text.strip().split())
        if not src:
            return "", "", 0

        badge = detect_lang_badge(src)
        cache_key = ("in", mode, "game" if self.game_mode else "std", self._normalize_cache_text(src))
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached, badge, 0

        if self.game_mode:
            prompt = (
                "将以下游戏对话或界面文本翻译为简短自然的中文口语，保留玩家ID与专业术语，仅输出翻译结果：\n\n"
                f"{src}"
            )
        else:
            prompt = (
                "将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\n"
                f"{src}"
            )

        zh, elapsed_ms = self._chat_complete(prompt, max_tokens=256)
        if zh:
            with self._lock:
                self._cache[cache_key] = zh
                self._cache.move_to_end(cache_key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)

        return zh, badge, elapsed_ms

    def translate_to_target(
        self, text: str, target_lang: str = "en", tone: str = "natural"
    ) -> tuple[str, int]:
        """Bidirectional output translation (typically Chinese -> target language) for copy/paste."""
        src = " ".join(text.strip().split())
        if not src:
            return "", 0

        lang_name = _LANG_FULL_NAMES.get(target_lang, "English")
        norm = self._normalize_cache_text(src)
        cache_key = ("out", target_lang, tone, norm)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached, 0

        if tone == "game":
            style_hint = "Use ultra-concise, idiomatic online gaming team chat / callout phrasing."
        elif tone == "formal":
            style_hint = "Use polite, clear, professional written tone."
        else:
            style_hint = "Use natural, idiomatic everyday conversational phrasing."

        prompt = (
            f"Translate the following text into {lang_name}. {style_hint} "
            f"Output ONLY the translated text without quotes, pinyin, or explanation:\n\n{src}"
        )
        out_text, elapsed_ms = self._chat_complete(prompt, max_tokens=256)
        if out_text:
            with self._lock:
                self._cache[cache_key] = out_text
                self._cache.move_to_end(cache_key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        return out_text, elapsed_ms

    def translate_blocks(
        self, blocks: list[tuple[str, str]], mode: str = "ocr"
    ) -> tuple[list[dict[str, str]], str, int]:
        """Translate a sequence of (author, text) blocks with per-item caching and 2x parallelism."""
        t0 = time.perf_counter()
        prepared: list[tuple[str, str, str, str]] = []
        misses_map: dict[str, str] = {}

        for author, raw_text in blocks:
            src = " ".join(raw_text.strip().split())
            if not src:
                continue
            norm = self._normalize_cache_text(src)
            non_space = len(re.sub(r"\s+", "", src))
            if non_space > 0 and (len(_CJK_RE.findall(src)) / non_space) > 0.65:
                prepared.append((author, src, norm, src))
                continue

            cached_zh: Optional[str] = None
            ck = ("in", mode, "game" if self.game_mode else "std", norm)
            with self._lock:
                hit = self._cache.get(ck)
                if hit is not None:
                    self._cache.move_to_end(ck)
                    cached_zh = hit
            if cached_zh is not None:
                prepared.append((author, src, norm, cached_zh))
            else:
                if norm not in misses_map:
                    misses_map[norm] = src
                prepared.append((author, src, norm, ""))

        if misses_map:
            miss_items = list(misses_map.items())
            if len(miss_items) == 1:
                norm_k, miss_src = miss_items[0]
                zh_val, _b, _ms = self.translate(miss_src, mode=mode)
                misses_map[norm_k] = zh_val
            else:
                def _run_one(pair: tuple[str, str]) -> tuple[str, str]:
                    nk, ms = pair
                    z, _b, _m = self.translate(ms, mode=mode)
                    return nk, z

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    for nk, z in pool.map(_run_one, miss_items):
                        misses_map[nk] = z

        items: list[dict[str, str]] = []
        for author, src, norm, preset_zh in prepared:
            zh_out = preset_zh if preset_zh else misses_map.get(norm, "")
            if zh_out:
                items.append({"author": author, "src": src, "zh": zh_out})

        badge = detect_lang_badge(" ".join(it["src"] for it in items)) if items else "英→中"
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return items, badge, elapsed_ms
