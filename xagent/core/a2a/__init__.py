"""A2A (Agent-to-Agent) 协议最小实现

基于 Google A2A 规范草案：
- Agent Card: 描述 Agent 能力
- Task: 跨 Agent 任务
- Client/Server: HTTP JSON 通信

无额外依赖，使用标准库 http.server + httpx（项目已有）
"""
from __future__ import annotations

__all__ = [
    "AgentCard",
    "Task",
    "TaskStatus",
    "Message",
    "Artifact",
    "TextPart",
    "FilePart",
    "A2AServer",
    "A2AClient",
]

from .models import AgentCard, Task, TaskStatus, Message, Artifact, TextPart, FilePart
from .server import A2AServer
from .client import A2AClient
