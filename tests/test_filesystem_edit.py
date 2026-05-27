"""
Filesystem edit_file 原子化测试
"""
import unittest
import tempfile
import os
from xagent.core.tool_registry import ToolRegistry
from xagent.tools.filesystem import register_filesystem_tools


class TestEditFileAtomic(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        register_filesystem_tools(self.registry)

    def test_single_block_edit(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write("def old():\n    pass\n")
            path = f.name
        try:
            result = self.registry.execute('edit_file', {
                'path': path,
                'old_string': 'def old():\n    pass',
                'new_string': 'def new():\n    return 1',
            })
            self.assertIn("已编辑", result['result'])
            with open(path, encoding='utf-8') as f:
                self.assertIn("def new():", f.read())
        finally:
            os.unlink(path)

    def test_multi_block_atomic_rollback(self):
        """多块编辑中任何一块失败时，文件应保持不变"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write("a\nb\nc\n")
            path = f.name
        try:
            multi = """<<<<<<< SEARCH
a
=======
x
>>>>>>> REPLACE

<<<<<<< SEARCH
NOT_FOUND
=======
y
>>>>>>> REPLACE"""
            result = self.registry.execute('edit_file', {
                'path': path,
                'old_string': multi,
                'new_string': '',
            })
            # edit_file 返回字符串错误信息，execute 包装为 ok=True
            self.assertIn("[错误]", result['result'])
            self.assertIn("未找到匹配块 #2", result['result'])
            # 验证文件未被修改
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read(), "a\nb\nc\n")
        finally:
            os.unlink(path)

    def test_multi_block_success(self):
        """多块编辑全部成功"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write("a\nb\nc\n")
            path = f.name
        try:
            multi = """<<<<<<< SEARCH
a
=======
x
>>>>>>> REPLACE

<<<<<<< SEARCH
c
=======
z
>>>>>>> REPLACE"""
            result = self.registry.execute('edit_file', {
                'path': path,
                'old_string': multi,
                'new_string': '',
            })
            self.assertIn("已编辑", result['result'])
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read(), "x\nb\nz\n")
        finally:
            os.unlink(path)

    def test_syntax_check_blocks_write(self):
        """语法检查失败时应拒绝写入"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write("def ok(): pass\n")
            path = f.name
        try:
            result = self.registry.execute('edit_file', {
                'path': path,
                'old_string': 'def ok(): pass',
                'new_string': 'def broken(: pass',  # 语法错误
            })
            self.assertIn("[拒绝]", result['result'])
            self.assertIn("语法检查失败", result['result'])
            # 文件应保持不变
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read(), "def ok(): pass\n")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
