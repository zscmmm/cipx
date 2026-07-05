"""压缩包（zip/rar/uvz）CIP 提取模块。

流程:
  1. 检查密码保护
  2. 统计 *.pdg 文件数量（低于阈值则跳过）
  3. 解析 bookinfo.dat → 提取字段（最高优先级）
  4. 解析 meta.xml → 提取字段（兜底，bookinfo.dat 无效时尝试）
  5. 尝试 leg001.pdg → 解码为图片 → ONNX 检测 + OCR
  6. 兜底：前 N 个 PDG 文件逐张尝试（OCR）

每一步达标（is_valid）即早停返回，不继续后续步骤。
"""

from __future__ import annotations

import ctypes
import io
import os
import sys
import tempfile
import time
import zipfile
from abc import ABC, abstractmethod
from importlib.resources import files
from pathlib import Path

from PIL import Image

from cipx.config import settings
from cipx.detector import Detector, get_detector
from cipx.models import BookInfo, ExtractResult, Locate, Meta

# ── 图片文件签名 ──
_JPEG_HEADER = b"\xff\xd8\xff"
_PNG_HEADER = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"

# ── 安全限制 ──
_MAX_BOOKINFO_SIZE = 1 * 1024 * 1024  # bookinfo.dat 最大 1MB
_MAX_PDG_SIZE = 50 * 1024 * 1024  # 单个 PDG 文件最大 50MB

# ── bookinfo.dat 字段映射 ──
_KEY_MAP: dict[str, str] = {
    "isbn号": "isbn",
    "isbn13": "isbn",
    "isbn10": "isbn",
    "isbn": "isbn",
    "ss号": "ssid",
    "ssid号": "ssid",
    "ssid": "ssid",
    "ss": "ssid",
    "书名": "title",
    "题名": "title",
    "bookname": "title",
    "作者": "author",
    "著者": "author",
    "author": "author",
    "出版社": "publisher",
    "出版者": "publisher",
    "publisher": "publisher",
    "press": "publisher",
    "出版日期": "pubdate",
    "出版时间": "pubdate",
    "出版年": "pubdate",
    "pubdate": "pubdate",
    "cip": "cip",
    "核字号": "cip",
}


def _get_names(arc) -> set[str]:
    """获取压缩包内所有文件路径的集合。"""
    return set(arc.namelist())


def _count_pdg(names: set[str]) -> int:
    """统计压缩包内 *.pdg 文件数量。"""
    return sum(1 for n in names if n.lower().endswith(".pdg"))


def _list_pdg(names: set[str]) -> list[str]:
    """列出压缩包内所有 *.pdg 文件路径（按文件名排序）。"""
    return sorted(n for n in names if n.lower().endswith(".pdg"))


# ── bookinfo.dat 解析 ──


def _decode_bookinfo(raw: bytes) -> tuple[str, str]:
    """解码 bookinfo.dat，返回 (文本, 编码)。"""
    try:
        text = raw.decode("gb18030")
        if "\ufffd" in text:
            text = raw.decode("utf-8", errors="replace")
            return text, "utf-8"
        return text, "gb18030"
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        return text, "utf-8"


def _parse_bookinfo(text: str) -> dict[str, str | None]:
    """从 bookinfo.dat 文本中提取全部图书字段。

    解析 ``[Section]`` 下的 ``key=value`` 格式，
    返回可直接传给 ``BookInfo(**result)`` 的字典。
    """
    result: dict[str, str | None] = {
        "title": None,
        "author": None,
        "publisher": None,
        "pubdate": None,
        "isbn": None,
        "cip": None,
        "ssid": None,
    }
    section: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section is None:
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue

        mapped = _KEY_MAP.get(key)
        if mapped is None:
            continue
        if result[mapped] is not None:
            continue  # 已有值则不覆盖

        if mapped == "isbn":
            cleaned = value.replace("-", "").replace(" ", "").upper()
            if len(cleaned) == 10:
                result["isbn"] = cleaned
            elif len(cleaned) == 13 and cleaned.startswith(("978", "979")):
                result["isbn"] = cleaned
        else:
            result[mapped] = value
    return result


