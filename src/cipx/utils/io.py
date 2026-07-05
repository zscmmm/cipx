"""IO 工具函数：图片加载、颜色解析、OCR JSON 读写。"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
from PIL import Image, ImageOps

if TYPE_CHECKING:
    from cv2.typing import MatLike

ExtractKind = Literal["image", "pdf", "epub", "archive"]

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
_PDF_SUFFIXES = (".pdf",)
_EPUB_SUFFIXES = (".epub",)
_ARCHIVE_SUFFIXES = (".zip", ".rar", ".uvz")


def require_suffix(path: str | Path, suffixes: tuple[str, ...], kind: str) -> Path:
    """快速校验文件后缀，避免把明显不对的输入交给下层解析。

    Args:
        path: 文件路径。
        suffixes: 允许的后缀集合，如 ``(".pdf",)``。
        kind: 描述文件类型的字符串，用于错误提示，如 ``"PDF"``。

    Returns:
        解析后的 ``Path`` 对象。

    Raises:
        ValueError: 后缀不匹配时抛出。
    """
    file_path = Path(path)
    ext = file_path.suffix.lower()
    if ext not in suffixes:
        allowed = ", ".join(suffixes)
        raise ValueError(f"不支持的{kind}格式: {ext or '无后缀'}（支持 {allowed}）")
    return file_path


def detect_file_kind(path: str | Path) -> ExtractKind:
    """根据后缀快速判断该文件适合走哪条提取路径。"""
    ext = Path(path).suffix.lower()
    if ext in _IMAGE_SUFFIXES:
        return "image"
    if ext in _PDF_SUFFIXES:
        return "pdf"
    if ext in _EPUB_SUFFIXES:
        return "epub"
    if ext in _ARCHIVE_SUFFIXES:
        return "archive"
    raise ValueError(f"不支持的文件格式: {ext or '无后缀'}（支持图片/pdf/epub/zip/rar/uvz）")


def load_image(img: str | Image.Image | MatLike | Path | bytes) -> Image.Image:
    """统一加载图片为 RGB PIL Image，支持路径 / bytes / ndarray / PIL。"""
    if isinstance(img, Image.Image):
        return ImageOps.exif_transpose(img).convert("RGB")
    if isinstance(img, (str, Path)):
        with Image.open(img) as image:
            return ImageOps.exif_transpose(image).convert("RGB")
    if isinstance(img, bytes):
        with Image.open(BytesIO(img)) as image:
            return ImageOps.exif_transpose(image).convert("RGB")

    array = np.asarray(img)
    if array.ndim == 2:
        return Image.fromarray(array).convert("RGB")
    if array.ndim == 3 and array.shape[2] == 4:
        return Image.fromarray(array[:, :, [2, 1, 0, 3]], "RGBA").convert("RGB")
    if array.ndim == 3 and array.shape[2] == 3:
        return Image.fromarray(array[:, :, ::-1], "RGB").convert("RGB")
    raise ValueError(f"Unsupported image array shape: {array.shape}")


def parse_rgb_color(value: str) -> tuple[int, int, int]:
    """解析 RGB 颜色字符串 "r,g,b" 为三元组。"""
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError(f"RGB color must have 3 values: {value}")
    return tuple(parts)  # type: ignore[return-value]


def save_ocr_json(data: list | dict, filename: str) -> None:
    """将 OCR 返回的数据保存为 JSON 文件。

    Args:
        data: OCR 识别结果数据（列表或字典）。
        filename: 输出 JSON 文件路径。
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_ocr_json(filename: str) -> list | dict:
    """读取 OCR JSON 文件。

    Args:
        filename: JSON 文件路径。

    Returns:
        解析后的 OCR 数据。
    """
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data
