# EPUB 提取模块

从 EPUB 文件中提取 CIP / ISBN。

流程：

1. 解析 OPF 元数据提取字段
2. 扫描 XHTML 版权页正则匹配（兜底）
3. 图片版 EPUB 渲染图片 → ONNX 检测（扫描件）

::: cipx.epub.EpubExtractor
    options:
      members:
        - extract
