"""PDF CIP 提取模块。

流程:
  1. pdf-inspector 判断 PDF 类型（text_based / scanned）
  2. 书签检测 → 候选页生成（版权页优先级最高）
  3. text_based: 提取文本 → extract_cip_fields 提取全字段 → 命中则返回
  4. scanned / text 失败: 渲染页面为图片 → Detector.process() 检测
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import fitz  # PyMuPDF
import pymupdf as _pymupdf
from PIL import Image

from cipx.config import settings
from cipx.detector import Detector, get_detector
from cipx.models import BookInfo, ExtractResult, Locate, Meta, OCRResult
from cipx.pdf_type import detect_pdf_type, detect_pdf_type2  # noqa: F401
from cipx.utils.rules import extract_cip_fields

# ── 全局抑制 MuPDF 错误输出 ──
_pymupdf._g_out_message = open(os.devnull, "w", encoding="utf-8")
_pymupdf.JM_mupdf_show_errors = 0
_pymupdf.JM_mupdf_show_warnings = 0

# ── 书签关键词 ──
_BOOKMARK_RULES: list[tuple[re.Pattern, int]] = [
    (re.compile(r"版\s*权"), 0),  # "版权"，优先级最高
]


def _open_pdf(pdf_path: str | Path) -> tuple[fitz.Document | None, str | None]:
    """打开 PDF 文件，自动处理密码/加密。

    Returns:
        (doc, error) — 成功时 ``doc`` 有效、``error`` 为 None；
        失败时 ``doc`` 为 None、``error`` 为失败原因。
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return None, "PDF 文件不存在"
    try:
        doc = fitz.open(str(pdf_path))
    except fitz.FileNotFoundError:
        return None, "PDF 文件不存在"
    except Exception:
        return None, "PDF 文件格式错误或损坏"
    if doc.needs_pass:
        doc.close()
        return None, "PDF 文件有密码保护"
    if doc.page_count == 0:
        doc.close()
        return None, "PDF 文件为空（0 页）"
    return doc, None


def _check_bookmarks(doc: fitz.Document) -> list[int]:
    """从书签中查找版权相关页，按优先级返回列表。

    Returns:
        按优先级排序的页码列表（1-indexed），空列表表示未找到。
    """
    toc = doc.get_toc()
    if not toc:
        return []

    matched: list[tuple[int, int]] = []  # (page, priority)
    seen_pages: set[int] = set()
    for _level, title, page in toc:
        if page in seen_pages:
            continue
        for pattern, priority in _BOOKMARK_RULES:
            if pattern.search(title):
                matched.append((page, priority))
                seen_pages.add(page)
                break

    # 按优先级排序
    matched.sort(key=lambda x: x[1])
    return [page for page, _ in matched]


def _get_candidate_pages(page_count: int) -> list[int]:
    """生成候选页码列表（1-indexed）。"""
    front_start = max(1, settings.pdf.front_start)
    front_end = min(page_count, settings.pdf.front_end)
    back_start = max(1, page_count - settings.pdf.back_start + 1)
    back_end = max(1, page_count - settings.pdf.back_end + 1)

    front = list(range(front_start, front_end + 1))
    back = list(range(back_start, back_end + 1))

    seen: set[int] = set()
    result: list[int] = []
    for p in front + back:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _extract_text_from_page(doc: fitz.Document, page_num: int) -> list[str]:
    """从 PDF 页面提取文本行。"""
    try:
        page = doc[page_num - 1]
        text = page.get_text()
        if not isinstance(text, str):
            text = str(text)
        return [line.strip() for line in text.split("\n") if line.strip()]
    except (IndexError, RuntimeError, ValueError, AttributeError):
        return []


def _render_page_to_image(doc: fitz.Document, page_num: int, zoom: float = 2.0) -> Image.Image:
    """将 PDF 单页渲染为 PIL Image。"""
    page = doc[page_num - 1]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


