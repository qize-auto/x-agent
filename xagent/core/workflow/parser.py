"""Workflow YAML 解析器"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from .models import Workflow, TaskNode, ConditionNode, EndNode, WorkflowNode


class WorkflowParser:
    """解析 YAML 格式的工作流定义"""

    @classmethod
    def from_file(cls, path: str | Path) -> Workflow:
        import yaml
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_string(cls, text: str) -> Workflow:
        import yaml
        data = yaml.safe_load(text)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> Workflow:
        name = data.get("name", "unnamed")
        description = data.get("description", "")
        entry = data.get("entry", "")

        nodes: dict[str, WorkflowNode] = {}
        for node_id, node_data in data.get("nodes", {}).items():
            node = cls._parse_node(node_id, node_data)
            nodes[node_id] = node

        return Workflow(name=name, description=description, entry=entry, nodes=nodes)

    @staticmethod
    def _parse_node(node_id: str, data: dict) -> WorkflowNode:
        node_type = data.get("type", "task")
        depends_on = data.get("depends_on", []) or []
        if isinstance(depends_on, str):
            depends_on = [depends_on]

        if node_type == "task":
            return TaskNode(
                id=node_id,
                node_type="task",
                depends_on=depends_on,
                goal=data.get("goal", ""),
                tools=data.get("tools", []),
                retries=data.get("retries", 0),
            )
        elif node_type == "condition":
            return ConditionNode(
                id=node_id,
                node_type="condition",
                depends_on=depends_on,
                condition=data.get("condition", ""),
                branches=data.get("branches", {}),
            )
        elif node_type == "end":
            return EndNode(
                id=node_id,
                node_type="end",
                depends_on=depends_on,
            )
        else:
            raise ValueError(f"Unknown node type: {node_type}")
