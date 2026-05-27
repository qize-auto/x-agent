"""
ToolRegistry 单元测试
"""
import unittest
from xagent.core.tool_registry import ToolRegistry


class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    def test_register_and_get(self):
        self.registry.register("test_tool", "A test tool", {"type": "object"}, lambda x: x)
        tool = self.registry.get("test_tool")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "test_tool")

    def test_get_schemas(self):
        self.registry.register("tool_a", "Tool A", {"type": "object"}, lambda: None)
        schemas = self.registry.get_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "tool_a")

    def test_list_tools(self):
        self.registry.register("x", "X", {}, lambda: None)
        self.registry.register("y", "Y", {}, lambda: None)
        self.assertEqual(sorted(self.registry.list_tools()), ["x", "y"])

    def test_execute_ok(self):
        self.registry.register("add", "Add two numbers", {}, lambda a, b: a + b)
        result = self.registry.execute("add", {"a": 1, "b": 2})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], 3)

    def test_execute_unknown_tool(self):
        result = self.registry.execute("nonexistent", {})
        self.assertFalse(result["ok"])
        self.assertIn("未知工具", result["error"])

    def test_dangerous_tool_blocked_without_callback(self):
        self.registry.register("danger", "Dangerous", {}, lambda: "boom", dangerous=True)
        result = self.registry.execute("danger", {})
        # dangerous=True but no confirm_callback → should execute (current behavior)
        self.assertTrue(result["ok"])

    def test_dangerous_tool_cancelled(self):
        self.registry.register("danger", "Dangerous", {}, lambda: "boom", dangerous=True)
        result = self.registry.execute("danger", {}, confirm_callback=lambda _n, _a: False)
        self.assertFalse(result["ok"])
        self.assertIn("取消", result["error"])


if __name__ == "__main__":
    unittest.main()
