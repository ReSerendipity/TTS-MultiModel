"""MCP (Model Context Protocol) 服务器模块。

提供符合 MCP 规范的服务器实现，允许 AI 助手（如 Claude Desktop、Cursor 等）
通过标准化协议调用 TTS_MultiModel 的功能：

提供的 MCP 工具 (Tools):
- text_to_speech: 文本转语音，支持语音设计和克隆
- list_engines: 列出可用 TTS 引擎
- list_personas: 列出可用音色
- get_model_status: 获取模型加载状态
- load_model: 加载指定引擎模型
- generate_voice: 统一语音生成接口

支持的传输方式：
- stdio: 标准输入输出（默认，用于 Claude Desktop 等桌面客户端）
- HTTP/SSE: 网络传输（预留）

设计要点：
- 遵循 MCP 规范（JSON-RPC 2.0 消息格式）
- 延迟导入 TTS 引擎，避免服务器启动时加载大模型
- 异步实现，支持并发请求
- 提供工具描述、参数 schema 供 LLM 理解
- 与现有 service_layer 集成，复用业务逻辑
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# MCP 协议常量
# ---------------------------------------------------------------------------

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_NAME = "tts-multimodel"
MCP_SERVER_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class MCPTool:
    """MCP 工具定义。

    Attributes:
        name: 工具名称（唯一标识）。
        description: 工具描述（供 LLM 理解用途）。
        input_schema: JSON Schema 定义输入参数。
        handler: 异步处理函数。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Any


@dataclass
class MCPRequest:
    """MCP JSON-RPC 请求。

    Attributes:
        id: 请求 ID（用于响应匹配）。
        method: 方法名。
        params: 参数字典。
    """

    id: int | str | None
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResponse:
    """MCP JSON-RPC 响应。

    Attributes:
        id: 请求 ID（与请求对应）。
        result: 成功结果。
        error: 错误信息。
    """

    id: int | str | None
    result: Any | None = None
    error: dict[str, Any] | None = None

    def to_json(self) -> str:
        """序列化为 JSON 字符串。

        Returns:
            JSON 字符串。
        """
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            response["error"] = self.error
        else:
            response["result"] = self.result
        return json.dumps(response, ensure_ascii=False)


# ---------------------------------------------------------------------------
# MCP 服务器类
# ---------------------------------------------------------------------------


