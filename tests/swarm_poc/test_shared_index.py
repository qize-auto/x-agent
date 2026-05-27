"""
SharedIndexManager 快速测试
==========================
验证 CodeIndexer 数据可序列化到共享内存并在子进程恢复。
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from xagent.core.code_intel.indexer import CodeIndexer
from xagent.core.swarm.shared_index import SharedIndexManager


def test_indexer_pickle():
    """测试 CodeIndexer 核心数据可 pickle"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试文件
        (Path(tmpdir) / "test.py").write_text("""
def hello():
    pass

class Foo:
    def bar(self):
        pass
""")
        indexer = CodeIndexer(tmpdir)
        files = indexer.index_all()
        assert len(files) == 1

        # 序列化/反序列化
        import pickle
        payload = pickle.dumps({"files": indexer._files})
        data = pickle.loads(payload)
        assert len(data["files"]) == 1
        print("[PASS] indexer_pickle")


def test_shared_memory_roundtrip():
    """测试共享内存读写"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "test.py").write_text("def add(a, b): return a + b\n")
        indexer = CodeIndexer(tmpdir)
        indexer.index_all()

        shm_name, shm_obj = SharedIndexManager.put_indexer(indexer, name="test_shm_001")
        loaded = SharedIndexManager.load_indexer(shm_name)

        assert str(loaded.project_root) == str(indexer.project_root)
        assert len(loaded._files) == 1

        # 验证搜索功能
        results = loaded.search("add")
        assert len(results) == 1
        assert results[0].name == "add"

        SharedIndexManager.cleanup(shm_name, shm_obj)
        print("[PASS] shared_memory_roundtrip")


def test_load_without_parser():
    """验证加载的 indexer 没有 Parser 但仍然可查询"""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "a.py").write_text("class A: pass\n")
        (Path(tmpdir) / "b.py").write_text("class B: pass\n")
        indexer = CodeIndexer(tmpdir)
        indexer.index_all()

        shm_name, shm_obj = SharedIndexManager.put_indexer(indexer)
        loaded = SharedIndexManager.load_indexer(shm_name)

        # 无 Parser 仍可搜索
        assert len(loaded.search("A")) == 1
        assert len(loaded.search("B")) == 1
        assert loaded._parsers == {}
        assert loaded._jedi_available is False

        SharedIndexManager.cleanup(shm_name, shm_obj)
        print("[PASS] load_without_parser")


def main():
    print("=" * 50)
    print("SharedIndexManager 测试")
    print("=" * 50)
    test_indexer_pickle()
    test_shared_memory_roundtrip()
    test_load_without_parser()
    print("=" * 50)
    print("All tests PASSED")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
