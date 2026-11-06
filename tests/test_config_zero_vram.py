"""阶段 6 F4 修复验证：vram_gb/ram_gb 放宽为 ge=0 后，config.yaml 完整过 pydantic。

判据设计（不依赖日志文本）：
- `voxcpm2.vram_gb == 6.5`（config.yaml 声明值）≠ schema 默认 6.0 —— 若仍在校验失败
  回退默认配置，该值会是 6.0，测试即红；
- `edge-tts.vram_gb == 0.0` 能通过校验（gt=0 时代必然回退）；
- 无权重引擎判据走 pydantic 路径即可判对：edge-tts True、voicebox（requires_gpu=False
  但 vram_gb=4.0、有本地权重）False —— 不误放行。
"""

from __future__ import annotations

from integrated_app.config import get_config
from integrated_app.routes.generate.utils import _is_weightless_engine


def test_config_yaml_fully_parsed_no_fallback() -> None:
    """① config.yaml 完整过 pydantic：文件值落位、零值合法（不再回退默认配置）。"""
    engines = get_config().pydantic_config.models.engines
    assert engines["voxcpm2"].vram_gb == 6.5, "若为 6.0 说明仍在用 schema 默认值（校验失败回退）"
    assert engines["edge-tts"].vram_gb == 0.0
    assert engines["edge-tts"].ram_gb == 0.0


def test_weightless_criterion_via_pydantic_path() -> None:
    """② 新语义下判据正确：edge-tts 放行，voicebox（有权重）不误放行。"""
    assert _is_weightless_engine("edge-tts") is True
    assert _is_weightless_engine("voicebox") is False
    assert _is_weightless_engine("voxcpm2") is False