class MCPServer:
    """MCP 服务器实现。

    处理 JSON-RPC 2.0 消息，提供 tools/list 和 tools/call 等标准方法，
    将 TTS 功能暴露给 AI 助手。

    Usage::

        server = MCPServer()
        await server.run_stdio()
    """

    def __init__(self) -> None:
        """初始化 MCP 服务器，注册所有工具。"""
        self._tools: dict[str, MCPTool] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册默认的 TTS 相关工具。"""
        self.register_tool(
            MCPTool(
                name="text_to_speech",
                description="将文本转换为语音。支持语音设计（描述声音风格）或语音克隆（使用参考音频）。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要转换的文本内容"},
                        "instruction": {
                            "type": "string",
                            "description": "声音描述指令，如'温柔的女性声音'、'低沉的男声'",
                            "default": "",
                        },
                        "reference_audio": {
                            "type": "string",
                            "description": "参考音频文件路径（用于语音克隆）",
                        },
                        "engine": {
                            "type": "string",
                            "description": "使用的引擎：voxcpm2 或 indextts2",
                            "default": "voxcpm2",
                        },
                        "cfg_value": {
                            "type": "number",
                            "description": "CFG 引导强度（1.0-5.0），越高越贴合指令",
                            "default": 2.0,
                        },
                        "output_path": {
                            "type": "string",
                            "description": "输出音频文件路径（WAV 格式）",
                        },
                    },
                    "required": ["text"],
                },
                handler=self._handle_text_to_speech,
            )
        )

        self.register_tool(
            MCPTool(
                name="list_engines",
                description="列出所有可用的 TTS 引擎及其状态。",
                input_schema={"type": "object", "properties": {}},
                handler=self._handle_list_engines,
            )
        )

        self.register_tool(
            MCPTool(
                name="list_personas",
                description="列出所有可用的音色角色。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键词",
                            "default": "",
                        }
                    },
                },
                handler=self._handle_list_personas,
            )
        )

        self.register_tool(
            MCPTool(
                name="get_model_status",
                description="获取当前模型加载状态和显存使用情况。",
                input_schema={"type": "object", "properties": {}},
                handler=self._handle_get_model_status,
            )
        )

        self.register_tool(
            MCPTool(
                name="load_model",
                description="加载指定引擎的 TTS 模型。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "engine": {
                            "type": "string",
                            "description": "引擎名称：voxcpm2 或 indextts2",
                        }
                    },
                    "required": ["engine"],
                },
                handler=self._handle_load_model,
            )
        )

    def register_tool(self, tool: MCPTool) -> None:
        """注册一个 MCP 工具。

        Args:
            tool: MCPTool 实例。
        """
        self._tools[tool.name] = tool

    async def _handle_request(self, request: MCPRequest) -> MCPResponse:
        """处理单个 MCP 请求。

        Args:
            request: MCP 请求。

        Returns:
            MCP 响应。
        """
        try:
            if request.method == "initialize":
                return self._handle_initialize(request)
            elif request.method == "tools/list":
                return self._handle_tools_list(request)
            elif request.method == "tools/call":
                return await self._handle_tools_call(request)
            elif request.method == "ping":
                return MCPResponse(id=request.id, result={})
            else:
                return MCPResponse(
                    id=request.id,
                    error={
                        "code": -32601,
                        "message": f"方法未找到: {request.method}",
                    },
                )
        except Exception as e:
            logger.error(f"[MCP] 处理请求失败: {e}", exc_info=True)
            return MCPResponse(
                id=request.id,
                error={
                    "code": -32603,
                    "message": f"内部错误: {str(e)}",
                },
            )

    def _handle_initialize(self, request: MCPRequest) -> MCPResponse:
        """处理 initialize 方法，返回服务器能力。

        Args:
            request: 初始化请求。

        Returns:
            包含服务器信息和能力的响应。
        """
        return MCPResponse(
            id=request.id,
            result={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {
                    "name": MCP_SERVER_NAME,
                    "version": MCP_SERVER_VERSION,
                },
                "capabilities": {
                    "tools": {"listChanged": False},
                },
            },
        )

    def _handle_tools_list(self, request: MCPRequest) -> MCPResponse:
        """处理 tools/list 方法，返回所有已注册工具。

        Args:
            request: 请求。

        Returns:
            工具列表响应。
        """
        tools_list = []
        for tool in self._tools.values():
            tools_list.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
            )

        return MCPResponse(
            id=request.id,
            result={"tools": tools_list},
        )

    async def _handle_tools_call(self, request: MCPRequest) -> MCPResponse:
        """处理 tools/call 方法，调度到对应工具处理函数。

        Args:
            request: 工具调用请求（包含 name 和 arguments 参数）。

        Returns:
            工具执行结果响应。
        """
        tool_name = request.params.get("name")
        arguments = request.params.get("arguments", {})

        if tool_name not in self._tools:
            return MCPResponse(
                id=request.id,
                error={
                    "code": -32602,
                    "message": f"未知工具: {tool_name}",
                },
            )

        tool = self._tools[tool_name]
        try:
            result = await tool.handler(**arguments)
            return MCPResponse(
                id=request.id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, indent=2),
                        }
                    ]
                },
            )
        except TypeError as e:
            return MCPResponse(
                id=request.id,
                error={
                    "code": -32602,
                    "message": f"参数错误: {str(e)}",
                },
            )
        except Exception as e:
            return MCPResponse(
                id=request.id,
                error={
                    "code": -32603,
                    "message": f"工具执行失败: {str(e)}",
                },
            )

    # -----------------------------------------------------------------------
    # 工具处理函数
    # -----------------------------------------------------------------------

    async def _handle_text_to_speech(
        self,
        text: str,
        instruction: str = "",
        reference_audio: str | None = None,
        engine: str = "voxcpm2",
        cfg_value: float = 2.0,
        output_path: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """文本转语音工具处理函数。

        Args:
            text: 输入文本。
            instruction: 声音描述。
            reference_audio: 参考音频路径。
            engine: 引擎名称。
            cfg_value: CFG 强度。
            output_path: 输出路径。

        Returns:
            包含生成结果信息的字典。
        """
        try:
            from .service_layer import get_generation_service, get_model_service

            model_svc = get_model_service()
            status = model_svc.get_model_status()

            if not status.loaded or status.engine != engine:
                return {
                    "success": False,
                    "message": f"模型未加载，请先加载 {engine} 引擎",
                    "hint": f"调用 load_model 工具，参数 engine='{engine}'",
                }

            gen_svc = get_generation_service()

            if reference_audio:
                result = gen_svc.generate_voice_clone(
                    text=text,
                    reference_audio=reference_audio,
                    instruction=instruction,
                    cfg_value=cfg_value,
                    **kwargs,
                )
            else:
                result = gen_svc.generate_voice_design(
                    text=text,
                    instruction=instruction,
                    cfg_value=cfg_value,
                    **kwargs,
                )

            response: dict[str, Any] = {
                "success": True,
                "audio_path": result.audio_path,
                "duration": result.duration,
                "engine": result.engine,
                "message": result.message,
            }

            if output_path and result.audio_path:
                import shutil

                shutil.copy2(result.audio_path, output_path)
                response["output_path"] = output_path

            return response

        except Exception as e:
            logger.error(f"[MCP] 文本转语音失败: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    async def _handle_list_engines(self, **kwargs: Any) -> dict[str, Any]:
        """列出可用引擎工具处理函数。

        Returns:
            引擎列表和状态。
        """
        try:
            from .engine_ui_data import get_all_engine_uis
            from .service_layer import get_model_service

            engines = []
            model_svc = get_model_service()
            status = model_svc.get_model_status()

            for ui in get_all_engine_uis():
                engines.append(
                    {
                        "id": ui.engine_id,
                        "name": ui.name_i18n,
                        "version": ui.version,
                        "min_vram_gb": ui.min_vram_gb,
                        "recommended_vram_gb": ui.recommended_vram_gb,
                        "features": [f.value for f in ui.features],
                        "is_loaded": status.engine == ui.engine_id and status.loaded,
                        "is_current": status.engine == ui.engine_id,
                    }
                )

            return {"engines": engines, "current_engine": status.engine}

        except Exception as e:
            logger.error(f"[MCP] 列出引擎失败: {e}", exc_info=True)
            return {"engines": [], "error": str(e)}

    async def _handle_list_personas(self, keyword: str = "", **kwargs: Any) -> dict[str, Any]:
        """列出音色工具处理函数。

        Args:
            keyword: 搜索关键词。

        Returns:
            音色列表。
        """
        try:
            from .service_layer import get_persona_service

            svc = get_persona_service()
            personas = svc.list_personas(search_keyword=keyword)

            return {
                "personas": [
                    {
                        "name": p.name,
                        "description": p.description,
                        "exists": p.exists,
                        "size_kb": p.wav_size_kb,
                        "created_at": p.created_at,
                    }
                    for p in personas
                ],
                "count": len(personas),
            }

        except Exception as e:
            logger.error(f"[MCP] 列出音色失败: {e}", exc_info=True)
            return {"personas": [], "count": 0, "error": str(e)}

    async def _handle_get_model_status(self, **kwargs: Any) -> dict[str, Any]:
        """获取模型状态工具处理函数。

        Returns:
            模型状态信息。
        """
        try:
            from .service_layer import get_model_service

            svc = get_model_service()
            status = svc.get_model_status()

            return {
                "engine": status.engine,
                "loaded": status.loaded,
                "ready": status.ready,
                "vram_usage_percent": status.vram_usage_percent,
                "info": status.info,
            }

        except Exception as e:
            logger.error(f"[MCP] 获取模型状态失败: {e}", exc_info=True)
            return {"error": str(e)}

    async def _handle_load_model(self, engine: str, **kwargs: Any) -> dict[str, Any]:
        """加载模型工具处理函数。

        Args:
            engine: 引擎名称。

        Returns:
            加载结果。
        """
        try:
            from .service_layer import get_model_service

            svc = get_model_service()
            result = svc.load_model(engine)

            return {
                "success": result.success,
                "message": result.message,
                "engine": result.engine,
                "load_time_seconds": result.load_time,
            }

        except Exception as e:
            logger.error(f"[MCP] 加载模型失败: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    # -----------------------------------------------------------------------
    # 传输层
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_message(line: str) -> MCPRequest | None:
        """解析一行 JSON-RPC 消息。

        Args:
            line: JSON 字符串行。

        Returns:
            MCPRequest 实例，解析失败返回 None。
        """
        line = line.strip()
        if not line:
            return None

        try:
            data = json.loads(line)
            return MCPRequest(
                id=data.get("id"),
                method=data.get("method", ""),
                params=data.get("params", {}),
            )
        except json.JSONDecodeError as e:
            logger.warning(f"[MCP] JSON 解析失败: {e}, line: {line[:100]}")
            return None

    async def run_stdio(self) -> None:
        """通过 stdio 运行 MCP 服务器。

        从 stdin 读取 JSON-RPC 请求，处理后将响应写入 stdout。
        这是 Claude Desktop 等桌面客户端的标准接入方式。
        """
        logger.info("[MCP] MCP 服务器启动 (stdio 模式)")

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)

        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break

                line_str = line.decode("utf-8", errors="replace")
                request = self._parse_message(line_str)

                if request is None:
                    continue

                response = await self._handle_request(request)
                response_json = response.to_json()

                sys.stdout.write(response_json + "\n")
                sys.stdout.flush()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MCP] 主循环错误: {e}", exc_info=True)

        logger.info("[MCP] MCP 服务器停止")


# ---------------------------------------------------------------------------
# 便捷启动函数
# ---------------------------------------------------------------------------


def run_mcp_server(transport: str = "stdio") -> None:
    """启动 MCP 服务器。

    Args:
        transport: 传输方式，目前支持 "stdio"。
    """
    server = MCPServer()

    if transport == "stdio":
        asyncio.run(server.run_stdio())
    else:
        raise ValueError(f"不支持的传输方式: {transport}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_mcp_server()
