"""
测试 Intent Tracker
===================
覆盖意图漂移检测的核心行为。
"""
import pytest

from xagent.core.intent_tracker import IntentTracker, IntentSnapshot


class TestIntentTracker:
    """意图追踪器测试"""

    def test_record_adds_snapshot(self):
        """record() 应添加 snapshot 到历史"""
        tracker = IntentTracker()
        tracker.record("hello")
        assert len(tracker.history) == 1
        assert tracker.history[0].text == "hello"

    def test_history_max_size(self):
        """历史应限制在 max_history 条"""
        tracker = IntentTracker(max_history=3)
        for i in range(5):
            tracker.record(f"msg {i}")
        assert len(tracker.history) == 3
        assert tracker.history[-1].text == "msg 4"

    def test_drift_with_similar_inputs(self):
        """相似输入的漂移应接近 0"""
        tracker = IntentTracker()
        tracker.record("帮我写 Python 脚本")
        tracker.record("再帮我写一个 Python 工具")
        drift = tracker.detect_drift("用 Python 处理数据")
        # 相似主题，漂移应较低
        assert drift < 0.5

    def test_drift_with_unrelated_inputs(self):
        """无关输入的漂移应较高"""
        tracker = IntentTracker()
        tracker.record("帮我写 Python 脚本")
        tracker.record("再帮我写一个 Python 工具")
        drift = tracker.detect_drift("今天天气怎么样")
        # 完全无关，漂移应较高
        assert drift > 0.3

    def test_drift_with_insufficient_history(self):
        """历史不足时漂移应为 0"""
        tracker = IntentTracker()
        tracker.record("hello")
        drift = tracker.detect_drift("world")
        assert drift == 0.0

    def test_is_drift_significant(self):
        """is_drift_significant 应正确判断阈值"""
        tracker = IntentTracker()
        tracker.record("帮我写 Python 脚本")
        tracker.record("用 Python 处理数据")
        # 相同主题不应显著漂移
        assert not tracker.is_drift_significant("Python 项目重构", threshold=0.5)

    def test_cosine_similarity_identical(self):
        """相同向量的余弦相似度应为 1"""
        vec = [1.0, 2.0, 3.0]
        sim = IntentTracker._cosine_similarity(vec, vec)
        assert sim == pytest.approx(1.0, abs=1e-6)

    def test_cosine_similarity_orthogonal(self):
        """正交向量的余弦相似度应为 0"""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        sim = IntentTracker._cosine_similarity(a, b)
        assert sim == pytest.approx(0.0, abs=1e-6)

    def test_fallback_embed_produces_vector(self):
        """降级 embedding 应产生 128 维向量"""
        vec = IntentTracker._fallback_embed("hello world")
        assert len(vec) == 128
        # 归一化检查
        norm = sum(v * v for v in vec) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)

    def test_snapshot_dataclass(self):
        """IntentSnapshot 应正确存储数据"""
        snap = IntentSnapshot(text="test", embedding=[0.1, 0.2])
        assert snap.text == "test"
        assert snap.embedding == [0.1, 0.2]
        assert snap.timestamp > 0

    def test_custom_embed_fn(self):
        """应支持自定义 embed 函数"""
        def mock_embed(text: str) -> list[float]:
            return [1.0, 0.0]

        tracker = IntentTracker(embed_fn=mock_embed)
        tracker.record("hello")
        assert tracker.history[0].embedding == [1.0, 0.0]
