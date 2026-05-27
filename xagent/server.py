"""
X-Agent HTTP API Server
========================
为 VS Code 插件提供本地 HTTP 接口。
使用 Python 内置 http.server，零额外依赖。

端点:
    POST /chat   {"message": "..."} → {"response": "..."}
    POST /task   {"goal": "..."}    → {"plan": {...}}
    GET  /status                  → {"model": "...", "router": {...}}
    GET  /tools                   → {"tools": [...]}
    GET  /health                  → {"ok": true}
"""
from __future__ import annotations
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

from .config import XAgentConfig
from .core.llm_client import LLMClient
from .core.tool_registry import ToolRegistry
from .core.memory_engine import MemoryEngine
from .core.agent_loop import AgentLoop
from .tools import register_all_tools


class AgentServer:
    """X-Agent HTTP 服务器"""

    def __init__(self, host: str = "127.0.0.1", port: int = 7727):
        self.host = host
        self.port = port
        self.agent_loop: AgentLoop | None = None
        self._httpd: HTTPServer | None = None

    def start(self):
        """初始化 Agent 并启动服务器"""
        config = XAgentConfig()
        llm = LLMClient.from_config(config.model)
        tools = ToolRegistry()
        register_all_tools(tools, project_root=str(config.project_root))
        memory = MemoryEngine(config.memory.get("persist_dir"))

        self.agent_loop = AgentLoop(
            llm=llm,
            tools=tools,
            memory=memory,
            project_root=str(config.project_root),
            router_config=config._data.get("routing"),
        )

        handler = self._make_handler()
        self._httpd = HTTPServer((self.host, self.port), handler)
        print(f"X-Agent Server running at http://{self.host}:{self.port}")
        print("Press Ctrl+C to stop")
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
            self._httpd.shutdown()

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # 静默日志，避免污染终端
                pass

            def do_OPTIONS(self):
                self._send_cors_headers()
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                self._send_cors_headers()
                path = self.path.split("?")[0]

                if path == "/health":
                    self._json_response({"ok": True})
                elif path == "/status":
                    status = {
                        "model": server.agent_loop.llm.model_id if server.agent_loop else "",
                        "provider": server.agent_loop.llm.provider if server.agent_loop else "",
                        "router": server.agent_loop.router.summary() if server.agent_loop and server.agent_loop.router else None,
                    }
                    self._json_response(status)
                elif path == "/tools":
                    tools = server.agent_loop.tools.get_schemas() if server.agent_loop else []
                    self._json_response({"tools": tools})
                else:
                    self._send_error(404, "Not found")

            def do_POST(self):
                self._send_cors_headers()
                path = self.path.split("?")[0]
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    self._send_error(400, "Invalid JSON")
                    return

                if path == "/chat":
                    message = data.get("message", "")
                    if not message:
                        self._send_error(400, "Missing 'message'")
                        return
                    try:
                        response = server.agent_loop.run(message)
                        self._json_response({"response": response})
                    except Exception as e:
                        self._send_error(500, str(e))

                elif path == "/task":
                    goal = data.get("goal", "")
                    if not goal:
                        self._send_error(400, "Missing 'goal'")
                        return
                    try:
                        plan = server.agent_loop.run_task(goal)
                        self._json_response({
                            "goal": plan.goal,
                            "status": plan.status,
                            "subtasks": [st.to_dict() for st in plan.subtasks],
                        })
                    except Exception as e:
                        self._send_error(500, str(e))

                else:
                    self._send_error(404, "Not found")

            def _send_cors_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def _json_response(self, data: dict):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

            def _send_error(self, code: int, message: str):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": message}).encode("utf-8"))

        return Handler


def main():
    import argparse
    parser = argparse.ArgumentParser(description="X-Agent HTTP Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7727)
    args = parser.parse_args()

    server = AgentServer(host=args.host, port=args.port)
    server.start()


if __name__ == "__main__":
    main()
