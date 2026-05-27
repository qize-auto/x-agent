"""
智能路由单元测试
"""
import unittest
from xagent.core.router import TaskClassifier, ModelRouter


class TestTaskClassifier(unittest.TestCase):
    def setUp(self):
        self.tc = TaskClassifier()

    def test_coding(self):
        self.assertEqual(self.tc.classify("帮我写一个快速排序函数"), "coding")
        self.assertEqual(self.tc.classify("debug this Python script"), "coding")

    def test_reasoning(self):
        self.assertEqual(self.tc.classify("分析一下这个架构的优缺点"), "reasoning")
        self.assertEqual(self.tc.classify("compare React and Vue"), "reasoning")

    def test_search(self):
        self.assertEqual(self.tc.classify("搜索最新的AI新闻"), "search")
        self.assertEqual(self.tc.classify("how to use docker"), "search")

    def test_simple(self):
        self.assertEqual(self.tc.classify("hello"), "simple")
        self.assertEqual(self.tc.classify("hi there"), "simple")


class TestModelRouter(unittest.TestCase):
    def setUp(self):
        self.router = ModelRouter({
            "enabled": True,
            "default_strategy": "balanced",
            "budget_usd_per_turn": 0.05,
        })

    def test_decide_coding(self):
        d = self.router.decide("帮我写一个函数")
        self.assertIn("coding", d.reason)
        self.assertTrue(d.model_id)

    def test_decide_simple(self):
        d = self.router.decide("hello")
        self.assertIn("simple", d.reason)

    def test_budget_enforcement(self):
        # 模拟超预算
        self.router.tracker.turn_total = 0.10
        d = self.router.decide("任何输入")
        self.assertIn("预算", d.reason)

    def test_fallback(self):
        d = self.router.decide("test")
        fb = self.router.get_fallback(d)
        self.assertNotEqual(fb.model_id, d.model_id)

    def test_error_reporting(self):
        self.router.report_error("timeout")
        d = self.router.decide("test")
        self.assertIn("备选", d.reason)
        self.router.reset_error()
        d2 = self.router.decide("test")
        self.assertNotIn("备选", d2.reason)


if __name__ == "__main__":
    unittest.main()
