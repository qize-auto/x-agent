"""A2A Client — 使用 httpx（项目已有依赖）"""
from __future__ import annotations
import httpx
from typing import Optional

from .models import AgentCard, Task, Message, TaskStatus


class A2AClient:
    """A2A 客户端

    用法:
        client = A2AClient("http://localhost:7728")
        card = client.get_agent_card()
        task = client.send_task("帮我总结这段文本", message=Message(...))
    """

    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def get_agent_card(self) -> Optional[AgentCard]:
        """获取远端 Agent 的 Agent Card"""
        try:
            resp = self._client.get(f"{self.base_url}/agent-card")
            resp.raise_for_status()
            data = resp.json()
            from .models import AgentSkill
            card = AgentCard(
                name=data.get("name", ""),
                description=data.get("description", ""),
                url=data.get("url", ""),
                version=data.get("version", "1.0"),
                capabilities=data.get("capabilities", {}),
                skills=[AgentSkill(**s) for s in data.get("skills", [])],
                default_input_modes=data.get("defaultInputModes", ["text"]),
                default_output_modes=data.get("defaultOutputModes", ["text"]),
            )
            return card
        except Exception:
            return None

    def send_task(self, text: str = "", message: Optional[Message] = None,
                  task_id: Optional[str] = None) -> Optional[Task]:
        """发送任务到远端 Agent"""
        if message is None:
            from .models import TextPart
            message = Message(role="user", parts=[TextPart(text=text)])

        payload = {
            "id": task_id,
            "message": message.to_dict(),
        }
        try:
            resp = self._client.post(f"{self.base_url}/tasks/send", json=payload)
            resp.raise_for_status()
            return self._task_from_dict(resp.json())
        except Exception as e:
            print(f"[A2A] 发送任务失败: {e}")
            return None

    def get_task(self, task_id: str) -> Optional[Task]:
        """查询任务状态"""
        try:
            resp = self._client.post(f"{self.base_url}/tasks/get", json={"id": task_id})
            resp.raise_for_status()
            return self._task_from_dict(resp.json())
        except Exception:
            return None

    def cancel_task(self, task_id: str) -> Optional[Task]:
        """取消任务"""
        try:
            resp = self._client.post(f"{self.base_url}/tasks/cancel", json={"id": task_id})
            resp.raise_for_status()
            return self._task_from_dict(resp.json())
        except Exception:
            return None

    def close(self):
        self._client.close()

    @staticmethod
    def _task_from_dict(data: dict) -> Task:
        from .models import Artifact, TextPart, FilePart
        task = Task(
            id=data.get("id", ""),
            status=TaskStatus(data.get("status", "submitted")),
            metadata=data.get("metadata", {}),
        )
        msg_data = data.get("message")
        if msg_data:
            task.message = Message.from_dict(msg_data)
        for a_data in data.get("artifacts", []):
            parts = []
            for p in a_data.get("parts", []):
                if p.get("type") == "text":
                    parts.append(TextPart(text=p.get("text", "")))
                elif p.get("type") == "file":
                    f = p.get("file", {})
                    parts.append(FilePart(file_name=f.get("name", ""), mime_type=f.get("mimeType", ""), bytes=f.get("bytes", "")))
            task.artifacts.append(Artifact(name=a_data.get("name", ""), parts=parts, metadata=a_data.get("metadata", {})))
        return task
