"""
本地 CIP 规则提取模块。

不同 OCR 后端的 JSON 格式可能不同，但转换成文本行后，
CIP 数据提取逻辑完全一致。本模块提供纯 ``list[str]`` → 字段的提取函数。
"""

from __future__ import annotations

import re

from cipx.models import BookInfo


def extract_isbn(lines: list[str]) -> str | None:
    """从文本行中提取 ISBN。

    支持 ISBN-10（末尾可为 X）和 ISBN-13。
    只提取紧跟 "ISBN" 后的数字/连字符序列（支持跨行）。
    回退策略：漏掉 "ISBN" 标记时，尝试 978/979 开头的 13 位候选。

    Args:
        lines: 文本行列表。

    Returns:
        纯数字 + 末尾大写 X 的 ISBN 字符串，未找到时返回 None。
    """
    normalized_lines = [_normalize_line(line) for line in lines]
    for index, line in enumerate(normalized_lines):
        marker = re.search(r"(?:ISBN|1SBN|IS8N)", line, re.IGNORECASE)
        if not marker:
            continue

        pieces = [line[marker.end() :]]
        candidate = _extract_isbn_from_window(" ".join(pieces))
        if _is_valid_isbn(candidate):
            return candidate

        if index + 1 < len(normalized_lines):
            next_line = normalized_lines[index + 1].lstrip()
            if next_line and (next_line[0].isdigit() or next_line[0] in "-Xx"):
                pieces.append(next_line)
                candidate = _extract_isbn_from_window(" ".join(pieces))
                if _is_valid_isbn(candidate):
                    return candidate

    # 少数 OCR 会漏掉 ISBN 标记，回退到 978/979 开头的 13 位候选。
    for line in normalized_lines:
        candidate = _extract_isbn_from_window(line, require_prefix=True)
        if _is_valid_isbn(candidate):
            return candidate

    full_text = " ".join(normalized_lines)
    candidate = _extract_isbn_from_window(full_text, require_prefix=True)
    if _is_valid_isbn(candidate):
        return candidate

    return None


def extract_cip(lines: list[str]) -> str | None:
    """从文本行中提取 CIP 数据核字号。

    支持多种格式:
        - CIP数据核字(2021)第188747号
        - CIP数据核字(2021)062688号（缺少"第"字）
        - CIP数据核字第XXXXXX号
        - 核字第XXXX号

    Args:
        lines: 文本行列表。

    Returns:
        规范化的 CIP 核字号字符串，未找到时返回 None。
    """
    full_text = " ".join(_normalize_line(line) for line in lines)

    # 格式1: CIP数据核字（2021）第188747号
    m = re.search(
        r"C[IT]P\s*数据核字\s*[（(]\s*(\d{4})\s*[）)]\s*第\s*([A-Z0-9]+)\s*号",
        full_text,
        re.IGNORECASE,
    )
    if m:
        return f"CIP数据核字({m.group(1)})第{m.group(2)}号"

    # 格式1.1: CIP数据核字（2017）062688号（少了“第”）
    m = re.search(
        r"C[IT]P\s*数据核字\s*[（(]\s*(\d{4})\s*[）)]\s*([A-Z0-9]+)\s*号",
        full_text,
        re.IGNORECASE,
    )
    if m:
        return f"CIP数据核字({m.group(1)})第{m.group(2)}号"

    # 格式2: CIP数据核字第XXXXXX号
    m = re.search(
        r"C[IT]P\s*数据核字第\s*([A-Z0-9]+)\s*号",
        full_text,
        re.IGNORECASE,
    )
    if m:
        return f"CIP数据核字第{m.group(1)}号"

    # 格式3: 核字第XXXX号 出现在同一行
    for line in lines:
        if re.search(r"C[IT]P", line, re.IGNORECASE) and "核字" in line:
            m = re.search(r"核字第?\s*([A-Z0-9]+)\s*号", line, re.IGNORECASE)
            if m:
                return f"CIP数据核字第{m.group(1)}号"
                # break not needed — return exits

    return None


# ── 主提取函数 ──────────────────────────────────────────────


