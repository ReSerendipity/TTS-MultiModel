"""Smoke tests for the MCP (Model Context Protocol) server module.

Covers:
- MCPRequest / MCPResponse dataclasses
- MCPServer request dispatch (initialize, tools/list, tools/call, ping, unknown)
- Tool registration
- _parse_message JSON-RPC parsing
- Response serialization (to_json)
"""

import json

import pytest

from integrated_app.mcp_server import (
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_NAME,
    MCP_SERVER_VERSION,
    MCPRequest,
    MCPResponse,
    MCPServer,
    MCPTool,
    run_mcp_server,
)

# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestMCPRequest:
    """Test MCPRequest dataclass."""

    def test_default_params(self):
        req = MCPRequest(id=1, method="ping")
        assert req.params == {}

    def test_custom_params(self):
        req = MCPRequest(id="abc", method="tools/call", params={"name": "test"})
        assert req.params["name"] == "test"


class TestMCPResponse:
    """Test MCPResponse dataclass."""

    def test_success_response(self):
        resp = MCPResponse(id=1, result={"status": "ok"})
        data = json.loads(resp.to_json())
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["result"] == {"status": "ok"}
        assert "error" not in data

    def test_error_response(self):
        resp = MCPResponse(id=2, error={"code": -32601, "message": "not found"})
        data = json.loads(resp.to_json())
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 2
        assert data["error"]["code"] == -32601
        assert "result" not in data


# ---------------------------------------------------------------------------
# MCPServer protocol tests
# ---------------------------------------------------------------------------


