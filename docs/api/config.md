# 配置模块

全局配置通过 pydantic-settings 加载 `.env` 文件或环境变量配置。

配置加载优先级：

1. 直接传入 `Settings()` 的关键字参数
2. 系统环境变量
3. `.env` 文件
4. 类属性默认值

## Settings

全局配置类。

::: cipx.config.Settings
    options:
      show_root_full_path: true

## LLMConfig

LLM 连接配置。

::: cipx.config.LLMConfig
    options:
      show_root_full_path: true

## OCRConfig

OCR 引擎配置。

::: cipx.config.OCRConfig
    options:
      show_root_full_path: true

## configure()

全局运行时配置函数。

::: cipx.config.configure
    options:
      show_root_full_path: true
