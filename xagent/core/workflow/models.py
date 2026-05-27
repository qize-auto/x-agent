"""Workflow 数据模型"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkflowContext:
    """工作流执行上下文"""
    variables: dict = field(default_factory=dict)
    node_results: dict = field(default_factory=dict)
    executed_nodes: set = field(default_factory=set)
    failed_nodes: set = field(default_factory=set)

    def set(self, key: str, value):
        self.variables[key] = value

    def get(self, key: str, default=None):
        return self.variables.get(key, default)


@dataclass
class WorkflowNode:
    """工作流节点基类"""
    id: str
    node_type: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class TaskNode(WorkflowNode):
    """任务节点：调用 Agent 执行目标"""
    goal: str = ""
    tools: list[str] = field(default_factory=list)
    retries: int = 0
    node_type: str = "task"


@dataclass
class ConditionNode(WorkflowNode):
    """条件节点：根据条件选择分支"""
    condition: str = ""
    branches: dict = field(default_factory=dict)  # {"true": {"next": "node_id"}, ...}
    node_type: str = "condition"


@dataclass
class EndNode(WorkflowNode):
    """结束节点"""
    node_type: str = "end"


@dataclass
class Workflow:
    """工作流定义"""
    name: str
    description: str = ""
    entry: str = ""
    nodes: dict[str, WorkflowNode] = field(default_factory=dict)

    def get_node(self, node_id: str) -> Optional[WorkflowNode]:
        return self.nodes.get(node_id)

    def predecessors(self, node_id: str) -> list[str]:
        """返回指定节点的所有前置节点"""
        node = self.get_node(node_id)
        if node is None:
            return []
        return list(node.depends_on)

    def successors(self, node_id: str) -> list[str]:
        """返回指定节点的所有后置节点（直接依赖它的节点）"""
        result = []
        for nid, node in self.nodes.items():
            if node_id in node.depends_on:
                result.append(nid)
        return result
