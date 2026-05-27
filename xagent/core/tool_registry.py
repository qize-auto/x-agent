"""
工具注册表
========
Agent 的"手脚"：统一管理可用工具，支持动态注册和 schema 描述。

设计参考：
- OpenClaw 的 skills + tools 分离架构
- Hermes Agent 的 40+ 工具分类
- MCP (Model Context Protocol) 的 schema 规范
"""
from __future__ import annotations
import json
import inspect
import time
from typing import Callable, Any
from dataclasses import dataclass, field

from .audit import AuditLog


@dataclass
class ToolSpec:
    """工具规范（OpenAI function calling 格式）"""
    name: str
    description: str
    parameters: dict   # JSON Schema
    func: Callable = field(repr=False)
    dangerous: bool = False   # 是否需要用户确认
    parallel_safe: bool = False  # 是否可以与其他工具并行执行（Phase 4 基础设施）

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """
    Agent 工具注册中心
    
    用法:
        registry = ToolRegistry()
        registry.register_tool("read_file", read_file_func, {...})
        
        # 获取工具 schema（传给 LLM）
        schemas = registry.get_schemas()
        
        # 执行工具调用
        result = registry.execute("read_file", {"path": "main.py"})
    """

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._audit = AuditLog()
        self._mcp_adapters: dict[str, Any] = {}  # MCP 适配器，延迟导入避免循环依赖

    def register(self, name: str, description: str, parameters: dict,
                 func: Callable, dangerous: bool = False,
                 parallel_safe: bool = False):
        """注册一个工具"""
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            func=func,
            dangerous=dangerous,
            parallel_safe=parallel_safe,
        )

    def get_schemas(self) -> list[dict]:
        """获取所有工具的 OpenAI schema 列表（含 MCP 动态工具）"""
        schemas = [t.to_openai_schema() for t in self._tools.values()]
        for adapter in self._mcp_adapters.values():
            schemas.extend([t.to_openai_schema() for t in adapter.list_tools()])
        return schemas

    def get(self, name: str) -> ToolSpec | None:
        if name in self._tools:
            return self._tools[name]
        # 查找 MCP 工具
        for adapter in self._mcp_adapters.values():
            for spec in adapter.list_tools():
                if spec.name == name:
                    return spec
        return None

    def list_tools(self) -> list[str]:
        tools = list(self._tools.keys())
        for adapter in self._mcp_adapters.values():
            tools.extend([t.name for t in adapter.list_tools()])
        return tools

    def register_mcp_adapter(self, adapter) -> None:
        """注册一个 MCP 适配器（动态工具源）"""
        self._mcp_adapters[adapter.server_name] = adapter

    def discover_mcp_tools(self) -> list[ToolSpec]:
        """发现并注册所有 MCP adapter 的工具"""
        all_specs = []
        for adapter in self._mcp_adapters.values():
            specs = adapter.discover_tools()
            for spec in specs:
                # 绑定 handler
                spec.func = adapter.make_handler(spec.name)
            all_specs.extend(specs)
        return all_specs

    def execute(self, name: str, arguments: dict, confirm_callback: Callable = None) -> dict:
        """
        执行工具调用
        
        Args:
            name: 工具名
            arguments: 参数字典
            confirm_callback: 危险操作确认回调，签名: (tool_name, args) -> bool
        
        Returns:
            {"ok": bool, "result": Any, "error": str}
        """
        tool = self.get(name)
        if not tool:
            return {"ok": False, "error": f"未知工具: {name}"}

        start = time.time()
        confirmed = True

        # 半自动安全：危险操作需要确认
        if tool.dangerous and confirm_callback:
            confirmed = confirm_callback(name, arguments)
            if not confirmed:
                result = {"ok": False, "error": "用户取消了危险操作"}
                self._audit.log(name, arguments, result, confirmed=confirmed, duration_ms=(time.time()-start)*1000)
                return result

        try:
            result_data = tool.func(**arguments)
            result = {"ok": True, "result": result_data}
        except Exception as e:
            result = {"ok": False, "error": str(e)}

        duration_ms = (time.time() - start) * 1000
        self._audit.log(name, arguments, result, confirmed=confirmed, duration_ms=duration_ms)
        return result

    def unregister(self, name: str) -> bool:
        """注销一个工具"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def register_from_spec(self, spec: ToolSpec):
        """从 ToolSpec 对象注册工具"""
        self._tools[spec.name] = spec

    def __repr__(self):
        return f"ToolRegistry(tools={list(self._tools.keys())})"
