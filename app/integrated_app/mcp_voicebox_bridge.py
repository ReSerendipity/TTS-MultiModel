"""Voicebox（语音转换）MCP 桥接模块。

作用：
    把「语音转换（voice conversion / voicebox）」能力以 MCP Tool 的形式暴露给
    AI 助手（Claude Desktop / Cursor 等），与现有的 ``mcp_server.MCPServer`` 对齐。

集成方式（与 mcp_server.py 现有工具同构）：
    - 本模块不重新实现 JSON-RPC 传输层，而是复用 ``MCPServer`` 的
      ``register_tool(MCPTool(...))`` 接口，由 ``register_voicebox_tools(server)``
      把 voicebox 工具挂到已有的 MCP 服务器实例上；
    - ``mcp_server._register_default_tools()`` 会调用 ``register_voicebox_tools(self)``，
      因此 voicebox 工具与 text_to_speech / list_engines 等一并出现在 tools/list 中；
    - 同时本模块自带 ``run_voicebox_mcp_server()`` 入口，可作为独立「仅 voicebox」的
      MCP 服务器启动（``python -m app.integrated_app.mcp_voicebox_bridge``）。

SCAFFOLD 说明：
    真实语音转换后端（engines/voicebox_engine.py）尚未接入，工具处理函数会在调用
    引擎后返回明确的「未实现」结果，不编造任何上游 API 行为。

设计约束（遵循 AGENTS.md）：
    - 延迟导入 TTS 引擎，避免 MCP 服务器启动即加载大模型；
    - 工具处理函数均为 async，异常由 MCPServer 的 tools/call 调度统一捕获；
    - 仅使用本仓已存在的数据类与日志器，不引入新依赖。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("tts_multimodel")

#: voicebox 引擎注册名（与未来接入 engine_registry 时保持一致）。
VOICEBOX_ENGINE_ID = "voicebox"


def register_voicebox_tools(server: Any) -> None:
    """将 voicebox 相关 MCP 工具注册到给定的 ``MCPServer`` 实例。

    与 ``mcp_server.MCPServer._register_default_tools`` 中注册 text_to_speech 等
    工具的方式完全一致：构造 ``MCPTool`` 并调用 ``server.register_tool(tool)``。

    Args:
        server: ``app.integrated_app.mcp_server.MCPServer`` 实例。
    """
    server.register_tool(
        _build_voice_conversion_tool(),
    )
    server.register_tool(
        _build_list_voicebox_models_tool(),
    )


def _build_voice_conversion_tool() -> Any:
    """构造 ``voice_conversion`` MCP 工具定义。"""
    from .mcp_server import MCPTool

    return MCPTool(
        name="voice_conversion",
        description=(
            "语音转换（voice conversion / voicebox）：将源说话人音频的音色转换为"
            "目标音色参考音频的音色，输出重说源内容的音频。SCAFFOLD：真实后端未接入。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_audio": {
                    "type": "string",
                    "description": "源说话人音频文件路径（要被转换音色的语音）",
                },
                "target_audio": {
                    "type": "string",
                    "description": "目标音色参考音频文件路径（提供目标音色）",
                },
                "output_path": {
                    "type": "string",
                    "description": "输出音频文件路径（WAV 格式，可选）",
                },
                "diffusion_steps": {
                    "type": "integer",
                    "description": "扩散推断步数（可选，待上游确认）",
                },
                "f0_method": {
                    "type": "string",
                    "description": "基频提取方法，如 rmvpe/crepe（可选，待上游确认）",
                },
                "denoise": {
                    "type": "number",
                    "description": "降噪强度 0.0-1.0（可选，待上游确认）",
                },
            },
            "required": ["source_audio", "target_audio"],
        },
        handler=_handle_voice_conversion,
    )


def _build_list_voicebox_models_tool() -> Any:
    """构造 ``list_voicebox_models`` MCP 工具定义（SCAFFOLD：返回占位列表）。"""
    from .mcp_server import MCPTool

    return MCPTool(
        name="list_voicebox_models",
        description="列出可用的语音转换模型/音色（SCAFFOLD：当前返回占位信息）。",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词（可选）",
                    "default": "",
                }
            },
        },
        handler=_handle_list_voicebox_models,
    )


# ---------------------------------------------------------------------------
# 工具处理函数（延迟导入引擎，避免启动时加载大模型）
# ---------------------------------------------------------------------------


async def _handle_voice_conversion(
    source_audio: str,
    target_audio: str,
    output_path: str | None = None,
    diffusion_steps: int | None = None,
    f0_method: str | None = None,
    denoise: float | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """``voice_conversion`` 工具处理函数。

    复用 engines.voicebox_engine.VoiceboxEngine；当前后端为 SCAFFOLD，
    会返回明确的「未实现」结果。
    """
    try:
        from .engines.voicebox_engine import VoiceboxEngine

        engine = VoiceboxEngine()
        result = engine.voice_conversion(
            source_audio=source_audio,
            target_audio=target_audio,
            output_path=output_path,
            diffusion_steps=diffusion_steps,
            f0_method=f0_method,
            denoise=denoise,
            **kwargs,
        )
        return result.to_dict()

    except Exception as e:  # noqa: BLE001
        logger.error(f"[MCP-Voicebox] 语音转换失败: {e}", exc_info=True)
        return {
            "success": False,
            "message": str(e),
            "engine": VOICEBOX_ENGINE_ID,
            "source_audio": source_audio,
            "target_audio": target_audio,
        }


async def _handle_list_voicebox_models(keyword: str = "", **kwargs: Any) -> dict[str, Any]:
    """``list_voicebox_models`` 工具处理函数（SCAFFOLD）。"""
    try:
        # TODO(voicebox): 接入真实后端后，从模型目录或引擎元数据读取可用模型列表。
        return {
            "models": [],
            "count": 0,
            "engine": VOICEBOX_ENGINE_ID,
            "note": (
                "SCAFFOLD: 尚未接入真实语音转换后端，暂无可用模型。"
                "请在 engines/voicebox_engine.py 实现后补充读取逻辑。"
            ),
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"[MCP-Voicebox] 列出模型失败: {e}", exc_info=True)
        return {"models": [], "count": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# 独立启动入口（与 mcp_server.run_mcp_server 同构）
# ---------------------------------------------------------------------------


def run_voicebox_mcp_server(transport: str = "stdio") -> None:
    """启动「仅 voicebox」的 MCP 服务器（stdio 传输）。

    作为独立命令暴露语音转换能力；同时主 MCP 服务器也会通过
    ``register_voicebox_tools`` 挂载相同工具。

    Args:
        transport: 传输方式，目前仅支持 "stdio"。
    """
    from .mcp_server import MCPServer

    server = MCPServer()
    register_voicebox_tools(server)

    if transport == "stdio":
        import asyncio

        asyncio.run(server.run_stdio())
    else:
        raise ValueError(f"不支持的传输方式: {transport}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_voicebox_mcp_server()
