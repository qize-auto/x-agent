"""
Task 数据模型单元测试
"""
import unittest
from xagent.core.task import TaskPlan, SubTask


class TestTaskPlan(unittest.TestCase):
    def setUp(self):
        self.plan = TaskPlan(goal="test goal")
        self.plan.subtasks = [
            SubTask(id="1", description="step 1"),
            SubTask(id="2", description="step 2", dependencies=["1"]),
            SubTask(id="3", description="step 3", dependencies=["2"]),
        ]

    def test_total_count(self):
        self.assertEqual(self.plan.total_count(), 3)

    def test_done_count_initially_zero(self):
        self.assertEqual(self.plan.done_count(), 0)

    def test_get_ready_subtasks(self):
        ready = self.plan.get_ready_subtasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "1")

    def test_dependency_resolution(self):
        self.plan.subtasks[0].status = "done"
        ready = self.plan.get_ready_subtasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "2")

    def test_all_done(self):
        for st in self.plan.subtasks:
            st.status = "done"
        self.assertTrue(self.plan.all_done())

    def test_any_failed(self):
        self.assertFalse(self.plan.any_failed())
        self.plan.subtasks[0].status = "failed"
        self.assertTrue(self.plan.any_failed())

    def test_find_subtask(self):
        st = self.plan.find_subtask("2")
        self.assertIsNotNone(st)
        self.assertEqual(st.description, "step 2")
        self.assertIsNone(self.plan.find_subtask("99"))

    def test_to_markdown(self):
        md = self.plan.to_markdown()
        self.assertIn("test goal", md)
        self.assertIn("step 1", md)


if __name__ == "__main__":
    unittest.main()
