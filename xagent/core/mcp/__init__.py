"""MCP (Model Context Protocol) 模块

提供 X-Agent 与外部 MCP Server 的通信能力。
"""
from .client import MCPClient, MCPTransport, StdioTransport, HttpSseTransport
from .registry_adapter import MCPAdapter
from .manager import MCPServerManager, ServerConfig

__all__ = [
    "MCPClient", "MCPTransport", "StdioTransport", "HttpSseTransport",
    "MCPAdapter", "MCPServerManager", "ServerConfig",
]
