"""
SwarmExecutor
=============
WorkflowEngine 的可插拔分布式执行器。
用 SwarmController 的进程池替代 ThreadPoolExecutor。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .controller import SwarmController


class SwarmExecutor:
    """
    符合 WorkflowEngine 执行器接口的 Swarm 包装器。

    用法：
        controller = SwarmController(num_workers=4, config=config, enabled=True)
        engine = WorkflowEngine(executor=SwarmExecutor(controller))
        engine.run(workflow)
    """

    def __init__(self, controller):
        self.controller = controller

    def __call__(self, nodes: list, ctx) -> dict:
        """
        执行接口：接收节点列表和上下文，返回 node_id -> result 映射。
        """
        return self.controller.execute_nodes(nodes, ctx)
