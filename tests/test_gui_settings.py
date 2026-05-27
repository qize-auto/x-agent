"""
Tests for GUI SettingsDialog vision/code_intel panels
"""
import json
import pytest
from pathlib import Path


class TestConfigVisionCodeIntel:
    def test_default_config_has_vision_and_code_intel(self):
        from xagent.config import DEFAULT_CONFIG
        assert "vision" in DEFAULT_CONFIG
        assert "code_intel" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["vision"].get("code_fusion_enabled") is True
        assert DEFAULT_CONFIG["code_intel"].get("enabled") is True
        assert DEFAULT_CONFIG["code_intel"].get("index_strategy") == "lazy"
        assert DEFAULT_CONFIG["code_intel"].get("repo_map_enabled") is True
        assert DEFAULT_CONFIG["code_intel"].get("semantic_edit_enabled") is True
        assert "exclude_patterns" in DEFAULT_CONFIG["code_intel"]

    def test_config_save_and_load(self, tmp_path):
        from xagent.config import XAgentConfig, CONFIG_PATH
        # 使用临时配置目录
        import xagent.config as cfg_mod
        orig_path = cfg_mod.CONFIG_PATH
        orig_dir = cfg_mod.CONFIG_DIR
        try:
            cfg_mod.CONFIG_DIR = tmp_path / ".xagent"
            cfg_mod.CONFIG_PATH = cfg_mod.CONFIG_DIR / "config.json"
            config = XAgentConfig()
            config._data["vision"] = {
                "enabled": False,
                "strategy": "ocr",
                "screenshot_dir": str(tmp_path / "shots"),
                "ocr_language": "eng",
                "code_fusion_enabled": False,
            }
            config._data["code_intel"] = {
                "enabled": False,
                "index_strategy": "manual",
                "repo_map_enabled": False,
                "semantic_edit_enabled": False,
                "exclude_patterns": ["vendor/"],
            }
            config.save()

            config2 = XAgentConfig()
            assert config2._data["vision"]["enabled"] is False
            assert config2._data["vision"]["strategy"] == "ocr"
            assert config2._data["vision"]["code_fusion_enabled"] is False
            assert config2._data["code_intel"]["enabled"] is False
            assert config2._data["code_intel"]["index_strategy"] == "manual"
            assert config2._data["code_intel"]["repo_map_enabled"] is False
            assert config2._data["code_intel"]["exclude_patterns"] == ["vendor/"]
        finally:
            cfg_mod.CONFIG_PATH = orig_path
            cfg_mod.CONFIG_DIR = orig_dir


class TestAgentLoopVisionCodeIntelSwitches:
    def test_agent_loop_respects_code_intel_disabled(self, tmp_path):
        from xagent.core.agent_loop import AgentLoop
        from xagent.core.llm_client import LLMClient
        from xagent.core.tool_registry import ToolRegistry
        from xagent.core.memory_engine import MemoryEngine

        llm = type("FakeLLM", (), {
            "model_id": "test",
            "provider": "test",
            "chat": lambda *a, **k: type("FakeResp", (), {"content": "ok", "reasoning": "", "tool_calls": [], "usage": {}})(),
            "get_cost_estimate": lambda self, u: 0.0,
        })()
        loop = AgentLoop(
            llm=llm,
            tools=ToolRegistry(),
            memory=MemoryEngine(persist_dir=str(tmp_path / "mem")),
            project_root=str(tmp_path),
            config={
                "code_intel": {"enabled": False},
                "vision": {"enabled": False},
            },
        )
        assert loop._code_indexer is None
        assert loop._vision is None
        assert loop._vision_fusion is None

    def test_agent_loop_respects_vision_enabled_code_fusion_disabled(self, tmp_path):
        from xagent.core.agent_loop import AgentLoop
        from xagent.core.tool_registry import ToolRegistry
        from xagent.core.memory_engine import MemoryEngine

        # 构造一个能过初始化的 FakeLLM
        llm = type("FakeLLM", (), {
            "model_id": "test",
            "provider": "test",
            "chat": lambda *a, **k: type("FakeResp", (), {"content": "ok", "reasoning": "", "tool_calls": [], "usage": {}})(),
            "get_cost_estimate": lambda self, u: 0.0,
        })()

        loop = AgentLoop(
            llm=llm,
            tools=ToolRegistry(),
            memory=MemoryEngine(persist_dir=str(tmp_path / "mem")),
            project_root=str(tmp_path),
            config={
                "code_intel": {"enabled": True},
                "vision": {"enabled": True, "code_fusion_enabled": False, "strategy": "auto"},
            },
        )
        # code_indexer 可能被初始化（因为 enabled=True）
        # vision 应该被初始化（因为 vision.enabled=True）
        # vision_fusion 应该为 None（因为 code_fusion_enabled=False）
        assert loop._vision_fusion is None
