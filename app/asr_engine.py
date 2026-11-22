"""Multi-backend audio ASR (Faster-Whisper CUDA/CPU + sherpa-onnx 0-VRAM CPU) & Mic recorder."""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import proctap
import psutil
import pyaudiowpatch as pyaudio

from .dll_setup import has_nvidia_cuda_gpu, setup_cuda_dlls

setup_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402

try:
    import sherpa_onnx  # noqa: E402
except Exception:
    sherpa_onnx = None  # type: ignore[assignment]

from .config import (  # noqa: E402
    ASR_MAX_CHUNK_SEC,
    ASR_MIN_SPEECH_SEC,
    ASR_OVERLAP_SEC,
    WHISPER_MODEL_DIR,
)
from .llm_service import LocalLLMTranslator  # noqa: E402
from .model_manager import get_asr_model_dir, is_asr_model_installed  # noqa: E402

_HALLUCINATION_EXACT = {
    "thank you",
    "thank you.",
    "thanks for watching",
    "thanks for watching.",
    "thank you for watching",
    "thank you for watching.",
    "subscribe",
    "bye",
    "bye.",
    "you",
    "you.",
    "mbc",
    "amara.org",
    "subtitles by the amara.org community",
}

_LETTER_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u0400-\u04FF\u3040-\u30FF\uAC00-\uD7AF\u4e00-\u9fff]")
_PA_LOCK = threading.Lock()


def _collect_loopback_devices(pa: pyaudio.PyAudio) -> list[dict]:
    """Enumerate Windows WASAPI loopback devices using an existing PyAudio handle."""
    devices: list[dict] = []
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_idx = int(wasapi.get("defaultOutputDevice", -1))
        default_name = ""
        if default_idx >= 0:
            default_info = pa.get_device_info_by_index(default_idx)
            default_name = str(default_info.get("name", ""))
    except Exception:
        default_name = ""

    for dev in pa.get_loopback_device_info_generator():
        name = str(dev.get("name", ""))
        is_default = bool(default_name and name.startswith(default_name))
        devices.append(
            {
                "index": int(dev["index"]),
                "name": name,
                "channels": max(1, int(dev.get("maxInputChannels", 2))),
                "rate": int(dev.get("defaultSampleRate", 48000)),
                "is_default": is_default,
            }
        )

    devices.sort(key=lambda d: (not d["is_default"], d["index"]))
    return devices


def list_loopback_devices() -> list[dict]:
    """Enumerate available Windows WASAPI loopback output devices safely."""
    with _PA_LOCK:
        pa = pyaudio.PyAudio()
        try:
            return _collect_loopback_devices(pa)
        finally:
            pa.terminate()


def _collect_input_microphones(pa: pyaudio.PyAudio) -> list[dict]:
    """Enumerate non-loopback physical/virtual microphones using an existing PyAudio handle."""
    mics: list[dict] = []
    default_in = -1
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_in = int(wasapi.get("defaultInputDevice", -1))
    except Exception:
        pass
    if default_in < 0:
        try:
            default_in = int(pa.get_default_input_device_info().get("index", -1))
        except Exception:
            default_in = -1

    seen_names: set[str] = set()
    try:
        count = int(pa.get_device_count())
    except Exception:
        count = 0

    for idx in range(count):
        try:
            info = pa.get_device_info_by_index(idx)
        except Exception:
            continue
        if int(info.get("maxInputChannels", 0)) <= 0:
            continue
        if bool(info.get("isLoopbackDevice", False)):
            continue
        name = str(info.get("name", "")).strip()
        if not name or "[loopback]" in name.lower():
            continue
        # Prefer WASAPI endpoints cleanly while avoiding duplicate names
        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        mics.append(
            {
                "index": int(idx),
                "name": name,
                "channels": max(1, min(2, int(info.get("maxInputChannels", 1)))),
                "rate": int(info.get("defaultSampleRate", 48000)),
                "is_default": idx == default_in,
            }
        )

    mics.sort(key=lambda d: (not d["is_default"], d["index"]))
    return mics


def list_input_microphones() -> list[dict]:
    """Safely enumerate input microphones for bidirectional voice output."""
    with _PA_LOCK:
        pa = pyaudio.PyAudio()
        try:
            return _collect_input_microphones(pa)
        finally:
            pa.terminate()


