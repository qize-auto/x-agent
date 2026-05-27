"""
Tool-Call Repair Pipeline (工具调用修复流水线)
================================================
解决 DeepSeek 已知的 tool-use 问题，避免浪费一轮对话让模型重新输出。

四层修复通道：
1. Scavenge:   从 reasoning_content 回收遗漏的 tool calls
2. Truncation: 修复 JSON 截断（补全括号、去尾随逗号）
3. Flatten:    深层 schema 的反扁平化（dot-notation -> nested）
4. Storm:      滑动窗口内重复调用去重

参考：DeepSeek-Reasonix 的 Pillar 2 — Tool-Call Repair
"""
from __future__ import annotations
import json
import hashlib
import re
from collections import deque
from typing import Optional

from .llm_client import LLMResponse
from .tool_registry import ToolRegistry


class SchemaFlattener:
    """
    Schema 扁平化管理器。

    DeepSeek 对深层（depth>2）或宽（>10 叶子参数）的 schema 容易漏字段。
    解决方案：
    1. 给模型看扁平化的 dot-notation schema
    2. 模型输出扁平参数
    3. 执行前反扁平化为嵌套结构
    """

    FLATTEN_THRESHOLD_PARAMS = 10
    FLATTEN_THRESHOLD_DEPTH = 2

    def __init__(self, tools: ToolRegistry):
        self._flatten_map: dict[str, dict] = {}  # tool_name -> {flat_key: nested_path}
        self._original_schemas: dict[str, dict] = {}
        self._flat_schemas: dict[str, dict] = {}
        self._build(tools)

    def _build(self, tools: ToolRegistry):
        """构建扁平化映射"""
        schemas = tools.get_schemas() or []
        for schema in schemas:
            func = schema.get("function", {})
            name = func.get("name", "")
            params = func.get("parameters", {})

            self._original_schemas[name] = schema

            leaf_count, max_depth = self._analyze_depth(params)
            if leaf_count > self.FLATTEN_THRESHOLD_PARAMS or max_depth > self.FLATTEN_THRESHOLD_DEPTH:
                flat_schema, mapping = self._flatten_schema(schema)
                self._flat_schemas[name] = flat_schema
                self._flatten_map[name] = mapping
            else:
                self._flat_schemas[name] = schema

    @staticmethod
    def _analyze_depth(obj: dict, current_depth: int = 0) -> tuple[int, int]:
        """返回 (叶子参数数, 最大深度)"""
        if not isinstance(obj, dict):
            return 1, current_depth

        leaf_count = 0
        max_depth = current_depth
        properties = obj.get("properties", {})

        for value in properties.values():
            if isinstance(value, dict) and "properties" in value:
                lc, md = SchemaFlattener._analyze_depth(value, current_depth + 1)
                leaf_count += lc
                max_depth = max(max_depth, md)
            elif isinstance(value, dict) and value.get("type") == "object" and "properties" not in value:
                # object 类型但没有定义 properties，算一个叶子
                leaf_count += 1
                max_depth = max(max_depth, current_depth + 1)
            else:
                leaf_count += 1
                max_depth = max(max_depth, current_depth + 1)

        return leaf_count, max_depth

    @staticmethod
    def _flatten_schema(schema: dict) -> tuple[dict, dict]:
        """
        将深层 schema 扁平化为 dot-notation。

        Returns:
            (flat_schema, mapping)
            mapping: {dot_key: [nested, path, list]}
        """
        func = schema.get("function", {})
        params = func.get("parameters", {})
        properties = params.get("properties", {})

        flat_properties = {}
        mapping = {}

        def _walk(obj: dict, prefix: str = ""):
            props = obj.get("properties", {})
            for key, value in props.items():
                dot_key = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict) and "properties" in value:
                    _walk(value, dot_key)
                else:
                    flat_properties[dot_key] = value
                    path = dot_key.split(".")
                    mapping[dot_key] = path

        _walk(params)  # 传入 params（JSON Schema 对象），让 _walk 自己提取 properties

        flat_schema = {
            "type": "function",
            "function": {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": flat_properties,
                    "required": list(flat_properties.keys()),
                },
            },
        }
        return flat_schema, mapping

    def get_schemas_for_model(self) -> list[dict]:
        """返回给模型看的 schema 列表（可能已扁平化）"""
        return list(self._flat_schemas.values())

    def unflatten_args(self, tool_name: str, args: dict) -> dict:
        """
        将扁平参数反扁平化为嵌套结构。

        例: {"user.profile.name": "Alice"} -> {"user": {"profile": {"name": "Alice"}}}
        """
        if tool_name not in self._flatten_map:
            return args

        mapping = self._flatten_map[tool_name]
        nested = {}

        for flat_key, value in args.items():
            if flat_key not in mapping:
                # 可能模型直接用了原始嵌套格式，保留原样
                nested[flat_key] = value
                continue

            path = mapping[flat_key]
            current = nested
            for part in path[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[path[-1]] = value

        return nested

    def is_flattened(self, tool_name: str) -> bool:
        """判断工具是否被扁平化过"""
        return tool_name in self._flatten_map


class ScavengePass:
    """
    Pass 1: 从 reasoning_content 回收遗漏的 tool calls。

    DeepSeek R1 有时在 thinking 过程写了完整的 tool call JSON，
    但正式 tool_calls 字段为空。
    """

    # 匹配 thinking 中的 tool call 模式
    _PATTERNS = [
        r'```tool\s*\n?\s*(\{[^`]+\})\s*```',
        r'<tool>(\{[^<]+\})</tool>',
        r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\})\s*\}',
    ]

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    def run(self, resp: LLMResponse) -> LLMResponse:
        if not resp.reasoning:
            return resp
        # 即使已有 tool_calls，也可能从 reasoning 中发现遗漏的调用

        found = []
        for pattern in self._PATTERNS:
            for match in re.findall(pattern, resp.reasoning, re.DOTALL):
                try:
                    if isinstance(match, tuple):
                        # 第三个 pattern 返回 (name, args_json)
                        tool_data = {"name": match[0], "arguments": json.loads(match[1])}
                    else:
                        tool_data = json.loads(match)

                    name = tool_data.get("name", "")
                    if name and self.tools.has_tool(name):
                        found.append({
                            "id": f"scavenged_{hashlib.sha256(match.encode()).hexdigest()[:8]}",
                            "name": name,
                            "arguments": tool_data.get("arguments", {}),
                        })
                except (json.JSONDecodeError, AttributeError):
                    continue

        if found:
            resp.tool_calls = resp.tool_calls + found if resp.tool_calls else found

        return resp


