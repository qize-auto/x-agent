"""MCP (Model Context Protocol) 客户端实现

基于 JSON-RPC 2.0，支持 stdio 传输。
轻量级实现，零外部依赖（仅使用标准库 subprocess + json）。

参考: https://modelcontextprotocol.io/specification
"""
from __future__ import annotations
import json
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class MCPTool:
    """MCP Tool 描述"""
    name: str
    description: str
    input_schema: dict

    @classmethod
    def from_dict(cls, data: dict) -> MCPTool:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}),
        )

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class MCPCallResult:
    """MCP 工具调用结果"""
    content: list[dict] = field(default_factory=list)
    is_error: bool = False

    @property
    def text(self) -> str:
        """提取文本内容（方便使用）"""
        texts = []
        for item in self.content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)


class MCPTransport(ABC):
    """MCP 传输层抽象"""

    @abstractmethod
    def send(self, message: dict) -> None:
        ...

    @abstractmethod
    def receive(self) -> Optional[dict]:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def is_alive(self) -> bool:
        ...


class HttpSseTransport(MCPTransport):
    """
    HTTP + Server-Sent Events 传输。
    用于连接远程 MCP server（如部署在云端或局域网内的 server）。

    握手流程：
      1. POST /initialize → 返回 server info
      2. POST /message    → 发送 JSON-RPC 请求
      3. GET  /sse        → 接收 SSE 事件流（可选，用于 async 通知）
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._alive = False
        self._req_id = 0
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        """HTTP transport 不需要显式启动子进程，但需要标记 alive"""
        self._alive = True

    def send(self, message: dict) -> None:
        import urllib.request
        import urllib.error
        url = f"{self.base_url}/message"
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # HTTP transport 可能在响应中直接返回结果
                body = resp.read().decode("utf-8", errors="replace")
                if body:
                    try:
                        result = json.loads(body)
                        msg_id = message.get("id")
                        if msg_id is not None:
                            with self._lock:
                                self._pending[str(msg_id)] = result
                    except json.JSONDecodeError:
                        pass
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            msg_id = message.get("id")
            if msg_id is not None:
                with self._lock:
                    self._pending[str(msg_id)] = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": e.code, "message": body}}
        except Exception:
            pass

    def receive(self) -> Optional[dict]:
        # HTTP transport 采用请求-响应模式，结果在 _pending 中
        with self._lock:
            # 返回最早的一个 pending response
            for key in list(self._pending.keys()):
                return self._pending.pop(key)
        return None

    def close(self) -> None:
        self._alive = False
        with self._lock:
            self._pending.clear()

    def is_alive(self) -> bool:
        return self._alive

    def get_pending(self, req_id: str) -> Optional[dict]:
        with self._lock:
            return self._pending.pop(req_id, None)


class StdioTransport(MCPTransport):
    """
    stdio 传输：通过子进程的标准输入/输出进行 JSON-RPC 通信。
    这是 MCP 最常用的传输方式（如 npx / uvx 启动的 server）。
    """

    def __init__(
        self,
        command: str,
        args: list[str] = None,
        env: dict = None,
        cwd: str = None,
        timeout: float = 30.0,
    ):
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._read_buffer = ""
        self._request_id = 0

    def start(self) -> None:
        """启动子进程"""
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                cwd=self.cwd,
                text=True,
                bufsize=1,  # line buffered
            )
            # 等待一小会儿让 server 启动
            time.sleep(0.5)
        except Exception as e:
            raise RuntimeError(f"Failed to start MCP server: {e}")

    def send(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Transport not started or closed")
        line = json.dumps(message, ensure_ascii=False)
        with self._lock:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()

    def receive(self) -> Optional[dict]:
        if self._proc is None or self._proc.stdout is None:
            return None
        # 逐行读取 JSON-RPC 消息
        try:
            line = self._proc.stdout.readline()
            if not line:
                return None
            return json.loads(line)
        except json.JSONDecodeError:
            return None
        except Exception:
            return None

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            finally:
                self._proc = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stderr_lines(self, max_lines: int = 10) -> list[str]:
        """读取 stderr 的最近几行（用于调试）"""
        if self._proc is None or self._proc.stderr is None:
            return []
        # stderr 非阻塞读取较复杂，这里简单处理
        return []


class MCPClient:
    """
    MCP 协议客户端。

    用法:
        transport = StdioTransport("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/path"])
        client = MCPClient(transport)
        client.connect()
        tools = client.list_tools()
        result = client.call_tool("read_file", {"path": "/tmp/test.txt"})
        client.disconnect()
    """

    def __init__(self, transport: MCPTransport, name: str = ""):
        self.transport = transport
        self.name = name or "unnamed"
        self._connected = False
        self._server_info: dict = {}
        self._lock = threading.Lock()
        self._req_id = 0

    # ── 生命周期 ──

    def connect(self) -> dict:
        """
        连接并初始化 MCP server。
        Returns: server info / capabilities
        """
        if hasattr(self.transport, "start"):
            self.transport.start()

        # initialize 握手
        init_req = self._make_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "sampling": {},
                "roots": {"listChanged": True},
            },
            "clientInfo": {
                "name": "x-agent",
                "version": "1.0.0",
            },
        })
        self.transport.send(init_req)
        resp = self._wait_response(init_req["id"], timeout=30.0)
        if resp is None or "result" not in resp:
            raise RuntimeError("MCP initialize failed: no response from server")

        self._server_info = resp["result"]

        # 发送 initialized 通知（无 id）
        self.transport.send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

        self._connected = True
        return self._server_info

    def disconnect(self) -> None:
        """优雅断开"""
        if self._connected:
            try:
                self.transport.send({
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": str(uuid.uuid4()), "reason": "Client disconnecting"},
                })
            except Exception:
                pass
        self.transport.close()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self.transport.is_alive()

    # ── 工具发现 ──

    def list_tools(self) -> list[MCPTool]:
        """获取 server 提供的所有工具"""
        req = self._make_request("tools/list", {})
        self.transport.send(req)
        resp = self._wait_response(req["id"], timeout=30.0)
        if resp is None or "result" not in resp:
            return []
        tools_data = resp["result"].get("tools", [])
        return [MCPTool.from_dict(t) for t in tools_data]

    # ── 工具调用 ──

    def call_tool(self, name: str, arguments: dict) -> MCPCallResult:
        """调用指定工具"""
        req = self._make_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        self.transport.send(req)
        resp = self._wait_response(req["id"], timeout=60.0)
        if resp is None:
            return MCPCallResult(content=[{"type": "text", "text": "Error: no response from MCP server"}], is_error=True)

        if "error" in resp:
            err = resp["error"]
            return MCPCallResult(
                content=[{"type": "text", "text": f"MCP Error {err.get('code')}: {err.get('message')}"}],
                is_error=True,
            )

        result = resp.get("result", {})
        return MCPCallResult(
            content=result.get("content", []),
            is_error=result.get("isError", False),
        )

    # ── 资源 / Prompt（可选）──

    def list_resources(self) -> list[dict]:
        req = self._make_request("resources/list", {})
        self.transport.send(req)
        resp = self._wait_response(req["id"], timeout=30.0)
        if resp and "result" in resp:
            return resp["result"].get("resources", [])
        return []

    def read_resource(self, uri: str) -> dict:
        req = self._make_request("resources/read", {"uri": uri})
        self.transport.send(req)
        resp = self._wait_response(req["id"], timeout=30.0)
        if resp and "result" in resp:
            return resp["result"]
        return {}

    def list_prompts(self) -> list[dict]:
        req = self._make_request("prompts/list", {})
        self.transport.send(req)
        resp = self._wait_response(req["id"], timeout=30.0)
        if resp and "result" in resp:
            return resp["result"].get("prompts", [])
        return []

    def get_prompt(self, name: str, arguments: dict = None) -> dict:
        req = self._make_request("prompts/get", {"name": name, "arguments": arguments or {}})
        self.transport.send(req)
        resp = self._wait_response(req["id"], timeout=30.0)
        if resp and "result" in resp:
            return resp["result"]
        return {}

    # ── 内部辅助 ──

    def _make_request(self, method: str, params: dict) -> dict:
        with self._lock:
            self._req_id += 1
            req_id = self._req_id
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

    def _wait_response(self, req_id: int, timeout: float = 30.0) -> Optional[dict]:
        """阻塞等待匹配的 response"""
        deadline = time.time() + timeout
        # HTTP transport 的特殊处理：结果可能已在 _pending 中
        if hasattr(self.transport, "get_pending"):
            while time.time() < deadline:
                msg = self.transport.get_pending(str(req_id))
                if msg is not None:
                    return msg
                # 也尝试通用的 receive
                msg2 = self.transport.receive()
                if msg2 and msg2.get("id") == req_id:
                    return msg2
                time.sleep(0.05)
            return None

        # stdio 模式的原始逻辑
        while time.time() < deadline:
            msg = self.transport.receive()
            if msg is None:
                time.sleep(0.05)
                continue
            # 忽略通知（无 id）
            if "id" not in msg:
                continue
            if msg["id"] == req_id:
                return msg
            # id 不匹配：可能是乱序或 server bug，继续等待
        return None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
