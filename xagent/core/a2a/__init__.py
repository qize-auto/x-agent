"""
A2A (Agent-to-Agent) 预留接口
=============================
当前为架构预留，待明确出现"远程异构 Agent 协作"场景后完整实现。

设计参考：
- Google A2A Protocol (v1.0, 2026)
- Agent Card: 能力声明与发现
- Task: 任务生命周期管理
- Artifact: 交付物交换
"""
from .agent_card import AgentCard
from .task_client import A2ATaskClient

__all__ = ["AgentCard", "A2ATaskClient"]
