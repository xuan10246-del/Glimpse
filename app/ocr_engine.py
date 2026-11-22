"""Multilingual PP-OCRv5 screen region OCR manager with exact block geometry & color sampling."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Optional

import mss
import numpy as np
from PIL import Image
from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

from .config import OCR_MODEL_DIR, OCR_TEXT_SIM_THRESHOLD
from .llm_service import LocalLLMTranslator

_NOISE_NUM_RE = re.compile(r"^[\d.,kKmMbB+%:\-/~\s]+$")
_SCRIPT_LETTER_RE = re.compile(
    r"[A-Za-z\u00C0-\u024F\u0400-\u04FF\u3040-\u30FF\uAC00-\uD7AF\u4e00-\u9fff]"
)
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_MIXED_GIBBERISH_RE = re.compile(r"(?:[A-Za-z]\d[A-Za-z]|[A-Z]{2,}[a-z][A-Z])")
_CYRILLIC_WORD_RE = re.compile(r"[\u0400-\u04FF]{2,}")

_FOOTER_LINE_RE = re.compile(
    r"^(?:\d{1,4}[-/.]\d{1,2}(?:[-/.]\d{2,4})?|\d+\s*[smhdwy]|just\s*now|yesterday|"
    r"\d+\s*(?:秒前|分钟前|小时前|天前|周前|个月前|年前))?\s*(?:reply|回复)"
    r"(?:\s+[\d.,kKmMbB+]+)?$",
    re.IGNORECASE,
)
_UI_NOISE_LINE_RE = re.compile(
    r"^(?:"
    r"comments?(?:\s*[\d.,kKmMbB+]+)?(?:\s*[×xX])?|"
    r"评论(?:\s*[\d.,kKmMbB+万亿]+)?(?:\s*[×xX])?|"
    r"(?:view|hide)\s+(?:all\s+)?(?:\d+\s+)?repl(?:y|ies|les)(?:\s*[v^<>▾▴⌄])?|"
    r"(?:查看|展开|收起)\s*\d*\s*条?回复.*|"
    r"see\s+(?:translation|original)|查看翻译|查看原文|翻译|"
    r"add\s+(?:a\s+)?comment.*|添加评论.*|发一条友善的评论.*|"
    r"pinned(?:\s+by\s+\S+)?|liked\s+by\s+creator|author|creator|置顶|作者赞过|"
    r"[×xX円@☺😀🤣♥♡·•…]+|"
    r"[@#]?[円@][A-Za-z\u4e00-\u9fff]?"
    r")$",
    re.IGNORECASE,
)


@dataclass
class OCRBlock:
    """One segmented paragraph or comment block extracted from screen OCR with geometry."""

    author: str
    text: str
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # (x0, y0, x1, y1) in physical crop px
    bg_rgb: tuple[int, int, int] = (18, 18, 17)
    fg_rgb: tuple[int, int, int] = (244, 240, 232)


@dataclass
class _OCRRow:
    text: str
    tokens: list[tuple[float, float, str]]  # (x_min, x_max, text)
    y_min: float
    y_max: float
    x_min: float
    x_max: float
    height: float


_LANG_MAP: dict[str, LangRec] = {
    "ch": LangRec.CH,
    "eslav": LangRec.ESLAV,
    "en": LangRec.EN,
    "korean": LangRec.KOREAN,
    "latin": LangRec.LATIN,
}


def is_noise_only(text: str) -> bool:
    """Return True if text is pure UI counter/timestamp noise with no real words."""
    cleaned = text.strip()
    if not cleaned:
        return True
    if _NOISE_NUM_RE.match(cleaned):
        return True
    if len(_SCRIPT_LETTER_RE.findall(cleaned)) < 2:
        return True
    return False


def is_noise_or_chinese(text: str, filter_chinese: bool = False) -> bool:
    """Return True if text is pure UI noise, or (when filter_chinese=True) mostly Chinese."""
    if is_noise_only(text):
        return True
    if filter_chinese:
        cleaned = text.strip()
        non_space = len(re.sub(r"\s+", "", cleaned))
        if non_space > 0:
            cjk_count = len(_CJK_CHAR_RE.findall(cleaned))
            if (cjk_count / non_space) > 0.78:
                return True
    return False


def _line_quality_score(text: str, score: float) -> float:
    q = float(score)
    if _MIXED_GIBBERISH_RE.search(text):
        q -= 0.25
    if _CYRILLIC_WORD_RE.search(text):
        q += 0.08
    if re.search(r"[\u0100-\u024F]", text):
        q -= 0.12
    return q


def _sample_block_colors(
    bgr: Optional[np.ndarray], bbox: tuple[int, int, int, int]
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Sample median background color around bbox (excluding glyphs inside) + contrasting ink."""
    if bgr is None or bgr.ndim < 3:
        return (18, 18, 17), (244, 240, 232)
    h, w = bgr.shape[:2]
    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0:
        return (18, 18, 17), (244, 240, 232)

    ox0, oy0 = max(0, x0 - 4), max(0, y0 - 4)
    ox1, oy1 = min(w, x1 + 4), min(h, y1 + 4)

    samples: list[np.ndarray] = []
    if oy0 < y0:
        samples.append(bgr[oy0:y0, ox0:ox1].reshape(-1, 3))
    if y1 < oy1:
        samples.append(bgr[y1:oy1, ox0:ox1].reshape(-1, 3))
    if ox0 < x0:
        samples.append(bgr[max(0, y0):min(h, y1), ox0:x0].reshape(-1, 3))
    if x1 < ox1:
        samples.append(bgr[max(0, y0):min(h, y1), x1:ox1].reshape(-1, 3))

    if samples:
        ring = np.concatenate(samples, axis=0)
    else:
        ring = bgr[max(0, y0):min(h, y1), max(0, x0):min(w, x1)].reshape(-1, 3)

    if len(ring) == 0:
        return (18, 18, 17), (244, 240, 232)

    med_bgr = np.median(ring, axis=0)
    b_val, g_val, r_val = int(med_bgr[0]), int(med_bgr[1]), int(med_bgr[2])
    bg_rgb = (max(0, min(255, r_val)), max(0, min(255, g_val)), max(0, min(255, b_val)))
    luma = 0.299 * bg_rgb[0] + 0.587 * bg_rgb[1] + 0.114 * bg_rgb[2]
    fg_rgb = (18, 18, 17) if luma > 135.0 else (244, 240, 232)
    return bg_rgb, fg_rgb


