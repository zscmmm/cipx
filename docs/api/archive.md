# 压缩包提取

从 zip / rar / uvz 压缩包中提取 CIP 信息。

支持以下流程：

1. 检查密码保护
2. 统计 `*.pdg` 文件数量（低于阈值则跳过）
3. 解析 `bookinfo.dat` → 提取 ISBN
4. 尝试 `leg001.pdg` → 解码为图片 → ONNX 检测
5. 兜底：前 N 个 PDG 文件逐张尝试

::: cipx.archive.ArchiveExtractor
    options:
      members:
        - extract