def clean_cip(lines: list[str]) -> dict[str, str | None]:
    """从 CIP 文本行中提取图书元信息。

    与 OCR 后端共用此核心逻辑。
    输入已是按阅读顺序排列的文本行。

    提取流程:
        1. 定位 "图书在版编目" 头部行
        2. 收集书目上下文行并分割著者/出版信息
        3. 逐字段提取 title / author / publisher / pubdate
        4. 调用 extract_isbn / extract_cip 提取 ISBN 和 CIP 核字号
        5. 非标准格式则回退到通用提取

    Args:
        lines: 规范化后的文本行列表。

    Returns:
        包含 title/author/publisher/pubdate/isbn/cip 的字典。
    """
    lines = [_normalize_line(line) for line in lines if _normalize_line(line)]
    if not lines:
        return _empty_result()

    # 定位 CIP 头部行
    cip_index = -1
    for i, line in enumerate(lines):
        if "图书在版编目" in line or "CIPPage" in line:
            cip_index = i
            break

    if cip_index == -1 or cip_index + 1 >= len(lines):
        return _extract_generic(lines)

    context_lines = _collect_bibliographic_lines(lines, cip_index)

    if not context_lines:
        return _extract_generic(lines)

    result = _empty_result()
    bibliography = _join_context_lines(context_lines)
    title_author_text, pub_text = _split_title_author_and_pub(bibliography)

    result["title"] = _extract_title(title_author_text)
    result["author"] = _extract_author(title_author_text)
    result["publisher"], result["pubdate"] = _extract_publisher_and_date(pub_text or "")

    # ── ISBN ──
    result["isbn"] = extract_isbn(lines)

    # ── CIP ──
    result["cip"] = extract_cip(lines)

    return result


# ── 通用回退 ──────────────────────────────────────────────


def _extract_generic(lines: list[str]) -> dict[str, str | None]:
    """当 CIP 标准格式不匹配时的通用回退提取。

    通过正则表达式依次尝试提取 ISBN、CIP 核字号、书名、作者、出版社、出版日期。

    Args:
        lines: 文本行列表。

    Returns:
        包含 title/author/publisher/pubdate/isbn/cip 的字典。
    """
    text = "\n".join(lines)
    clean_text = re.sub(r"\s+", " ", text).strip()
    result = _empty_result()

    # ISBN
    result["isbn"] = extract_isbn(lines)

    # CIP
    result["cip"] = extract_cip(lines)

    # 书名
    title = re.search(r"[书题]名[：:]\s*([^。；，,；]{4,40})", clean_text)
    if title:
        result["title"] = title.group(1)
    else:
        title = re.search(r"《([^》]+)》", clean_text)
        if title:
            result["title"] = title.group(1)
        else:
            first = re.split(r"[。；\n]", clean_text)[0]
            if first and not re.match(r"^[\d\s目录序前言]+$", first):
                result["title"] = first.strip()

    # 作者
    author = re.search(r"[作著][者：:]\s*([^，,。；;]{2,20})", clean_text)
    if author:
        result["author"] = author.group(1)

    # 出版社
    pub = re.search(r"出版(?:社|发行)[：:]\s*([^，,。；;]{2,30})", clean_text)
    if pub:
        result["publisher"] = pub.group(1).strip("-. —")

    # 日期
    date = re.search(r"出版(?:日期|年月)[：:]\s*([^，,。；;]{4,20})", clean_text)
    if date:
        result["pubdate"] = date.group(1)
    else:
        date = re.search(r"\b(19|20)\d{2}[年\-\.]\d{1,2}月?", clean_text)
        if date:
            result["pubdate"] = date.group(0)
        else:
            year = re.search(r"\b(19|20)\d{2}\b", clean_text)
            if year:
                result["pubdate"] = year.group(0)

    return result


# ── 工具 ──────────────────────────────────────────────────


