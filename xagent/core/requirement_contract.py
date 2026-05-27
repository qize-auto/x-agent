"""
Requirement Contract — 需求契约
===============================
任务执行前与用户共同确认的结构化规格。

设计原则：
- 纯数据类，无外部依赖
- 支持版本化修订
- 可序列化（存入 TaskPlan / MemoryEngine）
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import json
import time


@dataclass
class RequirementContract:
    """需求契约——任务执行前的共同确认规格"""

    # 原始目标
    raw_goal: str

    # 澄清后的精确目标
    refined_goal: str = ""

    # 约束条件（不可协商）
    hard_constraints: list[str] = field(default_factory=list)
    # 例: ["必须用 Python", "不能引入新依赖", "兼容 Windows"]

    # 偏好条件（可协商）
    soft_preferences: list[str] = field(default_factory=list)
    # 例: ["prefer 简洁实现", "希望有单元测试"]

    # 范围边界（明确不在范围内）
    out_of_scope: list[str] = field(default_factory=list)
    # 例: ["不做前端界面", "不处理并发"]

    # 验收标准
    acceptance_criteria: list[str] = field(default_factory=list)
    # 例: ["所有测试通过", "代码覆盖率 > 80%"]

    # 契约建立过程中的 Q&A
    clarifications: list[dict] = field(default_factory=list)
    # [{"question": "...", "answer": "..."}, ...]

    # 契约版本（支持修订）
    version: int = 1

    # 用户确认状态
    confirmed: bool = False
    confirmed_at: float = 0.0

    def confirm(self) -> None:
        """用户确认契约"""
        self.confirmed = True
        self.confirmed_at = time.time()

    def revise(self, new_clarifications: list[dict]) -> "RequirementContract":
        """基于新的澄清修订契约"""
        new_contract = RequirementContract(
            raw_goal=self.raw_goal,
            refined_goal=self.refined_goal,
            hard_constraints=list(self.hard_constraints),
            soft_preferences=list(self.soft_preferences),
            out_of_scope=list(self.out_of_scope),
            acceptance_criteria=list(self.acceptance_criteria),
            clarifications=self.clarifications + new_clarifications,
            version=self.version + 1,
        )
        return new_contract

    def to_context_string(self) -> str:
        """转换为可注入 LLM 上下文的字符串"""
        lines = ["## Requirement Contract"]
        if self.refined_goal:
            lines.append(f"Refined Goal: {self.refined_goal}")
        else:
            lines.append(f"Goal: {self.raw_goal}")

        sections = [
            ("Hard Constraints", self.hard_constraints),
            ("Soft Preferences", self.soft_preferences),
            ("Out of Scope", self.out_of_scope),
            ("Acceptance Criteria", self.acceptance_criteria),
        ]
        for title, items in sections:
            if items:
                lines.append(f"\n{title}:")
                for item in items:
                    lines.append(f"  - {item}")

        return "\n".join(lines)

    def to_markdown(self) -> str:
        """生成人类可读的契约摘要"""
        lines = ["### 📋 Requirement Contract", ""]
        lines.append(f"**Goal**: {self.refined_goal or self.raw_goal}")

        if self.hard_constraints:
            lines.append("\n**Hard Constraints** (不可协商):")
            for c in self.hard_constraints:
                lines.append(f"- {c}")

        if self.soft_preferences:
            lines.append("\n**Soft Preferences** (可协商):")
            for p in self.soft_preferences:
                lines.append(f"- {p}")

        if self.out_of_scope:
            lines.append("\n**Out of Scope**:")
            for o in self.out_of_scope:
                lines.append(f"- {o}")

        if self.acceptance_criteria:
            lines.append("\n**Acceptance Criteria**:")
            for a in self.acceptance_criteria:
                lines.append(f"- {a}")

        if self.clarifications:
            lines.append("\n**Clarification History**:")
            for qa in self.clarifications:
                lines.append(f"- Q: {qa.get('question', '')}")
                lines.append(f"  A: {qa.get('answer', '')}")

        status_icon = "✅" if self.confirmed else "⏳"
        lines.append(f"\n*Version {self.version} | {status_icon} {'Confirmed' if self.confirmed else 'Draft'}*")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """序列化为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RequirementContract":
        """从字典反序列化"""
        # 过滤掉 dataclass 中没有的字段，保证向前兼容
        valid_keys = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def __repr__(self) -> str:
        status = "confirmed" if self.confirmed else "draft"
        return (
            f"RequirementContract(v{self.version}, {status}, "
            f"constraints={len(self.hard_constraints)})"
        )
