# CIP 规则提取模块

本地 CIP 规则提取，从 OCR 文本行中提取图书元信息。

## 主提取函数

::: cipx.utils.rules.clean_cip
    options:
      show_root_full_path: true

## ISBN 提取

::: cipx.utils.rules.extract_isbn
    options:
      show_root_full_path: true

## CIP 核字号提取

::: cipx.utils.rules.extract_cip
    options:
      show_root_full_path: true

## extract_cip_fields

全字段提取（合并书名、作者、出版社、日期、ISBN、CIP）。

::: cipx.utils.rules.extract_cip_fields
    options:
      show_root_full_path: true
