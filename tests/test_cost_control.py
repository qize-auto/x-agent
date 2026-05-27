"""
测试 Cost Control 组件
======================
覆盖 CostController 和 ContextCompressor
"""
import pytest

from xagent.core.cost_control import CostController, CostControlConfig, ContextCompressor


class TestCostController:
    """成本控制器测试"""

    def test_default_preset_flash(self):
        cc = CostController()
        model = cc.select_model()
        assert "flash" in model

    def test_preset_pro(self):
        cc = CostController()
        cc.set_preset("pro")
        model = cc.select_model()
        assert "pro" in model

    def test_arm_pro_single_turn(self):
        """/pro 武装后只生效一回合"""
        cc = CostController(config=CostControlConfig(pro_single_turn=True))
        cc.arm_pro()
        assert cc.pro_armed is True

        model1 = cc.select_model()
        assert "pro" in model1
        assert cc.pro_armed is False  # 自动解除

        model2 = cc.select_model()
        assert "flash" in model2  # 恢复默认

    def test_failure_signal_escalation(self):
        """失败信号达到阈值自动升级"""
        config = CostControlConfig(auto_escalation=True, escalation_threshold=2)
        cc = CostController(config=config)

        cc.record_failure_signal("search_not_found")
        model = cc.select_model()
        assert "flash" in model  # 未达阈值

        cc.record_failure_signal("tool_call_repair")
        model = cc.select_model()
        assert "pro" in model  # 达到阈值，升级

    def test_failure_signal_not_triggered(self):
        """未达阈值不升级"""
        config = CostControlConfig(auto_escalation=True, escalation_threshold=5)
        cc = CostController(config=config)

        cc.record_failure_signal("search_not_found")
        cc.record_failure_signal("tool_call_repair")
        model = cc.select_model()
        assert "flash" in model

    def test_unknown_signal_ignored(self):
        """未知信号不计入失败"""
        cc = CostController()
        result = cc.record_failure_signal("some_random_event")
        assert result is False
        assert cc.failure_count_this_turn == 0

    def test_reset_turn(self):
        cc = CostController()
        cc.record_failure_signal("search_not_found")
        cc.record_failure_signal("tool_call_repair")
        cc.reset_turn()
        assert cc.failure_count_this_turn == 0
        assert cc.pro_escalated is False

    def test_escalation_persists_within_turn(self):
        """自动升级状态在回合内持续"""
        config = CostControlConfig(auto_escalation=True, escalation_threshold=1)
        cc = CostController(config=config)

        cc.record_failure_signal("search_not_found")
        model1 = cc.select_model()
        assert "pro" in model1

        # 再次选择，仍然用 pro
        model2 = cc.select_model()
        assert "pro" in model2

    def test_invalid_preset_rejected(self):
        cc = CostController()
        result = cc.set_preset("invalid")
        assert result is False
        assert cc.current_preset == "flash"

    def test_should_compact(self):
        config = CostControlConfig(
            context_window_tokens=100000,
            context_ratio_proactive=0.4,
            context_ratio_emergency=0.8,
        )
        cc = CostController(config=config)

        assert cc.should_compact(10000) == "none"      # 10%
        assert cc.should_compact(50000) == "proactive"  # 50%
        assert cc.should_compact(90000) == "emergency"  # 90%

    def test_should_compact_disabled(self):
        config = CostControlConfig(turn_end_compaction=False)
        cc = CostController(config=config)
        assert cc.should_compact(999999) == "none"

    def test_get_stats(self):
        cc = CostController()
        cc.arm_pro()
        cc.record_failure_signal("search_not_found")
        stats = cc.get_stats()
        assert stats["preset"] == "flash"
        assert stats["pro_armed"] is True
        assert stats["failure_count_this_turn"] == 1


class TestContextCompressor:
    """上下文压缩器测试"""

    def test_short_content_not_compacted(self):
        """短内容不应被压缩"""
        comp = ContextCompressor(llm_client=None, threshold_tokens=100)
        messages = [
            {"role": "tool", "content": "short output"},
        ]
        result = comp.compact_messages(messages)
        assert result[0]["content"] == "short output"

    def test_long_content_compacted(self):
        """长内容应被压缩"""
        comp = ContextCompressor(llm_client=None, threshold_tokens=10)
        long_content = "line1\nline2\nline3\nline4\nline5\n" + "x" * 200
        messages = [
            {"role": "tool", "content": long_content},
        ]
        result = comp.compact_messages(messages)
        assert "COMPACTED" in result[0]["content"]
        assert "Summary:" in result[0]["content"]

    def test_non_tool_messages_unchanged(self):
        """非 tool 消息不应被修改"""
        comp = ContextCompressor(llm_client=None, threshold_tokens=10)
        messages = [
            {"role": "user", "content": "x" * 1000},
            {"role": "assistant", "content": "x" * 1000},
        ]
        result = comp.compact_messages(messages)
        assert result[0]["content"] == "x" * 1000
        assert result[1]["content"] == "x" * 1000

    def test_estimate_tokens(self):
        """token 估算测试"""
        messages = [
            {"role": "user", "content": "abcd" * 10},  # 40 chars -> ~10 tokens
        ]
        assert ContextCompressor.estimate_tokens(messages) == 10

    def test_does_not_modify_original(self):
        """不应修改原始消息列表"""
        comp = ContextCompressor(llm_client=None, threshold_tokens=10)
        original = [{"role": "tool", "content": "x" * 500}]
        result = comp.compact_messages(original)
        assert original[0]["content"] == "x" * 500
        assert "COMPACTED" in result[0]["content"]
