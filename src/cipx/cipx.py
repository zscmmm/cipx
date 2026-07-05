"""ISBN 提取入口，提供统一的提取接口。"""

from __future__ import annotations

from pathlib import Path

from cipx.archive import ArchiveExtractor
from cipx.config import Settings, configure, settings
from cipx.detector import get_detector
from cipx.epub import EpubExtractor
from cipx.models import ExtractResult
from cipx.pdf import PdfExtractor
from cipx.utils.io import detect_file_kind, load_image, require_suffix


class CIPX:
    def __init__(self, config: Settings | None = None) -> None:
        """初始化 CIPX 入口。

        Args:
            config: 自定义配置，为 None 时使用全局默认配置。
        """
        self.config = config or settings
        self._apply_config()
        self._detector = get_detector()  # 创建时自动预热

    def _apply_config(self) -> None:
        """将实例级配置同步到全局 settings 对象。"""
        if self.config is not settings:
            configure(**self.config.model_dump())

    # ── 单张图片 ──

    def from_image(
        self,
        path: str | Path,
        page: int = 1,
    ) -> ExtractResult:
        """从单张图片中提取 CIP / ISBN。

        Args:
            path: 图片文件路径。
            page: 页码（暂只支持第 1 页）。

        Returns:
            :class:`~cipx.models.ExtractResult` 包含提取结果。
        """
        if page != 1:
            raise NotImplementedError("多页图片暂不支持指定页码")

        image = load_image(path)
        return self._detector.process(image, source=str(path), source_type="image")

    # ── 统一入口 ──

    def extract(
        self,
        path: str | Path,
        page: int = 1,
    ) -> ExtractResult:
        """根据文件后缀自动选择对应的提取方法。

        Args:
            path: 文件路径（支持图片/PDF/EPUB/压缩包）。
            page: 页码（仅图片有效，暂只支持第 1 页）。

        Returns:
            :class:`~cipx.models.ExtractResult` 包含提取结果。
        """
        kind = detect_file_kind(path)
        if kind == "image":
            return self.from_image(path, page=page)
        if kind == "pdf":
            return self.from_pdf(path)
        if kind == "epub":
            return self.from_epub(path)
        return self.from_archive(path)

    # ── PDF ──

    def from_pdf(
        self,
        path: str | Path,
    ) -> ExtractResult:
        """从 PDF 文件中提取 ISBN。

        支持文本型 PDF（文本提取）和扫描件（渲染为图片后 ONNX 检测）。
        先通过 pdf-inspector 判断类型，优先尝试文本提取 CIP 字段，
        失败时渲染候选页为图片交由 Detector.process() 检测。

        Args:
            path: PDF 文件路径。

        Returns:
            :class:`~cipx.models.ExtractResult` 包含提取结果。
        """
        require_suffix(path, (".pdf",), "PDF")
        return PdfExtractor.extract(path, detector=self._detector)

    # ── EPUB ──

    def from_epub(
        self,
        path: str | Path,
    ) -> ExtractResult:
        """从 EPUB 文件中提取 ISBN（暂只支持文本扫描，不支持图片版 EPUB）。

        流程：
        1. 解析 OPF 元数据提取 ISBN
        2. 扫描 XHTML 版权页正则匹配（兜底）
        3. 图片版 EPUB 渲染图片 → ONNX 检测（扫描件）

        Args:
            path: EPUB 文件路径。

        Returns:
            :class:`~cipx.models.ExtractResult` 包含提取结果（仅 ISBN 字段）。
        """
        require_suffix(path, (".epub",), "EPUB")
        return EpubExtractor.extract(path)

    # ── 压缩包（PDG / 其它） ──

    def from_archive(
        self,
        path: str | Path,
    ) -> ExtractResult:
        """从压缩包（zip/rar/uvz）中提取 CIP / ISBN。

        流程：
        1. 解析 ``bookinfo.dat`` 提取全字段
        2. 尝试 ``leg001.pdg`` 解码为图片后 ONNX 检测
        3. 兜底：前 N 个 PDG 文件逐张尝试（由 ``archive_pdg_fallback_count`` 控制）

        Args:
            path: 压缩包文件路径。

        Returns:
            :class:`~cipx.models.ExtractResult` 包含提取结果。
        """
        require_suffix(path, (".zip", ".rar", ".uvz"), "压缩包")
        return ArchiveExtractor.extract(path, detector=self._detector)


def extract(
    path: str | Path,
    config: Settings | None = None,
    page: int = 1,
) -> ExtractResult:
    """通用提取函数，根据后缀自动分发到最合适的提取入口。

    Args:
        path: 文件路径，支持图片 / PDF / EPUB / 压缩包。
        config: 可选的配置对象，若不传则使用全局配置。
        page: 页码，仅对图片有效，PDF/EPUB/压缩包忽略。

    Returns:
        :class:`~cipx.models.ExtractResult` 包含提取结果。
    """
    return CIPX(config=config).extract(path, page=page)
