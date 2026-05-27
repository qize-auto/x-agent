"""
Swarm 分布式执行模块
===================
单机多进程并行执行，兼容 Windows spawn 模式。

核心组件：
- SwarmController: 进程池管理器
- CheckpointStore: 节点级状态持久化
- SwarmExecutor: 工作流引擎的分布式执行器
"""
from __future__ import annotations

__all__ = [
    "SwarmController",
    "SwarmExecutor",
    "CheckpointStore",
    "SwarmCheckpoint",
]

from .checkpoint import CheckpointStore, SwarmCheckpoint
from .controller import SwarmController
from .executor import SwarmExecutor
