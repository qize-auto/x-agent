"""
缓存优先的上下文管理 (Cache-First Context)
==========================================
核心设计：三区域上下文模型，确保 DeepSeek prefix caching 最大化命中。

分区：
- ImmutablePrefix: 静态前缀（system + tools + few_shots），session 开始时锁定
- AppendOnlyLog: 单调增长的消息日志，只允许追加
- VolatileScratch: 轮次级临时状态，每轮清空

参考：DeepSeek-Reasonix 的 Cache-First Loop 设计
"""
from __future__ import annotations
import copy
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ImmutablePrefix:
    """
    不可变前缀。构造后任何修改尝试都会失败。
    用于保证缓存前缀的绝对字节级稳定。

    关键不变量：
    1. system_content 不含任何动态变量（时间、目录、记忆等）
    2. tool_schemas 使用 tuple 且按名称排序，保证序列化一致性
    3. JSON 序列化使用 sort_keys，消除 dict 遍历顺序不确定性
    """
    system_content: str
    tool_schemas: tuple[dict, ...] = ()
    few_shots: tuple[dict, ...] = ()
    _fingerprint: str = field(init=False, repr=False)

    def __post_init__(self):
        # frozen=True 时需要用 object.__setattr__ 设置字段
        fp = self._compute_hash()
        object.__setattr__(self, '_fingerprint', fp)

    def to_messages(self) -> list[dict]:
        """生成前缀消息列表（OpenAI 兼容格式）。"""
        msgs = [{"role": "system", "content": self.system_content}]
        msgs.extend(list(self.few_shots))
        return msgs

    def to_api_tools(self) -> list[dict]:
        """返回 API 用的工具 schema 列表（tuple -> list）。"""
        return list(self.tool_schemas)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def _compute_hash(self) -> str:
        """计算 SHA-256 指纹，用于调试和快速比较。"""
        # 先对 schema 做稳定排序，消除传入顺序的影响
        sorted_schemas = tuple(sorted(
            self.tool_schemas,
            key=lambda s: json.dumps(s, sort_keys=True)
        ))
        content = self.system_content + json.dumps(sorted_schemas, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]


class AppendOnlyLog:
    """
    只允许追加的消息日志。禁止插入、修改、删除。
    这是缓存命中的核心 invariant：历史消息一旦写入就永不改变，
    确保后续请求的前缀能严格匹配。
    """

    def __init__(self):
        self._messages: list[dict] = []
        self._lock = threading.RLock()
        self._append_count: int = 0

    def append(self, message: dict) -> None:
        """追加一条消息。唯一允许的写操作。"""
        with self._lock:
            # 深拷贝防止外部修改已写入的消息
            self._messages.append(copy.deepcopy(message))
            self._append_count += 1

    def extend(self, messages: list[dict]) -> None:
        """批量追加（原子操作）。"""
        with self._lock:
            for m in messages:
                self._messages.append(copy.deepcopy(m))
            self._append_count += len(messages)

    def snapshot(self, n: Optional[int] = None) -> list[dict]:
        """
        获取当前日志的副本。

        Args:
            n: 如果指定，只返回前 n 条。用于上下文窗口管理。
        """
        with self._lock:
            msgs = self._messages[:n] if n is not None else self._messages
            return copy.deepcopy(msgs)

    def clear(self) -> None:
        """清空日志。用于 run() 重置（Q3: 预热请求模式）。"""
        with self._lock:
            self._messages.clear()
            self._append_count = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._messages)

    def __getitem__(self, idx):
        raise TypeError("AppendOnlyLog does not support item access. Use snapshot().")

    def __setitem__(self, idx, value):
        raise TypeError("AppendOnlyLog is append-only. Use append() or extend().")

    def __repr__(self):
        return f"AppendOnlyLog(length={len(self)}, appends={self._append_count})"


@dataclass
class VolatileScratch:
    """
    每轮重置的临时状态。从不直接进入 API 请求。
    轮次结束后，通过提炼（distillation）决定是否写入 AppendOnlyLog。

    DeepSeek 官方建议：不要把 reasoning_content 喂回模型。
    Scratch 的内容必须经过提炼才能进入 Log。
    """
    reasoning_content: str = ""
    memory_results: list[dict] = field(default_factory=list)
    current_cwd: str = ""
    plan_state: dict = field(default_factory=dict)
    tool_outputs_raw: list[dict] = field(default_factory=list)

    def clear(self) -> None:
        """轮次边界：清空所有临时状态。"""
        self.reasoning_content = ""
        self.memory_results = []
        self.current_cwd = ""
        self.plan_state = {}
        self.tool_outputs_raw = []