class TruncationFixPass:
    """
    Pass 2: 修复截断的 JSON tool call arguments。

    当 max_tokens 命中或模型输出不完整时，JSON 可能缺少闭合括号。
    """

    def run(self, resp: LLMResponse) -> LLMResponse:
        if not resp.tool_calls:
            return resp

        for tc in resp.tool_calls:
            args = tc.get("arguments", {})
            raw = None

            if isinstance(args, dict):
                # 如果 dict 中只包含 _raw，提取原始字符串尝试修复
                if len(args) == 1 and "_raw" in args:
                    raw = args.get("_raw", "")
                else:
                    continue  # 已经是合法 dict
            elif isinstance(args, str):
                raw = args

            if raw is not None:
                fixed = self._attempt_repair(raw)
                if fixed:
                    try:
                        tc["arguments"] = json.loads(fixed)
                    except json.JSONDecodeError:
                        tc["arguments"] = {"_raw": raw, "_repair_attempted": True}

        return resp

    @staticmethod
    def _attempt_repair(raw: str) -> Optional[str]:
        """尝试修复不完整的 JSON 字符串"""
        if not raw or not isinstance(raw, str):
            return None

        repaired = raw.strip()

        # 补全闭合括号（按 LIFO 顺序：先闭方括号，再闭花括号）
        open_brackets = repaired.count('[') - repaired.count(']')
        open_braces = repaired.count('{') - repaired.count('}')
        repaired += ']' * max(0, open_brackets)
        repaired += '}' * max(0, open_braces)

        # 去除尾随逗号（在闭合括号前）
        repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

        # 去除 trailing text after last closing brace
        # 找到最后一个 } 或 ]，截断后面的内容
        last_close = max(repaired.rfind('}'), repaired.rfind(']'))
        if last_close > 0:
            repaired = repaired[:last_close + 1]

        # 验证是否为合法 JSON
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            return None


