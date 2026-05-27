"""
User Profile — 用户画像层
=========================
从契约历史中提取硬约束模式，形成用户偏好画像。

设计原则：
- 不依赖复杂 NLP，仅做约束文本频率统计
- 出现多次的约束视为用户"一贯偏好"
- 可作为 ClarificationEngine 的上下文输入
"""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import time


@dataclass
class UserProfile:
    """
    用户画像。

    从 MemoryEngine 的契约记录中聚合偏好模式。
    """

    # 约束文本 -> (出现次数, 最近出现时间)
    _constraint_stats: dict[str, tuple[int, float]] = field(default_factory=dict)

    def ingest_contracts(self, contracts: list[dict]):
        """
        从召回的契约记录中摄取约束。

        Args:
            contracts: memory.recall() 返回的结果列表
        """
        for item in contracts:
            meta = item.get("metadata", {})
            hc_raw = meta.get("hard_constraints", "[]")
            if isinstance(hc_raw, str):
                try:
                    hc_raw = json.loads(hc_raw)
                except Exception:
                    continue
            if isinstance(hc_raw, list):
                for constraint in hc_raw:
                    constraint = constraint.strip()
                    if not constraint:
                        continue
                    count, _ = self._constraint_stats.get(constraint, (0, 0))
                    self._constraint_stats[constraint] = (count + 1, time.time())

    def get_profile_hints(self, min_occurrences: int = 2) -> list[str]:
        """
        获取用户画像提示（出现次数 >= min_occurrences 的约束）。

        Returns:
            按出现次数排序的约束文本列表
        """
        frequent = [
            (constraint, count)
            for constraint, (count, _) in self._constraint_stats.items()
            if count >= min_occurrences
        ]
        frequent.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in frequent]

    def to_context_string(self, min_occurrences: int = 2) -> str:
        """转换为可注入 LLM 上下文的字符串"""
        hints = self.get_profile_hints(min_occurrences)
        if not hints:
            return ""
        lines = ["## User Profile (inferred from past contracts)"]
        for h in hints:
            count, _ = self._constraint_stats[h]
            lines.append(f"  - {h} (confirmed {count} times)")
        return "\n".join(lines)

    def is_empty(self) -> bool:
        """画像是否为空"""
        return len(self._constraint_stats) == 0
