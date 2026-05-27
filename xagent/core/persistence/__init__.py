"""Persistence — 任务状态持久化层

支持任务状态的保存、恢复、自动检查点。
"""
from .task_store import TaskStore, TaskSummary
from .checkpoint import CheckpointManager

__all__ = ["TaskStore", "TaskSummary", "CheckpointManager"]
