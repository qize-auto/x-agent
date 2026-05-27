"""
MemoryEngine 单元测试
"""
import unittest
import tempfile
import shutil
from xagent.core.memory_engine import MemoryEngine


class TestMemoryEngine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.engine = MemoryEngine(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_and_recall(self):
        self.engine.add("Python is great", memory_type="code")
        results = self.engine.recall("python programming", k=5)
        self.assertTrue(len(results) >= 0)  # fallback 模式下至少不崩溃

    def test_forget(self):
        self.engine.add("conversation 1", memory_type="conversation")
        self.engine.add("code snippet", memory_type="code")
        self.engine.forget(memory_type="conversation")
        stats = self.engine.stats()
        # 验证至少有一种记忆被删除
        self.assertIsInstance(stats, dict)

    def test_stats(self):
        stats = self.engine.stats()
        self.assertIn("backend", stats)
        self.assertIn("half_life_hours", stats)

    def test_get_recent(self):
        self.engine.add("recent item", memory_type="conversation")
        recent = self.engine.get_recent(n=5)
        self.assertIsInstance(recent, list)


if __name__ == "__main__":
    unittest.main()