def _to_mono_16k(samples: np.ndarray, channels: int, src_rate: int) -> np.ndarray:
    if channels > 1:
        usable = (len(samples) // channels) * channels
        mono = samples[:usable].reshape(-1, channels).mean(axis=1)
    else:
        mono = samples

    if src_rate == 16000 or len(mono) == 0:
        return mono.astype(np.float32, copy=False)

    if src_rate == 48000:
        usable = (len(mono) // 3) * 3
        if usable == 0:
            return np.zeros(0, dtype=np.float32)
        return mono[:usable].reshape(-1, 3).mean(axis=1).astype(np.float32, copy=False)

    target_len = max(1, int(round(len(mono) * 16000.0 / float(src_rate))))
    x_old = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
    return np.interp(x_new, x_old, mono).astype(np.float32)


def _find_first(root: Path, patterns: tuple[str, ...]) -> Optional[Path]:
    for pat in patterns:
        hits = sorted(root.rglob(pat))
        if hits:
            return hits[0]
    return None


def _build_sherpa_recognizer(backend: str, threads: int = 4) -> Any:
    if sherpa_onnx is None:
        raise RuntimeError("sherpa-onnx 未安装")
    model_dir = get_asr_model_dir(backend)
    if not model_dir.exists():
        raise FileNotFoundError(f"语音模型目录不存在: {model_dir}")

    tokens = _find_first(model_dir, ("tokens.txt",))
    if tokens is None:
        raise FileNotFoundError(f"缺少 tokens.txt: {model_dir}")

    if backend == "sensevoice":
        mdl = _find_first(model_dir, ("model.int8.onnx", "*.onnx"))
        if mdl is None:
            raise FileNotFoundError(f"缺少 SenseVoice .onnx: {model_dir}")
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(mdl),
            tokens=str(tokens),
            num_threads=threads,
            use_itn=True,
            language="",
            provider="cpu",
        )

    if backend == "moonshine":
        prep = _find_first(model_dir, ("preprocess.onnx",))
        enc = _find_first(model_dir, ("encode.int8.onnx", "encode*.onnx"))
        udec = _find_first(model_dir, ("uncached_decode.int8.onnx", "uncached_decode*.onnx"))
        cdec = _find_first(model_dir, ("cached_decode.int8.onnx", "cached_decode*.onnx"))
        if not all([prep, enc, udec, cdec]):
            raise FileNotFoundError(f"Moonshine 模型文件不完整: {model_dir}")
        return sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=str(prep),
            encoder=str(enc),
            uncached_decoder=str(udec),
            cached_decoder=str(cdec),
            tokens=str(tokens),
            num_threads=threads,
            provider="cpu",
        )

    if backend == "parakeet":
        enc = _find_first(model_dir, ("encoder.int8.onnx", "encoder*.onnx"))
        dec = _find_first(model_dir, ("decoder.int8.onnx", "decoder*.onnx"))
        joi = _find_first(model_dir, ("joiner.int8.onnx", "joiner*.onnx"))
        if enc and dec and joi:
            return sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(enc),
                decoder=str(dec),
                joiner=str(joi),
                tokens=str(tokens),
                num_threads=threads,
                model_type="nemo_transducer",
                provider="cpu",
            )
        ctc_mdl = _find_first(model_dir, ("model.int8.onnx", "*.onnx"))
        if ctc_mdl is None:
            raise FileNotFoundError(f"Parakeet 模型文件不完整: {model_dir}")
        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(ctc_mdl),
            tokens=str(tokens),
            num_threads=threads,
            provider="cpu",
        )

    raise ValueError(f"未知 sherpa-onnx 后端: {backend}")


