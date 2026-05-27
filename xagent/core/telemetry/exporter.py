"""
Telemetry 导出器
===============
支持 JSONL 本地文件、控制台、OpenTelemetry 三种导出方式。
"""
from __future__ import annotations
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .spans import AgentTrace


class Exporter(ABC):
    """导出器抽象基类"""

    @abstractmethod
    def export_trace(self, trace: AgentTrace) -> None:
        ...

    def shutdown(self) -> None:
        pass


class JSONLExporter(Exporter):
    """
    将 Trace 追加写入 JSONL 文件。
    每行一个 JSON 对象，便于后续用 jq / pandas 分析。
    """

    def __init__(self, filepath: str | Path):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

    def export_trace(self, trace: AgentTrace) -> None:
        line = json.dumps(trace.to_dict(), ensure_ascii=False, default=str)
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class ConsoleExporter(Exporter):
    """直接打印到 stdout（调试用）"""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def export_trace(self, trace: AgentTrace) -> None:
        print("\n" + "=" * 60)
        print(trace.summary_table())
        if self.verbose:
            print(json.dumps(trace.to_dict(), indent=2, ensure_ascii=False, default=str))
        print("=" * 60)


class OpenTelemetryExporter(Exporter):
    """
    OpenTelemetry 协议导出器（预留接口）。

    实际使用时需要安装 opentelemetry-sdk / opentelemetry-exporter-otlp。
    当前实现为 no-op，避免硬依赖。
    """

    def __init__(self, endpoint: str = "", headers: dict = None):
        self.endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        self.headers = headers or {}
        self._tracer = None

    def _ensure_tracer(self):
        if self._tracer is not None:
            return True
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider = TracerProvider()
            exporter = OTLPSpanExporter(
                endpoint=self.endpoint + "/v1/traces",
                headers=self.headers,
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("xagent")
            return True
        except ImportError:
            return False

    def export_trace(self, trace_obj: AgentTrace) -> None:
        if not self._ensure_tracer():
            # 降级为静默跳过
            return
        with self._tracer.start_as_current_span("agent.run", attributes={
            "agent.trace_id": trace_obj.trace_id,
            "agent.user_input": trace_obj.user_input[:200],
        }) as span:
            span.set_attribute("agent.total_cost_usd", trace_obj.total_cost_usd)
            span.set_attribute("agent.total_tokens", trace_obj.total_tokens)
            for ls in trace_obj.llm_spans:
                with self._tracer.start_span("llm.chat") as child:
                    child.set_attribute("gen_ai.system", ls.provider)
                    child.set_attribute("gen_ai.request.model", ls.model)
                    child.set_attribute("gen_ai.usage.input_tokens", ls.prompt_tokens)
                    child.set_attribute("gen_ai.usage.output_tokens", ls.completion_tokens)
                    child.set_attribute("gen_ai.usage.cached_tokens", ls.cached_tokens)
