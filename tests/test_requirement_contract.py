"""
测试 Requirement Contract
=========================
覆盖需求契约的数据模型、序列化、修订等核心行为。
"""
import pytest
import time

from xagent.core.requirement_contract import RequirementContract


class TestRequirementContractCreation:
    """契约创建测试"""

    def test_minimal_creation(self):
        c = RequirementContract(raw_goal="设计系统")
        assert c.raw_goal == "设计系统"
        assert c.refined_goal == ""
        assert c.hard_constraints == []
        assert c.confirmed is False
        assert c.version == 1

    def test_full_creation(self):
        c = RequirementContract(
            raw_goal="设计系统",
            refined_goal="设计单节点缓存系统",
            hard_constraints=["必须用 Python", "兼容 Windows"],
            soft_preferences=["希望有单元测试"],
            out_of_scope=["不做前端"],
            acceptance_criteria=["所有测试通过"],
            clarifications=[{"question": "Q1", "answer": "A1"}],
        )
        assert c.refined_goal == "设计单节点缓存系统"
        assert len(c.hard_constraints) == 2
        assert len(c.clarifications) == 1


class TestRequirementContractConfirm:
    """契约确认测试"""

    def test_confirm_sets_flag_and_timestamp(self):
        c = RequirementContract(raw_goal="test")
        assert c.confirmed is False
        assert c.confirmed_at == 0.0

        c.confirm()
        assert c.confirmed is True
        assert c.confirmed_at > 0
        assert c.confirmed_at <= time.time()


class TestRequirementContractRevise:
    """契约修订测试"""

    def test_revise_increments_version(self):
        c = RequirementContract(raw_goal="test", refined_goal="v1", version=1)
        c.confirm()
        c2 = c.revise([{"question": "Q2", "answer": "A2"}])

        assert c2.version == 2
        assert c2.raw_goal == c.raw_goal
        assert c2.refined_goal == c.refined_goal
        assert c2.confirmed is False  # 修订后需重新确认
        assert len(c2.clarifications) == 1

    def test_revise_does_not_mutate_original(self):
        c = RequirementContract(raw_goal="test", hard_constraints=["a"])
        c.confirm()
        c2 = c.revise([{"question": "Q", "answer": "A"}])

        # 原契约不受影响
        assert c.version == 1
        assert c.confirmed is True
        assert len(c.hard_constraints) == 1

    def test_revise_preserves_lists(self):
        c = RequirementContract(
            raw_goal="test",
            hard_constraints=["x"],
            soft_preferences=["y"],
            out_of_scope=["z"],
            acceptance_criteria=["w"],
        )
        c2 = c.revise([])
        assert c2.hard_constraints == ["x"]
        assert c2.soft_preferences == ["y"]
        assert c2.out_of_scope == ["z"]
        assert c2.acceptance_criteria == ["w"]


class TestRequirementContractContextString:
    """上下文字符串生成测试"""

    def test_basic_context(self):
        c = RequirementContract(raw_goal="设计缓存系统")
        s = c.to_context_string()
        assert "Requirement Contract" in s
        assert "设计缓存系统" in s

    def test_context_with_refined_goal(self):
        c = RequirementContract(
            raw_goal="设计系统",
            refined_goal="设计单节点缓存系统",
        )
        s = c.to_context_string()
        assert "Refined Goal: 设计单节点缓存系统" in s

    def test_context_includes_all_sections(self):
        c = RequirementContract(
            raw_goal="test",
            hard_constraints=["Python"],
            soft_preferences=["测试"],
            out_of_scope=["前端"],
            acceptance_criteria=["通过"],
        )
        s = c.to_context_string()
        assert "Hard Constraints:" in s
        assert "Python" in s
        assert "Soft Preferences:" in s
        assert "测试" in s
        assert "Out of Scope:" in s
        assert "前端" in s
        assert "Acceptance Criteria:" in s
        assert "通过" in s

    def test_context_omits_empty_sections(self):
        c = RequirementContract(raw_goal="test")
        s = c.to_context_string()
        assert "Hard Constraints:" not in s
        assert "Soft Preferences:" not in s


