"""
API 测试工具
===========
对 http_request 的响应执行断言，支持 status code、JSON path、regex、包含检查。
"""
from __future__ import annotations
import json
import re
from typing import Any


def api_test_assert(
    response: dict,
    expected_status: int = None,
    json_path: str = None,
    expected_value: Any = None,
    contains: str = None,
    regex: str = None,
    max_response_time_ms: float = None,
) -> dict:
    """
    对 HTTP 响应执行断言。

    Args:
        response: http_request 返回的响应字典
        expected_status: 期望的 HTTP 状态码
        json_path: JSON path，用点号分隔，如 "data.items.0.name"
        expected_value: 与 json_path 对应的期望值
        contains: 断言响应体文本包含某字符串
        regex: 断言响应体文本匹配某正则表达式
        max_response_time_ms: 断言响应时间不超过某毫秒数

    Returns:
        {"passed": bool, "message": str, "actual": Any, "expected": Any}
    """
    errors = []
    actuals = {}

    if expected_status is not None:
        actual_status = response.get("status")
        actuals["status"] = actual_status
        if actual_status != expected_status:
            errors.append(f"Status: expected {expected_status}, got {actual_status}")

    if max_response_time_ms is not None:
        actual_time = response.get("elapsed_ms", 0)
        actuals["elapsed_ms"] = actual_time
        if actual_time > max_response_time_ms:
            errors.append(f"Response time: expected <= {max_response_time_ms}ms, got {actual_time}ms")

    body_text = response.get("body_text", "")
    body_json = response.get("body_json")

    if contains is not None:
        actuals["contains"] = contains in body_text
        if contains not in body_text:
            errors.append(f"Body does not contain: {contains!r}")

    if regex is not None:
        actuals["regex"] = bool(re.search(regex, body_text))
        if not re.search(regex, body_text):
            errors.append(f"Body does not match regex: {regex!r}")

    if json_path is not None:
        value = _get_json_path(body_json, json_path)
        actuals["json_path"] = value
        if value is _MISSING:
            errors.append(f"JSON path not found: {json_path}")
        elif expected_value is not None and value != expected_value:
            errors.append(f"JSON path {json_path}: expected {expected_value!r}, got {value!r}")

    if errors:
        return {
            "passed": False,
            "message": "; ".join(errors),
            "actual": actuals,
            "expected": {
                "status": expected_status,
                "json_path": json_path,
                "expected_value": expected_value,
                "contains": contains,
                "regex": regex,
                "max_response_time_ms": max_response_time_ms,
            },
        }

    return {
        "passed": True,
        "message": "All assertions passed",
        "actual": actuals,
        "expected": None,
    }


_MISSING = object()


def _get_json_path(data: Any, path: str) -> Any:
    """简单的 JSON path 解析（仅支持点号和整数索引）"""
    if data is None:
        return _MISSING
    parts = path.split(".")
    current = data
    for part in parts:
        if current is _MISSING:
            return _MISSING
        if isinstance(current, dict):
            current = current.get(part, _MISSING)
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else _MISSING
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current


def register_api_test_tools(registry):
    registry.register(
        name="api_test_assert",
        description="对 HTTP 响应执行断言测试：检查状态码、JSON path、文本包含、正则匹配、响应时间。",
        parameters={
            "type": "object",
            "properties": {
                "response": {"type": "object", "description": "http_request 返回的响应字典"},
                "expected_status": {"type": "integer", "description": "期望的 HTTP 状态码"},
                "json_path": {"type": "string", "description": "JSON path，如 data.items.0.name"},
                "expected_value": {"description": "json_path 对应的期望值"},
                "contains": {"type": "string", "description": "断言响应体包含此字符串"},
                "regex": {"type": "string", "description": "断言响应体匹配此正则"},
                "max_response_time_ms": {"type": "number", "description": "最大响应时间（毫秒）"},
            },
            "required": ["response"],
        },
        func=api_test_assert,
        parallel_safe=True,
    )
