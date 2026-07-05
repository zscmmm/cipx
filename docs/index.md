# cipx

从 PDF / 图片 / 压缩包 / EPUB 文件中提取 **CIP** 信息的 Python 工具。

## 功能特性

- **多格式支持**：PDF（文本型 + 扫描件）、图片（JPG/PNG）、压缩包（zip/rar/uvz）、EPUB
- **ONNX 加速**：基于 YOLO 的深度学习检测器，精准定位 CIP 区域
- **OCR 识别**：集成 RapidOCR，支持中文文本识别
- **智能提取**：从 OCR 文本中提取书名、作者、出版社、出版日期、ISBN、CIP 核字号
- **灵活配置**：通过 `.env` 文件或环境变量自定义行为

## 测试报告

[📊 测试报告 - isbnx](https://isbnx.readthedocs.io/zh-cn/latest/isbnx_vs_cipx_report/#1)

## 快速开始

```bash
pip install cipx
```

```python
from cipx import CIPX

cipx = CIPX()

# 根据文件后缀自动识别文件类型（推荐）
result = cipx.extract("book.pdf")
print(result.bookinfo.title)

# 或指定具体方法
result = cipx.from_image("book_cover.jpg")
print(result.bookinfo.isbn)
result = cipx.from_epub("book.epub")
print(result.bookinfo.isbn)
result = cipx.from_archive("book.uvz")
print(result.bookinfo.isbn)
```

## 配置参数



```python
from cipx import CIPX
from cipx.config import configure

# 全局配置
configure(log_level="DEBUG", strict=3)

# 或传入自定义 Settings
from cipx.config import Settings

custom_settings = Settings(
    log_level="DEBUG",
    ocr={"ocr_model": "medium"},
    detector_conf_threshold=0.5,
)
cipx = CIPX(config=custom_settings)
```


## 提取流程

了解各文件类型的详细处理管线：

- [全流程概览](flow/cipx_flow.md) — 整体提取管线一览
- [图片提取](flow/image_flow.md) — ONNX 检测 + OCR + 字段提取
- [PDF 提取](flow/pdf_flow.md) — 文本型与扫描件 PDF
- [压缩包提取](flow/archive_flow.md) — ZIP / RAR / UVZ
- [EPUB 提取](flow/epub_flow.md) — 纯文本扫描

## API 参考

- [cipx — 主入口](api/cipx.md)
- [archive — 压缩包提取](api/archive.md)
- [config — 配置模块](api/config.md)
- [detector — ONNX 检测器](api/detector.md)
- [epub — EPUB 提取](api/epub.md)
- [models — 数据模型](api/models.md)
- [pdf — PDF 提取](api/pdf.md)
- [ocr — OCR 引擎](api/ocr.md)
- [utils/io — IO 工具](api/utils/io.md)
- [utils/rules — CIP 规则提取](api/utils/rules.md)
