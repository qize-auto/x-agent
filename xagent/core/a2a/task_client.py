"""
A2A Task Client
===============
向远程 A2A Agent 提交任务并获取结果。
当前为预留实现，接口已对齐 A2A v1.0 规范。
"""
from __future__ import annotations
import json
import uuid
from typing import Any

from .agent_card import AgentCard


class A2ATaskClient:
    """
    A2A 任务客户端。

    用法（预留）:
        card = AgentCard(name="remote_agent", url="http://...")
        client = A2ATaskClient(card)
        result = client.send_task("分析这份代码")
    """

    def __init__(self, agent_card: AgentCard):
        self.agent_card = agent_card

    def send_task(self, task_input: str, context: dict = None) -> dict:
        """
        向远程 Agent 发送任务。

        Args:
            task_input: 任务描述或用户输入
            context: 可选上下文

        Returns:
            {"status": "completed"|"failed", "artifacts": list, "error": str|None}
        """
        # 预留实现：实际生产环境需通过 HTTP + SSE 与远程 Agent 通信
        return {
            "status": "pending",
            "task_id": str(uuid.uuid4())[:12],
            "artifacts": [],
            "error": None,
            "note": "A2A 客户端当前为预留接口，尚未实现真实网络通信。",
        }

    def get_task_status(self, task_id: str) -> dict:
        """查询任务状态（预留）"""
        return {"task_id": task_id, "status": "pending", "artifacts": []}