class MultilingualOCRManager:
    """Caches PP-OCRv5 ONNX engines and handles fast rec-only fallback + comment segmentation."""

    def __init__(self) -> None:
        self._engines: dict[str, RapidOCR] = {}
        self._rec_engines: dict[str, RapidOCR] = {}
        self._lock = threading.Lock()

    def _get_engine(self, lang_code: str) -> RapidOCR:
        key = lang_code if lang_code in _LANG_MAP else "ch"
        with self._lock:
            if key in self._engines:
                return self._engines[key]
            OCR_MODEL_DIR.mkdir(parents=True, exist_ok=True)
            engine = RapidOCR(
                params={
                    "Global.model_root_dir": str(OCR_MODEL_DIR),
                    "Global.use_cls": False,
                    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    "Det.engine_type": EngineType.ONNXRUNTIME,
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.lang_type": LangDet.MULTI,
                    "Det.model_type": ModelType.MOBILE,
                    "Det.limit_type": "max",
                    "Det.limit_side_len": 960,
                    "Rec.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.lang_type": _LANG_MAP[key],
                    "Rec.model_type": ModelType.MOBILE,
                }
            )
            self._engines[key] = engine
            return engine

    def _get_rec_engine(self, lang_code: str) -> RapidOCR:
        key = lang_code if lang_code in _LANG_MAP else "eslav"
        with self._lock:
            if key in self._rec_engines:
                return self._rec_engines[key]
            OCR_MODEL_DIR.mkdir(parents=True, exist_ok=True)
            engine = RapidOCR(
                params={
                    "Global.model_root_dir": str(OCR_MODEL_DIR),
                    "Global.use_det": False,
                    "Global.use_cls": False,
                    "Global.use_rec": True,
                    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    "Rec.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.lang_type": _LANG_MAP[key],
                    "Rec.model_type": ModelType.MOBILE,
                }
            )
            self._rec_engines[key] = engine
            return engine

    def preload_defaults(self) -> None:
        self._get_engine("ch")
        self._get_rec_engine("eslav")

    @staticmethod
    def _extract_items(out: Any) -> list[tuple[Any, str, float]]:
        if out is None or not getattr(out, "txts", None):
            return []
        boxes = getattr(out, "boxes", None)
        txts = getattr(out, "txts", ())
        scores = getattr(out, "scores", ())
        items: list[tuple[Any, str, float]] = []
        for idx, (txt, sc) in enumerate(zip(txts, scores)):
            t = str(txt).strip()
            s = float(sc)
            if not t or s < 0.45:
                continue
            box = (
                boxes[idx]
                if boxes is not None and idx < len(boxes)
                else [[0, idx * 20], [100, idx * 20], [100, idx * 20 + 18], [0, idx * 20 + 18]]
            )
            items.append((box, t, s))
        return items

    @staticmethod
    def _group_rows(items: list[tuple[Any, str, float]]) -> list[_OCRRow]:
        filtered = [(b, t, s) for b, t, s in items if s >= 0.50 and t]
        if not filtered:
            return []

        parsed: list[tuple[float, float, float, float, float, str]] = []
        heights: list[float] = []
        for box, txt, _sc in filtered:
            xs = [float(pt[0]) for pt in box]
            ys = [float(pt[1]) for pt in box]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            yc = (y_min + y_max) * 0.5
            h = max(1.0, y_max - y_min)
            heights.append(h)
            parsed.append((yc, x_min, x_max, y_min, y_max, txt))

        avg_h = sum(heights) / len(heights)
        parsed.sort(key=lambda item: item[0])

        clusters: list[list[tuple[float, float, float, float, float, str]]] = []
        cur_cluster: list[tuple[float, float, float, float, float, str]] = []
        cur_y: Optional[float] = None
        for entry in parsed:
            yc = entry[0]
            h = max(1.0, entry[4] - entry[3])
            if cur_y is None or abs(yc - cur_y) <= max(avg_h, h) * 0.55:
                cur_cluster.append(entry)
                cur_y = sum(e[0] for e in cur_cluster) / len(cur_cluster)
            else:
                clusters.append(cur_cluster)
                cur_cluster = [entry]
                cur_y = yc
        if cur_cluster:
            clusters.append(cur_cluster)

        rows: list[_OCRRow] = []
        for cl in clusters:
            cl.sort(key=lambda e: e[1])
            tokens = [(e[1], e[2], e[5]) for e in cl]
            joined = re.sub(r"\s+", " ", " ".join(e[5] for e in cl)).strip()
            y_min = min(e[3] for e in cl)
            y_max = max(e[4] for e in cl)
            x_max = max(e[2] for e in cl)
            rows.append(
                _OCRRow(
                    text=joined,
                    tokens=tokens,
                    y_min=y_min,
                    y_max=y_max,
                    x_min=tokens[0][0],
                    x_max=x_max,
                    height=max(1.0, y_max - y_min),
                )
            )
        rows.sort(key=lambda r: r.y_min)
        return rows

    @staticmethod
    def _clean_row_text(row: _OCRRow) -> str:
        toks = list(row.tokens)
        if (
            len(toks) >= 2
            and _NOISE_NUM_RE.match(toks[-1][2])
            and (toks[-1][0] - toks[-2][1]) > max(14.0, row.height * 0.6)
        ):
            toks.pop()
        return re.sub(r"\s+", " ", " ".join(t[2] for t in toks)).strip()

    @classmethod
    def _segment_blocks(
        cls,
        rows: list[_OCRRow],
        bgr_img: Optional[np.ndarray] = None,
        smart_clean: bool = True,
    ) -> list[OCRBlock]:
        if not rows:
            return []
        avg_h = sum(r.height for r in rows) / len(rows)

        if not smart_clean:
            # Group tight vertical lines into paragraph blocks while preserving exact bboxes
            para_groups: list[list[_OCRRow]] = []
            cur: list[_OCRRow] = []
            for row in rows:
                txt = row.text.strip()
                if not txt:
                    continue
                if cur and (row.y_min - cur[-1].y_max) > (avg_h * 0.85):
                    para_groups.append(cur)
                    cur = []
                cur.append(row)
            if cur:
                para_groups.append(cur)

            blocks: list[OCRBlock] = []
            for grp in para_groups:
                body = re.sub(r"\s+", " ", " ".join(r.text for r in grp)).strip()
                if not body:
                    continue
                bbox = (
                    int(min(r.x_min for r in grp)),
                    int(min(r.y_min for r in grp)),
                    int(max(r.x_max for r in grp)),
                    int(max(r.y_max for r in grp)),
                )
                bg_rgb, fg_rgb = _sample_block_colors(bgr_img, bbox)
                blocks.append(OCRBlock(author="", text=body, bbox=bbox, bg_rgb=bg_rgb, fg_rgb=fg_rgb))
            return blocks

        raw_blocks: list[tuple[list[_OCRRow], bool]] = []
        cur_rows: list[_OCRRow] = []
        has_footer_markers = False

        for row in rows:
            clean_txt = cls._clean_row_text(row)
            if not clean_txt:
                continue

            if _FOOTER_LINE_RE.match(clean_txt):
                has_footer_markers = True
                if cur_rows:
                    raw_blocks.append((cur_rows, True))
                    cur_rows = []
                continue

            if (
                _UI_NOISE_LINE_RE.match(clean_txt)
                or _NOISE_NUM_RE.match(clean_txt)
                or len(_SCRIPT_LETTER_RE.findall(clean_txt)) < 2
            ):
                if cur_rows and re.match(
                    r"^(?:comments?|评论|view|hide|查看|展开|收起)", clean_txt, re.I
                ):
                    raw_blocks.append((cur_rows, False))
                    cur_rows = []
                continue

            cleaned_row = _OCRRow(
                text=clean_txt,
                tokens=row.tokens,
                y_min=row.y_min,
                y_max=row.y_max,
                x_min=row.x_min,
                x_max=row.x_max,
                height=row.height,
            )
            if cur_rows and (cleaned_row.y_min - cur_rows[-1].y_max) > (avg_h * 0.85):
                raw_blocks.append((cur_rows, False))
                cur_rows = []
            cur_rows.append(cleaned_row)

        if cur_rows:
            raw_blocks.append((cur_rows, False))

        blocks = []
        for block_rows, ended_with_footer in raw_blocks:
            author = ""
            body_rows = block_rows
            if has_footer_markers and len(block_rows) >= 2:
                r0, r1 = block_rows[0], block_rows[1]
                if (
                    len(r0.text) <= 28
                    and len(r0.text.split()) <= 3
                    and not re.search(r"[.?!。！？]$", r0.text)
                    and (ended_with_footer or abs(r0.x_min - r1.x_min) <= 24.0)
                ):
                    author = r0.text
                    body_rows = block_rows[1:]

            body = re.sub(r"\s+", " ", " ".join(r.text for r in body_rows)).strip()
            if (
                not body
                or _NOISE_NUM_RE.match(body)
                or _UI_NOISE_LINE_RE.match(body)
                or len(_SCRIPT_LETTER_RE.findall(body)) < 2
            ):
                continue

            bbox = (
                int(min(r.x_min for r in body_rows)),
                int(min(r.y_min for r in body_rows)),
                int(max(r.x_max for r in body_rows)),
                int(max(r.y_max for r in body_rows)),
            )
            bg_rgb, fg_rgb = _sample_block_colors(bgr_img, bbox)
            blocks.append(OCRBlock(author=author, text=body, bbox=bbox, bg_rgb=bg_rgb, fg_rgb=fg_rgb))

        return blocks

    def recognize_structured(
        self, img: np.ndarray, lang: str = "auto", smart_clean: bool = True
    ) -> tuple[list[OCRBlock], int]:
        """Run OCR and return clean segmented blocks (with bboxes & sampled colors) + elapsed ms."""
        if img.ndim == 3 and img.shape[2] == 4:
            img = img[:, :, :3]

        t0 = time.perf_counter()
        if lang != "auto" and lang in _LANG_MAP:
            eng = self._get_engine(lang)
            with self._lock:
                out = eng(img)
            chosen = self._extract_items(out)
        else:
            eng_ch = self._get_engine("ch")
            with self._lock:
                out_ch = eng_ch(img)
            ch_items = self._extract_items(out_ch)
            if not ch_items:
                return [], int((time.perf_counter() - t0) * 1000)

            h_img, w_img = img.shape[:2]
            chosen = []
            rec_ru: Optional[RapidOCR] = None
            for box_c, txt_c, sc_c in ch_items:
                skip_fallback = (
                    _NOISE_NUM_RE.match(txt_c)
                    or _FOOTER_LINE_RE.match(txt_c)
                    or _UI_NOISE_LINE_RE.match(txt_c)
                    or len(_CJK_CHAR_RE.findall(txt_c)) > 0
                )
                needs_crop = (not skip_fallback) and (
                    sc_c < 0.74 or bool(_MIXED_GIBBERISH_RE.search(txt_c))
                )
                if not needs_crop:
                    chosen.append((box_c, txt_c, sc_c))
                    continue

                xs = [int(pt[0]) for pt in box_c]
                ys = [int(pt[1]) for pt in box_c]
                x0 = max(0, min(xs) - 4)
                y0 = max(0, min(ys) - 3)
                x1 = min(w_img, max(xs) + 4)
                y1 = min(h_img, max(ys) + 3)
                if (x1 - x0) < 8 or (y1 - y0) < 6:
                    chosen.append((box_c, txt_c, sc_c))
                    continue

                if rec_ru is None:
                    rec_ru = self._get_rec_engine("eslav")
                crop = img[y0:y1, x0:x1]
                with self._lock:
                    out_r = rec_ru(crop)
                txts_r = getattr(out_r, "txts", None) if out_r is not None else None
                scores_r = getattr(out_r, "scores", None) if out_r is not None else None
                if txts_r and scores_r:
                    txt_r = str(txts_r[0]).strip()
                    sc_r = float(scores_r[0])
                    if txt_r and _line_quality_score(txt_r, sc_r) > _line_quality_score(txt_c, sc_c) + 0.01:
                        chosen.append((box_c, txt_r, sc_r))
                        continue
                chosen.append((box_c, txt_c, sc_c))

        rows = self._group_rows(chosen)
        if not rows:
            return [], int((time.perf_counter() - t0) * 1000)

        blocks = self._segment_blocks(rows, bgr_img=img, smart_clean=smart_clean)
        return blocks, int((time.perf_counter() - t0) * 1000)

    def recognize(self, img: np.ndarray, lang: str = "auto") -> tuple[str, int]:
        """Legacy plain-text OCR wrapper."""
        blocks, ms = self.recognize_structured(img, lang=lang, smart_clean=False)
        return (" ".join(b.text for b in blocks).strip(), ms)