# ── meta.xml 解析 ──

_META_XML_MAP: dict[str, str] = {
    "title": "title",
    "creator": "author",
    "publisher": "publisher",
    "date": "pubdate",
    "ssid": "ssid",
}


def _parse_meta_xml(raw: bytes) -> dict[str, str | None]:
    """解析 meta.xml，提取图书字段。

    meta.xml 是部分压缩包中附带的元数据文件，
    编码可能是 UTF-8 或 GBK（常见于中文 PDG 压缩包），
    例如::

        <meta>
        <ssid>96391214</ssid>
        <title>李鸿藻书札</title>
        <creator>陆德富，谢亚衡编</creator>
        <publisher>西泠印社出版社</publisher>
        <date>2024.06</date>
        <pages>245</pages>
        </meta>

    Returns:
        可直接传给 BookInfo(**result) 的字典。
    """
    result: dict[str, str | None] = {
        "title": None,
        "author": None,
        "publisher": None,
        "pubdate": None,
        "ssid": None,
    }

    import xml.etree.ElementTree as ET  # noqa: PLC0415

    def _parse_xml_text(text: str) -> dict[str, str | None]:
        """从已解码的 XML 字符串中提取字段。"""
        parsed: dict[str, str | None] = {
            "title": None,
            "author": None,
            "publisher": None,
            "pubdate": None,
            "ssid": None,
        }
        # 传入 str 时 ET 会忽略 XML 声明的 encoding，直接按 Unicode 处理
        try:
            root = ET.fromstring(text)
        except Exception:
            return parsed

        for child in root:
            tag = child.tag.lower().strip()
            mapped = _META_XML_MAP.get(tag)
            if mapped and child.text:
                t = child.text.strip()
                if t and parsed[mapped] is None:
                    parsed[mapped] = t
        return parsed

    # ── 策略 1: 直接让 ET.fromstring 处理（它支持 XML 声明的 encoding）──
    try:
        root = ET.fromstring(raw)
        for child in root:
            tag = child.tag.lower().strip()
            mapped = _META_XML_MAP.get(tag)
            if mapped and child.text:
                t = child.text.strip()
                if t and result[mapped] is None:
                    result[mapped] = t
        # 如果至少解析出一个字段，直接返回
        if any(v is not None for v in result.values()):
            return result
    except Exception:
        pass

    # ── 策略 2: 尝试常见中文编码 ──
    for enc in ("gb18030", "gbk", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            parsed = _parse_xml_text(text)
            if any(v is not None for v in parsed.values()):
                return parsed
        except Exception:
            continue

    # ── 策略 3: 兜底 — 用 replace 模式解码后尝试 ──
    try:
        for enc in ("gb18030", "utf-8"):
            text = raw.decode(enc, errors="replace")
            parsed = _parse_xml_text(text)
            if any(v is not None for v in parsed.values()):
                return parsed
    except Exception:
        pass

    return result


# ── PDG 文件检测与解码 ──


def _is_image_file(data: bytes) -> bool:
    """检查字节数据是否为常见图片格式（jpg/png）。"""
    return data.startswith(_JPEG_HEADER) or data.startswith(_PNG_HEADER)


def _pdg_to_image(data: bytes) -> Image.Image | None:
    """将 PDG 字节数据解码为 PIL Image。

    流程:
      1. 检查文件头是否为常见图片格式（jpg/png/gif/webp）
      2. 是 → 直接用 PIL 读取
      3. 否 → PdgView.dll 解码 → 再用 PIL 读取
    """
    # 先检查是不是直接就是图片（PDG 实际是 jpg/png 改名）
    if _is_image_file(data):
        try:
            return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            return None

    # 不是标准图片 → 用 PdgView.dll 解码
    try:
        img = _pdg_decode_with_dll(data)
        if img is not None:
            return img
    except Exception:
        pass

    # 最后兜底：让 PIL 试试（某些 pdg 可能被 PIL 直接支持）
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _parse_pdg_header(data: bytes) -> tuple[int, int, int] | None:
    """解析 PDG 文件头，返回 (width, height, pdg_type) 或 None。"""
    if len(data) < 140:
        return None
    pdg_type = data[15]
    if pdg_type == 0xFF or pdg_type == 0x10:
        return None
    x_pix = int.from_bytes(data[16:18], "little", signed=False)
    y_pix = int.from_bytes(data[18:20], "little", signed=False)
    if pdg_type in (0xAA, 0xAC):
        x_pix, y_pix = 1120, 1568
    elif pdg_type == 0xAB:
        x_pix, y_pix = y_pix, x_pix
    return x_pix, y_pix, pdg_type


def _pdg_decode_with_dll(data: bytes) -> Image.Image | None:
    """使用 PdgView.dll 解码 PDG 文件（仅 Windows 平台有效）。"""
    if sys.platform != "win32":
        return None
    pkg_path = files(__package__) / settings.archive.pdgview_path
    if pkg_path.is_file():
        dll_path = Path(str(pkg_path))  # 包内有文件
    else:
        dll_path = Path(settings.archive.pdgview_path)  # 走文件系统路径
        if not dll_path.is_file():
            return None

    dll = ctypes.cdll.LoadLibrary(str(dll_path))
    dll.pdgInit()

    header = _parse_pdg_header(data)
    if header is None:
        return None
    x_pix, y_pix, _ = header

    fd, tmp_path = tempfile.mkstemp(suffix=".pdg")
    decoded_ok = False
    img_buffer_ptr = ctypes.c_void_p()
    try:
        os.write(fd, data)
        os.close(fd)

        size = ctypes.c_int()
        imgtype = ctypes.c_int()
        ret = dll.pdgDecode(
            ctypes.c_char_p(tmp_path.encode("utf-8") + b"\0"),
            ctypes.c_int(x_pix),
            ctypes.c_int(y_pix),
            ctypes.byref(img_buffer_ptr),
            ctypes.byref(size),
            ctypes.byref(imgtype),
        )
        if ret != 0 or not img_buffer_ptr:
            return None
        decoded_ok = True

        buf = (ctypes.c_byte * size.value).from_address(img_buffer_ptr.value)  # type: ignore[arg-type]
        img = Image.open(io.BytesIO(bytes(buf))).convert("RGB")
        return img
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if decoded_ok:
            try:
                dll.pdgFreeBuffer(img_buffer_ptr)
            except Exception:
                pass


def _extract_bookinfo_from_image(data: bytes, detector=None) -> BookInfo | None:
    """将 PDG 字节解码为图片后运行 ONNX 检测 + OCR，返回 BookInfo。"""
    img = _pdg_to_image(data)
    if img is None:
        return None
    det = detector or get_detector()
    result = det.process(img)
    return result.bookinfo if result.bookinfo.isbn_valid else None


# ═══════════════════════════════════════════════════════════
#  压缩包抽象层（统一 zip / rar / uvz 接口）
# ═══════════════════════════════════════════════════════════


class _ArchiveReader(ABC):
    """压缩包读取器抽象基类。"""

    @abstractmethod
    def namelist(self) -> list[str]: ...

    @abstractmethod
    def read(self, name: str) -> bytes: ...

    @abstractmethod
    def getinfo(self, name: str): ...

    @abstractmethod
    def is_encrypted(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _ZipReader(_ArchiveReader):
    """ZIP / UVZ 读取器。"""

    def __init__(self, path: str) -> None:
        self._zf = zipfile.ZipFile(path, "r")

    def namelist(self) -> list[str]:
        return self._zf.namelist()

    def read(self, name: str) -> bytes:
        return self._zf.read(name)

    def getinfo(self, name: str):
        return self._zf.getinfo(name)

    def is_encrypted(self) -> bool:
        for info in self._zf.infolist():
            if info.flag_bits & 0x1:
                return True
        return False

    def close(self) -> None:
        self._zf.close()


class _RarReader(_ArchiveReader):
    """RAR 读取器。"""

    def __init__(self, path: str) -> None:
        import rarfile  # noqa: PLC0415

        self._rf = rarfile.RarFile(path)

    def namelist(self) -> list[str]:
        return self._rf.namelist()

    def read(self, name: str) -> bytes:
        return self._rf.read(name)

    def getinfo(self, name: str):
        return self._rf.getinfo(name)

    def is_encrypted(self) -> bool:
        return any(getattr(info, "encrypted", False) for info in self._rf.infolist())

    def close(self) -> None:
        self._rf.close()


def _open_archive(path: Path) -> _ArchiveReader:
    """根据文件扩展名打开压缩包。"""
    ext = path.suffix.lower()
    if ext in (".zip", ".uvz"):
        return _ZipReader(str(path))
    if ext == ".rar":
        return _RarReader(str(path))
    raise ValueError(f"不支持的压缩包格式: {ext}（支持 zip/rar/uvz）")


def _get_info_ignore_case(arc: _ArchiveReader, names: set[str], target: str):
    """按文件名（忽略大小写、忽略目录层级）查找文件信息。"""
    target_lower = target.lower()
    for name in names:
        if Path(name).name.lower() == target_lower:
            try:
                return arc.getinfo(name)
            except Exception:
                return None
    return None


def _read_file_ignore_case2(arc: _ArchiveReader, names: set[str], target: str) -> bytes | None:
    """按文件名（忽略大小写、忽略目录层级）读取文件。"""
    target_lower = target.lower()
    for name in names:
        if Path(name).name.lower() == target_lower:
            try:
                return arc.read(name)
            except Exception:
                return None
    return None


# ═══════════════════════════════════════════════════════════
#  ArchiveExtractor
# ═══════════════════════════════════════════════════════════


class ArchiveExtractor:
    """压缩包（zip/rar/uvz）CIP 提取器。"""

    @classmethod
    def extract(cls, archive_path: str | Path, detector: Detector | None = None) -> ExtractResult:
        """从压缩包（zip/rar/uvz）中提取 CIP / ISBN。

        流程:
          1. 检查密码保护
          2. 统计 *.pdg 文件数量（低于阈值则跳过）
          3. 解析 bookinfo.dat → 提取 ISBN
          4. 解析 meta.xml → 提取字段（兜底）
          5. 尝试 leg001.pdg → 解码为图片 → ONNX 检测
          6. 兜底：前 N 个 PDG 文件逐张尝试

        Args:
            archive_path: 压缩包文件路径。
            detector: 外部传入的 Detector 实例，为 None 时使用全局单例。

        Returns:
            ExtractResult — 包含 bookinfo、定位信息、耗时等。
        """
        t0 = time.perf_counter()
        archive_path = Path(archive_path)

        try:
            with _open_archive(archive_path) as arc:
                names = _get_names(arc)

                # ── 步骤 0: 密码检查 ──
                if arc.is_encrypted():
                    return ExtractResult(
                        bookinfo=BookInfo(),
                        meta=Meta(source=str(archive_path), source_type="archive"),
                        error="压缩包有密码保护",
                        elapsed=time.perf_counter() - t0,
                    )

                # ── 步骤 1: PDG 数量检查 ──
                pdg_count = _count_pdg(names)
                if pdg_count < settings.archive.pdg_min_count:
                    return ExtractResult(
                        bookinfo=BookInfo(),
                        meta=Meta(source=str(archive_path), source_type="archive"),
                        error=f"PDG 数量不足: {pdg_count} < {settings.archive.pdg_min_count}",
                        elapsed=time.perf_counter() - t0,
                    )

                # ── 步骤 2: bookinfo.dat 解析 ──
                bookinfo: BookInfo | None = None
                bi_info = _get_info_ignore_case(arc, names, "bookinfo.dat")
                if bi_info is not None and getattr(bi_info, "file_size", 0) <= _MAX_BOOKINFO_SIZE:
                    raw = _read_file_ignore_case2(arc, names, "bookinfo.dat")
                    if raw:
                        text, encoding = _decode_bookinfo(raw)
                        parsed = _parse_bookinfo(text)
                        bookinfo = BookInfo(**parsed)
                        if bookinfo.is_valid(settings.strict):
                            return ExtractResult(
                                bookinfo=bookinfo,
                                meta=Meta(source=str(archive_path), source_type="archive", encoding=encoding),
                                locate=Locate(page=-20, method="bookinfo", extraction="text"),
                                elapsed=time.perf_counter() - t0,
                            )

                # ── 步骤 3: meta.xml 解析（兜底：bookinfo.dat 无结果时尝试）──
                if bookinfo is None or not bookinfo.is_valid(settings.strict):
                    meta_xml_raw = _read_file_ignore_case2(arc, names, "meta.xml")
                    if meta_xml_raw:
                        meta_parsed = _parse_meta_xml(meta_xml_raw)
                        bookinfo_meta = BookInfo(**meta_parsed)
                        if bookinfo_meta.is_valid(settings.strict):
                            return ExtractResult(
                                bookinfo=bookinfo_meta,
                                meta=Meta(source=str(archive_path), source_type="archive", encoding="utf-8"),
                                locate=Locate(page=-21, method="bookinfo", extraction="text"),
                                elapsed=time.perf_counter() - t0,
                            )

                # ── 步骤 5: leg001.pdg → 图片 → ONNX ──
                leg_info = _get_info_ignore_case(arc, names, "leg001.pdg")
                if leg_info is not None and getattr(leg_info, "file_size", 0) <= _MAX_PDG_SIZE:
                    raw_pdg = _read_file_ignore_case2(arc, names, "leg001.pdg")
                    if raw_pdg:
                        bookinfo = _extract_bookinfo_from_image(raw_pdg, detector)
                        if bookinfo:
                            return ExtractResult(
                                bookinfo=bookinfo,
                                meta=Meta(source=str(archive_path), source_type="archive"),
                                locate=Locate(page=-10, method="leg001", extraction="ocr"),
                                elapsed=time.perf_counter() - t0,
                            )

                # ── 步骤 6: 兜底 — 前 N 个 PDG ──
                pdg_files = _list_pdg(names)
                fallback_count = settings.archive.pdg_fallback_count
                for idx, pdg_name in enumerate(pdg_files[:fallback_count]):
                    try:
                        info = arc.getinfo(pdg_name)
                        if getattr(info, "file_size", 0) > _MAX_PDG_SIZE:
                            continue
                        raw_pdg = arc.read(pdg_name)
                    except Exception:
                        continue
                    bookinfo = _extract_bookinfo_from_image(raw_pdg, detector)
                    if bookinfo:
                        return ExtractResult(
                            bookinfo=bookinfo,
                            meta=Meta(source=str(archive_path), source_type="archive"),
                            locate=Locate(page=idx + 1, method="pdg", extraction="ocr"),
                            elapsed=time.perf_counter() - t0,
                        )

                # 所有步骤均失败
                return ExtractResult(
                    bookinfo=BookInfo(),
                    meta=Meta(source=str(archive_path), source_type="archive"),
                    error="未提取到 ISBN",
                    elapsed=time.perf_counter() - t0,
                )

        except Exception as e:
            return ExtractResult(
                bookinfo=BookInfo(),
                meta=Meta(source=str(archive_path), source_type="archive"),
                error=f"压缩包提取异常: {e}",
                elapsed=time.perf_counter() - t0,
            )
