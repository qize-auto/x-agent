"""
Tests for Telemetry & Profiling module
"""
import json
import time
import pytest
from pathlib import Path

from xagent.core.telemetry.spans import LLMCallSpan, ToolCallSpan, AgentTrace, TraceStep
from xagent.core.telemetry.exporter import JSONLExporter, ConsoleExporter
from xagent.core.telemetry.collector import TelemetryCollector


class TestSpans:
    def test_llm_call_span_to_dict(self):
        span = LLMCallSpan(
            model="gpt-4o",
            provider="openrouter",
            prompt_tokens=10,
            completion_tokens=5,
            cached_tokens=2,
            ttft_ms=150.5,
            total_latency_ms=1200.0,
            cost_usd=0.0001,
        )
        d = span.to_dict()
        assert d["type"] == "llm_call"
        assert d["model"] == "gpt-4o"
        assert d["prompt_tokens"] == 10
        assert d["completion_tokens"] == 5
        assert d["cached_tokens"] == 2
        assert d["ttft_ms"] == 150.5
        assert d["cost_usd"] == 0.0001
        assert d["error"] is None

    def test_tool_call_span_to_dict(self):
        span = ToolCallSpan(
            tool_name="read_file",
            arguments={"path": "test.py"},
            result_preview="ok",
            latency_ms=45.0,
        )
        d = span.to_dict()
        assert d["type"] == "tool_call"
        assert d["tool_name"] == "read_file"
        assert d["arguments"]["path"] == "test.py"
        assert d["latency_ms"] == 45.0

    def test_agent_trace_lifecycle(self):
        trace = AgentTrace(user_input="hello")
        assert trace.trace_id
        assert trace.start_time > 0

        trace.add_step("llm", "gpt-4o", 1000.0, {"tokens": 100})
        trace.add_step("tool", "read_file", 50.0)

        llm = LLMCallSpan(model="gpt-4o", provider="openrouter", prompt_tokens=10, completion_tokens=5, cost_usd=0.0001)
        trace.add_llm_span(llm)

        tool = ToolCallSpan(tool_name="read_file", latency_ms=50.0)
        trace.add_tool_span(tool)

        trace.finish("final response")
        assert trace.end_time > 0
        assert trace.total_cost_usd == 0.0001
        assert trace.total_tokens == 15
        assert len(trace.steps) == 2
        assert len(trace.llm_spans) == 1
        assert len(trace.tool_spans) == 1

        d = trace.to_dict()
        assert d["user_input"] == "hello"
        assert d["total_tokens"] == 15
        assert len(d["steps"]) == 2

    def test_agent_trace_summary_table(self):
        trace = AgentTrace(user_input="test")
        trace.finish("done")
        summary = trace.summary_table()
        assert "Trace:" in summary
        assert "test" in summary
        assert "ms" in summary


class TestExporters:
    def test_jsonl_exporter(self, tmp_path):
        filepath = tmp_path / "traces.jsonl"
        exporter = JSONLExporter(filepath)
        trace = AgentTrace(user_input="hi")
        trace.finish("hello")
        exporter.export_trace(trace)

        lines = filepath.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["user_input"] == "hi"
        assert data["final_response"] == "hello"

    def test_console_exporter_verbose(self, capsys):
        exporter = ConsoleExporter(verbose=False)
        trace = AgentTrace(user_input="hi")
        trace.finish("hello")
        exporter.export_trace(trace)
        captured = capsys.readouterr()
        assert "Trace:" in captured.out


