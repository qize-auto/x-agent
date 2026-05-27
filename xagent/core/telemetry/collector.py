"""
Telemetry 收集器
===============
统一管理 Trace 的收集、聚合和导出。
线程安全，支持多线程 AgentLoop。
"""
from __future__ import annotations
import threading
import time
from typing import Optional

from .spans import AgentTrace, LLMCallSpan, ToolCallSpan
from .exporter import Exporter, JSONLExporter, ConsoleExporter


class TelemetryCollector:
    """
    Telemetry 收集器。

    用法:
        collector = TelemetryCollector.from_config(config)
        trace = collector.start_trace("用户输入")
        # ... Agent 循环 ...
        collector.finish_trace(trace, "最终回复")
    """

    def __init__(self, exporters: list[Exporter] = None, sample_rate: float = 1.0):
        self.exporters = exporters or []
        self.sample_rate = max(0.0, min(1.0, sample_rate))
        self._lock = threading.Lock()
        self._active_traces: dict[str, AgentTrace] = {}
        self._stats = {
            "traces_collected": 0,
            "traces_exported": 0,
            "total_llm_calls": 0,
            "total_tool_calls": 0,
            "total_cost_usd": 0.0,
        }

    @classmethod
    def from_config(cls, config: dict) -> "TelemetryCollector":
        """从配置创建收集器"""
        tel_cfg = config.get("telemetry", {}) if isinstance(config, dict) else {}
        if not tel_cfg.get("enabled", False):
            return cls(exporters=[], sample_rate=0.0)

        exporters = []
        backend = tel_cfg.get("backend", "jsonl")
        profile_dir = Path(tel_cfg.get("profile_dir", "~/.xagent/profiles")).expanduser()
        profile_dir.mkdir(parents=True, exist_ok=True)

        if backend == "jsonl":
            exporters.append(JSONLExporter(profile_dir / f"traces_{int(time.time())}.jsonl"))
        elif backend == "console":
            exporters.append(ConsoleExporter(verbose=tel_cfg.get("verbose", False)))
        elif backend == "otel":
            from .exporter import OpenTelemetryExporter
            exporters.append(OpenTelemetryExporter(
                endpoint=tel_cfg.get("otel_endpoint", ""),
                headers=tel_cfg.get("otel_headers", {}),
            ))
        elif backend == "combined":
            exporters.append(JSONLExporter(profile_dir / f"traces_{int(time.time())}.jsonl"))
            exporters.append(ConsoleExporter(verbose=tel_cfg.get("verbose", False)))

        return cls(
            exporters=exporters,
            sample_rate=tel_cfg.get("sample_rate", 1.0),
        )

    def start_trace(self, user_input: str) -> Optional[AgentTrace]:
        """开始一次 Trace 记录"""
        if self.sample_rate <= 0.0:
            return None
        trace = AgentTrace(user_input=user_input)
        with self._lock:
            self._active_traces[trace.trace_id] = trace
            self._stats["traces_collected"] += 1
        return trace

    def finish_trace(self, trace: AgentTrace, final_response: str = "", error: Optional[str] = None):
        """结束并导出 Trace"""
        if trace is None:
            return
        trace.finish(final_response=final_response, error=error)
        with self._lock:
            self._active_traces.pop(trace.trace_id, None)
            self._stats["traces_exported"] += 1
            self._stats["total_llm_calls"] += len(trace.llm_spans)
            self._stats["total_tool_calls"] += len(trace.tool_spans)
            self._stats["total_cost_usd"] += trace.total_cost_usd
        for exporter in self.exporters:
            try:
                exporter.export_trace(trace)
            except Exception:
                pass  # 导出失败不应影响主流程

    def record_llm(self, trace: AgentTrace, span: LLMCallSpan):
        """记录 LLM 调用到当前 Trace"""
        if trace is None or span is None:
            return
        trace.add_llm_span(span)

    def record_tool(self, trace: AgentTrace, span: ToolCallSpan):
        """记录工具调用到当前 Trace"""
        if trace is None or span is None:
            return
        trace.add_tool_span(span)

    def get_stats(self) -> dict:
        """获取收集器统计"""
        with self._lock:
            return dict(self._stats)

    def shutdown(self):
        """优雅关闭，等待导出完成"""
        for exporter in self.exporters:
            try:
                exporter.shutdown()
            except Exception:
                pass


# 兼容 typing 导入
from pathlib import Path
