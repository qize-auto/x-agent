"""
测试 Cache-First Context 组件
==============================
覆盖 ImmutablePrefix、AppendOnlyLog、VolatileScratch
"""
import pytest
import json

from xagent.core.cache_context import ImmutablePrefix, AppendOnlyLog, VolatileScratch


class TestImmutablePrefix:
    """不可变前缀测试"""

    def test_basic_construction(self):
        prefix = ImmutablePrefix(
            system_content="You are a test agent.",
            tool_schemas=(),
            few_shots=(),
        )
        assert prefix.system_content == "You are a test agent."
        assert len(prefix.fingerprint) == 16

    def test_immutable_frozen(self):
        prefix = ImmutablePrefix(system_content="test")
        with pytest.raises(FrozenInstanceError):
            prefix.system_content = "changed"

    def test_to_messages_format(self):
        prefix = ImmutablePrefix(
            system_content="sys",
            few_shots=([{"role": "user", "content": "shot1"}]),
        )
        msgs = prefix.to_messages()
        assert msgs[0] == {"role": "system", "content": "sys"}
        assert msgs[1] == {"role": "user", "content": "shot1"}

    def test_fingerprint_consistency(self):
        """相同内容应产生相同指纹"""
        p1 = ImmutablePrefix(system_content="sys", tool_schemas=())
        p2 = ImmutablePrefix(system_content="sys", tool_schemas=())
        assert p1.fingerprint == p2.fingerprint

    def test_fingerprint_difference(self):
        """不同内容应产生不同指纹"""
        p1 = ImmutablePrefix(system_content="sys1")
        p2 = ImmutablePrefix(system_content="sys2")
        assert p1.fingerprint != p2.fingerprint

    def test_tool_schema_sorting(self):
        """schema 顺序不影响指纹（因为内部会排序）"""
        schema_a = {"function": {"name": "a"}, "type": "function"}
        schema_b = {"function": {"name": "b"}, "type": "function"}
        p1 = ImmutablePrefix(system_content="sys", tool_schemas=(schema_a, schema_b))
        p2 = ImmutablePrefix(system_content="sys", tool_schemas=(schema_b, schema_a))
        assert p1.fingerprint == p2.fingerprint

    def test_to_api_tools(self):
        schema = {"function": {"name": "test"}, "type": "function"}
        prefix = ImmutablePrefix(system_content="sys", tool_schemas=(schema,))
        tools = prefix.to_api_tools()
        assert tools == [schema]
        assert isinstance(tools, list)


class TestAppendOnlyLog:
    """追加日志测试"""

    def test_append_and_snapshot(self):
        log = AppendOnlyLog()
        log.append({"role": "user", "content": "hello"})
        log.append({"role": "assistant", "content": "hi"})

        snapshot = log.snapshot()
        assert len(snapshot) == 2
        assert snapshot[0]["content"] == "hello"
        assert snapshot[1]["content"] == "hi"

    def test_append_isolation(self):
        """外部修改不应影响已写入的消息"""
        log = AppendOnlyLog()
        msg = {"role": "user", "content": "original"}
        log.append(msg)
        msg["content"] = "modified"

        snapshot = log.snapshot()
        assert snapshot[0]["content"] == "original"

    def test_snapshot_isolation(self):
        """snapshot 副本不应影响内部状态"""
        log = AppendOnlyLog()
        log.append({"role": "user", "content": "hello"})

        snapshot = log.snapshot()
        snapshot[0]["content"] = "modified"

        snapshot2 = log.snapshot()
        assert snapshot2[0]["content"] == "hello"

    def test_extend(self):
        log = AppendOnlyLog()
        log.extend([
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ])
        assert len(log) == 2

    def test_clear(self):
        log = AppendOnlyLog()
        log.append({"role": "user", "content": "test"})
        log.clear()
        assert len(log) == 0
        assert log.snapshot() == []

    def test_getitem_blocked(self):
        log = AppendOnlyLog()
        log.append({"role": "user", "content": "x"})
        with pytest.raises(TypeError):
            _ = log[0]

    def test_setitem_blocked(self):
        log = AppendOnlyLog()
        with pytest.raises(TypeError):
            log[0] = {"role": "user", "content": "x"}

    def test_thread_safety_basic(self):
        """基本并发追加不应崩溃"""
        import threading
        log = AppendOnlyLog()

        def worker(n):
            for i in range(10):
                log.append({"role": "user", "content": f"{n}-{i}"})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(log) == 50


class TestVolatileScratch:
    """临时状态测试"""

    def test_clear(self):
        scratch = VolatileScratch()
        scratch.reasoning_content = "some reasoning"
        scratch.memory_results = [{"text": "memory"}]
        scratch.current_cwd = "/tmp"

        scratch.clear()
        assert scratch.reasoning_content == ""
        assert scratch.memory_results == []
        assert scratch.current_cwd == ""

    def test_default_empty(self):
        scratch = VolatileScratch()
        assert scratch.reasoning_content == ""
        assert scratch.memory_results == []
        assert scratch.plan_state == {}


# 兼容 Python 3.13+ 的 FrozenInstanceError
FrozenInstanceError = getattr(__builtins__, "FrozenInstanceError", AttributeError)
