"""
测试 Tool-Call Repair Pipeline
==============================
覆盖 Scavenge、Truncation、Flatten、Storm 四层修复通道
"""
import pytest
from xagent.core.llm_client import LLMResponse
from xagent.core.repair_pipeline import (
    ToolCallRepairPipeline,
    ScavengePass,
    TruncationFixPass,
    StormPass,
    SchemaFlattener,
)


class MockToolRegistry:
    """模拟 ToolRegistry"""

    def __init__(self, schemas=None, tool_names=None):
        self._schemas = schemas or []
        self._tool_names = set(tool_names or ["read_file", "write_file", "run_command"])

    def get_schemas(self):
        return self._schemas

    def has_tool(self, name):
        return name in self._tool_names

    def list_tools(self):
        return list(self._tool_names)


@pytest.fixture
def mock_tools():
    return MockToolRegistry(
        schemas=[
            {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "write_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
        ],
        tool_names=["read_file", "write_file"],
    )


class TestScavengePass:
    """Scavenge 测试：从 reasoning_content 回收遗漏 tool calls"""

    def test_no_reasoning_noop(self, mock_tools):
        resp = LLMResponse(content="hi", tool_calls=[{"name": "read_file", "arguments": {}}])
        p = ScavengePass(mock_tools)
        result = p.run(resp)
        assert len(result.tool_calls) == 1

    def test_scavenge_from_reasoning(self, mock_tools):
        resp = LLMResponse(
            content="Let me read the file",
            reasoning='I need to check the file content first.\n```tool\n{"name": "read_file", "arguments": {"path": "/tmp/test.txt"}}\n```\nThen analyze it.',
            tool_calls=[],
        )
        p = ScavengePass(mock_tools)
        result = p.run(resp)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "read_file"
        assert result.tool_calls[0]["arguments"]["path"] == "/tmp/test.txt"

    def test_scavenge_append_not_replace(self, mock_tools):
        resp = LLMResponse(
            content="ok",
            reasoning='```tool\n{"name": "read_file", "arguments": {"path": "/a.txt"}}\n```',
            tool_calls=[{"name": "write_file", "arguments": {"path": "/b.txt"}}],
        )
        p = ScavengePass(mock_tools)
        result = p.run(resp)
        assert len(result.tool_calls) == 2

    def test_scavenge_invalid_json_ignored(self, mock_tools):
        resp = LLMResponse(
            content="ok",
            reasoning='```tool\n{this is not json}\n```',
            tool_calls=[],
        )
        p = ScavengePass(mock_tools)
        result = p.run(resp)
        assert len(result.tool_calls) == 0


class TestTruncationFixPass:
    """Truncation 测试：修复 JSON 截断"""

    def test_no_truncation_noop(self):
        resp = LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}])
        p = TruncationFixPass()
        result = p.run(resp)
        assert result.tool_calls[0]["arguments"] == {"path": "/a"}

    def test_fix_unclosed_brace(self):
        resp = LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"_raw": '{"path": "/a"'}}])
        p = TruncationFixPass()
        result = p.run(resp)
        assert result.tool_calls[0]["arguments"] == {"path": "/a"}

    def test_fix_trailing_comma(self):
        resp = LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"_raw": '{"path": "/a",}'}}])
        p = TruncationFixPass()
        result = p.run(resp)
        assert result.tool_calls[0]["arguments"] == {"path": "/a"}

    def test_fix_unclosed_bracket(self):
        resp = LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"_raw": '{"items": [1, 2'}}])
        p = TruncationFixPass()
        result = p.run(resp)
        assert result.tool_calls[0]["arguments"] == {"items": [1, 2]}

    def test_unfixable_returns_none(self):
        resp = LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"_raw": '{"path": '}}])
        p = TruncationFixPass()
        result = p.run(resp)
        # unfixable, keep _raw
        assert "_raw" in result.tool_calls[0]["arguments"]


class TestStormPass:
    """Storm 测试：滑动窗口去重"""

    def test_no_duplicates_noop(self):
        resp = LLMResponse(tool_calls=[
            {"name": "read_file", "arguments": {"path": "/a"}},
            {"name": "read_file", "arguments": {"path": "/b"}},
        ])
        p = StormPass(window_size=5)
        result = p.run(resp)
        assert len(result.tool_calls) == 2

    def test_deduplicates_identical_calls(self):
        resp = LLMResponse(tool_calls=[
            {"name": "read_file", "arguments": {"path": "/a"}},
            {"name": "read_file", "arguments": {"path": "/a"}},  # duplicate
        ])
        p = StormPass(window_size=5)
        result = p.run(resp)
        assert len(result.tool_calls) == 1
        assert "Storm" in (result.content or "")

    def test_window_respects_size(self):
        p = StormPass(window_size=2)
        # First call
        r1 = p.run(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}]))
        assert len(r1.tool_calls) == 1

        # Different call
        r2 = p.run(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/b"}}]))
        assert len(r2.tool_calls) == 1

        # Third call, different -> /a falls out of window
        r3 = p.run(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/c"}}]))
        assert len(r3.tool_calls) == 1

        # Fourth call, same as first -> /a not in window [(/b), (/c)], passes
        r4 = p.run(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}]))
        assert len(r4.tool_calls) == 1

    def test_reset_clears_window(self):
        p = StormPass(window_size=5)
        p.run(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}]))
        p.reset()
        r = p.run(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}]))
        assert len(r.tool_calls) == 1  # not deduped after reset


