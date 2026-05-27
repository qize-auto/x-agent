"""
测试 Clarification Engine
==========================
覆盖歧义检测、问题生成、契约构建等核心行为。
"""
import pytest
import json

from xagent.core.clarification_engine import (
    ClarificationEngine,
    CLARIFICATION_TEMPLATES,
)
from xagent.core.requirement_contract import RequirementContract


class MockLLMClient:
    """模拟 LLM 客户端"""

    def __init__(self, response_content=""):
        self._response = response_content
        self.last_call_messages = None
        self.last_call_model_id = None

    def chat(self, messages, **kwargs):
        self.last_call_messages = messages
        self.last_call_model_id = kwargs.get("model_id")

        class FakeResp:
            content = self._response

        return FakeResp()


class TestNeedsClarification:
    """歧义检测测试"""

    def test_simple_commands_no_clarification(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        assert engine.needs_clarification("ls -la") is False
        assert engine.needs_clarification("git status") is False
        assert engine.needs_clarification("git log --oneline") is False
        assert engine.needs_clarification("cat file.py") is False
        assert engine.needs_clarification("grep foo bar.py") is False

    def test_simple_chinese_commands_no_clarification(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        assert engine.needs_clarification("读取文件") is False
        assert engine.needs_clarification("查看日志") is False
        assert engine.needs_clarification("运行测试") is False

    def test_pure_questions_no_clarification(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        assert engine.needs_clarification("What is Python?") is False
        assert engine.needs_clarification("这个函数是做什么的？") is False

    def test_ambiguous_goals_need_clarification(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        assert engine.needs_clarification("帮我设计一个系统") is True
        assert engine.needs_clarification("给我做个工具") is True
        assert engine.needs_clarification("做一个网站") is True
        assert engine.needs_clarification("优化一下代码") is True
        assert engine.needs_clarification("处理这个数据") is True

    def test_vague_modifiers_need_clarification(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        assert engine.needs_clarification("最好使用什么方案") is True
        assert engine.needs_clarification("最合适的方式") is True
        assert engine.needs_clarification("等等之类的功能") is True
        assert engine.needs_clarification("差不多的效果") is True

    def test_short_goals_need_clarification(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        assert engine.needs_clarification("fix it") is True
        assert engine.needs_clarification("更新") is True

    def test_file_reading_no_clarification(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        # 包含具体文件路径的读取操作被视为具体目标
        assert engine.needs_clarification("read main.py 第 42 行") is False
        assert engine.needs_clarification("show utils.py 中的 sort 函数") is False

    def test_empty_and_none_input(self):
        engine = ClarificationEngine(llm=MockLLMClient())
        assert engine.needs_clarification("") is False
        assert engine.needs_clarification("   ") is False
        assert engine.needs_clarification(None) is False


class TestGenerateQuestions:
    """问题生成测试"""

    def test_uses_cheap_model(self):
        llm = MockLLMClient("Q1\nQ2")
        engine = ClarificationEngine(llm=llm, cheap_model_id="cheap-model")
        engine.generate_questions("设计系统")
        assert llm.last_call_model_id == "cheap-model"

    def test_respects_max_questions(self):
        llm = MockLLMClient("Q1\nQ2\nQ3\nQ4\nQ5\nQ6")
        engine = ClarificationEngine(llm=llm, max_questions=3)
        qs = engine.generate_questions("设计系统")
        assert len(qs) <= 3

    def test_falls_back_to_template_on_llm_error(self):
        class BrokenLLM:
            def chat(self, messages, **kwargs):
                raise RuntimeError("API error")

        engine = ClarificationEngine(llm=BrokenLLM(), max_questions=2)
        qs = engine.generate_questions("设计系统", task_type="coding")
        assert len(qs) <= 2
        assert all(isinstance(q, str) for q in qs)

    def test_default_task_type(self):
        llm = MockLLMClient("What is the goal?\nAny constraints?")
        engine = ClarificationEngine(llm=llm)
        qs = engine.generate_questions("test")
        assert len(qs) == 2

    def test_coding_task_type(self):
        llm = MockLLMClient("What language?\nAny dependencies?")
        engine = ClarificationEngine(llm=llm)
        qs = engine.generate_questions("写代码", task_type="coding")
        assert len(qs) == 2
        # 验证 prompt 中包含 coding 相关信息
        prompt = llm.last_call_messages[0]["content"]
        assert "coding" in prompt
        assert "implementation details" in prompt


class TestBuildContract:
    """契约构建测试"""

    def test_build_contract_from_qa(self):
        llm = MockLLMClient(json.dumps({
            "refined_goal": "设计 Python 单节点缓存",
            "hard_constraints": ["Python", "单节点"],
            "soft_preferences": ["有单元测试"],
            "out_of_scope": ["分布式"],
            "acceptance_criteria": ["测试通过"],
        }))
        engine = ClarificationEngine(llm=llm)
        contract = engine.build_contract(
            goal="设计缓存系统",
            questions=["什么语言？", "规模？"],
            answers=["Python", "单节点"],
        )

        assert isinstance(contract, RequirementContract)
        assert contract.raw_goal == "设计缓存系统"
        assert contract.refined_goal == "设计 Python 单节点缓存"
        assert "Python" in contract.hard_constraints
        assert "单节点" in contract.hard_constraints
        assert "有单元测试" in contract.soft_preferences
        assert "分布式" in contract.out_of_scope
        assert "测试通过" in contract.acceptance_criteria
        assert len(contract.clarifications) == 2

    def test_build_contract_uses_cheap_model(self):
        llm = MockLLMClient(json.dumps({
            "refined_goal": "test",
            "hard_constraints": [],
            "soft_preferences": [],
            "out_of_scope": [],
            "acceptance_criteria": [],
        }))
        engine = ClarificationEngine(llm=llm, cheap_model_id="cheap")
        engine.build_contract("goal", ["Q"], ["A"])
        assert llm.last_call_model_id == "cheap"

    def test_build_contract_fallback_on_llm_error(self):
        class BrokenLLM:
            def chat(self, messages, **kwargs):
                raise RuntimeError("API down")

        engine = ClarificationEngine(llm=BrokenLLM())
        contract = engine.build_contract(
            goal="设计系统",
            questions=["Q1", "Q2"],
            answers=["A1", "A2"],
        )

        # 降级：返回基本契约
        assert contract.raw_goal == "设计系统"
        assert contract.refined_goal == "设计系统"
        assert len(contract.clarifications) == 2
        assert contract.hard_constraints == []

    def test_build_contract_extracts_json_from_code_block(self):
        llm = MockLLMClient(
            '```json\n{"refined_goal":"x","hard_constraints":["a"],'
            '"soft_preferences":[],"out_of_scope":[],"acceptance_criteria":[]}\n```'
        )
        engine = ClarificationEngine(llm=llm)
        contract = engine.build_contract("goal", ["Q"], ["A"])
        assert contract.refined_goal == "x"
        assert contract.hard_constraints == ["a"]

    def test_build_contract_extracts_json_from_generic_code_block(self):
        llm = MockLLMClient(
            '```\n{"refined_goal":"y","hard_constraints":[],'
            '"soft_preferences":[],"out_of_scope":[],"acceptance_criteria":[]}\n```'
        )
        engine = ClarificationEngine(llm=llm)
        contract = engine.build_contract("goal", ["Q"], ["A"])
        assert contract.refined_goal == "y"


class TestEngineConfiguration:
    """引擎配置测试"""

    def test_max_questions_bounds(self):
        # 小于 1 时取 1
        engine = ClarificationEngine(llm=MockLLMClient(), max_questions=0)
        assert engine.max_questions == 1

        # 大于 5 时取 5
        engine = ClarificationEngine(llm=MockLLMClient(), max_questions=10)
        assert engine.max_questions == 5

        # 正常值
        engine = ClarificationEngine(llm=MockLLMClient(), max_questions=3)
        assert engine.max_questions == 3

    def test_templates_exist_for_known_types(self):
        for task_type in [
            "coding", "architecture", "refactoring", "debugging",
            "data_cleaning", "devops", "writing", "default",
        ]:
            assert task_type in CLARIFICATION_TEMPLATES
            assert "questions" in CLARIFICATION_TEMPLATES[task_type]
            assert "focus" in CLARIFICATION_TEMPLATES[task_type]
            assert len(CLARIFICATION_TEMPLATES[task_type]["questions"]) >= 2


class TestCounterfactualMode:
    """反事实模式 (architect mode) 测试"""

    def test_architect_mode_includes_counterfactual_instruction(self):
        """architect 模式下 prompt 应包含反事实指令"""
        llm = MockLLMClient("Q1\nQ2")
        engine = ClarificationEngine(llm=llm)
        questions = engine.generate_questions("设计系统", mode="architect")
        
        # 验证 LLM 收到的 prompt 包含 ARCHITECT 关键字
        assert llm.last_call_messages is not None
        prompt = llm.last_call_messages[0]["content"]
        assert "ARCHITECT" in prompt
        assert "counterfactual" in prompt.lower()
        assert len(questions) == 2

    def test_standard_mode_no_counterfactual_instruction(self):
        """standard 模式下 prompt 不应包含反事实指令"""
        llm = MockLLMClient("Q1\nQ2")
        engine = ClarificationEngine(llm=llm)
        questions = engine.generate_questions("设计系统", mode="standard")
        
        prompt = llm.last_call_messages[0]["content"]
        assert "ARCHITECT" not in prompt
        assert len(questions) == 2

    def test_architect_mode_passed_through_build_contract(self):
        """build_contract 接收 mode 参数不抛异常"""
        llm = MockLLMClient(
            '{"refined_goal":"x","hard_constraints":[],'
            '"soft_preferences":[],"out_of_scope":[],"acceptance_criteria":[]}'
        )
        engine = ClarificationEngine(llm=llm)
        contract = engine.build_contract("goal", ["Q"], ["A"], mode="architect")
        assert contract.refined_goal == "x"
