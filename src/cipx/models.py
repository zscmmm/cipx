"""CIP 提取的数据模型。

定义 CIP 提取全流程中使用的所有数据结构，
包括配置、字段、检测结果和最终输出。
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from mneia_isbn import ISBN  # type: ignore[import-untyped]
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from cipx.config import CORE_FIELDS, settings

DETECT_CLASSES: dict[int, str] = {
    0: "alone",  # 独立的 ISBN 文字
    1: "cip",  # 出版社 CIP 页 ISBN
    2: "bar",  # 条形码 ISBN
}


class Detect(BaseModel):
    """ONNX 模型检测结果（单张图片的单个目标）。

    Attributes:
        box: 检测框坐标 (left, top, right, bottom)。
        image: 裁剪后的目标区域图片。
        score: 检测置信度 (0~1)。
        class_id: 检测到的目标类别 ID。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    box: tuple[int, int, int, int]
    image: Image.Image
    score: float
    class_id: int = 0

    @property
    def class_name(self) -> str:
        """类别名称，如 ``cip``。"""
        return DETECT_CLASSES.get(self.class_id, f"unknown({self.class_id})")

    def __repr__(self) -> str:
        return f"Detect(box={self.box}, score={self.score:.3f}, class={self.class_name})"


class Locate(BaseModel):
    """ISBN 在文件中的位置信息。

    对于多页文档（如 PDF），记录命中页码和定位方式；
    对于单张图片，方法固定为 ``"onnx"``。

    Attributes:
        page: 命中页码（1-indexed），单张图片固定为 1。
            特殊值（仅压缩包/EPUB，无真实页码概念时使用）：

            - ``-1``: EPUB（无页概念）
            - ``-10``: 压缩包 leg001.pdg
            - ``-20``: 压缩包 bookinfo.dat
            - ``-21``: 压缩包 meta.xml

        method: 定位方式。

            - ``"onnx"``: 通过 ONNX 模型检测定位
            - ``"text"``: 通过 PDF 文本搜索定位
            - ``"bookmark"``: 通过书签（版权页/封底）定位
            - ``"bookinfo"``: 从压缩包 bookinfo.dat 元数据提取
            - ``"leg001"``: 从压缩包 leg001.pdg 图片提取
            - ``"pdg"``: 从压缩包兜底 PDG 图片提取
            - ``"epub"``: 从 EPUB 元数据或 XHTML 内容提取

        detect: ONNX 检测结果（OCR 成功的那一个），仅 ``onnx`` 方法时有值。
        candidates: 所有满足置信度阈值的 ONNX 检测候选框列表，仅 ``onnx`` 方法时有值。
    """

    page: int
    method: Literal["bookmark", "text", "onnx", "bookinfo", "leg001", "pdg", "epub"]
    extraction: Literal["text", "ocr", "opf", "opf+xhtml"] = "ocr"
    """数据提取方式。

    - ``"text"``: 从 PDF 文本直接提取（未经过 OCR）
    - ``"ocr"``: 通过 ONNX 检测 + OCR 识别
    - ``"opf"``: 从 EPUB 的 OPF 元数据提取
    - ``"opf+xhtml"``: 从 EPUB 的 OPF 元数据 + XHTML 内容提取
    """

    detect: Detect | None = None
    candidates: list[Detect] = Field(default_factory=list)

    # ── 便捷属性，保持下游访问 .image / .box / .score 的一致性 ──
    @property
    def image(self) -> Image.Image | None:
        """裁剪后的 ISBNX 区域图片，仅 onnx 方法有值。"""
        return self.detect.image if self.detect else None

    @property
    def box(self) -> tuple[int, int, int, int] | None:
        """检测框坐标，仅 onnx 方法有值。"""
        return self.detect.box if self.detect else None

    @property
    def score(self) -> float | None:
        """检测置信度，仅 onnx 方法有值。"""
        return self.detect.score if self.detect else None

    def save(self, directory: str | Path, *, overwrite: bool = False) -> list[Path]:
        """把所有 ONNX 候选裁剪图保存到指定目录。

        Args:
            directory: 目标目录路径。
            overwrite: 是否覆盖已存在的同名文件。

        Returns:
            成功写入的文件路径列表。
        """
        target_dir = Path(directory)
        target_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: list[Path] = []
        for index, detect in enumerate(self.candidates, 1):
            score_tag = int(round(detect.score * 10000))
            filename = f"page{self.page:03d}_{detect.class_id}_{score_tag:03d}.png"
            path = target_dir / filename
            if path.exists() and not overwrite:
                path = target_dir / f"page{self.page:03d}_{detect.class_id}_{score_tag:03d}_{index}.png"
            detect.image.save(path)
            saved_paths.append(path)
        return saved_paths

    def __repr__(self) -> str:
        return f"Locate(page={self.page}, method={self.method}, extraction={self.extraction}, detect={self.detect})"