@dataclass
class _RegionState:
    region_id: str
    lang: str
    enabled: bool
    smart_clean: bool
    get_rect: Callable[[], Optional[tuple[int, int, int, int]]]
    prev_thumb: Optional[np.ndarray] = None
    last_text: str = ""
    last_block_keys: list[str] = field(default_factory=list)


class MultiRegionOCRWorker:
    """Scans screen capture regions with motion debounce & on-demand stealth shot support."""

    def __init__(
        self,
        llm: LocalLLMTranslator,
        ocr_mgr: MultilingualOCRManager,
        on_result: Callable[[str, str, str, list[dict[str, Any]], str, int, int], None],
        on_error: Optional[Callable[[str, str], None]] = None,
        on_motion: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.llm = llm
        self.ocr_mgr = ocr_mgr
        self.on_result = on_result
        self.on_error = on_error
        self.on_motion = on_motion

        self._regions_lock = threading.Lock()
        self._regions: dict[str, _RegionState] = {}
        self._interval_sec = 0.28
        self._diff_threshold = 1.6
        self._continuous_paused = False
        self._filter_chinese = False

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def set_tuning(self, interval_sec: float, diff_threshold: float) -> None:
        self._interval_sec = max(0.15, min(2.0, float(interval_sec)))
        self._diff_threshold = max(0.3, min(12.0, float(diff_threshold)))

    def set_filter_chinese(self, enabled: bool) -> None:
        val = bool(enabled)
        if val != self._filter_chinese:
            self._filter_chinese = val
            self.reset_dedup()

    def set_continuous_paused(self, paused: bool) -> None:
        """Pause background polling (e.g., Stealth mode uses zero idle CPU)."""
        self._continuous_paused = bool(paused)

    def upsert_region(
        self,
        region_id: str,
        lang: str,
        enabled: bool,
        get_rect: Callable[[], Optional[tuple[int, int, int, int]]],
        smart_clean: bool = True,
    ) -> None:
        with self._regions_lock:
            existing = self._regions.get(region_id)
            if existing is not None:
                lang_changed = existing.lang != lang
                clean_changed = existing.smart_clean != smart_clean
                existing.lang = lang
                existing.enabled = enabled
                existing.smart_clean = smart_clean
                existing.get_rect = get_rect
                if lang_changed or clean_changed or enabled:
                    existing.prev_thumb = None
                    if lang_changed or clean_changed:
                        existing.last_text = ""
                        existing.last_block_keys = []
            else:
                self._regions[region_id] = _RegionState(
                    region_id=region_id,
                    lang=lang,
                    enabled=enabled,
                    smart_clean=smart_clean,
                    get_rect=get_rect,
                )

    def remove_region(self, region_id: str) -> None:
        with self._regions_lock:
            self._regions.pop(region_id, None)

    def reset_dedup(self, region_id: Optional[str] = None) -> None:
        with self._regions_lock:
            if region_id is None:
                for st in self._regions.values():
                    st.prev_thumb = None
                    st.last_text = ""
                    st.last_block_keys = []
            elif region_id in self._regions:
                self._regions[region_id].prev_thumb = None
                self._regions[region_id].last_text = ""
                self._regions[region_id].last_block_keys = []

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="MultiOCRWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.5)
        self._thread = None

    @staticmethod
    def _make_thumb(bgr: np.ndarray) -> np.ndarray:
        gray = Image.fromarray(bgr).convert("L").resize((160, 80), Image.Resampling.BILINEAR)
        return np.asarray(gray, dtype=np.float32)

    @staticmethod
    def _tile_max_mad(a: np.ndarray, b: np.ndarray) -> float:
        """Compute max per-tile MAD over a 4x2 grid so partial text updates inside wide regions never get swallowed."""
        diff = np.abs(a - b)
        # thumb shape is (80, 160) -> reshape into (2, 40, 4, 40)
        tile_means = diff.reshape(2, 40, 4, 40).mean(axis=(1, 3))
        return float(np.max(tile_means))

    def _translate_and_emit(
        self,
        region_id: str,
        blocks: list[OCRBlock],
        ocr_ms: int,
        crop_size: tuple[int, int] = (0, 0),
    ) -> tuple[str, list[str]]:
        pairs = [(b.author, b.text) for b in blocks]
        items, badge, llm_ms = self.llm.translate_blocks(pairs, mode="ocr")
        if not items:
            return "", []

        # Enrich each translated item with matching OCR geometry + sampled background colors
        src_lookup: dict[str, OCRBlock] = {}
        for b in blocks:
            norm_b = " ".join(b.text.strip().split())
            if norm_b not in src_lookup:
                src_lookup[norm_b] = b

        cw, ch = crop_size
        enriched: list[dict[str, Any]] = []
        for idx, it in enumerate(items):
            blk = blocks[idx] if idx < len(blocks) and blocks[idx].text.strip() == it["src"] else src_lookup.get(it["src"])
            item_dict: dict[str, Any] = {
                "author": it["author"],
                "src": it["src"],
                "zh": it["zh"],
                "bbox": list(blk.bbox) if blk is not None else [0, 0, 0, 0],
                "bg_rgb": list(blk.bg_rgb) if blk is not None else [18, 18, 17],
                "fg_rgb": list(blk.fg_rgb) if blk is not None else [244, 240, 232],
                "crop_w": int(cw),
                "crop_h": int(ch),
            }
            enriched.append(item_dict)

        src_plain = "\n".join(
            f"{it['author']}: {it['src']}" if it["author"] else it["src"] for it in enriched
        )
        zh_plain = "\n".join(
            f"{it['author']}: {it['zh']}" if it["author"] else it["zh"] for it in enriched
        )
        self.on_result(region_id, src_plain, zh_plain, enriched, badge, ocr_ms, llm_ms)
        return src_plain, [b.text.lower() for b in blocks]

    def _run_loop(self) -> None:
        with mss.mss() as sct:
            while not self._stop_event.is_set():
                if self._continuous_paused:
                    self._stop_event.wait(0.25)
                    continue

                with self._regions_lock:
                    snapshot = list(self._regions.values())

                any_active = False
                for st in snapshot:
                    if self._stop_event.is_set() or self._continuous_paused:
                        break
                    if not st.enabled:
                        continue
                    rect = st.get_rect()
                    if not rect:
                        continue
                    left, top, width, height = rect
                    if width < 20 or height < 20:
                        continue
                    any_active = True

                    try:
                        shot = sct.grab(
                            {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
                        )
                        bgr = np.asarray(shot)[:, :, :3]
                        thumb = self._make_thumb(bgr)
                        if st.prev_thumb is not None:
                            mad = self._tile_max_mad(thumb, st.prev_thumb)
                            if mad < self._diff_threshold:
                                continue
                            # Large pixel scroll/scene switch: clear In-Place overlay immediately to prevent ghosting
                            if self.on_motion is not None and mad >= max(self._diff_threshold * 2.5, 7.5):
                                self.on_motion(st.region_id)

                        blocks, ocr_ms = self.ocr_mgr.recognize_structured(
                            bgr, lang=st.lang, smart_clean=st.smart_clean
                        )
                        st.prev_thumb = thumb
                        filter_zh = bool(self._filter_chinese)
                        blocks = [b for b in blocks if not is_noise_or_chinese(b.text, filter_chinese=filter_zh)]
                        if not blocks:
                            continue

                        keys = [b.text.lower() for b in blocks]
                        if len(blocks) >= 2:
                            if (
                                st.last_block_keys
                                and len(keys) == len(st.last_block_keys)
                                and all(
                                    SequenceMatcher(None, a, b).ratio() >= 0.92
                                    for a, b in zip(keys, st.last_block_keys)
                                )
                            ):
                                continue
                        elif st.last_text:
                            sim = SequenceMatcher(
                                None, blocks[0].text.lower(), st.last_text.lower()
                            ).ratio()
                            if sim >= OCR_TEXT_SIM_THRESHOLD:
                                continue

                        src_plain, saved_keys = self._translate_and_emit(
                            st.region_id, blocks, ocr_ms, crop_size=(int(width), int(height))
                        )
                        if src_plain:
                            st.last_text = blocks[0].text if len(blocks) == 1 else src_plain
                            st.last_block_keys = saved_keys
                    except Exception as exc:
                        if self.on_error is not None:
                            self.on_error(st.region_id, f"OCR 异常: {exc}")

                self._stop_event.wait(self._interval_sec if any_active else 0.25)

    def capture_region_once(self, region_id: str) -> bool:
        """One-shot capture & translate on an existing region (used by Stealth hotkey trigger)."""
        with self._regions_lock:
            st = self._regions.get(region_id)
        if st is None:
            return False
        rect = st.get_rect()
        if not rect:
            return False
        left, top, width, height = rect
        if width < 15 or height < 15:
            return False
        try:
            with mss.mss() as sct:
                shot = sct.grab(
                    {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
                )
            bgr = np.asarray(shot)[:, :, :3]
            blocks, ocr_ms = self.ocr_mgr.recognize_structured(
                bgr, lang=st.lang, smart_clean=st.smart_clean
            )
            filter_zh = bool(self._filter_chinese)
            valid_blocks = [b for b in blocks if not is_noise_or_chinese(b.text, filter_chinese=filter_zh)]
            if not valid_blocks:
                if self.on_error is not None:
                    msg = (
                        "取词框内仅检测到中文（已开启忽略纯中文）"
                        if (blocks and filter_zh)
                        else "取词框内暂未检测到外语/有效文本"
                    )
                    self.on_error(region_id, msg)
                return False
            self._translate_and_emit(region_id, valid_blocks, ocr_ms, crop_size=(int(width), int(height)))
            return True
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(region_id, f"热键识别失败: {exc}")
            return False

    def snip_translate_rect(
        self,
        rect: tuple[int, int, int, int],
        target_region_id: str,
        lang: str = "auto",
        smart_clean: bool = True,
    ) -> None:
        """One-shot screen capture OCR & translate ignoring frame diff & dedup."""
        left, top, width, height = rect
        if width < 15 or height < 15:
            return
        try:
            with mss.mss() as sct:
                shot = sct.grab(
                    {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
                )
            bgr = np.asarray(shot)[:, :, :3]
            blocks, ocr_ms = self.ocr_mgr.recognize_structured(
                bgr, lang=lang, smart_clean=smart_clean
            )
            filter_zh = bool(self._filter_chinese)
            valid_blocks = [b for b in blocks if not is_noise_or_chinese(b.text, filter_chinese=filter_zh)]
            if not valid_blocks:
                if self.on_error is not None:
                    self.on_error(target_region_id, "选区内暂未识别到有效文本")
                return
            self._translate_and_emit(target_region_id, valid_blocks, ocr_ms, crop_size=(int(width), int(height)))
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(target_region_id, f"框选识别失败: {exc}")