class PdfExtractor:
    """PDF CIP 提取器。"""

    @classmethod
    def extract(cls, pdf_path: str | Path, detector: Detector | None = None) -> ExtractResult:
        """从 PDF 中提取 CIP / ISBN。

        Args:
            pdf_path: PDF 文件路径。
            detector: 外部传入的 Detector 实例，为 None 时使用全局单例。

        Returns:
            ExtractResult — 包含 bookinfo、定位信息、耗时等。
        """
        t0 = time.perf_counter()
        pdf_path = Path(pdf_path)

        # ── 打开 PDF ──
        doc, open_error = _open_pdf(pdf_path)
        if doc is None:
            return ExtractResult(
                bookinfo=BookInfo(),
                meta=Meta(source=str(pdf_path), source_type="pdf"),
                error=open_error or "无法打开 PDF 文件",
                elapsed=time.perf_counter() - t0,
            )

        try:
            # ── 1. PDF 类型检测 ──

            import pdf_inspector  # type: ignore

            pdf_type = pdf_inspector.detect_pdf(str(pdf_path)).pdf_type
        except Exception:
            pdf_type = detect_pdf_type(doc)

        try:
            page_count = doc.page_count
            # ── 2. 书签检测 + 候选页 ──
            bookmark_pages = _check_bookmarks(doc)
            candidates = _get_candidate_pages(page_count)
            # 按优先级将书签页插入候选列表最前
            for page in reversed(bookmark_pages):
                if page in candidates:
                    candidates.remove(page)
                candidates.insert(0, page)

            if not candidates:
                return ExtractResult(
                    bookinfo=BookInfo(),
                    meta=Meta(source=str(pdf_path), source_type="pdf", pdf_type=pdf_type),
                    error="无候选页面",
                    elapsed=time.perf_counter() - t0,
                )

            # ── 3. text_based: 文本提取（其他类型直接走渲染+ONNX）──
            if pdf_type == "text_based":
                for page_num in candidates:
                    lines = _extract_text_from_page(doc, page_num)
                    if not lines:
                        continue

                    # 尝试提取全字段（书名、作者、出版社、ISBN、CIP）
                    bookinfo = extract_cip_fields(lines)
                    if bookinfo.is_valid(settings.strict):
                        locate_method = "bookmark" if page_num in bookmark_pages else "text"
                        return ExtractResult(
                            bookinfo=bookinfo,
                            meta=Meta(source=str(pdf_path), source_type="pdf", pdf_type=pdf_type),
                            locate=Locate(page=page_num, method=locate_method, extraction="text"),
                            ocr=OCRResult(lines=lines),
                            elapsed=time.perf_counter() - t0,
                        )

            # ── 4. scanned / text 失败: 渲染 + ONNX 检测 ──
            best_result: ExtractResult | None = None
            best_score: float = 0.0

            for page_num in candidates:
                img = _render_page_to_image(doc, page_num)
                det = detector or get_detector()
                result = det.process(img, source=str(pdf_path), source_type="pdf")
                if result.success:
                    result.locate.page = page_num  # type: ignore[union-attr]
                    result.elapsed = time.perf_counter() - t0
                    return result

                # 未成功但 ONNX 有检测 → 记录置信度最高的
                score = result.locate.detect.score if result.locate and result.locate.detect else 0.0
                if score > best_score:
                    best_score = score
                    best_result = result
                    best_result.locate.page = page_num  # type: ignore[union-attr]

            # 所有候选页均失败，但保留检测信息
            if best_result is not None:
                best_result.elapsed = time.perf_counter() - t0
                return best_result

            return ExtractResult(
                bookinfo=BookInfo(),
                meta=Meta(source=str(pdf_path), source_type="pdf", pdf_type=pdf_type),
                error="未检测到 CIP 区域",
                elapsed=time.perf_counter() - t0,
            )

        finally:
            doc.close()
