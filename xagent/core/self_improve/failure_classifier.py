"""
FailureClassifier
=================
6 类失败模式规则分类器。
零 LLM 成本、可解释、可扩展。
"""
from __future__ import annotations
import json
import re
from enum import Enum, auto
from typing import Optional

from ..llm_client import LLMResponse
from ..tool_registry import ToolRegistry


class FailureType(Enum):
    TOOL_PARSE_ERROR = auto()       # JSON 截断/格式错误
    TOOL_SCHEMA_MISMATCH = auto()   # 参数类型/必填字段缺失
    TOOL_EXECUTION_ERROR = auto()   # 运行时异常（文件不存在等）
    TOOL_HALLUCINATION = auto()     # 调用不存在工具
    TOOL_REDUNDANCY = auto()        # 重复调用（Storm 检测）
    CONTEXT_DRIFT = auto()          # 多轮后偏离用户意图
    UNKNOWN = auto()                # 无法归类


class FailureClassifier:
    """
    失败分类器。

    输入: LLMResponse + 工具执行结果
    输出: FailureType + 置信度 + 原始证据
    """

    def __init__(self, tools: ToolRegistry):
        self.tools = tools
        self._dangerous_list = getattr(tools, "_dangerous_list", [])

    def classify(self, resp: LLMResponse, tool_results: list[dict] = None) -> dict:
        """
        对一次 Agent 循环的失败进行分类。

        Args:
            resp: LLM 响应
            tool_results: 每个 tool_call 的执行结果列表
                          [{"name": str, "ok": bool, "error": str}]

        Returns:
            {"type": FailureType, "confidence": float, "evidence": str}
        """
        tool_results = tool_results or []

        # 1. 检查 tool hallucination
        if resp.tool_calls:
            for tc in resp.tool_calls:
                name = tc.get("name", "")
                if not self.tools.has_tool(name):
                    return {
                        "type": FailureType.TOOL_HALLUCINATION,
                        "confidence": 1.0,
                        "evidence": f"Tool '{name}' does not exist in registry",
                    }

        # 2. 检查 JSON parse error（TruncationFixPass 的特征）
        if resp.tool_calls:
            for tc in resp.tool_calls:
                args = tc.get("arguments", {})
                if isinstance(args, dict) and "_repair_attempted" in args:
                    return {
                        "type": FailureType.TOOL_PARSE_ERROR,
                        "confidence": 0.95,
                        "evidence": f"JSON repair attempted for tool {tc.get('name')}",
                    }
                if isinstance(args, str):
                    return {
                        "type": FailureType.TOOL_PARSE_ERROR,
                        "confidence": 0.95,
                        "evidence": f"Arguments still string after parsing: {args[:100]}",
                    }

        # 3. 检查 schema mismatch（从 tool_results 中推断）
        for tr in tool_results:
            if not tr.get("ok", True):
                err = str(tr.get("error", "")).lower()
                if any(k in err for k in ("missing", "required", "type error", "validation", "schema")):
                    return {
                        "type": FailureType.TOOL_SCHEMA_MISMATCH,
                        "confidence": 0.85,
                        "evidence": f"Schema error in {tr.get('name')}: {err[:200]}",
                    }
                # 执行错误
                return {
                    "type": FailureType.TOOL_EXECUTION_ERROR,
                    "confidence": 0.85,
                    "evidence": f"Execution error in {tr.get('name')}: {err[:200]}",
                }

        # 4. 检查 context drift（启发式：content 包含系统提示或反思关键词）
        content = (resp.content or "").lower()
        drift_indicators = [
            "system prompt", "ignore previous", "new instruction",
            "instead, you should", "your new role",
        ]
        if any(ind in content for ind in drift_indicators):
            return {
                "type": FailureType.CONTEXT_DRIFT,
                "confidence": 0.7,
                "evidence": f"Drift indicator found in response: {content[:200]}",
            }

        # 5. 默认未知
        return {
            "type": FailureType.UNKNOWN,
            "confidence": 0.5,
            "evidence": "No clear failure pattern detected",
        }

    @staticmethod
    def classify_from_trace(trace: dict) -> dict:
        """
        从 telemetry trace 中分类失败（后验分析）。
        """
        error = trace.get("error", "")
        tool_calls = trace.get("tool_calls", [])

        # 从 trace 中的 tool call 推断
        for tc in tool_calls:
            result = tc.get("result", {})
            if isinstance(result, dict) and not result.get("ok", True):
                err = str(result.get("error", "")).lower()
                if any(k in err for k in ("missing", "required", "type error", "validation")):
                    return {
                        "type": FailureType.TOOL_SCHEMA_MISMATCH,
                        "confidence": 0.8,
                        "evidence": err[:200],
                    }
                return {
                    "type": FailureType.TOOL_EXECUTION_ERROR,
                    "confidence": 0.8,
                    "evidence": err[:200],
                }

        if error:
            return {
                "type": FailureType.UNKNOWN,
                "confidence": 0.5,
                "evidence": error,
            }

        return {
            "type": FailureType.UNKNOWN,
            "confidence": 0.0,
            "evidence": "No error in trace",
        }