def _normalize_line(line: str) -> str:
    """清理 OCR/Markdown 噪声，但保留足够的原始文字用于字段提取。

    Args:
        line: 原始文本行。

    Returns:
        规范化后的文本行。
    """
    line = str(line).strip()
    line = re.sub(r"^#+\s*", "", line)
    line = line.replace("／", "/")
    line = line.replace("（", "(").replace("）", ")")
    line = line.replace("．", ".")
    line = line.replace("—", "-").replace("－", "-").replace("–", "-")
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _collect_bibliographic_lines(lines: list[str], cip_index: int) -> list[str]:
    """收集 CIP 标题下方的书目描述行。

    从 CIP 标题行的下一行开始，遇到 ISBN 或 CIP 关键词时停止。

    Args:
        lines: 所有文本行。
        cip_index: "图书在版编目" 行所在索引。

    Returns:
        书目描述行列表。
    """
    context_lines: list[str] = []
    for line in lines[cip_index + 1 :]:
        if re.search(r"(?:ISBN|1SBN|IS8N)", line, re.IGNORECASE):
            before_isbn = re.split(r"(?:ISBN|1SBN|IS8N)", line, maxsplit=1, flags=re.IGNORECASE)[0]
            before_isbn = before_isbn.strip(" -.。-—一")  # noqa: B005
            if before_isbn and not _is_bibliography_stop_line(before_isbn):
                context_lines.append(before_isbn)
            break
        if _is_bibliography_stop_line(line):
            break
        context_lines.append(line)
    return context_lines


def _is_bibliography_stop_line(line: str) -> bool:
    if re.search(r"(?:ISBN|1SBN|IS8N)", line, re.IGNORECASE):
        return True
    if re.search(r"C[IT]P", line, re.IGNORECASE) or "核字" in line:
        return True
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVXLCDM]+[.、．]?\s*[①(（]", line, re.IGNORECASE):
        return True
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVXLCDM]+[.、．]\s*", line, re.IGNORECASE):
        return True
    return False


def _join_context_lines(lines: list[str]) -> str:
    """合并书目描述行，去除多余空格。

    Args:
        lines: 文本行列表。

    Returns:
        合并后的连续文本字符串。
    """
    text = "".join(lines)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*([,，:：;；.。])\s*", r"\1", text)
    return text.strip()


def _split_title_author_and_pub(text: str) -> tuple[str, str | None]:
    """将 CIP 书目串切成「书名/作者」和「出版地/出版社/日期」两段。

    常见分隔符包括 `. --`、`. -`、`. —`，
    离线 OCR 又常把长横线识别成 `一` 或把 `编. -- 北京` 识别成 `编：一北京`。

    Args:
        text: 合并后的书目文本。

    Returns:
        (书名/作者部分, 出版信息部分)。
    """
    author_pub_match = _match_author_pub_boundary(text)
    if author_pub_match:
        return author_pub_match

    separators = [
        r"[.。]\s*-{1,2}",
        r"[:：]\s*[一-]",
        r"\s-{1,2}\s*(?=[\u4e00-\u9fff]{1,8}[:：])",
        r"[.。]\s*(?=[\u4e00-\u9fff]{1,8}[:：])",
        r"[.。]\s*[一]",
    ]
    for sep in separators:
        parts = re.split(sep, text, maxsplit=1)
        if len(parts) == 2 and "/" in parts[0]:
            return parts[0].strip(), parts[1].strip(" -:：")

    return text, ""


def _match_author_pub_boundary(text: str) -> tuple[str, str] | None:
    if "/" in text:
        title, after_slash = text.split("/", 1)
        m = re.search(
            r"^(.+?(?:编著|副主编|主编|著|编|撰|译|校注|点校|整理))[,.。:：-]*\s*(.+)$",
            after_slash,
        )
        if m and re.search(r"[:：].*(?:出版社|出版公司|书局|书店|印书馆)", m.group(2)):
            return f"{title}/{m.group(1)}".strip(), m.group(2).strip(" -:：")
        return None

    m = re.search(
        r"^(.+?(?:编著|副主编|主编|著|编|撰|译|校注|点校|整理))[,.。:：-]*\s*(.+)$",
        text,
    )
    if m and re.search(r"[:：].*(?:出版社|出版公司|书局|书店|印书馆)", m.group(2)):
        return m.group(1).strip(), m.group(2).strip(" -:：")
    return None


def _extract_title(title_author_text: str) -> str | None:
    if "/" not in title_author_text:
        no_slash = _split_no_slash_title_author(title_author_text)
        if no_slash:
            return _clean_text_value(no_slash[0]) or None
        return _clean_text_value(title_author_text) or None
    title = title_author_text.split("/", 1)[0]
    return _clean_text_value(title) or None


