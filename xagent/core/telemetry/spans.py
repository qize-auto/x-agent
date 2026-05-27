"""
Telemetry 数据结构
=================
定义 LLM 调用、工具执行、Agent 循环的观测数据结构。
"""
from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMCallSpan:
    """单次 LLM API 调用的观测数据"""
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    ttft_ms: float = 0.0           # Time To First Token
    total_latency_ms: float = 0.0
    cost_usd: float = 0.0
    timestamp: float = field(default_factory=time.time)
    call_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    trace_id: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": "llm_call",
            "call_id": self.call_id,
            "trace_id": self.trace_id,
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "ttft_ms": round(self.ttft_ms, 2),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "cost_usd": round(self.cost_usd, 6),
            "timestamp": self.timestamp,
            "error": self.error,
        }


@dataclass
class ToolCallSpan:
    """单次工具执行的观测数据"""
    tool_name: str
    arguments: dict = field(default_factory=dict)
    result_preview: str = ""
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    call_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    trace_id: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": "tool_call",
            "call_id": self.call_id,
            "trace_id": self.trace_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result_preview": self.result_preview[:200],
            "latency_ms": round(self.latency_ms, 2),
            "timestamp": self.timestamp,
            "error": self.error,
        }


@dataclass
class TraceStep:
    """Agent 循环中的一个步骤"""
    step_type: str          # "llm" | "tool" | "cache_hit" | "thinking" | "error"
    name: str = ""          # tool_name / model_name
    latency_ms: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "step_type": self.step_type,
            "name": self.name,
            "latency_ms": round(self.latency_ms, 2),
            "metadata": self.metadata,
        }


@dataclass
class AgentTrace:
    """一次完整用户请求的追踪"""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    user_input: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    steps: list[TraceStep] = field(default_factory=list)
    llm_spans: list[LLMCallSpan] = field(default_factory=list)
    tool_spans: list[ToolCallSpan] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    final_response: str = ""
    error: Optional[str] = None

    def add_step(self, step_type: str, name: str = "", latency_ms: float = 0.0, metadata: dict = None):
        self.steps.append(TraceStep(
            step_type=step_type,
            name=name,
            latency_ms=latency_ms,
            metadata=metadata or {},
        ))

    def add_llm_span(self, span: LLMCallSpan):
        span.trace_id = self.trace_id
        self.llm_spans.append(span)
        self.total_cost_usd += span.cost_usd
        self.total_tokens += span.prompt_tokens + span.completion_tokens

    def add_tool_span(self, span: ToolCallSpan):
        span.trace_id = self.trace_id
        self.tool_spans.append(span)

    def finish(self, final_response: str = "", error: Optional[str] = None):
        self.end_time = time.time()
        self.final_response = final_response
        self.error = error

    @property
    def total_latency_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "user_input": self.user_input,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "final_response": self.final_response[:200],
            "error": self.error,
            "steps": [s.to_dict() for s in self.steps],
            "llm_calls": [s.to_dict() for s in self.llm_spans],
            "tool_calls": [s.to_dict() for s in self.tool_spans],
        }

    def summary_table(self) -> str:
        """生成人类可读的摘要表格"""
        lines = [
            f"Trace: {self.trace_id}",
            f"  Input: {self.user_input[:60]}..." if len(self.user_input) > 60 else f"  Input: {self.user_input}",
            f"  Latency: {self.total_latency_ms:.0f} ms",
            f"  Cost: ${self.total_cost_usd:.6f}",
            f"  Tokens: {self.total_tokens}",
            f"  LLM calls: {len(self.llm_spans)} | Tool calls: {len(self.tool_spans)}",
        ]
        if self.error:
            lines.append(f"  Error: {self.error}")
        for s in self.steps:
            lines.append(f"    [{s.step_type}] {s.name} ({s.latency_ms:.0f}ms)")
        return "\n".join(lines)
