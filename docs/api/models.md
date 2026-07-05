# 数据模型

CIP 提取全流程中使用的所有数据结构，包括配置、字段、检测结果和最终输出。

## Detect

ONNX 模型检测结果（单张图片的单个目标）。

::: cipx.models.Detect
    options:
      members:
        - box
        - image
        - score
        - class_id
        - class_name

## Locate

ISBN 在文件中的位置信息。

::: cipx.models.Locate
    options:
      members:
        - page
        - method
        - extraction
        - detect
        - candidates
        - image
        - box
        - score

## Meta

提取的文件级元信息。

::: cipx.models.Meta
    options:
      members:
        - source
        - source_type
        - pdf_type
        - encoding

## OCRResult

标准化 OCR 识别结果。

::: cipx.models.OCRResult
    options:
      members:
        - lines
        - rawocr
        - text

## BookInfo

提取的图书字段信息。

::: cipx.models.BookInfo
    options:
      members:
        - title
        - author
        - publisher
        - pubdate
        - isbn
        - cip
        - ssid
        - isbn_valid
        - isbn13
        - isbn10
        - core_filled
        - is_valid

## ExtractResult

一个文件的完整提取结果。

::: cipx.models.ExtractResult
    options:
      members:
        - bookinfo
        - meta
        - locate
        - ocr
        - elapsed
        - error
        - success
