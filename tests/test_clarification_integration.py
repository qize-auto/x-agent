"""
测试 Clarification 集成
========================
覆盖 AgentLoop、TaskPlanner、CacheFirstLoop 与 RequirementContract 的整合。
"""
import pytest

from xagent.core.agent_loop import AgentLoop
from xagent.core.cache_loop import CacheFirstLoop
from xagent.core.planner import TaskPlanner
from xagent.core.requirement_contract import RequirementContract
from xagent.core.task import TaskPlan


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
    """模拟工具注册表"""

    def list_tools(self):
        return []

    def get_schemas(self):
        return []

    def get(self, name):
        return None

    def execute(self, name, args):
        return {"ok": True}


class MockMemoryEngine:
    """模拟记忆引擎"""

    def recall(self, query, k=5):
        return []

    def add(self, text, memory_type="conversation"):
        pass

    def stats(self):
        return {}

    def forget(self, memory_type=None):
        pass


class TestTaskPlannerContract:
    """TaskPlanner 契约注入测试"""

    def test_plan_without_contract(self):
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        planner = TaskPlanner(llm)
        plan = planner.plan("simple goal")
        assert plan.goal == "simple goal"
        assert plan.contract is None
        assert len(plan.subtasks) == 1

    def test_plan_with_contract(self):
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        planner = TaskPlanner(llm)
        contract = RequirementContract(
            raw_goal="设计系统",
            refined_goal="设计 Python 缓存",
            hard_constraints=["必须用 Python"],
        )
        plan = planner.plan("设计系统", contract=contract)
        assert plan.contract is contract
        # 验证契约内容被注入到 LLM 调用中
        assert llm.last_messages is not None
        user_msg = llm.last_messages[-1]["content"]
        assert "Requirement Contract" in user_msg
        assert "必须用 Python" in user_msg


class TestAgentLoopContract:
    """AgentLoop 契约集成测试"""

    def test_run_task_disabled_by_default(self):
        """默认配置下不触发澄清，直接规划"""
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
            config={},  # 默认关闭
        )
        plan = loop.run_task("设计系统")
        assert plan.status != "cancelled"
        assert plan.goal == "设计系统"

    def test_run_task_skips_simple_goal(self):
        """简单目标自动跳过澄清"""
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
            config={"clarification": {"enabled": True}},
            ask_user_callback=lambda q: "test",
        )
        # 简单目标：ls
        plan = loop.run_task("ls -la")
        assert plan.status != "cancelled"
        assert plan.goal == "ls -la"

    def test_run_task_with_ask_callback(self):
        """启用澄清且有回调时建立契约"""
        # LLM 需要回答多次：generate_questions + build_contract + plan + execute
        # 使用无限默认响应避免 StopIteration 导致 replan 循环
        _responses = iter([
            "Q1\nQ2",  # generate_questions
            '{"refined_goal":"设计 Python 缓存","hard_constraints":["Python"],"soft_preferences":[],"out_of_scope":[],"acceptance_criteria":[]}',
            '[{"id":"1","description":"do thing","tool_hint":"run_command","dependencies":[]}]',  # plan
        ])

        def chat_override(messages, **kwargs):
            try:
                content = next(_responses)
            except StopIteration:
                content = "execution done"
            resp = type("FakeResp", (), {})()
            resp.content = content
            resp.tool_calls = []
            resp.reasoning = ""
            resp.usage = {}
            return resp

        llm = MockLLMClient()
        llm.chat = chat_override

        # 给工具注册表添加 run_command 工具，避免工具查找失败
        class ToolsWithRun(MockToolRegistry):
            def list_tools(self):
                return ["run_command"]
            def get_schemas(self):
                return []
            def get(self, name):
                if name == "run_command":
                    class Tool:
                        dangerous = False
                    return Tool()
                return None
            def execute(self, name, args):
                return {"ok": True, "output": "done"}

        answers = iter(["Python", "单节点"])
        loop = AgentLoop(
            llm=llm,
            tools=ToolsWithRun(),
            memory=MockMemoryEngine(),
            config={"clarification": {"enabled": True, "max_questions_per_task": 2}},
            ask_user_callback=lambda q: next(answers),
        )
        plan = loop.run_task("帮我设计一个缓存系统")
        # 由于 confirm_callback 为 None，契约自动确认
        assert plan.status != "cancelled"

    def test_run_task_user_cancels(self):
        """用户取消回答时返回 cancelled"""
        llm = MockLLMClient("Q1")
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
            config={"clarification": {"enabled": True}},
            ask_user_callback=lambda q: None,  # 用户取消
        )
        plan = loop.run_task("帮我设计一个系统")
        assert plan.status == "cancelled"

    def test_run_task_no_callback_skips(self):
        """无 ask_user_callback 时跳过澄清"""
        llm = MockLLMClient("Q1")
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
            config={"clarification": {"enabled": True}},
            ask_user_callback=None,
        )
        plan = loop.run_task("帮我设计一个系统")
        # 无法交互，跳过澄清 → 返回 cancelled（当前实现）
        assert plan.status == "cancelled"

    def test_plan_task_passes_contract(self):
        """plan_task 将契约传递给 planner"""
        llm = MockLLMClient('[{"id":"1","description":"do thing"}]')
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
        )
        contract = RequirementContract(
            raw_goal="设计系统",
            refined_goal="设计缓存",
            confirmed=True,
        )
        plan = loop.plan_task("设计系统", contract=contract)
        assert plan.contract is contract


