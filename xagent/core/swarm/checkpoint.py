"""
Swarm Checkpoint 存储
=====================
节点级快照持久化，支持文件系统 + 可选 Redis。
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class SwarmCheckpoint:
    """Swarm 节点执行检查点"""
    checkpoint_id: str
    task_id: str
    status: str                       # pending | running | completed | failed
    created_at: float
    updated_at: float
    node_id: str = ""                 # WorkflowNode.id
    result: dict | None = None
    error: str | None = None
    retry_count: int = 0


class CheckpointStore:
    """
    Checkpoint 持久化存储。

    默认使用文件系统（单机无需外部依赖），可选 Redis 用于分布式。
    """

    def __init__(self, base_dir: Path, redis_url: str | None = None):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._redis = None
        if redis_url:
            try:
                import redis as _redis
                self._redis = _redis.from_url(redis_url)
            except Exception:
                pass  # Redis 不可用，降级为纯文件系统

    def _path(self, checkpoint_id: str) -> Path:
        return self.base_dir / f"{checkpoint_id}.json"

    def save(self, cp: SwarmCheckpoint) -> None:
        """保存或更新 checkpoint"""
        cp.updated_at = time.time()
        data = asdict(cp)
        path = self._path(cp.checkpoint_id)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if self._redis:
            try:
                self._redis.setex(f"cp:{cp.checkpoint_id}", 3600, json.dumps(data))
            except Exception:
                pass

    def load(self, checkpoint_id: str) -> Optional[SwarmCheckpoint]:
        """加载 checkpoint"""
        if self._redis:
            try:
                raw = self._redis.get(f"cp:{checkpoint_id}")
                if raw:
                    return SwarmCheckpoint(**json.loads(raw))
            except Exception:
                pass
        path = self._path(checkpoint_id)
        if not path.exists():
            return None
        return SwarmCheckpoint(**json.loads(path.read_text(encoding="utf-8")))

    def list_all(self) -> list[SwarmCheckpoint]:
        """列出所有 checkpoint"""
        cps = []
        for p in self.base_dir.glob("*.json"):
            try:
                cps.append(SwarmCheckpoint(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return sorted(cps, key=lambda x: x.updated_at, reverse=True)

    def delete(self, checkpoint_id: str) -> bool:
        """删除 checkpoint"""
        path = self._path(checkpoint_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def cleanup_old(self, max_age_sec: float = 86400) -> int:
        """清理超过 max_age_sec 的旧 checkpoint（基于 created_at）"""
        now = time.time()
        removed = 0
        for p in self.base_dir.glob("*.json"):
            try:
                cp = SwarmCheckpoint(**json.loads(p.read_text(encoding="utf-8")))
                if now - cp.created_at > max_age_sec:
                    p.unlink()
                    removed += 1
            except Exception:
                continue
        return removed
