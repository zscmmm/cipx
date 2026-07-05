"""ONNX YOLO 检测器：定位并裁剪 CIP 区域。"""

from __future__ import annotations

import time
from functools import cache, cached_property
from importlib.resources import files
from pathlib import Path
from typing import Literal

import numpy as np
import onnxruntime as ort
from PIL import Image

from cipx.config import settings
from cipx.models import BookInfo, Detect, ExtractResult, Locate, Meta, OCRResult
from cipx.ocr.cipx_rapiocr import CIPXRapidOCR
from cipx.utils.rules import extract_cip_fields

# ── 检测器 ──


@cache
def get_detector() -> Detector:
    """获取全局共享的 Detector 单例。"""
    return Detector()


# ── 模型文件校验 ──


class Detector:
    """ONNX YOLO 检测器，定位并裁剪 CIP 区域。

    所有配置从 ``settings`` 读取。
    """

    def __init__(self) -> None:
        # 预热：主动触发惰性加载，避免首次推理时等待
        _ = self._session  # 触发 @cached_property，加载 ONNX 模型
        _ = self._ocr  # 触发 @cached_property，加载 OCR 引擎

    # @staticmethod
    # def _whiten_background(image: Image.Image) -> Image.Image:
    #     """将任意底色（泛黄、泛灰、黑底等）矫正为白底黑字，提升 ONNX 检测准确率。

    #     策略：
    #     - 从四角采样估算背景色
    #     - 浅色背景 → 线性拉伸使背景变白
    #     - 深色背景 → 反色后再线性拉伸使背景变白
    #     """
    #     arr = np.array(image, dtype=np.float32)
    #     h, w = arr.shape[:2]

    #     # 从四角采样背景色
    #     margin = 10
    #     corners = np.concatenate(
    #         [
    #             arr[:margin, :margin],
    #             arr[:margin, -margin:],
    #             arr[-margin:, :margin],
    #             arr[-margin:, -margin:],
    #         ]
    #     )
    #     bg = corners.mean(axis=(0, 1))  # (R, G, B)

    #     # 背景已接近白色，跳过
    #     if np.all(bg > 245):
    #         return image

    #     # 计算背景亮度 (ITU-R BT.601 亮度公式)
    #     luminance = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]

    #     if luminance > 128:
    #         # ── 浅色背景（米黄/浅灰等）：拉伸背景 → 白色 ──
    #         scale = 255.0 / np.maximum(bg, 1.0)
    #         corrected = np.clip(arr * scale, 0, 255).astype(np.uint8)
    #     else:
    #         # ── 深色背景（黑/深灰等）：反色后再拉伸 → 白底黑字 ──
    #         inverted = 255.0 - arr
    #         inv_bg = 255.0 - bg
    #         scale = 255.0 / np.maximum(inv_bg, 1.0)
    #         corrected = np.clip(inverted * scale, 0, 255).astype(np.uint8)

    #     return Image.fromarray(corrected)

    @staticmethod
    def _whiten_background(image: Image.Image) -> Image.Image:
        gray = image.convert("L")
        return gray.convert("RGB")

    # ── 公开方法 ──

    def detect(self, image: Image.Image) -> list[Detect]:
        """检测图片中的目标区域，返回所有满足置信度阈值的检测框。"""
        image = self._whiten_background(image)
        tensor, scale, pad_x, pad_y = self._preprocess(image)
        output = self._run(tensor)
        boxes = self._pick_boxes(output)
        detects: list[Detect] = []
        for box, score, class_id in boxes:
            crop_box = self._scale_box(box, image.size, scale, pad_x, pad_y)
            if crop_box is not None:
                detects.append(Detect(box=crop_box, image=image.crop(crop_box), score=score, class_id=class_id))
        return detects

    def process(
        self,
        image: Image.Image,
        source: str = "",
        source_type: Literal["pdf", "image", "archive", "epub"] = "image",
    ) -> ExtractResult:
        """一步完成 ONNX 检测 + OCR + 字段提取。

        流程：
        1. ONNX YOLO 检测所有 CIP 区域
        2. 按置信度降序遍历各候选框，OCR 识别
        3. 用 ``extract_cip_fields()`` 提取全字段（书名/作者/出版社/日期/ISBN/CIP）
        4. 按 ``settings.strict`` 等级校验，通过即返回
        5. 全部候选框均不满足则返回失败结果
        """
        t0 = time.perf_counter()
        detects = self.detect(image)

        if not detects:
            return ExtractResult(
                bookinfo=BookInfo(),
                meta=Meta(source=source, source_type=source_type),
                error="未检测到 CIP 区域",
                elapsed=time.perf_counter() - t0,
            )

        # 只处理 CIP 区域（class_id == 1）
        cip_detects = [d for d in detects if d.class_id == 1]
        if not cip_detects:
            return ExtractResult(
                bookinfo=BookInfo(),
                meta=Meta(source=source, source_type=source_type),
                locate=Locate(page=1, method="onnx", candidates=detects),
                error="未检测到 CIP 区域（class_id == 1）",
                elapsed=time.perf_counter() - t0,
            )

        last_detect: Detect | None = cip_detects[-1]
        last_ocr: OCRResult | None = None
        last_bookinfo: BookInfo | None = None

        for det in cip_detects:
            ocr_img = det.image
            w, h = ocr_img.size

            # ── 图像缩放 ──
            min_dim = settings.ocr.min_input_dim
            if min_dim > 0 and min(w, h) < min_dim:
                s = max(1, (min_dim + min(w, h) - 1) // min(w, h))
                ocr_img = ocr_img.resize((w * s, h * s), Image.Resampling.LANCZOS)
                w, h = ocr_img.size

            max_dim = settings.ocr.max_input_dim
            if max_dim > 0 and (w > max_dim or h > max_dim):
                s = min(max_dim / w, max_dim / h)
                ocr_img = ocr_img.resize((round(w * s), round(h * s)), Image.Resampling.LANCZOS)

            # ── OCR ──
            ocr_result = self._ocr.recognize(ocr_img)
            last_ocr = ocr_result

            if ocr_result is None or not ocr_result.lines:
                continue

            # ── 全字段提取（书名、作者、出版社、出版日期、ISBN、CIP）──
            bookinfo = extract_cip_fields(ocr_result.lines)
            last_bookinfo = bookinfo  # 即使校验不通过也保留提取结果
            if bookinfo.is_valid(settings.strict):
                locate = Locate(page=1, method="onnx", detect=det, candidates=detects)
                return ExtractResult(
                    bookinfo=bookinfo,
                    meta=Meta(source=source, source_type=source_type),
                    locate=locate,
                    ocr=ocr_result,
                    elapsed=time.perf_counter() - t0,
                )

        # ── 全部失败 ──
        locate = Locate(page=1, method="onnx", detect=last_detect, candidates=detects)
        return ExtractResult(
            bookinfo=(last_bookinfo or BookInfo()) if settings.keep_partial else BookInfo(),
            meta=Meta(source=source, source_type=source_type),
            locate=locate,
            ocr=last_ocr,
            error="未能从 CIP 区域提取到有效 ISBN",
            elapsed=time.perf_counter() - t0,
        )

    # ── OCR 引擎（惰性初始化） ──

    @cached_property
    def _ocr(self) -> CIPXRapidOCR:
        return CIPXRapidOCR()

    # ── ONNX 模型 ──

    @cached_property
    def _session(self) -> ort.InferenceSession:
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = settings.detector.num_threads
        return ort.InferenceSession(
            str(self._model_path),
            sess_options,
            providers=["CPUExecutionProvider"],
        )

    @cached_property
    def _model_path(self) -> Path:
        # 包内路径（优先）
        p = files(__package__) / settings.detector.model_path
        if p.is_file():
            return Path(str(p))
        # 回退：直接作为文件系统路径
        p = Path(settings.detector.model_path)
        if p.is_file():
            return p.resolve()
        return p

    # ── 预处理 ──

    def _preprocess(self, image: Image.Image) -> tuple[np.ndarray, float, int, int]:
        target_w, target_h = settings.detector.input_width, settings.detector.input_height
        width, height = image.size
        scale = min(target_w / width, target_h / height)
        resized_w = round(width * scale)
        resized_h = round(height * scale)
        resized = image.resize((resized_w, resized_h), Image.Resampling.BILINEAR)

        color = tuple(int(x) for x in settings.detector.letterbox_color.split(","))
        canvas = Image.new("RGB", (target_w, target_h), color)
        pad_x = (target_w - resized_w) // 2
        pad_y = (target_h - resized_h) // 2
        canvas.paste(resized, (pad_x, pad_y))

        array = np.asarray(canvas, dtype=np.float32) / 255.0
        tensor = array.transpose(2, 0, 1)[None]
        return np.ascontiguousarray(tensor), scale, pad_x, pad_y

    # ── 推理 ──

    def _run(self, tensor: np.ndarray) -> np.ndarray:
        input_name = self._session.get_inputs()[0].name
        outputs = self._session.run(None, {input_name: tensor})
        if not outputs:
            raise RuntimeError("ONNX 模型未返回任何输出")
        return np.asarray(outputs[0], dtype=np.float32)

    # ── 后处理：YOLO 输出解析 ──

    def _pick_boxes(self, output: np.ndarray) -> list[tuple[np.ndarray, float, int]]:
        """从 YOLO ONNX 输出中取所有满足置信度阈值的检测框。

        Returns:
            [(box, score, class_id), ...] 按置信度降序排列。
            box: [x1, y1, x2, y2] 在 letterbox 画布上的像素坐标。
        """
        detections = np.asarray(output, dtype=np.float32)
        if detections.ndim == 3:
            detections = detections[0]
        if detections.ndim != 2:
            raise ValueError(f"不支持的 ONNX 输出形状: {output.shape}")

        threshold = settings.detector.conf_threshold

        if detections.shape[1] == 6:
            # YOLO NMS 输出: [x1, y1, x2, y2, score, class_id]
            scores = detections[:, 4]
            mask = scores >= threshold
            if not mask.any():
                return []

            results: list[tuple[np.ndarray, float, int]] = []
            for i in np.where(mask)[0]:
                results.append(
                    (
                        detections[i, :4].astype(np.float32),
                        float(scores[i]),
                        int(detections[i, 5]),
                    )
                )
            results.sort(key=lambda x: x[1], reverse=True)
            return results

        raise ValueError(f"不支持的 YOLO ONNX 输出形状: {output.shape}")

    # ── 坐标映射 ──

    def _scale_box(
        self,
        box: np.ndarray,
        image_size: tuple[int, int],
        scale: float,
        pad_x: int,
        pad_y: int,
    ) -> tuple[int, int, int, int] | None:
        width, height = image_size
        x1, y1, x2, y2 = box.astype(float)
        padding = settings.detector.padding
        x1 = (x1 - pad_x) / scale - padding
        y1 = (y1 - pad_y) / scale - padding
        x2 = (x2 - pad_x) / scale + padding
        y2 = (y2 - pad_y) / scale + padding

        left = max(0, min(width, int(np.floor(x1))))
        right = max(0, min(width, int(np.ceil(x2))))
        top = max(0, min(height, int(np.floor(y1))))
        bottom = max(0, min(height, int(np.ceil(y2))))

        if right - left < 1 or bottom - top < 1:
            return None
        return left, top, right, bottom
