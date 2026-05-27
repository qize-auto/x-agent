"""Tests for MCPServerManager — 不启动真实子进程，纯配置管理测试"""
import json
import tempfile
from pathlib import Path

from xagent.core.mcp.manager import MCPServerManager, ServerConfig


def test_install_and_list():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "mcp_servers.json"
        mgr = MCPServerManager(config_file=cfg_file)

        ok = mgr.install("test-srv", transport="stdio", command="echo", args=["hello"], trusted=True)
        assert ok is True

        servers = mgr.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "test-srv"
        assert servers[0]["transport"] == "stdio"
        assert servers[0]["running"] is False


def test_install_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "mcp_servers.json"
        mgr = MCPServerManager(config_file=cfg_file)
        mgr.install("dup", command="echo")
        ok = mgr.install("dup", command="echo")
        assert ok is False


def test_uninstall():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "mcp_servers.json"
        mgr = MCPServerManager(config_file=cfg_file)
        mgr.install("to-remove", command="echo")
        ok = mgr.uninstall("to-remove")
        assert ok is True
        assert len(mgr.list_servers()) == 0


def test_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "mcp_servers.json"
        mgr1 = MCPServerManager(config_file=cfg_file)
        mgr1.install("persist", command="node", args=["-v"], trusted=True)
        del mgr1

        mgr2 = MCPServerManager(config_file=cfg_file)
        servers = mgr2.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "persist"
        assert servers[0]["trusted"] is True


def test_server_config_defaults():
    cfg = ServerConfig(name="x")
    assert cfg.args == []
    assert cfg.env == {}
    assert cfg.transport == "stdio"
    assert cfg.timeout == 30.0


if __name__ == "__main__":
    test_install_and_list()
    test_install_duplicate()
    test_uninstall()
    test_persistence()
    test_server_config_defaults()
    print("All MCP Manager tests passed ✓")
