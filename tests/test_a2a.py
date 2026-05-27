"""
Tests for A2A (Agent-to-Agent) reserved interface
"""
import pytest

from xagent.core.a2a.agent_card import AgentCard
from xagent.core.a2a.task_client import A2ATaskClient


class TestAgentCard:
    def test_to_dict(self):
        card = AgentCard(
            name="test_agent",
            description="A test agent",
            version="1.0.0",
            url="http://localhost:8080",
            skills=[{"name": "coding", "description": "Write code"}],
        )
        d = card.to_dict()
        assert d["name"] == "test_agent"
        assert d["description"] == "A test agent"
        assert d["version"] == "1.0.0"
        assert d["url"] == "http://localhost:8080"
        assert len(d["skills"]) == 1

    def test_from_dict(self):
        data = {
            "name": "remote",
            "description": "Remote agent",
            "version": "2.0",
            "url": "http://example.com",
        }
        card = AgentCard.from_dict(data)
        assert card.name == "remote"
        assert card.version == "2.0"


class TestA2ATaskClient:
    def test_send_task_returns_pending(self):
        card = AgentCard(name="remote", description="remote agent", url="http://localhost")
        client = A2ATaskClient(card)
        result = client.send_task("do something")
        assert result["status"] == "pending"
        assert "task_id" in result
        assert "预留接口" in result["note"]

    def test_get_task_status(self):
        card = AgentCard(name="remote", description="remote agent", url="http://localhost")
        client = A2ATaskClient(card)
        status = client.get_task_status("task_123")
        assert status["task_id"] == "task_123"
        assert status["status"] == "pending"


class TestAgentLoopDelegate:
    def test_delegate_no_target(self):
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
            memory=MemoryEngine(persist_dir="/tmp/test_mem"),
        )
        result = loop.delegate("task")
        assert result["status"] == "failed"
        assert "未指定目标 Agent" in result["error"]

    def test_delegate_with_url(self):
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
            memory=MemoryEngine(persist_dir="/tmp/test_mem"),
        )
        result = loop.delegate("task", target_agent_url="http://localhost:8080")
        assert result["status"] == "pending"
        assert "预留接口" in result["note"]