class MicrophoneRecorder:
    """Non-blocking WASAPI microphone recorder for Bidirectional Voice Output."""

    def __init__(self, on_level: Optional[Callable[[float], None]] = None) -> None:
        self.on_level = on_level
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        self._channels = 1
        self._rate = 16000
        self._use_float = True

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self, device_name: str = "") -> str:
        """Start capturing microphone PCM into buffer. Returns chosen mic name."""
        if self._recording:
            self.stop()

        chosen_name = "默认麦克风"
        with self._lock:
            self._chunks.clear()

        def _callback(in_data, _frame_count, _time_info, _status):
            if in_data and self._recording:
                if self._use_float:
                    arr = np.frombuffer(in_data, dtype=np.float32)
                else:
                    arr = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
                mono = _to_mono_16k(arr, self._channels, self._rate)
                if len(mono) > 0:
                    with self._lock:
                        self._chunks.append(mono)
                    if self.on_level is not None:
                        rms = float(np.sqrt(np.mean(mono * mono)))
                        self.on_level(min(1.0, rms * 16.0))
            return (None, pyaudio.paContinue)

        with _PA_LOCK:
            pa = pyaudio.PyAudio()
            mics = _collect_input_microphones(pa)
            if not mics:
                pa.terminate()
                raise RuntimeError("未检测到可用麦克风输入设备")
            dev = mics[0]
            if device_name:
                for m in mics:
                    if m["name"] == device_name:
                        dev = m
                        break

            chosen_name = dev["name"]
            self._channels = dev["channels"]
            self._rate = dev["rate"]
            frames = max(256, int(self._rate * 0.08))
            self._recording = True
            self._use_float = True
            try:
                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=self._channels,
                    rate=self._rate,
                    input=True,
                    input_device_index=dev["index"],
                    frames_per_buffer=frames,
                    stream_callback=_callback,
                )
            except Exception:
                self._use_float = False
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=self._channels,
                    rate=self._rate,
                    input=True,
                    input_device_index=dev["index"],
                    frames_per_buffer=frames,
                    stream_callback=_callback,
                )
            self._pa = pa
            self._stream = stream

        return chosen_name

    def stop(self) -> np.ndarray:
        """Stop capturing and return concatenated 16kHz mono float32 waveform."""
        self._recording = False
        if self.on_level is not None:
            self.on_level(0.0)
        with _PA_LOCK:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._pa is not None:
                try:
                    self._pa.terminate()
                except Exception:
                    pass
                self._pa = None

        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            out = np.concatenate(self._chunks)
            self._chunks.clear()
            return out


