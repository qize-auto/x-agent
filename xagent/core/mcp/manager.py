"""
MCP Server Manager
==================
生产级 MCP Server 生命周期管理：
- 安装/卸载/列表
- 启动/停止/重启
- 心跳保活与健康检查
- 配置持久化 (~/.xagent/mcp_servers.json)
- 动态工具变更通知
"""
from __future__ import annotations
import json
import os
import time
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .client import MCPClient, StdioTransport, HttpSseTransport
from .registry_adapter import MCPAdapter
from .security import MCPSecurityBundle


CONFIG_DIR = Path.home() / ".xagent"
CONFIG_FILE = CONFIG_DIR / "mcp_servers.json"


@dataclass
class ServerConfig:
    """MCP Server 配置"""
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list = None
    env: dict = None
    cwd: str = None
    base_url: str = ""
    timeout: float = 30.0
    trusted: bool = False
    enabled: bool = True
    auto_start: bool = True
    max_memory_mb: int = 512
    max_cpu_time_sec: int = 60

    def __post_init__(self):
        if self.args is None:
            self.args = []
        if self.env is None:
            self.env = {}


class MCPServerInstance:
    """运行中的 MCP Server 实例"""

    def __init__(self, config: ServerConfig, adapter: MCPAdapter):
        self.config = config
        self.adapter = adapter
        self.client = adapter.client
        self.last_heartbeat: float = 0.0
        self.heartbeat_failures: int = 0
        self.tools_discovered: int = 0
        self._lock = threading.Lock()
        self._shutdown = False

    def is_healthy(self) -> bool:
        return (
            self.client.is_connected()
            and self.heartbeat_failures < 3
            and not self._shutdown
        )

    def mark_heartbeat(self, ok: bool):
        with self._lock:
            if ok:
                self.last_heartbeat = time.time()
                self.heartbeat_failures = 0
            else:
                self.heartbeat_failures += 1

    def shutdown(self):
        self._shutdown = True
        try:
            self.adapter.disconnect()
        except Exception:
            pass


class MCPServerManager:
    """MCP Server 管理器"""

    HEARTBEAT_INTERVAL = 30.0
    HEARTBEAT_TIMEOUT = 10.0

    def __init__(self, registry=None, security: MCPSecurityBundle = None,
                 config_file: Optional[Path] = None):
        self.registry = registry
        self.security = security or MCPSecurityBundle()
        self._servers: dict[str, ServerConfig] = {}
        self._instances: dict[str, MCPServerInstance] = {}
        self._lock = threading.RLock()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._shutdown = False
        self._config_file = config_file or CONFIG_FILE
        self._load_config()

    def _load_config(self):
        if not self._config_file.exists():
            return
        try:
            data = json.loads(self._config_file.read_text(encoding="utf-8"))
            for name, cfg in data.get("servers", {}).items():
                self._servers[name] = ServerConfig(name=name, **cfg)
        except Exception:
            pass

    def _save_config(self):
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "servers": {
                name: {k: v for k, v in asdict(cfg).items() if k != "name"}
                for name, cfg in self._servers.items()
            }
        }
        self._config_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def install(self, name: str, transport: str = "stdio",
                command: str = "", args: list = None,
                base_url: str = "", trusted: bool = False,
                **kwargs) -> bool:
        if name in self._servers:
            return False
        self._servers[name] = ServerConfig(
            name=name, transport=transport, command=command,
            args=args or [], base_url=base_url, trusted=trusted, **kwargs,
        )
        self._save_config()
        return True

    def uninstall(self, name: str) -> bool:
        self.stop(name)
        if name in self._servers:
            del self._servers[name]
            self._save_config()
            return True
        return False

    def list_servers(self) -> list[dict]:
        result = []
        with self._lock:
            for name, cfg in self._servers.items():
                inst = self._instances.get(name)
                result.append({
                    "name": name,
                    "enabled": cfg.enabled,
                    "transport": cfg.transport,
                    "running": inst is not None and inst.is_healthy(),
                    "tools": inst.tools_discovered if inst else 0,
                    "trusted": cfg.trusted,
                })
        return result

    def start(self, name: str) -> bool:
        cfg = self._servers.get(name)
        if not cfg or not cfg.enabled:
            return False
        with self._lock:
            if name in self._instances and self._instances[name].is_healthy():
                return True
            try:
                if cfg.transport == "stdio":
                    transport = StdioTransport(
                        command=cfg.command, args=cfg.args,
                        env={**os.environ, **cfg.env} if cfg.env else None,
                        cwd=cfg.cwd, timeout=cfg.timeout,
                    )
                elif cfg.transport == "sse":
                    transport = HttpSseTransport(base_url=cfg.base_url, timeout=cfg.timeout)
                else:
                    return False
            except Exception as e:
                print(f"[MCP] Transport error {name}: {e}")
                return False

            try:
                client = MCPClient(transport, name=name)
                adapter = MCPAdapter(server_name=name, client=client,
                                     trusted=cfg.trusted, security=self.security)
                adapter.connect()
                specs = adapter.discover_tools()
                if self.registry:
                    for spec in specs:
                        spec.func = adapter.make_handler(spec.name)
                        self.registry.register_from_spec(spec)
                instance = MCPServerInstance(config=cfg, adapter=adapter)
                instance.tools_discovered = len(specs)
                self._instances[name] = instance
                return True
            except Exception as e:
                print(f"[MCP] Start error {name}: {e}")
                return False

    def stop(self, name: str) -> bool:
        with self._lock:
            inst = self._instances.pop(name, None)
            if inst:
                inst.shutdown()
                if self.registry:
                    for tool in inst.adapter.list_tools():
                        self.registry.unregister(tool.name)
                return True
        return False

    def start_all(self):
        for name, cfg in self._servers.items():
            if cfg.auto_start and cfg.enabled:
                self.start(name)

    def stop_all(self):
        with self._lock:
            names = list(self._instances.keys())
        for name in names:
            self.stop(name)

    def _heartbeat_loop(self):
        while not self._shutdown:
            time.sleep(self.HEARTBEAT_INTERVAL)
            with self._lock:
                instances = list(self._instances.items())
            for name, inst in instances:
                if inst._shutdown:
                    continue
                try:
                    _ = inst.client.list_tools()
                    inst.mark_heartbeat(ok=True)
                except Exception:
                    inst.mark_heartbeat(ok=False)
                    if inst.heartbeat_failures >= 3:
                        print(f"[MCP] {name} unhealthy, restarting...")
                        self.stop(name)
                        self.start(name)

    def start_monitoring(self):
        if self._heartbeat_thread is None or not self._heartbeat_thread.is_alive():
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True, name="mcp-heartbeat")
            self._heartbeat_thread.start()

    def shutdown(self):
        self._shutdown = True
        self.stop_all()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