class TestSchemaFlattener:
    """Flatten 测试：schema 扁平化与反扁平化"""

    def test_shallow_schema_not_flattened(self):
        """浅层 schema 不应被扁平化"""
        tools = MockToolRegistry(schemas=[
            {"type": "function", "function": {"name": "simple", "parameters": {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            }}},
        ])
        f = SchemaFlattener(tools)
        assert not f.is_flattened("simple")

    def test_deep_schema_flattened(self):
        """深层 schema 应被扁平化"""
        tools = MockToolRegistry(schemas=[
            {"type": "function", "function": {"name": "deep", "parameters": {
                "type": "object",
                "properties": {
                    "user": {"type": "object", "properties": {
                        "profile": {"type": "object", "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                            "address": {"type": "string"},
                            "city": {"type": "string"},
                            "country": {"type": "string"},
                            "zip": {"type": "string"},
                            "bio": {"type": "string"},
                            "avatar": {"type": "string"},
                            "website": {"type": "string"},
                        }},
                    }},
                },
            }}},
        ])
        f = SchemaFlattener(tools)
        assert f.is_flattened("deep")

    def test_unflatten_args(self):
        tools = MockToolRegistry(schemas=[
            {"type": "function", "function": {"name": "update_user", "parameters": {
                "type": "object",
                "properties": {
                    "user": {"type": "object", "properties": {
                        "profile": {"type": "object", "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                        }},
                    }},
                },
            }}},
        ])
        f = SchemaFlattener(tools)
        flat_args = {"user.profile.name": "Alice", "user.profile.age": 30}
        nested = f.unflatten_args("update_user", flat_args)
        assert nested == {"user": {"profile": {"name": "Alice", "age": 30}}}

    def test_unflatten_non_flattened_tool(self):
        """对未被扁平化的工具，unflatten 应返回原样"""
        tools = MockToolRegistry(schemas=[
            {"type": "function", "function": {"name": "simple", "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            }}},
        ])
        f = SchemaFlattener(tools)
        args = {"path": "/tmp"}
        result = f.unflatten_args("simple", args)
        assert result == args


class TestToolCallRepairPipeline:
    """整合测试：完整流水线"""

    def test_full_pipeline(self, mock_tools):
        pipeline = ToolCallRepairPipeline(mock_tools, enable_flatten=False, storm_window=5)
        resp = LLMResponse(
            content="",
            tool_calls=[
                {"name": "read_file", "arguments": {"path": "/a"}},
                {"name": "read_file", "arguments": {"path": "/a"}},  # duplicate
            ],
        )
        result = pipeline.repair(resp)
        # Storm 应去重
        assert len(result.tool_calls) == 1

    def test_pipeline_with_scavenge_and_storm(self, mock_tools):
        pipeline = ToolCallRepairPipeline(mock_tools, enable_flatten=False, storm_window=5)
        resp = LLMResponse(
            content="",
            reasoning='```tool\n{"name": "read_file", "arguments": {"path": "/scavenged.txt"}}\n```',
            tool_calls=[],
        )
        result = pipeline.repair(resp)
        # Scavenge 应回收
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "read_file"

    def test_pipeline_with_truncation(self, mock_tools):
        pipeline = ToolCallRepairPipeline(mock_tools, enable_flatten=False, storm_window=5)
        resp = LLMResponse(
            content="",
            tool_calls=[{"name": "read_file", "arguments": {"_raw": '{"path": "/truncated"'}}],
        )
        result = pipeline.repair(resp)
        # Truncation 应修复
        assert result.tool_calls[0]["arguments"] == {"path": "/truncated"}

    def test_reset_turn(self, mock_tools):
        pipeline = ToolCallRepairPipeline(mock_tools, enable_flatten=False, storm_window=5)
        # 第一回合
        r1 = pipeline.repair(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}]))
        assert len(r1.tool_calls) == 1

        # 第二回合，相同调用
        r2 = pipeline.repair(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}]))
        assert len(r2.tool_calls) == 0  # deduped

        # 重置
        pipeline.reset_turn()

        # 第三回合，相同调用
        r3 = pipeline.repair(LLMResponse(tool_calls=[{"name": "read_file", "arguments": {"path": "/a"}}]))
        assert len(r3.tool_calls) == 1  # not deduped after reset
