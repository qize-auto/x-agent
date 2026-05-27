"""
测试 Thought Harvester
======================
覆盖 R1 reasoning_content 的结构化提取
"""
import pytest

from xagent.core.thought_harvester import ThoughtHarvester


class MockLLMClient:
    """模拟 LLM 客户端"""
    def __init__(self, response_content=""):
        self._response = response_content

    def chat(self, messages, **kwargs):
        class FakeResp:
            content = self._response
        return FakeResp()


class TestThoughtHarvesterFast:
    """快速提取测试（规则基础，无 LLM 调用）"""

    def test_empty_reasoning(self):
        h = ThoughtHarvester(llm=None)
        state = h.harvest_fast("")
        assert state == {"subgoals": [], "hypotheses": [], "uncertainties": [], "rejected_paths": []}

    def test_extract_subgoals(self):
        h = ThoughtHarvester(llm=None)
        reasoning = """
        Let me think about this step by step.
        Goal 1: Understand the current codebase structure
        Goal 2: Identify the bug location
        Step 3: Write a fix
        """
        state = h.harvest_fast(reasoning)
        assert len(state["subgoals"]) >= 2
        assert any("Understand" in s for s in state["subgoals"])

    def test_extract_hypotheses(self):
        h = ThoughtHarvester(llm=None)
        reasoning = """
        I have several hypotheses:
        Hypothesis 1: The bug is in the auth module
        Approach 2: Check the database connection first
        """
        state = h.harvest_fast(reasoning)
        assert len(state["hypotheses"]) >= 1

    def test_extract_uncertainties(self):
        h = ThoughtHarvester(llm=None)
        reasoning = """
        I'm uncertain about whether the API supports pagination.
        unclear: what happens when the token limit is reached?
        """
        state = h.harvest_fast(reasoning)
        assert len(state["uncertainties"]) >= 1

    def test_extract_rejected_paths(self):
        h = ThoughtHarvester(llm=None)
        reasoning = """
        I considered rewriting the whole module, but that doesn't work for this timeline.
        abandoned: using a global variable approach
        """
        state = h.harvest_fast(reasoning)
        assert len(state["rejected_paths"]) >= 1

    def test_chinese_reasoning(self):
        h = ThoughtHarvester(llm=None)
        reasoning = """
        首先，我需要理解代码结构。
        方案：直接修改核心模块
        不确定：这个改动会不会影响其他功能？
        排除：重写整个项目（时间不够）
        """
        state = h.harvest_fast(reasoning)
        assert len(state["subgoals"]) >= 1
        assert len(state["hypotheses"]) >= 1
        assert len(state["uncertainties"]) >= 1
        assert len(state["rejected_paths"]) >= 1


class TestThoughtHarvesterLLM:
    """LLM-based 提取测试"""

    def test_llm_extraction_success(self):
        llm = MockLLMClient('{"subgoals": ["a"], "hypotheses": ["b"], "uncertainties": [], "rejected_paths": []}')
        h = ThoughtHarvester(llm=llm)
        state = h.harvest("some reasonably long reasoning content that should trigger the llm extraction because it is definitely more than fifty characters long")
        assert state["subgoals"] == ["a"]
        assert state["hypotheses"] == ["b"]

    def test_llm_extraction_with_json_block(self):
        llm = MockLLMClient('```json\n{"subgoals": ["x"], "hypotheses": [], "uncertainties": ["y"], "rejected_paths": []}\n```')
        h = ThoughtHarvester(llm=llm)
        state = h.harvest("this is a sufficiently long reasoning content to pass the minimum length threshold for llm extraction")
        assert state["subgoals"] == ["x"]
        assert state["uncertainties"] == ["y"]

    def test_llm_extraction_invalid_json_fallback(self):
        llm = MockLLMClient("not valid json at all")
        h = ThoughtHarvester(llm=llm)
        state = h.harvest("reasoning")
        assert state == {"subgoals": [], "hypotheses": [], "uncertainties": [], "rejected_paths": []}

    def test_llm_extraction_short_reasoning_skipped(self):
        llm = MockLLMClient("should not be called")
        h = ThoughtHarvester(llm=llm)
        state = h.harvest("short")
        assert state == {"subgoals": [], "hypotheses": [], "uncertainties": [], "rejected_paths": []}

    def test_llm_extraction_exception_fallback(self):
        class BadLLM:
            def chat(self, **kwargs):
                raise RuntimeError("network error")
        h = ThoughtHarvester(llm=BadLLM())
        state = h.harvest("some reasonably long reasoning content that should trigger llm call")
        assert state == {"subgoals": [], "hypotheses": [], "uncertainties": [], "rejected_paths": []}
