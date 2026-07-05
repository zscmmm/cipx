# ONNX YOLO 检测器

定位并裁剪 CIP 区域，然后进行 OCR 识别和字段提取。

## get_detector()

获取全局共享的 Detector 单例。

::: cipx.detector.get_detector
    options:
      show_root_full_path: true

## Detector

ONNX YOLO 检测器，定位并裁剪 CIP 区域。

::: cipx.detector.Detector
    options:
      members:
        - detect
        - process
