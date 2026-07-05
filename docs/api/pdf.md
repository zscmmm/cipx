# PDF 提取模块

从 PDF 文件中提取 CIP / ISBN。

流程：

1. pdf-inspector 判断 PDF 类型（text_based / scanned）
2. 书签检测 → 候选页生成（版权页优先级最高）
3. text_based：提取文本 → extract_cip_fields 提取全字段 → 命中则返回
4. scanned / text 失败：渲染页面为图片 → Detector.process() 检测

## PdfExtractor

PDF CIP 提取器。

::: cipx.pdf.PdfExtractor
    options:
      members:
        - extract
