"""Agent 合成器

根据任务特征动态选择最优 Agent 角色组合。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from ..task import TaskPlan


class AgentRole(Enum):
    """预定义 Agent 角色"""
    CODER = auto()       # 编码实现
    REVIEWER = auto()    # 代码审查
    TESTER = auto()      # 测试验证
    ARCHITECT = auto()   # 架构设计
    RESEARCHER = auto()  # 调研分析
    SYNTHESIZER = auto() # 结果整合


@dataclass
class RoleConfig:
    """角色配置"""
    role: AgentRole
    model_hint: str = ""      # 建议使用的模型
    temperature: float = 0.7
    system_prompt: str = ""


class AgentSynthesizer:
    """
    根据 TaskPlan 特征合成 Agent 团队。

    用法:
        synthesizer = AgentSynthesizer()
        team_config = synthesizer.synthesize(plan)
        # team_config = [RoleConfig(CODER), RoleConfig(REVIEWER)]
    """

    def __init__(self):
        self._role_prompts = {
            AgentRole.CODER: "You are an expert programmer. Write clean, well-tested code.",
            AgentRole.REVIEWER: "You are a senior code reviewer. Find bugs, suggest improvements, check for security issues.",
            AgentRole.TESTER: "You are a QA engineer. Write comprehensive tests and verify edge cases.",
            AgentRole.ARCHITECT: "You are a software architect. Design clean, scalable solutions.",
            AgentRole.RESEARCHER: "You are a researcher. Gather information, compare alternatives, provide evidence.",
            AgentRole.SYNTHESIZER: "You are an editor. Synthesize multiple perspectives into a coherent output.",
        }

    def synthesize(self, plan: TaskPlan) -> list[RoleConfig]:
        """根据任务计划合成角色组合"""
        goal_lower = plan.goal.lower()

        # 分析任务特征
        is_coding = any(k in goal_lower for k in ["code", "implement", "fix", "refactor", "build", "write"])
        is_review = any(k in goal_lower for k in ["review", "audit", "check", "analyze"])
        is_research = any(k in goal_lower for k in ["research", "investigate", "compare", "survey"])
        has_tests = any(st.tool_hint in ("run_tests", "lint_code") for st in plan.subtasks)

        roles = []

        if is_coding:
            roles.append(RoleConfig(AgentRole.ARCHITECT, model_hint="reasoning"))
            roles.append(RoleConfig(AgentRole.CODER, model_hint="coding"))
            if has_tests or len(plan.subtasks) > 3:
                roles.append(RoleConfig(AgentRole.TESTER, model_hint="fast"))
            roles.append(RoleConfig(AgentRole.REVIEWER, model_hint="reasoning"))
        elif is_review:
            roles.append(RoleConfig(AgentRole.REVIEWER, model_hint="reasoning"))
            roles.append(RoleConfig(AgentRole.CODER, model_hint="coding"))
        elif is_research:
            roles.append(RoleConfig(AgentRole.RESEARCHER, model_hint="fast"))
            roles.append(RoleConfig(AgentRole.SYNTHESIZER, model_hint="reasoning"))
        else:
            # 默认：单 Coder
            roles.append(RoleConfig(AgentRole.CODER))

        # 填充 system prompt
        for rc in roles:
            rc.system_prompt = self._role_prompts.get(rc.role, "")

        return roles

    def estimate_cost(self, roles: list[RoleConfig], plan: TaskPlan) -> dict:
        """估算多 Agent 执行的成本"""
        base_cost = len(plan.subtasks) * 0.5  # 假设每个子任务 0.5 单位
        role_multipliers = {
            AgentRole.ARCHITECT: 2.0,
            AgentRole.CODER: 1.5,
            AgentRole.REVIEWER: 1.2,
            AgentRole.TESTER: 1.0,
            AgentRole.RESEARCHER: 1.5,
            AgentRole.SYNTHESIZER: 1.0,
        }
        multiplier = sum(role_multipliers.get(r.role, 1.0) for r in roles)
        return {
            "estimated_cost_units": round(base_cost * multiplier, 2),
            "agent_count": len(roles),
            "role_breakdown": [(r.role.name, r.model_hint) for r in roles],
        }