class Meta(BaseModel):
    """提取的文件级元信息。

    Attributes:
        source: 源文件路径。
        source_type: 源文件类型，"pdf" / "image" / "archive" / "epub"。
        pdf_type: PDF 子类型（仅 PDF 有效），"text_based" / "scanned" / "image_based" / "mixed"。
        encoding: 压缩包(只针对bookinfo.dat文件)或 EPUB 内部文件的编码（仅 archive / epub 有效）。
    """

    source: str
    source_type: Literal["pdf", "image", "archive", "epub"]
    pdf_type: str | None = None
    encoding: str | None = None

    def __repr__(self) -> str:
        parts = [f"source={self.source!r}", f"type={self.source_type!r}"]
        if self.pdf_type:
            parts.append(f"pdf_type={self.pdf_type!r}")
        if self.encoding:
            parts.append(f"encoding={self.encoding!r}")
        return f"Meta({', '.join(parts)})"


class OCRResult(BaseModel):
    """标准化 OCR 识别结果。

    Attributes:
        lines: OCR 识别的文本行列表（已清洗去空）。
        rawocr: OCR 引擎的原始输出（调试用）。
    """

    lines: list[str] = Field(default_factory=list)
    rawocr: Any = None

    @property
    def text(self) -> str:
        """全部文本行，用换行符拼接。"""
        return "\n".join(self.lines)

    def __repr__(self) -> str:
        return f"OCRResult(\n  lines={self.lines},\n  rawocr={self.rawocr}\n)"


class BookInfo(BaseModel):
    """提取的图书字段信息。

    Attributes:
        title: 书名。
        author: 作者。
        publisher: 出版社。
        pubdate: 出版日期。
        isbn: ISBN 号（纯数字 + 末尾 X）。
        cip: CIP 数据核字号。
    """

    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    pubdate: str | None = None
    isbn: str | None = None
    cip: str | None = None
    ssid: str | None = None  # 压缩包专用 ssid 号 (仅压缩包内可能有用)

    @cached_property
    def _isbn(self) -> ISBN | None:
        """缓存的 ISBN 对象，避免重复解析。"""
        if not self.isbn:
            return None
        return ISBN(self.isbn)

    @property
    def isbn_valid(self) -> bool:
        """校验 ISBN 是否有效。"""
        obj = self._isbn
        return obj.is_valid if obj else False

    @property
    def isbn13(self) -> str | None:
        """返回 ISBN-13 格式（如 ``9787123456789``）。"""
        obj = self._isbn
        return str(obj.as_isbn13) if (obj and obj.is_valid) else None

    @property
    def isbn10(self) -> str | None:
        """返回 ISBN-10 格式（如 ``712345678X``）。"""
        obj = self._isbn
        return str(obj.as_isbn10) if (obj and obj.is_valid) else None

    # ── 校验等级 ──

    @property
    def core_filled(self) -> int:
        """非空核心字段数（不含 ssid）。"""
        return sum(1 for f in CORE_FIELDS if getattr(self, f))

    def is_valid(self, strict: int = 4) -> bool:
        """按等级校验 BookInfo 是否有效。数字越小越严格。

        Args:
            strict: 等级 (1-8)，默认 4。
                6 个核心字段: title, author, publisher, pubdate, isbn, cip。

                - 1 (最严格): 全部 6 个核心字段 + ssid 都不能缺失
                - 2: 全部 6 个核心字段都不能缺失
                - 3: ≥3 个核心字段，且必须含 ISBN
                - 4 (默认): ≥3 个核心字段，且 ISBN 或 ssid 至少有一个
                - 5: ≥3 个核心字段
                - 6: ISBN 有效即有效
                - 7: ≥1 个核心字段
                - 8 (最宽松): ≥1 个字段（含 ssid）

        Returns:
            True 表示满足该等级要求。
        """
        filled = self.core_filled
        if strict == 1:
            return filled == len(CORE_FIELDS) and bool(self.ssid)
        if strict == 2:
            return filled == len(CORE_FIELDS)
        if strict == 3:
            return filled >= 3 and bool(self.isbn)
        if strict == 4:
            return filled >= 3 and (bool(self.isbn) or bool(self.ssid))
        if strict == 5:
            return filled >= 3
        if strict == 6:
            return self.isbn_valid
        if strict == 7:
            return filled >= 1
        if strict == 8:
            return filled >= 1 or bool(self.ssid)

        return False