class TestCacheLoopContract:
    """CacheFirstLoop 契约注入测试"""

    def test_build_enriched_user_message_with_contract(self):
        """enriched message 包含契约内容"""
        llm = MockLLMClient()
        loop = CacheFirstLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
        )
        contract = RequirementContract(
            raw_goal="设计系统",
            refined_goal="设计缓存",
            hard_constraints=["Python"],
            confirmed=True,
        )
        msg = loop._build_enriched_user_message("用户请求", contract)
        assert "Requirement Contract" in msg
        assert "设计缓存" in msg
        assert "Python" in msg
        assert "用户请求" in msg

    def test_build_enriched_user_message_without_contract(self):
        """无契约时不包含契约内容"""
        llm = MockLLMClient()
        loop = CacheFirstLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
        )
        msg = loop._build_enriched_user_message("用户请求")
        assert "Requirement Contract" not in msg
        assert "用户请求" in msg

    def test_build_enriched_user_message_unconfirmed_contract(self):
        """未确认的契约不注入"""
        llm = MockLLMClient()
        loop = CacheFirstLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
        )
        contract = RequirementContract(
            raw_goal="设计系统",
            confirmed=False,
        )
        msg = loop._build_enriched_user_message("用户请求", contract)
        assert "Requirement Contract" not in msg


class TestAgentLoopSimpleGoal:
    """AgentLoop 简单目标判定测试"""

    def test_is_simple_goal_commands(self):
        llm = MockLLMClient()
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
        )
        assert loop._is_simple_goal("ls -la") is True
        assert loop._is_simple_goal("git status") is True
        assert loop._is_simple_goal("cat file.py") is True

    def test_is_simple_goal_questions(self):
        llm = MockLLMClient()
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
        )
        assert loop._is_simple_goal("What is Python?") is True
        assert loop._is_simple_goal("这个函数是做什么的？") is True

    def test_is_simple_goal_ambiguous(self):
        llm = MockLLMClient()
        loop = AgentLoop(
            llm=llm,
            tools=MockToolRegistry(),
            memory=MockMemoryEngine(),
        )
        assert loop._is_simple_goal("帮我设计一个系统") is False
        assert loop._is_simple_goal("优化代码") is False


class TestRequirementContractRevision:
    """契约修订与 TaskPlan 集成测试"""

    def test_contract_revision_in_plan(self):
        contract_v1 = RequirementContract(
            raw_goal="设计系统",
            refined_goal="设计缓存",
            hard_constraints=["Python"],
            confirmed=True,
            version=1,
        )
        plan = TaskPlan(goal="设计系统", contract=contract_v1)
        assert plan.contract.version == 1

        # 修订契约
        contract_v2 = contract_v1.revise([{"question": "QPS?", "answer": "1000"}])
        plan.contract = contract_v2
        assert plan.contract.version == 2
        assert plan.contract.confirmed is False
