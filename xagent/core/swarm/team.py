"""Agent 团队执行器

协调多个 Agent 角色的协作执行。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..task import TaskPlan, SubTask
from .synthesizer import RoleConfig, AgentRole


@dataclass
class AgentResult:
    """单个 Agent 的执行结果"""
    role: AgentRole
    content: str
    confidence: float = 0.0  # 0.0 - 1.0


class AgentTeam:
    """
    Agent 团队执行器。

    目前为骨架实现，支持顺序执行各角色的任务。
    未来可扩展为并行执行和消息传递。
    """

    def __init__(self, roles: list[RoleConfig],
                 execute_fn: Callable[[str, str], str] = None):
        self.roles = roles
        self.execute_fn = execute_fn or (lambda role, task: f"[{role}] Done: {task}")
        self.results: list[AgentResult] = []

    def run(self, plan: TaskPlan) -> list[AgentResult]:
        """
        按角色顺序执行任务。

        流程：
        1. Architect → 设计整体方案
        2. Coder → 实现代码
        3. Tester → 验证测试
        4. Reviewer → 审查代码
        """
        self.results = []
        context = f"Goal: {plan.goal}\nSubtasks: {[st.description for st in plan.subtasks]}"

        # 按角色优先级排序
        priority = {
            AgentRole.ARCHITECT: 0,
            AgentRole.RESEARCHER: 0,
            AgentRole.CODER: 1,
            AgentRole.TESTER: 2,
            AgentRole.REVIEWER: 3,
            AgentRole.SYNTHESIZER: 4,
        }
        sorted_roles = sorted(self.roles, key=lambda r: priority.get(r.role, 99))

        for role_config in sorted_roles:
            prompt = self._build_prompt(role_config, context)
            response = self.execute_fn(role_config.role.name, prompt)
            result = AgentResult(
                role=role_config.role,
                content=response,
                confidence=0.8,  # 简化：固定置信度
            )
            self.results.append(result)
            # 将当前结果加入上下文，供后续角色使用
            context += f"\n\n[{role_config.role.name}]\n{response}"

        return self.results

    def synthesize_output(self) -> str:
        """整合所有角色的输出为最终结果"""
        if not self.results:
            return ""
        # Synthesizer 角色的输出优先
        for r in self.results:
            if r.role == AgentRole.SYNTHESIZER:
                return r.content
        # 否则返回最后一个角色的输出
        return self.results[-1].content

    @staticmethod
    def _build_prompt(role_config: RoleConfig, context: str) -> str:
        parts = [role_config.system_prompt, "", context]
        if role_config.role == AgentRole.REVIEWER:
            parts.append("\nPlease review the implementation above. List issues and improvements.")
        elif role_config.role == AgentRole.TESTER:
            parts.append("\nPlease write tests to verify the implementation.")
        return "\n".join(parts)
