"""Tests for MCP (Model Context Protocol) client."""
import json
import time
from unittest.mock import MagicMock

import pytest

from xagent.core.mcp.client import MCPClient, MCPTransport, StdioTransport, MCPTool, MCPCallResult
from xagent.core.mcp.registry_adapter import MCPAdapter
from xagent.core.tool_registry import ToolRegistry


class MockTransport(MCPTransport):
    """内存中的 MCP transport，用于测试"""

    def __init__(self):
        self._outbox: list[dict] = []
        self._inbox: list[dict] = []
        self._alive = True

    def queue_response(self, msg: dict):
        self._inbox.append(msg)

    def send(self, message: dict) -> None:
        self._outbox.append(message)

    def receive(self) -> dict | None:
        if self._inbox:
            return self._inbox.pop(0)
        return None

    def close(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


class TestMCPTool:
    def test_from_dict(self):
        t = MCPTool.from_dict({
            "name": "read_file",
            "description": "Read a file",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        })
        assert t.name == "read_file"
        assert t.description == "Read a file"
        assert t.input_schema["type"] == "object"

    def test_to_openai_schema(self):
        t = MCPTool(name="calc", description="Calculate", input_schema={"type": "object"})
        schema = t.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "calc"


class TestMCPCallResult:
    def test_text_extraction(self):
        r = MCPCallResult(content=[
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ])
        assert r.text == "Hello\nWorld"

    def test_empty_text(self):
        r = MCPCallResult(content=[{"type": "image", "data": "abc"}])
        assert r.text == ""


class TestMCPClientConnect:
    def test_initialize_handshake(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "test-server", "version": "1.0"},
                "capabilities": {},
            },
        })

        client = MCPClient(transport)
        info = client.connect()

        assert client.is_connected()
        assert info["serverInfo"]["name"] == "test-server"
        # 应发送 initialize + notifications/initialized
        assert len(transport._outbox) == 2
        assert transport._outbox[0]["method"] == "initialize"
        assert transport._outbox[1]["method"] == "notifications/initialized"

    def test_initialize_timeout(self):
        transport = MockTransport()
        client = MCPClient(transport)
        with pytest.raises(RuntimeError, match="initialize failed"):
            client.connect()

    def test_disconnect(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        client = MCPClient(transport)
        client.connect()
        client.disconnect()
        assert not client.is_connected()
        assert not transport.is_alive()


class TestMCPClientListTools:
    def test_list_tools_success(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "read_file", "description": "Read", "inputSchema": {}},
                    {"name": "write_file", "description": "Write", "inputSchema": {}},
                ]
            },
        })

        client = MCPClient(transport)
        client.connect()
        tools = client.list_tools()

        assert len(tools) == 2
        assert tools[0].name == "read_file"
        assert tools[1].name == "write_file"

    def test_list_tools_empty(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": []},
        })

        client = MCPClient(transport)
        client.connect()
        tools = client.list_tools()
        assert tools == []


class TestMCPClientCallTool:
    def test_call_tool_success(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "content": [{"type": "text", "text": "file content"}],
                "isError": False,
            },
        })

        client = MCPClient(transport)
        client.connect()
        result = client.call_tool("read_file", {"path": "/tmp/test.txt"})

        assert not result.is_error
        assert result.text == "file content"

    def test_call_tool_error(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "error": {"code": -32600, "message": "Invalid request"},
        })

        client = MCPClient(transport)
        client.connect()
        result = client.call_tool("bad_tool", {})

        assert result.is_error
        assert "Invalid request" in result.text

    def test_call_tool_no_response(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        # 不排队 call_tool 的 response

        client = MCPClient(transport)
        client.connect()
        result = client.call_tool("read_file", {"path": "/tmp/test.txt"})

        assert result.is_error
        assert "no response" in result.text


class TestMCPClientResourcesAndPrompts:
    def test_list_resources(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"resources": [{"uri": "file:///tmp/a.txt", "name": "a"}]},
        })

        client = MCPClient(transport)
        client.connect()
        resources = client.list_resources()
        assert len(resources) == 1
        assert resources[0]["uri"] == "file:///tmp/a.txt"

    def test_list_prompts(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"prompts": [{"name": "review_code"}]},
        })

        client = MCPClient(transport)
        client.connect()
        prompts = client.list_prompts()
        assert len(prompts) == 1
        assert prompts[0]["name"] == "review_code"