class TestRequirementContractMarkdown:
    """Markdown 生成测试"""

    def test_markdown_basic(self):
        c = RequirementContract(raw_goal="test")
        md = c.to_markdown()
        assert "Requirement Contract" in md
        assert "test" in md
        assert "⏳ Draft" in md

    def test_markdown_confirmed(self):
        c = RequirementContract(raw_goal="test")
        c.confirm()
        md = c.to_markdown()
        assert "✅ Confirmed" in md

    def test_markdown_sections(self):
        c = RequirementContract(
            raw_goal="test",
            hard_constraints=["HC"],
            soft_preferences=["SP"],
            out_of_scope=["OOS"],
            acceptance_criteria=["AC"],
            clarifications=[{"question": "Q", "answer": "A"}],
        )
        md = c.to_markdown()
        assert "Hard Constraints" in md
        assert "HC" in md
        assert "Soft Preferences" in md
        assert "SP" in md
        assert "Out of Scope" in md
        assert "OOS" in md
        assert "Acceptance Criteria" in md
        assert "AC" in md
        assert "Clarification History" in md
        assert "Q:" in md
        assert "A:" in md


class TestRequirementContractSerialization:
    """序列化测试"""

    def test_to_dict(self):
        c = RequirementContract(
            raw_goal="test",
            refined_goal="refined",
            hard_constraints=["a"],
            confirmed=True,
            version=2,
        )
        d = c.to_dict()
        assert d["raw_goal"] == "test"
        assert d["refined_goal"] == "refined"
        assert d["hard_constraints"] == ["a"]
        assert d["confirmed"] is True
        assert d["version"] == 2

    def test_from_dict_roundtrip(self):
        original = RequirementContract(
            raw_goal="test",
            refined_goal="refined",
            hard_constraints=["a", "b"],
            soft_preferences=["c"],
            clarifications=[{"q": "Q", "a": "A"}],
            version=3,
            confirmed=True,
            confirmed_at=123456.0,
        )
        d = original.to_dict()
        restored = RequirementContract.from_dict(d)

        assert restored.raw_goal == original.raw_goal
        assert restored.refined_goal == original.refined_goal
        assert restored.hard_constraints == original.hard_constraints
        assert restored.soft_preferences == original.soft_preferences
        assert restored.clarifications == original.clarifications
        assert restored.version == original.version
        assert restored.confirmed == original.confirmed
        assert restored.confirmed_at == original.confirmed_at

    def test_from_dict_ignores_extra_keys(self):
        """向前兼容：忽略字典中多余的字段"""
        d = {
            "raw_goal": "test",
            "refined_goal": "refined",
            "hard_constraints": [],
            "soft_preferences": [],
            "out_of_scope": [],
            "acceptance_criteria": [],
            "clarifications": [],
            "version": 1,
            "confirmed": False,
            "confirmed_at": 0.0,
            "future_field": "should be ignored",
        }
        c = RequirementContract.from_dict(d)
        assert c.raw_goal == "test"
        assert not hasattr(c, "future_field")

    def test_from_dict_missing_keys_use_defaults(self):
        """向后兼容：缺失字段使用默认值"""
        d = {"raw_goal": "minimal"}
        c = RequirementContract.from_dict(d)
        assert c.raw_goal == "minimal"
        assert c.refined_goal == ""
        assert c.hard_constraints == []
        assert c.confirmed is False


class TestRequirementContractRepr:
    """__repr__ 测试"""

    def test_draft_repr(self):
        c = RequirementContract(raw_goal="test")
        assert "draft" in repr(c)
        assert "v1" in repr(c)

    def test_confirmed_repr(self):
        c = RequirementContract(raw_goal="test")
        c.confirm()
        assert "confirmed" in repr(c)