class RealtimeASREngine:
    """Real-time dual-mode audio capture + Whisper / sherpa-onnx + translation."""

    def __init__(
        self,
        llm: LocalLLMTranslator,
        on_result: Callable[[str, str, str, int, int], None],
        on_level: Optional[Callable[[float], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.llm = llm
        self.on_result = on_result
        self.on_level = on_level
        self.on_status = on_status
        self.on_error = on_error

        self._whisper_model: Optional[WhisperModel] = None
        self._sherpa_rec: Optional[Any] = None
        self._model_lock = threading.Lock()
        self.asr_backend: str = "whisper"
        self.force_cpu: bool = False
        self.device_mode: str = "cuda"

        self._enabled = False
        self._stop_event = threading.Event()
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=4)
        self._capture_thread: Optional[threading.Thread] = None
        self._infer_thread: Optional[threading.Thread] = None

        self._cfg_lock = threading.Lock()
        self._audio_mode: str = "device"
        self._target_pid: int = 0
        self._target_exe: str = ""
        self._device_name: str = ""
        self._asr_lang: str = "auto"
        self._rms_threshold: float = 0.008
        self._silence_flush_sec: float = 0.45
        self._source_dirty = False

    def load_model(self, backend: Optional[str] = None, force_cpu: Optional[bool] = None) -> None:
        if backend is not None:
            self.asr_backend = backend
        if force_cpu is not None:
            self.force_cpu = bool(force_cpu)

        with self._model_lock:
            target = self.asr_backend
            if target in ("sensevoice", "moonshine", "parakeet") and is_asr_model_installed(target):
                try:
                    threads = max(2, min(4, (os.cpu_count() or 4) // 2))
                    self._sherpa_rec = _build_sherpa_recognizer(target, threads=threads)
                    self._whisper_model = None
                    self.device_mode = f"sherpa-{target}"
                    return
                except Exception as exc:
                    if self.on_error is not None:
                        self.on_error(f"sherpa-onnx 加载失败，回退 Whisper: {exc}")

            self._sherpa_rec = None
            if not WHISPER_MODEL_DIR.exists():
                raise FileNotFoundError(f"Whisper model directory missing: {WHISPER_MODEL_DIR}")

            if (not self.force_cpu) and has_nvidia_cuda_gpu():
                try:
                    self._whisper_model = WhisperModel(
                        str(WHISPER_MODEL_DIR),
                        device="cuda",
                        compute_type="int8_float16",
                        local_files_only=True,
                    )
                    self.device_mode = "cuda"
                    return
                except Exception:
                    pass

            threads = 4 if self.force_cpu else max(4, (os.cpu_count() or 4) // 2)
            self._whisper_model = WhisperModel(
                str(WHISPER_MODEL_DIR),
                device="cpu",
                compute_type="int8",
                cpu_threads=threads,
                local_files_only=True,
            )
            self.device_mode = "cpu"

    def switch_backend(self, backend: str, force_cpu: bool = False) -> None:
        """Reload ASR backend cleanly at runtime (e.g. toggling Game Mode or SenseVoice)."""
        if backend == self.asr_backend and force_cpu == self.force_cpu and (
            self._whisper_model is not None or self._sherpa_rec is not None
        ):
            return
        self.load_model(backend=backend, force_cpu=force_cpu)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            if self.on_level is not None:
                self.on_level(0.0)
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break

    def set_lang(self, lang: str) -> None:
        with self._cfg_lock:
            self._asr_lang = lang or "auto"

    def set_tuning(self, rms_threshold: float, silence_flush_sec: float) -> None:
        with self._cfg_lock:
            self._rms_threshold = max(0.001, min(0.1, float(rms_threshold)))
            self._silence_flush_sec = max(0.2, min(1.5, float(silence_flush_sec)))

    def configure_source(
        self,
        audio_mode: str,
        target_pid: int = 0,
        target_exe: str = "",
        device_name: str = "",
    ) -> None:
        with self._cfg_lock:
            self._audio_mode = "process" if audio_mode == "process" else "device"
            self._target_pid = int(target_pid or 0)
            self._target_exe = target_exe or ""
            self._device_name = device_name or ""
            self._source_dirty = True

    def start(self) -> None:
        self._stop_event.clear()
        if self._capture_thread is None or not self._capture_thread.is_alive():
            self._capture_thread = threading.Thread(
                target=self._capture_loop, name="ASRCapture", daemon=True
            )
            self._capture_thread.start()
        if self._infer_thread is None or not self._infer_thread.is_alive():
            self._infer_thread = threading.Thread(
                target=self._infer_loop, name="ASRInfer", daemon=True
            )
            self._infer_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._capture_thread is not None and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.5)
        if self._infer_thread is not None and self._infer_thread.is_alive():
            self._infer_thread.join(timeout=2.5)
        self._capture_thread = None
        self._infer_thread = None

    @staticmethod
    def _filter_segments(segments) -> str:
        kept: list[str] = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            low = text.lower().strip()
            if low in _HALLUCINATION_EXACT:
                continue
            no_speech = getattr(seg, "no_speech_prob", 0.0) or 0.0
            avg_logprob = getattr(seg, "avg_logprob", 0.0) or 0.0
            if no_speech > 0.75:
                continue
            if len(text) <= 18 and avg_logprob < -1.15:
                continue
            kept.append(text)
        joined = re.sub(r"\s+", " ", " ".join(kept)).strip()
        if len(_LETTER_RE.findall(joined)) < 2:
            return ""
        if joined.lower() in _HALLUCINATION_EXACT:
            return ""
        return joined

    def transcribe_audio_16k(
        self, audio_f32: np.ndarray, lang: Optional[str] = None
    ) -> tuple[str, int]:
        """Transcribe 16kHz mono float32 audio using active backend (Whisper or sherpa-onnx)."""
        if self._whisper_model is None and self._sherpa_rec is None:
            self.load_model()
        if len(audio_f32) < 1600:
            return "", 0

        if lang is None:
            with self._cfg_lock:
                lang = None if self._asr_lang == "auto" else self._asr_lang
        elif lang == "auto":
            lang = None

        t0 = time.perf_counter()
        with self._model_lock:
            if self._sherpa_rec is not None:
                stream = self._sherpa_rec.create_stream()
                stream.accept_waveform(16000, audio_f32.astype(np.float32, copy=False))
                self._sherpa_rec.decode_stream(stream)
                txt = str(getattr(stream.result, "text", "") or "").strip()
                asr_ms = int((time.perf_counter() - t0) * 1000)
                if len(_LETTER_RE.findall(txt)) < 1 or txt.lower() in _HALLUCINATION_EXACT:
                    return "", asr_ms
                return txt, asr_ms

            assert self._whisper_model is not None
            segments, _info = self._whisper_model.transcribe(
                audio_f32,
                language=lang,
                beam_size=1,
                best_of=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )
            seg_list = list(segments)
        asr_ms = int((time.perf_counter() - t0) * 1000)
        return self._filter_segments(seg_list), asr_ms

    def _enqueue_audio(self, chunk: np.ndarray) -> None:
        try:
            self._audio_queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(chunk)
            except queue.Full:
                pass

    def _feed_vad_chunk(self, mono_16k: np.ndarray, state: dict) -> None:
        if len(mono_16k) == 0:
            return
        with self._cfg_lock:
            rms_thresh = self._rms_threshold
            sil_flush = self._silence_flush_sec

        chunk_dur = len(mono_16k) / 16000.0
        rms = float(np.sqrt(np.mean(mono_16k * mono_16k)))
        if self.on_level is not None:
            self.on_level(min(1.0, rms * 18.0))

        if rms >= rms_thresh:
            state["chunks"].append(mono_16k)
            state["samples"] += len(mono_16k)
            state["silence"] = 0.0
            state["in_speech"] = True
        elif state["in_speech"]:
            state["chunks"].append(mono_16k)
            state["samples"] += len(mono_16k)
            state["silence"] += chunk_dur

        total_dur = state["samples"] / 16000.0
        overlap_samples = int(ASR_OVERLAP_SEC * 16000)

        if state["in_speech"] and state["silence"] >= sil_flush:
            if total_dur >= ASR_MIN_SPEECH_SEC:
                self._enqueue_audio(np.concatenate(state["chunks"]))
            state["chunks"].clear()
            state["samples"] = 0
            state["silence"] = 0.0
            state["in_speech"] = False
        elif state["in_speech"] and total_dur >= ASR_MAX_CHUNK_SEC:
            full = np.concatenate(state["chunks"])
            self._enqueue_audio(full)
            tail = full[-overlap_samples:] if len(full) > overlap_samples else full
            state["chunks"] = [tail]
            state["samples"] = len(tail)
            state["silence"] = 0.0
            state["in_speech"] = True

    def _run_process_capture(self, target_pid: int, target_exe: str) -> None:
        if target_pid <= 0 or not psutil.pid_exists(target_pid):
            if self.on_status is not None:
                self.on_status("未锁定软件（请在控制中心选择或拾取窗口）")
            self._stop_event.wait(0.8)
            return

        cap = None
        state = {"chunks": [], "samples": 0, "silence": 0.0, "in_speech": False}
        buf_16k: list[np.ndarray] = []
        buf_samples = 0

        try:
            cap = proctap.ProcessAudioCapture(pid=target_pid)
            fmt = cap.get_format()
            rate = int(fmt.get("sample_rate", 48000))
            channels = int(fmt.get("channels", 2))
            is_float = "float" in str(fmt.get("sample_format", "float32")).lower()
            cap.start()
            if self.on_status is not None:
                self.on_status(f"进程隔离 · {target_exe or target_pid} ({target_pid})")

            last_pid_check = time.monotonic()
            while not self._stop_event.is_set() and self._enabled:
                with self._cfg_lock:
                    if self._source_dirty:
                        break
                now = time.monotonic()
                if now - last_pid_check >= 1.5:
                    last_pid_check = now
                    if not psutil.pid_exists(target_pid):
                        if self.on_status is not None:
                            self.on_status(f"目标软件已关闭 (PID {target_pid})")
                        break

                raw = cap.read(timeout=0.12)
                if not raw:
                    if self.on_level is not None and not state["in_speech"]:
                        self.on_level(0.0)
                    continue

                if is_float:
                    arr = np.frombuffer(raw, dtype=np.float32)
                else:
                    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                mono_16k = _to_mono_16k(arr, channels, rate)
                if len(mono_16k) == 0:
                    continue

                buf_16k.append(mono_16k)
                buf_samples += len(mono_16k)
                if buf_samples >= 1600:
                    merged = np.concatenate(buf_16k)
                    buf_16k.clear()
                    buf_samples = 0
                    self._feed_vad_chunk(merged, state)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(f"进程音频捕获失败: {exc}")
            self._stop_event.wait(1.0)
        finally:
            if cap is not None:
                try:
                    cap.stop()
                except Exception:
                    pass
                try:
                    cap.close()
                except Exception:
                    pass

    def _run_device_capture(self, device_name: str) -> None:
        pa: Optional[pyaudio.PyAudio] = None
        stream = None
        pcm_q: queue.Queue[bytes] = queue.Queue(maxsize=32)
        state = {"chunks": [], "samples": 0, "silence": 0.0, "in_speech": False}

        def _on_audio(in_data, _frame_count, _time_info, _status):
            if in_data:
                try:
                    pcm_q.put_nowait(in_data)
                except queue.Full:
                    try:
                        pcm_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        pcm_q.put_nowait(in_data)
                    except queue.Full:
                        pass
            return (None, pyaudio.paContinue)

        try:
            dev = None
            with _PA_LOCK:
                pa = pyaudio.PyAudio()
                devices = _collect_loopback_devices(pa)
                if devices:
                    dev = devices[0]
                    if device_name:
                        for d in devices:
                            if d["name"] == device_name:
                                dev = d
                                break

                    channels = dev["channels"]
                    rate = dev["rate"]
                    frames_per_buf = max(256, int(rate * 0.1))

                    use_float = True
                    try:
                        stream = pa.open(
                            format=pyaudio.paFloat32,
                            channels=channels,
                            rate=rate,
                            input=True,
                            input_device_index=dev["index"],
                            frames_per_buffer=frames_per_buf,
                            stream_callback=_on_audio,
                        )
                    except Exception:
                        use_float = False
                        stream = pa.open(
                            format=pyaudio.paInt16,
                            channels=channels,
                            rate=rate,
                            input=True,
                            input_device_index=dev["index"],
                            frames_per_buffer=frames_per_buf,
                            stream_callback=_on_audio,
                        )

            if dev is None:
                if self.on_error is not None:
                    self.on_error("未检测到可用 WASAPI 声卡环回设备")
                self._stop_event.wait(1.5)
                return

            if self.on_status is not None:
                short_name = dev["name"][:22] + ("…" if len(dev["name"]) > 22 else "")
                self.on_status(f"整机声卡 · {short_name}")

            while not self._stop_event.is_set() and self._enabled:
                with self._cfg_lock:
                    if self._source_dirty:
                        break
                try:
                    raw = pcm_q.get(timeout=0.12)
                except queue.Empty:
                    if self.on_level is not None and not state["in_speech"]:
                        self.on_level(0.0)
                    continue
                if use_float:
                    arr = np.frombuffer(raw, dtype=np.float32)
                else:
                    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                mono_16k = _to_mono_16k(arr, channels, rate)
                self._feed_vad_chunk(mono_16k, state)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(f"声卡捕获异常: {exc}")
            self._stop_event.wait(1.0)
        finally:
            with _PA_LOCK:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                if pa is not None:
                    try:
                        pa.terminate()
                    except Exception:
                        pass

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._enabled or (self._whisper_model is None and self._sherpa_rec is None):
                self._stop_event.wait(0.2)
                continue
            with self._cfg_lock:
                self._source_dirty = False
                mode = self._audio_mode
                t_pid = self._target_pid
                t_exe = self._target_exe
                d_name = self._device_name

            if mode == "process":
                self._run_process_capture(t_pid, t_exe)
            else:
                self._run_device_capture(d_name)

    def _infer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                audio_16k = self._audio_queue.get(timeout=0.3)
            except queue.Empty:
                continue

            if not self._enabled or (self._whisper_model is None and self._sherpa_rec is None):
                continue

            try:
                src_text, asr_ms = self.transcribe_audio_16k(audio_16k)
                if not src_text:
                    continue
                zh_text, badge, llm_ms = self.llm.translate(src_text, mode="asr")
                if zh_text:
                    self.on_result(src_text, zh_text, badge, asr_ms, llm_ms)
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(f"语音翻译异常: {exc}")