class TestMCPClientContextManager:
    def test_with_statement(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })

        with MCPClient(transport) as client:
            assert client.is_connected()
        assert not transport.is_alive()


class TestMCPAdapter:
    def test_discover_tools(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "read_file", "description": "Read", "inputSchema": {}},
                ]
            },
        })

        client = MCPClient(transport, name="fs")
        adapter = MCPAdapter("filesystem", client)
        adapter.connect()
        specs = adapter.discover_tools()

        assert len(specs) == 1
        assert specs[0].name == "filesystem.read_file"
        assert "[filesystem]" in specs[0].description
        assert specs[0].parallel_safe is True  # read 启发式为安全

    def test_make_handler(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "echo", "description": "Echo", "inputSchema": {}},
                ]
            },
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 3,
            "result": {
                "content": [{"type": "text", "text": "hello"}],
                "isError": False,
            },
        })

        client = MCPClient(transport, name="test")
        adapter = MCPAdapter("test", client)
        adapter.connect()
        adapter.discover_tools()

        handler = adapter.make_handler("test.echo")
        result = handler(msg="hello")
        # handler 现在返回原始文本结果，由 ToolRegistry.execute 统一包装
        assert result == "hello"

    def test_namespaced_tool_names(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "delete", "description": "Delete", "inputSchema": {}},
                ]
            },
        })

        client = MCPClient(transport)
        adapter = MCPAdapter("fs", client)
        adapter.connect()
        specs = adapter.discover_tools()

        assert specs[0].name == "fs.delete"
        assert specs[0].parallel_safe is False  # delete 启发式为不安全


class TestToolRegistryMCPIntegration:
    def test_register_and_discover_mcp_tools(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "list_dir", "description": "List", "inputSchema": {}},
                ]
            },
        })

        registry = ToolRegistry()
        client = MCPClient(transport)
        adapter = MCPAdapter("fs", client)
        adapter.connect()

        registry.register_mcp_adapter(adapter)
        specs = registry.discover_mcp_tools()

        assert len(specs) == 1
        assert specs[0].name == "fs.list_dir"
        assert "fs.list_dir" in registry.list_tools()

    def test_mcp_tool_execution_via_registry(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "greet", "description": "Greet", "inputSchema": {}},
                ]
            },
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 3,
            "result": {
                "content": [{"type": "text", "text": "Hi, Alice"}],
                "isError": False,
            },
        })

        registry = ToolRegistry()
        client = MCPClient(transport)
        adapter = MCPAdapter("demo", client)
        adapter.connect()

        registry.register_mcp_adapter(adapter)
        registry.discover_mcp_tools()

        result = registry.execute("demo.greet", {"name": "Alice"})
        assert result["ok"] is True
        assert result["result"] == "Hi, Alice"

    def test_get_mcp_tool(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "calc", "description": "Calc", "inputSchema": {}},
                ]
            },
        })

        registry = ToolRegistry()
        registry.register("builtin", "Builtin tool", {}, lambda: None)

        client = MCPClient(transport)
        adapter = MCPAdapter("ext", client)
        adapter.connect()
        registry.register_mcp_adapter(adapter)
        registry.discover_mcp_tools()

        assert registry.get("builtin") is not None
        assert registry.get("ext.calc") is not None
        assert registry.get("nonexistent") is None

    def test_get_schemas_includes_mcp(self):
        transport = MockTransport()
        transport.queue_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "s"}, "capabilities": {}},
        })
        transport.queue_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [
                    {"name": "fetch", "description": "Fetch", "inputSchema": {}},
                ]
            },
        })

        registry = ToolRegistry()
        registry.register("local", "Local", {}, lambda: None)

        client = MCPClient(transport)
        adapter = MCPAdapter("web", client)
        adapter.connect()
        registry.register_mcp_adapter(adapter)
        registry.discover_mcp_tools()

        schemas = registry.get_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "local" in names
        assert "web.fetch" in names
