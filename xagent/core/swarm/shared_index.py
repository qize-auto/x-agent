"""
SharedIndexManager
==================
通过 multiprocessing.shared_memory 在进程间共享 CodeIndexer 数据。

解决 Windows spawn 模式下每个 Worker 重复构建代码索引的问题。
"""
from __future__ import annotations
import pickle
import struct
from multiprocessing import shared_memory
from pathlib import Path
from typing import Optional


class SharedIndexManager:
    """
    管理 CodeIndexer 数据的共享内存生命周期。

    用法：
        # 主进程
        mgr = SharedIndexManager()
        indexer = CodeIndexer("/path/to/project")
        indexer.index_all()
        shm_name = mgr.put_indexer(indexer)

        # Worker 进程（通过名称访问）
        indexer2 = SharedIndexManager.load_indexer(shm_name)
    """

    SHM_PREFIX = "xagent_index_"

    def __init__(self, name: str | None = None):
        self._shm_name = name
        self._shm: Optional[shared_memory.SharedMemory] = None

    # ------------------------------------------------------------------
    # 主进程：序列化并放入共享内存
    # ------------------------------------------------------------------

    @classmethod
    def put_indexer(cls, indexer, name: str | None = None):
        """
        将 CodeIndexer 的核心数据序列化到共享内存。
        返回 (shm_name, shm) 元组。调用方必须保存 shm 引用，
        否则 Windows 下共享内存会立即释放。
        """
        # 只序列化纯数据部分（_files）
        data = {
            "project_root": str(indexer.project_root),
            "files": indexer._files,
        }
        payload = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
        size = len(payload)

        # 4 字节 header = payload 长度
        total_size = 4 + size
        shm_name = name or f"{cls.SHM_PREFIX}{id(indexer)}"

        try:
            # 尝试先清理同名旧共享内存
            old = shared_memory.SharedMemory(name=shm_name)
            old.close()
            old.unlink()
        except FileNotFoundError:
            pass

        shm = shared_memory.SharedMemory(create=True, size=total_size, name=shm_name)
        # 写入 header
        shm.buf[:4] = struct.pack("I", size)
        # 写入 payload
        shm.buf[4:4 + size] = payload
        # 注意：不 close() —— 主进程必须保持引用，直到 Worker 用完
        return shm_name, shm

    # ------------------------------------------------------------------
    # Worker 进程：从共享内存反序列化
    # ------------------------------------------------------------------

    @classmethod
    def load_indexer(cls, shm_name: str):
        """
        从共享内存加载 CodeIndexer 数据，重建轻量 CodeIndexer。
        返回的 CodeIndexer 仅支持查询（search），不支持增量索引。
        """
        shm = shared_memory.SharedMemory(name=shm_name)
        try:
            # 读取 header
            size = struct.unpack("I", bytes(shm.buf[:4]))[0]
            # 读取 payload
            payload = bytes(shm.buf[4:4 + size])
            data = pickle.loads(payload)

            # 重建轻量 CodeIndexer
            from ..code_intel.indexer import CodeIndexer
            indexer = CodeIndexer.__new__(CodeIndexer)
            indexer.project_root = Path(data["project_root"])
            indexer.ignore_patterns = set(CodeIndexer.DEFAULT_IGNORE)
            indexer._files = data["files"]
            indexer._parsers = {}
            indexer._jedi_available = False
            return indexer
        finally:
            shm.close()

    @classmethod
    def cleanup(cls, shm_name: str, shm=None):
        """清理共享内存（由主进程在 shutdown 时调用）"""
        if shm is not None:
            try:
                shm.close()
                shm.unlink()
                return
            except Exception:
                pass
        try:
            shm = shared_memory.SharedMemory(name=shm_name)
            shm.close()
            shm.unlink()
        except FileNotFoundError:
            pass