class TestMCPServerProtocol:
    """Test MCP server JSON-RPC protocol handling."""

    @pytest.fixture
    def server(self):
        return MCPServer()

    @pytest.mark.asyncio
    async def test_initialize(self, server):
        req = MCPRequest(id=1, method="initialize")
        resp = await server._handle_request(req)
        assert resp.id == 1
        assert resp.result["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert resp.result["serverInfo"]["name"] == MCP_SERVER_NAME
        assert resp.result["serverInfo"]["version"] == MCP_SERVER_VERSION
        assert "tools" in resp.result["capabilities"]

    @pytest.mark.asyncio
    async def test_ping(self, server):
        req = MCPRequest(id=2, method="ping")
        resp = await server._handle_request(req)
        assert resp.id == 2
        assert resp.result == {}

    @pytest.mark.asyncio
    async def test_unknown_method(self, server):
        req = MCPRequest(id=3, method="unknown/method")
        resp = await server._handle_request(req)
        assert resp.id == 3
        assert resp.error is not None
        assert resp.error["code"] == -32601

    @pytest.mark.asyncio
    async def test_tools_list(self, server):
        req = MCPRequest(id=4, method="tools/list")
        resp = await server._handle_request(req)
        assert resp.id == 4
        tools = resp.result["tools"]
        tool_names = [t["name"] for t in tools]
        assert "text_to_speech" in tool_names
        assert "list_engines" in tool_names
        assert "list_personas" in tool_names
        assert "get_model_status" in tool_names
        assert "load_model" in tool_names

    @pytest.mark.asyncio
    async def test_tools_call_unknown_tool(self, server):
        req = MCPRequest(
            id=5,
            method="tools/call",
            params={"name": "nonexistent", "arguments": {}},
        )
        resp = await server._handle_request(req)
        assert resp.id == 5
        assert resp.error is not None
        assert resp.error["code"] == -32602

    @pytest.mark.asyncio
    async def test_tools_call_text_to_speech_no_model(self, server):
        """text_to_speech should return failure when model not loaded."""
        req = MCPRequest(
            id=6,
            method="tools/call",
            params={"name": "text_to_speech", "arguments": {"text": "hello"}},
        )
        resp = await server._handle_request(req)
        assert resp.id == 6
        assert resp.result is not None
        content_text = resp.result["content"][0]["text"]
        result = json.loads(content_text)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_tools_call_get_model_status(self, server):
        """get_model_status should return status dict."""
        req = MCPRequest(
            id=7,
            method="tools/call",
            params={"name": "get_model_status", "arguments": {}},
        )
        resp = await server._handle_request(req)
        assert resp.id == 7
        assert resp.result is not None
        content_text = resp.result["content"][0]["text"]
        result = json.loads(content_text)
        assert "engine" in result or "error" in result

    @pytest.mark.asyncio
    async def test_tools_call_list_engines(self, server):
        """list_engines should return engines list."""
        req = MCPRequest(
            id=8,
            method="tools/call",
            params={"name": "list_engines", "arguments": {}},
        )
        resp = await server._handle_request(req)
        assert resp.id == 8
        assert resp.result is not None
        content_text = resp.result["content"][0]["text"]
        result = json.loads(content_text)
        assert "engines" in result

    @pytest.mark.asyncio
    async def test_tools_call_list_personas(self, server):
        """list_personas should return personas list."""
        req = MCPRequest(
            id=9,
            method="tools/call",
            params={"name": "list_personas", "arguments": {"keyword": "test"}},
        )
        resp = await server._handle_request(req)
        assert resp.id == 9
        assert resp.result is not None
        content_text = resp.result["content"][0]["text"]
        result = json.loads(content_text)
        assert "personas" in result or "error" in result

    @pytest.mark.asyncio
    async def test_internal_error_handling(self, server):
        """Server should catch exceptions and return -32603."""
        req = MCPRequest(id=10, method="tools/call", params={})
        resp = await server._handle_request(req)
        assert resp.id == 10
        assert resp.error is not None
        assert resp.error["code"] in (-32602, -32603)


# ---------------------------------------------------------------------------
# Tool registration tests
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Test MCP tool registration."""

    def test_register_custom_tool(self):
        server = MCPServer()

        async def custom_handler(**kwargs):
            return {"custom": True}

        tool = MCPTool(
            name="custom_tool",
            description="A custom test tool",
            input_schema={"type": "object", "properties": {}},
            handler=custom_handler,
        )
        server.register_tool(tool)

        assert "custom_tool" in server._tools

    @pytest.mark.asyncio
    async def test_call_custom_tool(self):
        server = MCPServer()

        async def custom_handler(x: str = "", **kwargs):
            return {"echo": x}

        tool = MCPTool(
            name="echo",
            description="Echo tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=custom_handler,
        )
        server.register_tool(tool)

        req = MCPRequest(
            id=1,
            method="tools/call",
            params={"name": "echo", "arguments": {"x": "hello"}},
        )
        resp = await server._handle_request(req)
        assert resp.result is not None
        content_text = resp.result["content"][0]["text"]
        result = json.loads(content_text)
        assert result["echo"] == "hello"


# ---------------------------------------------------------------------------
# Message parsing tests
# ---------------------------------------------------------------------------


class TestMessageParsing:
    """Test _parse_message static method."""

    def test_valid_json(self):
        line = json.dumps({"id": 1, "method": "ping", "params": {}})
        req = MCPServer._parse_message(line)
        assert req is not None
        assert req.id == 1
        assert req.method == "ping"

    def test_empty_line(self):
        assert MCPServer._parse_message("") is None
        assert MCPServer._parse_message("   ") is None

    def test_invalid_json(self):
        assert MCPServer._parse_message("not json") is None

    def test_missing_method(self):
        line = json.dumps({"id": 1})
        req = MCPServer._parse_message(line)
        assert req is not None
        assert req.method == ""


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestMCPConstants:
    """Test MCP protocol constants are properly defined."""

    def test_protocol_version(self):
        assert MCP_PROTOCOL_VERSION == "2024-11-05"

    def test_server_name(self):
        assert MCP_SERVER_NAME == "tts-multimodel"

    def test_server_version(self):
        assert MCP_SERVER_VERSION == "1.0.0"


# ---------------------------------------------------------------------------
# run_mcp_server tests
# ---------------------------------------------------------------------------


class TestRunMCPServer:
    """Test run_mcp_server entry point."""

    def test_invalid_transport_raises(self):
        with pytest.raises(ValueError, match="不支持的传输方式"):
            run_mcp_server("invalid")
