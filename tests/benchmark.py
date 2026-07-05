"""性能分析：拆分 from_image 各步骤耗时。"""

import time
from pathlib import Path

from PIL import Image

from cipx.config import settings
from cipx.detector import get_detector

IMAGES_DIR = Path(__file__).parent / "data" / "images"


def profile_one(path: Path) -> dict[str, float]:
    """返回 {步骤名: 耗时秒}。"""
    det = get_detector()
    image = Image.open(path).convert("RGB")
    times: dict[str, float] = {}

    # ── 1. ONNX 检测（拆分为预处理 + 推理 + 后处理） ──
    t = time.perf_counter()
    tensor, scale, pad_x, pad_y = det._preprocess(image)
    times["det_preprocess"] = time.perf_counter() - t

    t = time.perf_counter()
    output = det._run(tensor)
    times["det_inference"] = time.perf_counter() - t

    t = time.perf_counter()
    boxes = det._pick_boxes(output)
    detects: list = []
    for box, score, class_id in boxes:
        crop_box = det._scale_box(box, image.size, scale, pad_x, pad_y)
        if crop_box is not None:
            detects.append((crop_box, image.crop(crop_box), score, class_id))
    times["det_postprocess"] = time.perf_counter() - t

    times["det_total"] = times["det_preprocess"] + times["det_inference"] + times["det_postprocess"]

    if not detects:
        return times

    # ── 2. 逐个候选框：图像缩放 + OCR + 字段提取 ──
    for idx, (crop_box, crop_img, score, cid) in enumerate(detects):
        tag = f"box{idx}(score={score:.3f})"
        w, h = crop_img.size

        # 图像缩放
        t = time.perf_counter()
        ocr_img = crop_img
        min_dim = settings.cipx_min_input_dim
        if min_dim > 0 and min(w, h) < min_dim:
            s = max(1, (min_dim + min(w, h) - 1) // min(w, h))
            ocr_img = ocr_img.resize((w * s, h * s), Image.Resampling.LANCZOS)
            w, h = ocr_img.size
        max_dim = settings.cipx_max_input_dim
        if max_dim > 0 and (w > max_dim or h > max_dim):
            s = min(max_dim / w, max_dim / h)
            ocr_img = ocr_img.resize((round(w * s), round(h * s)), Image.Resampling.LANCZOS)
        resize_t = time.perf_counter() - t

        # OCR
        t = time.perf_counter()
        ocr_result = det._ocr.recognize(ocr_img)
        ocr_t = time.perf_counter() - t

        # 字段提取
        t = time.perf_counter()
        if ocr_result and ocr_result.lines:
            from cipx.utils.rules import extract_cip_fields

            bookinfo = extract_cip_fields(ocr_result.lines)
            valid = bookinfo.is_valid(settings.strict)
        else:
            bookinfo = None
            valid = False
        extract_t = time.perf_counter() - t

        times[f"{tag}_resize"] = resize_t
        times[f"{tag}_ocr"] = ocr_t
        times[f"{tag}_extract"] = extract_t
        times[f"{tag}_total"] = resize_t + ocr_t + extract_t

        if valid:
            break  # 匹配到就停止，与实际流程一致

    return times


def main() -> None:
    images = sorted(IMAGES_DIR.glob("*.png"))
    if not images:
        print(f"[ERR] 未找到测试图片: {IMAGES_DIR}")
        return

    # 预热（首次加载慢是因为模型加载）
    print("正在预热（加载模型 & OCR 引擎）...")
    det = get_detector()
    det._session  # 触发 ONNX 加载
    det._ocr  # 触发 OCR 初始化
    warm = Image.open(images[0]).convert("RGB")
    det.process(warm)
    print("预热完成\n")

    for img_path in images:
        print(f"{'=' * 55}")
        print(f"  {img_path.name}")
        print(f"{'=' * 55}")

        times = profile_one(img_path)

        for key, val in times.items():
            bar = "█" * max(1, int(val * 100))
            print(f"  {key:<30s} {val * 1000:8.2f}ms  {bar}")

        # 汇总
        det_total = times.get("det_total", 0)
        ocr_total = sum(v for k, v in times.items() if k.endswith("_ocr") and not k.startswith("det_"))
        extract_total = sum(v for k, v in times.items() if k.endswith("_extract"))
        total = sum(v for k, v in times.items() if not k.startswith("det_") or k == "det_total")
        print(f"  {'─' * 50}")
        print(f"  {'检测(ONNX)':<30s} {det_total * 1000:8.2f}ms")
        print(f"  {'OCR识别':<30s} {ocr_total * 1000:8.2f}ms")
        print(f"  {'字段提取':<30s} {extract_total * 1000:8.2f}ms")
        print()


if __name__ == "__main__":
    main()