def _extract_author(title_author_text: str) -> str | None:
    if "/" not in title_author_text:
        no_slash = _split_no_slash_title_author(title_author_text)
        if no_slash:
            return _clean_text_value(no_slash[1]) or None
        return None
    author_text = title_author_text.split("/", 1)[1]
    author_text = re.split(r"[:：.。-]\s*(?=[\u4e00-\u9fff]{1,8}[:：])", author_text, maxsplit=1)[0]
    author_text = re.sub(r"(编著|主编|副主编|著|编|撰|译|校注|点校|整理).*$", "", author_text)
    return _clean_text_value(author_text) or None


def _split_no_slash_title_author(text: str) -> tuple[str, str] | None:
    m = re.search(
        r"(.+?)([\u4e00-\u9fff·・]{2,6}(?:[，,、;；][\u4e00-\u9fff·・]{2,6}){0,8})(编著|主编|副主编|著|编|撰|译|校注|点校|整理)$",
        text,
    )
    if not m:
        return None
    return m.group(1), m.group(2)


def _extract_publisher_and_date(pub_text: str) -> tuple[str | None, str | None]:
    if not pub_text:
        return None, None

    text = re.sub(r"^\s*[\-一:：.。]+", "", pub_text)
    text = re.sub(r"ISBN.*$", "", text, flags=re.IGNORECASE)
    date = _extract_pubdate(text)

    publisher = None
    publisher_match = re.search(
        r"[:：]\s*([^,，.。;；]{2,50}?(?:出版社|出版公司|书局|书店|印书馆|集团))",
        text,
    )
    if not publisher_match:
        publisher_match = re.search(r"([^,，.。;；]{2,50}?(?:出版社|出版公司|书局|书店|印书馆|集团))", text)
    if publisher_match:
        publisher = publisher_match.group(1)
        publisher = re.sub(r"^[^:：]{0,12}[:：]", "", publisher)
        publisher = _clean_text_value(publisher)

    return publisher or None, date


def _extract_pubdate(text: str) -> str | None:
    candidates = re.findall(r"(?<![\d-])((?:19|20)\d{2}(?:[.\-年]\d{1,2}月?)?)(?![\d-])", text)
    if not candidates:
        return None
    return candidates[-1].replace("年", ".").replace("月", "").rstrip(".")


def _clean_text_value(value: str) -> str:
    value = value.strip(" \t\r\n-—一.。,:：;；")
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"\s+", " ", value)
    value = value.replace(",", "，")
    return value.strip()


def _is_valid_isbn(value: str | None) -> bool:
    if not value:
        return False
    return bool(re.match(r"^\d{13}$", value) or re.match(r"^\d{10}$", value) or re.match(r"^\d{9}X$", value))


def _extract_isbn_from_window(value: str, require_prefix: bool = False) -> str | None:
    pattern = r"97[89][0-9Xx\s-]{9,24}[0-9Xx]" if require_prefix else r"[0-9Xx][0-9Xx\s-]{8,24}[0-9Xx]"
    for match in re.finditer(pattern, value):
        token = match.group(0)
        cleaned = re.sub(r"[^0-9Xx]", "", token).upper()
        if _is_valid_isbn(cleaned):
            return cleaned
    return None


def _empty_result() -> dict[str, str | None]:
    return {
        "title": None,
        "author": None,
        "publisher": None,
        "pubdate": None,
        "isbn": None,
        "cip": None,
    }


# ═══════════════════════════════════════════════════════════
#  公开 API — BookInfo 封装
# ═══════════════════════════════════════════════════════════


def extract_cip_fields(lines: list[str]) -> BookInfo:
    """从 CIP 版权页 OCR 文本行中提取图书字段。

    优先解析标准 CIP 格式（"图书在版编目" 头部），
    不匹配时回退到通用正则提取。

    Args:
        lines: OCR 识别的文本行列表（已清洗去空）。

    Returns:
        ``BookInfo``，未提取到的字段为 ``None``。
    """
    result = clean_cip(lines)
    return BookInfo(
        title=result.get("title"),
        author=result.get("author"),
        publisher=result.get("publisher"),
        pubdate=result.get("pubdate"),
        isbn=result.get("isbn"),
        cip=result.get("cip"),
    )
