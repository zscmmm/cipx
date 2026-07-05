# cipx


> 从 PDF、图片、压缩包和 EPUB 中提取 CIP 图书信息的 Python 工具。

[![Python](https://img.shields.io/badge/python-≥3.11-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-mkdocs-blue)](https://cipx.readthedocs.org)




## 安装

项目要求 Python 3.11 或更高版本。

```bash
pip install cipx
```


## 快速开始

```python
from cipx import CIPX, extract

cipx = CIPX()

# 根据文件后缀自动分发到最合适的提取入口
result = cipx.extract("book.pdf")
print(result.success)
print(result.bookinfo.title)
print(result.bookinfo.isbn)

# 也可以直接调用顶层函数
result = extract("book.epub")
print(result.bookinfo.isbn)
```

如果你已经明确知道文件类型，也可以直接调用对应方法：

```python
from cipx import CIPX

cipx = CIPX()

image_result = cipx.from_image("cover.jpg")
pdf_result = cipx.from_pdf("book.pdf")
epub_result = cipx.from_epub("book.epub")
archive_result = cipx.from_archive("book.uvz")
```

## 返回结果

提取结果是一个 `ExtractResult` 对象，常用字段包括：

- `success`：是否提取成功
- `bookinfo`：书目信息，包含 `title`、`author`、`publisher`、`pubdate`、`isbn`、`cip`
- `meta`：源文件信息
- `locate`：检测定位信息（图片、PDF、压缩包场景）
- `ocr`：OCR 识别结果
- `elapsed`：处理耗时
- `error`：失败时的错误信息

`bookinfo` 还提供 `isbn_valid`、`isbn13`、`isbn10` 等便捷属性。

## 配置

### 全局配置

```python
from cipx.config import configure

configure(log_level="DEBUG", strict=3)
```

### 自定义 Settings

```python
from cipx import CIPX
from cipx.config import Settings

settings = Settings(
	log_level="DEBUG",
	strict=4,
	detector={"conf_threshold": 0.5},
	ocr={"ocr_model": "medium"},
)

cipx = CIPX(config=settings)
```



## 支持格式

- 图片：`.jpg`、`.jpeg`、`.png` 等
- PDF：`.pdf`
- EPUB：`.epub`
- 压缩包：`.zip`、`.rar`、`.uvz`