class ExtractResult(BaseModel):
    """一个文件的完整提取结果。

    所有信息铺在顶层，不管来源是图片 / PDF / 压缩包 / EPUB，
    都能拿到一致的字段（部分字段可能因源类型不同为 None）。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── ISBN ──
    bookinfo: BookInfo = Field(default_factory=BookInfo)

    # ── 文件信息 ──
    meta: Meta

    # ── 定位与检测 ──
    locate: Locate | None = None

    # ── OCR 识别 ──
    ocr: OCRResult | None = None

    # ── 其他信息 ──
    elapsed: float | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        """是否成功提取到合法信息。

        EPUB 只以 ISBN 有效性判定，其他类型按全局 ``strict``。
        """
        if self.meta.source_type == "epub":
            return self.bookinfo.isbn_valid
        return self.bookinfo.is_valid(settings.strict)

    def __repr__(self) -> str:
        b = self.bookinfo
        lines = ["ExtractResult("]
        is_epub = self.meta.source_type == "epub"

        # ── success ──
        if is_epub:
            lines.append(f"  success={self.success}  (isbn_valid)")
        else:
            detail_parts = [f"strict={settings.strict}", f"core={b.core_filled}/6"]
            if b.ssid is not None or settings.strict <= 1:
                detail_parts.append(f"ssid={b.ssid!r}")
            lines.append(f"  success={self.success}  ({', '.join(detail_parts)})")

        # ── elapsed / error ──
        if self.elapsed is not None:
            lines.append(f"  elapsed={int(self.elapsed * 1000)}ms")
        if self.error:
            lines.append(f"  error={self.error!r}")

        # ── meta ──
        lines.append("  meta=Meta(")
        lines.append(f"    source={self.meta.source!r}")
        lines.append(f"    type={self.meta.source_type!r}")
        if self.meta.pdf_type:
            lines.append(f"    pdf_type={self.meta.pdf_type!r}")
        if self.meta.encoding:
            lines.append(f"    encoding={self.meta.encoding!r}")
        lines.append("  )")

        # ── bookinfo ──
        if is_epub:
            if b.isbn:
                lines.append("  bookinfo=BookInfo(")
                lines.append(f"    isbn={b.isbn!r}")
                lines.append(f"    isbn_valid={b.isbn_valid}")
                lines.append(f"    isbn13={b.isbn13!r}")
                lines.append(f"    isbn10={b.isbn10!r}")
                lines.append("  )")
            else:
                lines.append("  bookinfo=BookInfo()  (isbn=None)")
        else:
            lines.append("  bookinfo=BookInfo(")
            for label, value in (
                ("title", b.title),
                ("author", b.author),
                ("publisher", b.publisher),
                ("pubdate", b.pubdate),
                ("isbn", b.isbn),
                ("cip", b.cip),
            ):
                lines.append(f"    {label}={value!r}")
            lines.append(f"    isbn_valid={b.isbn_valid}")
            if b.isbn_valid:
                lines.append(f"    isbn13={b.isbn13!r}")
                lines.append(f"    isbn10={b.isbn10!r}")
            if b.ssid or settings.strict <= 1:
                lines.append(f"    ssid={b.ssid!r}")
            lines.append("  )")

        # ── locate / ocr（仅非 EPUB）──
        if not is_epub:
            if self.locate:
                loc = self.locate
                lines.append("  locate=Locate(")
                lines.append(f"    method={loc.method}")
                lines.append(f"    page={loc.page}")
                lines.append(f"    extraction={loc.extraction}")
                if loc.detect:
                    det = loc.detect
                    lines.append("    detect=Detect(")
                    lines.append(f"      box={det.box}")
                    lines.append(f"      score={det.score:.3f}")
                    lines.append(f"      class_id={det.class_id}")
                    lines.append(f"      class_name={det.class_name}")
                    lines.append("    )")
                if loc.candidates:
                    scores = ", ".join(f"{c.score:.3f}" for c in loc.candidates)
                    lines.append(f"    candidates={len(loc.candidates)}  scores=[{scores}]")
                lines.append("  )")
            else:
                lines.append("  locate=None")

            if self.ocr and self.ocr.lines:
                n = len(self.ocr.lines)
                lines.append(f"  ocr=OCRResult(lines={n}):")
                if n <= 15:
                    for j, line in enumerate(self.ocr.lines, 1):
                        lines.append(f"    [{j}] {line}")
                else:
                    for j, line in enumerate(self.ocr.lines[:10], 1):
                        lines.append(f"    [{j}] {line}")
                    lines.append(f"    ... ({n - 10} more)")
            elif self.ocr:
                lines.append("  ocr=OCRResult(lines=0)")
            else:
                lines.append("  ocr=None")

        lines.append(")")
        return "\n".join(lines)

    def save(self, output_dir: str | Path | None = None, *, overwrite: bool = False) -> list[Path]:
        """保存 ONNX 候选裁剪图。

        Args:
            output_dir: 目标根目录。为 ``None`` 时，默认保存到源文件同名目录（不含后缀）。
            overwrite: 是否覆盖已存在的同名文件。

        Returns:
            成功写入的文件路径列表。若当前结果没有 ONNX 候选，则返回空列表。
        """
        if not self.locate or not self.locate.candidates:
            return []

        if output_dir is None:
            source_path = Path(self.meta.source)
            output_dir = source_path.with_suffix("")

        return self.locate.save(output_dir, overwrite=overwrite)

    def __str__(self) -> str:
        return self.__repr__()
