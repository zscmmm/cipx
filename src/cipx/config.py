"""全局配置模块"""

from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings

# ── 类型别名 ──
SourceType = Literal["pdf", "image", "archive", "epub"]

# ── 全局常量 ──
CORE_FIELDS = ("title", "author", "publisher", "pubdate", "isbn", "cip")


class LLMConfig(BaseModel):
    """LLM 连接配置。

    Attributes:
        token: API 密钥。
        model: 模型名称。
        base_url: API 地址。
    """

    token: str = ""
    model: str = ""
    base_url: str = ""


class OCRConfig(BaseModel):
    """OCR 引擎配置。

    Attributes:
        ocr_model: OCR 模型精度，``"small"``（快速，默认）或 ``"medium"``（高精度）。
        use_cls: 是否启用方向分类器。ISBN 文字始终水平，无需分类，关闭可省 ~100-300ms。
        use_det: 是否启用文本检测（Det）阶段。
        use_line_split: 是否用水平投影分割文字行（替代 Det 神经网络）。
        det_limit_side_len: 检测模型输入图像短边缩放长度。
        max_input_dim: OCR 输入图片的最大边长（像素）。超过此值会等比例缩小。
        min_input_dim: OCR 输入图片的最小边长（像素）。低于此值会等比例放大。
    """

    ocr_model: Literal["small", "medium"] = "small"
    use_cls: bool = False
    use_det: bool = True
    use_line_split: bool = False
    det_limit_side_len: int = 320
    max_input_dim: int = 960
    min_input_dim: int = 300

    @model_validator(mode="after")
    def _validate_conflicts(self) -> "OCRConfig":
        if self.use_det and self.use_line_split:
            raise ValueError("use_det 和 use_line_split 不能同时为 True，行分割模式需要设置 use_det=False")
        return self


class DetectorConfig(BaseModel):
    """检测器（ONNX YOLO）配置。

    Attributes:
        model_path: ONNX 模型路径。可以是相对路径、绝对路径或包内路径。
        conf_threshold: 检测置信度阈值 (0~1)。
        padding: 检测框填充像素数。
        input_width: 模型输入宽度。
        input_height: 模型输入高度。
        letterbox_color: Letterbox 填充颜色 (R,G,B)。
        num_threads: ONNX 推理线程数。
    """

    model_path: str = "model/best.onnx"
    conf_threshold: float = 0.3
    padding: int = 0
    input_width: int = 640
    input_height: int = 640
    letterbox_color: str = "114,114,114"
    num_threads: int = 4

    @field_validator("model_path")
    @classmethod
    def _validate_model_path(cls, v: str) -> str:
        if not v.endswith(".onnx"):
            raise ValueError(f"ONNX 模型路径必须以 .onnx 结尾: {v}")
        p = Path(v)
        if p.is_file():
            return v
        p_pkg = files(__package__) / v
        if p_pkg.is_file():
            return v
        logger.warning(f"ONNX 模型文件不存在: {v}（将在首次检测时检查）")
        return v


class PDFConfig(BaseModel):
    """PDF 页码定位配置。

    控制 CIP 检测在 PDF 前页／后页的搜索范围（偏移量，1-indexed）。
    """

    front_start: int = 2
    front_end: int = 10
    back_start: int = 5
    back_end: int = 1


class ArchiveConfig(BaseModel):
    """压缩包（PDG）提取配置。

    Attributes:
        pdg_min_count: PDG 数量阈值，超过此值才触发提取。
        pdg_fallback_count: 兜底时尝试的前 N 个 PDG 文件数。
        pdgview_path: PdgView.dll 路径（用于解码 PDG 文件）。
    """

    pdg_min_count: int = 30
    pdg_fallback_count: int = 5
    pdgview_path: str = "pdgview/PdgView.dll"


class Settings(BaseSettings):
    """全局配置类，通过环境变量或直接传入参数加载。"""

    # ── 子配置 ──
    llm: LLMConfig = LLMConfig()
    ocr: OCRConfig = OCRConfig()
    detector: DetectorConfig = DetectorConfig()
    pdf: PDFConfig = PDFConfig()
    archive: ArchiveConfig = ArchiveConfig()

    # ── 校验等级 ──
    strict: int = 4
    """``BookInfo`` 有效性校验等级（1-8），数字越小越严格，默认 4。

    6 个核心字段: title, author, publisher, pubdate, isbn, cip。

    - 1 (最严格): 全部 6 个核心字段 + ssid 都不能缺失
    - 2: 全部 6 个核心字段都不能缺失
    - 3: ≥3 个核心字段，且必须含 ISBN
    - 4 (默认): ≥3 个核心字段，且 ISBN 或 ssid 至少有一个
    - 5: ≥3 个核心字段
    - 6: ISBN 有效即有效
    - 7: ≥1 个核心字段
    - 8 (最宽松): ≥1 个字段（含 ssid）
    """

    keep_partial: bool = True
    """校验不通过时，是否保留已提取的部分字段（如书名、作者）。设为 False 则返回空 BookInfo。"""

    # ── 日志 ──
    log_level: str = "INFO"


settings = Settings()


def configure(**kwargs: Any) -> None:
    """全局运行时配置，覆盖默认设置。

    只修改内存中的 settings 对象，不会写入配置文件。
    支持嵌套配置（自动识别 BaseModel 子字段并逐项更新）。

    Args:
        **kwargs: 配置项键值对，键名需与 Settings 类字段名一致。

    Examples:
        configure(log_level="DEBUG")
        configure(detector_conf_threshold=0.5)
        configure(llm={"token": "sk-xxx", "model": "gpt-4"})
    """
    for key, value in kwargs.items():
        if not hasattr(settings, key):
            logger.warning(f"未知配置项: {key}")
            continue

        target = getattr(settings, key)
        if isinstance(target, BaseModel) and isinstance(value, dict):
            # 先构建一份完整配置进行校验，校验通过才实际写入
            merged = target.model_dump()
            merged.update(value)
            validated = target.__class__.model_validate(merged)
            for nk, nv in validated.model_dump().items():
                setattr(target, nk, nv)
        elif isinstance(target, BaseModel) and isinstance(value, BaseModel):
            # 直接传入同类型对象
            try:
                setattr(settings, key, value)
            except (TypeError, ValueError) as e:
                logger.warning(f"配置项 {key} 赋值失败: {e}")
        else:
            # 普通字段
            expected_type = type(target)
            if not isinstance(value, expected_type):
                logger.warning(f"配置项 {key} 类型不匹配: 期望 {expected_type.__name__}, 实际 {type(value).__name__}")
                continue
            try:
                setattr(settings, key, value)
            except (TypeError, ValueError) as e:
                logger.warning(f"配置项 {key} 赋值失败: {e}")
