"""A2A Server — 基于标准库 http.server"""
from __future__ import annotations
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable, Optional

from .models import AgentCard, Task, TaskStatus, Message, TextPart


class _A2AHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    def log_message(self, format, *args):
        # 静默日志（避免污染 CLI）
        pass

    def do_GET(self):
        server = self.server  # type: ignore
        if self.path == "/agent-card" or self.path == "/.well-known/agent.json":
            self._send_json(200, server.agent_card.to_dict())
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        server = self.server  # type: ignore
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if self.path == "/tasks/send":
            self._handle_send(data, server)
        elif self.path == "/tasks/get":
            self._handle_get(data, server)
        elif self.path == "/tasks/cancel":
            self._handle_cancel(data, server)
        else:
            self._send_json(404, {"error": "Not found"})

    def _handle_send(self, data: dict, server):
        task_id = data.get("id") or server._new_task_id()
        message_data = data.get("message", {})
        message = Message.from_dict(message_data) if message_data else None

        task = Task(id=task_id, status=TaskStatus.WORKING, message=message)
        server.tasks[task_id] = task

        # 调用用户处理函数
        if server.task_handler:
            try:
                result_task = server.task_handler(task)
                server.tasks[task_id] = result_task
                self._send_json(200, result_task.to_dict())
                return
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.metadata["error"] = str(e)
                self._send_json(500, task.to_dict())
                return

        # 无处理函数，直接返回
        task.status = TaskStatus.COMPLETED
        task.artifacts.append(server._default_artifact(task))
        self._send_json(200, task.to_dict())

    def _handle_get(self, data: dict, server):
        task_id = data.get("id")
        task = server.tasks.get(task_id)
        if not task:
            self._send_json(404, {"error": "Task not found"})
            return
        self._send_json(200, task.to_dict())

    def _handle_cancel(self, data: dict, server):
        task_id = data.get("id")
        task = server.tasks.get(task_id)
        if not task:
            self._send_json(404, {"error": "Task not found"})
            return
        task.status = TaskStatus.CANCELED
        self._send_json(200, task.to_dict())

    def _send_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


class _A2AServer(HTTPServer):
    """内部 HTTP Server，持有共享状态"""

    def __init__(self, address, handler_class, agent_card: AgentCard,
                 task_handler: Optional[Callable] = None):
        super().__init__(address, handler_class)
        self.agent_card = agent_card
        self.task_handler = task_handler
        self.tasks: dict[str, Task] = {}
        self._task_id_counter = 0
        self._lock = threading.Lock()

    def _new_task_id(self) -> str:
        with self._lock:
            self._task_id_counter += 1
            return f"task-{self._task_id_counter}"

    def _default_artifact(self, task: Task):
        from .models import Artifact, TextPart
        text = ""
        if task.message:
            for p in task.message.parts:
                if isinstance(p, TextPart):
                    text += p.text + "\n"
        return Artifact(name="response", parts=[TextPart(text=text or "OK")])


class A2AServer:
    """A2A Server 包装器

    用法:
        def handle_task(task: Task) -> Task:
            task.status = TaskStatus.COMPLETED
            task.artifacts.append(Artifact(name="result", parts=[TextPart(text="Done")]))
            return task

        server = A2AServer(agent_card, task_handler=handle_task)
        server.start()
        # ...
        server.stop()
    """

    def __init__(self, agent_card: AgentCard, host: str = "127.0.0.1", port: int = 7728,
                 task_handler: Optional[Callable[[Task], Task]] = None):
        self.agent_card = agent_card
        self.host = host
        self.port = port
        self.task_handler = task_handler
        self._httpd: Optional[_A2AServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """在后台线程启动服务器"""
        self._httpd = _A2AServer((self.host, self.port), _A2AHandler,
                                 self.agent_card, self.task_handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """停止服务器"""
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