class FlattenPass:
    """
    Pass 3: 反扁平化工具参数。

    如果模型输出的是 dot-notation 参数（因为 schema 被扁平化展示），
    在执行前转换回嵌套结构。
    """

    def __init__(self, flattener: SchemaFlattener):
        self.flattener = flattener

    def run(self, resp: LLMResponse) -> LLMResponse:
        if not resp.tool_calls:
            return resp

        for tc in resp.tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})

            if not isinstance(args, dict):
                continue

            # 检查是否有 dot-notation 的 key
            if not any('.' in str(k) for k in args.keys()):
                continue

            if self.flattener.is_flattened(name):
                tc["arguments"] = self.flattener.unflatten_args(name, args)

        return resp


class StormPass:
    """
    Pass 4: 滑动窗口内重复调用去重。

    如果同一个工具+参数在短时间内被重复调用，只保留第一次，
    抑制后续重复调用。
    """

    def __init__(self, window_size: int = 10):
        self._window: deque[tuple[str, str]] = deque(maxlen=window_size)
        self._window_size = window_size

    def run(self, resp: LLMResponse) -> LLMResponse:
        if not resp.tool_calls:
            return resp

        deduped = []
        suppressed = []

        for tc in resp.tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {})

            try:
                args_hash = hashlib.sha256(
                    json.dumps(args, sort_keys=True).encode()
                ).hexdigest()[:16]
            except (TypeError, ValueError):
                args_hash = ""

            key = (name, args_hash)
            if key in self._window:
                # 抑制重复调用，刷新窗口位置
                self._window.append(key)
                suppressed.append(tc)
                continue

            self._window.append(key)
            deduped.append(tc)

        if suppressed:
            # 在 content 中追加反思提示
            reflection = f"\n[Storm: suppressed {len(suppressed)} duplicate call(s) — {suppressed[0].get('name', '?')}]"
            resp.content = (resp.content or "") + reflection

        resp.tool_calls = deduped
        return resp

    def reset(self):
        """重置窗口。在回合边界调用"""
        self._window.clear()


class ToolCallRepairPipeline:
    """
    工具调用修复流水线。

    用法:
        pipeline = ToolCallRepairPipeline(tools)
        resp = llm.chat(messages, tools=schemas)
        resp = pipeline.repair(resp)
        # 现在 resp.tool_calls 已经过四层修复
    """

    def __init__(self, tools: ToolRegistry, enable_flatten: bool = True,
                 storm_window: int = 10):
        self.scavenge = ScavengePass(tools)
        self.truncation = TruncationFixPass()
        self.storm = StormPass(window_size=storm_window)

        self._flattener: Optional[SchemaFlattener] = None
        self._flatten_enabled = enable_flatten
        if enable_flatten:
            self._flattener = SchemaFlattener(tools)

    @property
    def flattener(self) -> Optional[SchemaFlattener]:
        return self._flattener

    def get_schemas_for_model(self) -> list[dict]:
        """获取给模型看的 schema 列表（可能已扁平化）"""
        if self._flattener:
            return self._flattener.get_schemas_for_model()
        # fallback: 返回原始 schemas
        return []

    def repair(self, resp: LLMResponse) -> LLMResponse:
        """
        对 LLM 响应执行四层修复。
        顺序：Scavenge -> Truncation -> Flatten -> Storm
        """
        # Pass 1: Scavenge
        resp = self.scavenge.run(resp)

        # Pass 2: Truncation
        resp = self.truncation.run(resp)

        # Pass 3: Flatten (反扁平化)
        if self._flattener:
            flatten_pass = FlattenPass(self._flattener)
            resp = flatten_pass.run(resp)

        # Pass 4: Storm
        resp = self.storm.run(resp)

        return resp

    def reset_turn(self):
        """回合边界：重置有状态的 pass"""
        self.storm.reset()
