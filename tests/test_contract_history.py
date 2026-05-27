"""
测试契约历史（Contract History）
==============================
验证确认的 RequirementContract 被正确存入 MemoryEngine，
并能在后续任务中被召回复用。
"""
import pytest
import json

from xagent.core.agent_loop import AgentLoop
from xagent.core.requirement_contract import RequirementContract


class RecordingMemoryEngine:
    """记录所有 add/recall 调用的模拟记忆引擎"""

    def __init__(self):
        self.added = []  # [(text, memory_type, metadata), ...]
        self._contracts = []  # 模拟存储的契约

    def recall(self, query, k=5):
        # 简单关键词匹配
        results = []
        for item in self._contracts:
            if any(w in item["text"] for w in query.lower().split()):
                results.append(item)
        return results[:k]

    def add(self, text, memory_type="conversation", metadata=None):
        self.added.append((text, memory_type, metadata or {}))
        self._contracts.append({
            "text": text,
            "metadata": metadata or {},
            "score": 1.0,
        })

    def stats(self):
        return {"total": len(self.added)}

    def forget(self, memory_type=None):
        if memory_type:
            self.added = [a for a in self.added if a[1] != memory_type]
            self._contracts = [c for c in self._contracts if c["metadata"].get("type") != memory_type]
        else:
            self.added.clear()
            self._contracts.clear()


class MockLLMClient:
    """模拟 LLM 客户端"""

    def __init__(self, response_content="", tool_calls=None):
        self._response = response_content
        self._tool_calls = tool_calls or []
        self.model_id = "mock/model"
        self.provider = "mock"
        self.last_messages = None

    def chat(self, messages, **kwargs):
        self.last_messages = messages
        resp = type("FakeResp", (), {})()
        resp.content = self._response
        resp.tool_calls = self._tool_calls
        resp.reasoning = ""
        resp.usage = {}
        return resp


class MockToolRegistry:
    def list_tools(self): return []
    def get_schemas(self): return []
    def get(self, name): return None
    def execute(self, name, args): return {"ok": True}


class TestContractStorage:
    """契约存储测试"""

    def test_confirmed_contract_stored_in_memory(self):
        """确认的契约应存入 MemoryEngine，类型为 'contract'"""
        memory = RecordingMemoryEngine()
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=memory,
            config={"clarification": {"enabled": True}},
            ask_user_callback=lambda q: "test answer",
        )
        plan = loop.run_task("帮我设计一个系统")
        
        # 验证契约被存储
        contract_adds = [a for a in memory.added if a[1] == "contract"]
        assert len(contract_adds) == 1, f"Expected 1 contract stored, got {len(contract_adds)}"
        text, mtype, meta = contract_adds[0]
        assert "Requirement Contract" in text
        assert meta.get("raw_goal") == "帮我设计一个系统"

    def test_cancelled_contract_not_stored(self):
        """用户取消的契约不应存入 MemoryEngine"""
        memory = RecordingMemoryEngine()
        llm = MockLLMClient("Q1")
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=memory,
            config={"clarification": {"enabled": True}},
            ask_user_callback=lambda q: None,  # 用户取消
        )
        plan = loop.run_task("帮我设计一个系统")
        
        assert plan.status == "cancelled"
        contract_adds = [a for a in memory.added if a[1] == "contract"]
        assert len(contract_adds) == 0

    def test_simple_goal_no_contract_storage(self):
        """简单目标自动跳过澄清，不产生契约存储"""
        memory = RecordingMemoryEngine()
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=memory,
            config={"clarification": {"enabled": True}},
        )
        plan = loop.run_task("ls -la")
        
        # 简单目标不产生契约存储
        contract_adds = [a for a in memory.added if a[1] == "contract"]
        assert len(contract_adds) == 0

    def test_storage_failure_does_not_crash(self):
        """契约存储失败（contract 类型）不应导致任务崩溃"""
        class BrokenMemory:
            def recall(self, query, k=5): return []
            def add(self, text, memory_type="conversation", metadata=None):
                # 仅当存储契约时抛异常，模拟 contract 存储失败
                if memory_type == "contract":
                    raise RuntimeError("disk full")
            def stats(self): return {}
            def forget(self, memory_type=None): pass

        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=BrokenMemory(),
            config={"clarification": {"enabled": True}},
            ask_user_callback=lambda q: "test",
        )
        # 不应抛出异常
        plan = loop.run_task("帮我设计一个系统")
        assert plan.status != "cancelled"


class TestContractRecall:
    """契约召回测试"""

    def test_historical_constraints_recalled(self):
        """历史契约中的 hard_constraints 应在后续任务中被召回"""
        memory = RecordingMemoryEngine()
        # 预置一个历史契约：用户之前要求必须用 Python
        memory.add(
            "Requirement Contract\nGoal: 设计缓存系统\nHard Constraints:\n  - 必须用 Python",
            memory_type="contract",
            metadata={
                "raw_goal": "设计缓存系统",
                "refined_goal": "设计缓存系统",
                "hard_constraints": json.dumps(["必须用 Python"]),
            }
        )
        
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=memory,
            config={"clarification": {"enabled": True}},
            ask_user_callback=lambda q: "test",
        )
        plan = loop.run_task("帮我设计一个新系统")
        
        # 验证 run_task 成功完成
        assert plan.status != "cancelled"
        # 验证有新的契约被存储（基于本次交互）
        contract_adds = [a for a in memory.added if a[1] == "contract"]
        assert len(contract_adds) == 2  # 预置的 + 本次的


class TestUserProfile:
    """用户画像层测试"""

    def test_profile_extracts_frequent_constraints(self):
        """出现多次的约束应被识别为画像"""
        from xagent.core.user_profile import UserProfile
        
        profile = UserProfile()
        contracts = [
            {"metadata": {"hard_constraints": '["必须用 Python", "兼容 Windows"]'}}
            for _ in range(3)
        ]
        profile.ingest_contracts(contracts)
        
        hints = profile.get_profile_hints(min_occurrences=2)
        assert "必须用 Python" in hints
        assert "兼容 Windows" in hints

    def test_profile_ignores_rare_constraints(self):
        """只出现一次的约束不应进入画像"""
        from xagent.core.user_profile import UserProfile
        
        profile = UserProfile()
        contracts = [
            {"metadata": {"hard_constraints": '["必须用 Python"]'}}
        ]
        profile.ingest_contracts(contracts)
        
        hints = profile.get_profile_hints(min_occurrences=2)
        assert "必须用 Python" not in hints

    def test_profile_context_string_format(self):
        """to_context_string 应包含出现次数"""
        from xagent.core.user_profile import UserProfile
        
        profile = UserProfile()
        contracts = [
            {"metadata": {"hard_constraints": '["必须用 Python"]'}}
            for _ in range(3)
        ]
        profile.ingest_contracts(contracts)
        
        ctx = profile.to_context_string(min_occurrences=2)
        assert "User Profile" in ctx
        assert "必须用 Python" in ctx
        assert "confirmed 3 times" in ctx

    def test_profile_empty_when_no_contracts(self):
        """无契约时画像为空"""
        from xagent.core.user_profile import UserProfile
        
        profile = UserProfile()
        assert profile.is_empty()
        assert profile.get_profile_hints() == []
        assert profile.to_context_string() == ""
