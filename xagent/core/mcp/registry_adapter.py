"""ToolRegistry ↔ MCP 桥接器

将 MCP server 的动态工具无缝接入 X-Agent 的 ToolRegistry，
使 AgentLoop 无需感知工具来源（内置 vs MCP）。
"""
from __future__ import annotations
from typing import Callable

from ..tool_registry import ToolSpec
from .client import MCPClient, MCPCallResult
from .security import MCPSecurityBundle, MCPResponseFilter


class MCPAdapter:
    """
    包装一个 MCPClient，使其看起来像本地工具。

    用法:
        client = MCPClient(StdioTransport("npx", [...]))
        adapter = MCPAdapter("filesystem", client)
        adapter.connect()

        # 现在可以像本地工具一样使用
        for tool in adapter.discover_tools():
            registry.register(tool.name, tool.description, tool.parameters,
                              adapter.make_handler(tool.name))
    """

    def __init__(self, server_name: str, client: MCPClient,
                 sandbox: bool = False, trusted: bool = False,
                 security: MCPSecurityBundle = None):
        self.server_name = server_name
        self.client = client
        self.sandbox = sandbox
        self.trusted = trusted
        self.security = security or MCPSecurityBundle()
        self._tools: list[ToolSpec] = []
        self._blocked_tools: list[dict] = []
        self._connected = False

    def connect(self) -> dict:
        """初始化底层 MCPClient"""
        info = self.client.connect()
        self._connected = True
        return info

    def disconnect(self):
        self.client.disconnect()
        self._connected = False

    def discover_tools(self) -> list[ToolSpec]:
        """从 MCP server 发现工具并转为 ToolSpec（带安全扫描）"""
        if not self._connected:
            raise RuntimeError("MCPAdapter not connected. Call connect() first.")
        mcp_tools = self.client.list_tools()
        raw_tools = [
            {"name": mt.name, "description": mt.description, "inputSchema": mt.input_schema}
            for mt in mcp_tools
        ]
        safe_tools, blocked_tools = self.security.scan_and_filter_tools(raw_tools)
        self._blocked_tools = blocked_tools

        specs = []
        for tool in safe_tools:
            unique_name = f"{self.server_name}.{tool['name']}"
            spec = ToolSpec(
                name=unique_name,
                description=f"[{self.server_name}] {tool['description']}",
                parameters=tool.get("inputSchema", {}),
                func=None,  # 稍后绑定
                dangerous=not self.trusted,
                parallel_safe=self._is_parallel_safe(tool["name"]),
            )
            specs.append(spec)
        self._tools = specs
        return specs

    def make_handler(self, tool_name: str) -> Callable:
        """
        生成一个可被 ToolRegistry 调用的函数。
        tool_name 是带前缀的（如 filesystem.read_file）。
        """
        # 去掉前缀，得到原始 MCP 工具名
        prefix = f"{self.server_name}."
        raw_name = tool_name[len(prefix):] if tool_name.startswith(prefix) else tool_name

        def handler(**kwargs):
            if not self._connected:
                raise RuntimeError("MCP server disconnected")
            result: MCPCallResult = self.client.call_tool(raw_name, kwargs)
            if result.is_error:
                raise RuntimeError(result.text)
            # 应用响应过滤器
            filtered = self.security.response_filter.filter(result.text)
            if filtered["blocked"]:
                raise RuntimeError(f"MCP response blocked: {'; '.join(filtered['warnings'])}")
            return filtered["content"]

        return handler

    def list_tools(self) -> list[ToolSpec]:
        return self._tools

    @staticmethod
    def _is_parallel_safe(tool_name: str) -> bool:
        """启发式判断 MCP 工具是否可并行（保守策略）"""
        # 读操作通常安全，写操作不安全
        read_hints = {"read", "get", "list", "search", "query", "fetch", "describe"}
        write_hints = {"write", "create", "delete", "update", "insert", "modify",
                       "remove", "drop", "alter", "append", "save"}
        name_lower = tool_name.lower()
        if any(h in name_lower for h in read_hints):
            return True
        if any(h in name_lower for h in write_hints):
            return False
        # 默认保守：不可并行
        return False

    def __repr__(self):
        return f"MCPAdapter({self.server_name}, connected={self._connected}, tools={len(self._tools)})"