class TestCollector:
    def test_collector_disabled(self):
        collector = TelemetryCollector(exporters=[], sample_rate=0.0)
        trace = collector.start_trace("test")
        assert trace is None

    def test_collector_start_finish(self, tmp_path):
        filepath = tmp_path / "traces.jsonl"
        exporter = JSONLExporter(filepath)
        collector = TelemetryCollector(exporters=[exporter], sample_rate=1.0)

        trace = collector.start_trace("user input")
        assert trace is not None
        assert trace.trace_id in collector._active_traces

        llm = LLMCallSpan(model="gpt-4o", provider="openrouter", prompt_tokens=10, completion_tokens=5, cost_usd=0.0001)
        collector.record_llm(trace, llm)

        tool = ToolCallSpan(tool_name="read_file", latency_ms=50.0)
        collector.record_tool(trace, tool)

        collector.finish_trace(trace, "final")
        assert trace.trace_id not in collector._active_traces

        stats = collector.get_stats()
        assert stats["traces_collected"] == 1
        assert stats["traces_exported"] == 1
        assert stats["total_llm_calls"] == 1
        assert stats["total_tool_calls"] == 1
        assert stats["total_cost_usd"] == pytest.approx(0.0001)

        lines = filepath.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["user_input"] == "user input"

    def test_collector_from_config_enabled(self, tmp_path):
        config = {
            "telemetry": {
                "enabled": True,
                "backend": "jsonl",
                "profile_dir": str(tmp_path),
                "sample_rate": 1.0,
            }
        }
        collector = TelemetryCollector.from_config(config)
        assert collector.sample_rate == 1.0
        assert len(collector.exporters) == 1

    def test_collector_from_config_disabled(self):
        config = {"telemetry": {"enabled": False}}
        collector = TelemetryCollector.from_config(config)
        assert collector.sample_rate == 0.0
        assert len(collector.exporters) == 0

    def test_collector_shutdown(self):
        collector = TelemetryCollector(exporters=[], sample_rate=1.0)
        collector.shutdown()  # 不应抛出异常


class TestLLMClientTelemetry:
    def test_llm_response_has_latency(self):
        from xagent.core.llm_client import LLMResponse
        resp = LLMResponse(content="hi", latency_ms=1200.0, ttft_ms=150.0)
        assert resp.latency_ms == 1200.0
        assert resp.ttft_ms == 150.0


class TestAgentLoopTelemetryIntegration:
    def test_agent_loop_accepts_telemetry(self, tmp_path):
        from xagent.core.agent_loop import AgentLoop
        from xagent.core.llm_client import LLMClient
        from xagent.core.tool_registry import ToolRegistry
        from xagent.core.memory_engine import MemoryEngine
        from xagent.core.telemetry import TelemetryCollector

        collector = TelemetryCollector(exporters=[], sample_rate=1.0)
        # Mock LLM
        llm = type("FakeLLM", (), {
            "model_id": "test",
            "provider": "test",
            "chat": lambda *a, **k: type("FakeResp", (), {
                "content": "ok",
                "reasoning": "",
                "tool_calls": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                "latency_ms": 100.0,
                "ttft_ms": 20.0,
            })(),
            "get_cost_estimate": lambda self, u: 0.0,
        })()

        tools = ToolRegistry()
        memory = MemoryEngine(persist_dir=str(tmp_path / "memory"))
        loop = AgentLoop(
            llm=llm, tools=tools, memory=memory,
            telemetry_collector=collector,
        )
        assert loop._telemetry is collector

    def test_agent_loop_run_records_trace(self, tmp_path):
        from xagent.core.agent_loop import AgentLoop
        from xagent.core.tool_registry import ToolRegistry
        from xagent.core.memory_engine import MemoryEngine
        from xagent.core.telemetry import TelemetryCollector, JSONLExporter

        filepath = tmp_path / "traces.jsonl"
        exporter = JSONLExporter(filepath)
        collector = TelemetryCollector(exporters=[exporter], sample_rate=1.0)

        # Mock LLM with proper response object
        def fake_chat(*a, **k):
            resp = type("FakeResp", (), {})()
            resp.content = "hello"
            resp.reasoning = ""
            resp.tool_calls = []
            resp.usage = {"prompt_tokens": 5, "completion_tokens": 3}
            resp.latency_ms = 100.0
            resp.ttft_ms = 20.0
            return resp

        llm = type("FakeLLM", (), {
            "model_id": "test",
            "provider": "test",
            "chat": fake_chat,
            "get_cost_estimate": lambda self, u: 0.0,
        })()

        tools = ToolRegistry()
        memory = MemoryEngine(persist_dir=str(tmp_path / "memory"))
        loop = AgentLoop(
            llm=llm, tools=tools, memory=memory,
            telemetry_collector=collector,
        )

        result = loop.run("hi")
        assert result == "hello"

        # 给导出一点时间（虽然是同步的）
        stats = collector.get_stats()
        assert stats["traces_collected"] == 1
        assert stats["traces_exported"] == 1

        lines = filepath.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["user_input"] == "hi"
        assert data["final_response"] == "hello"
        assert len(data["llm_calls"]) == 1
