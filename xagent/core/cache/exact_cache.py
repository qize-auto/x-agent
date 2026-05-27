"""
Exact Match Cache
=================
基于 (model, messages_hash, temperature) 的精确匹配缓存。
使用 LRU + TTL 策略，支持磁盘持久化。
"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from typing import Optional


class ExactMatchCache:
    """
    精确匹配缓存。

    缓存键: sha256(model + json(messages) + temperature)
    存储值: {"content", "tool_calls", "usage", "timestamp"}
    """

    def __init__(self, max_size: int = 1000, ttl_sec: float = 3600,
                 persist_dir: str = None):
        self.max_size = max_size
        self.ttl_sec = ttl_sec
        self._cache: dict[str, dict] = {}
        self._access_order: list[str] = []
        self.persist_dir = Path(persist_dir) if persist_dir else None
        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    def _make_key(self, model: str, messages: list[dict], temperature: float) -> str:
        """生成缓存键"""
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, model: str, messages: list[dict], temperature: float) -> Optional[dict]:
        """获取缓存结果"""
        key = self._make_key(model, messages, temperature)
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.get("timestamp", 0) > self.ttl_sec:
            self._evict(key)
            return None
        # 更新访问顺序
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)
        return {
            "content": entry["content"],
            "tool_calls": entry.get("tool_calls", []),
            "usage": entry.get("usage", {}),
        }

    def put(self, model: str, messages: list[dict], temperature: float,
            content: str, tool_calls: list = None, usage: dict = None):
        """存入缓存"""
        key = self._make_key(model, messages, temperature)
        self._cache[key] = {
            "content": content,
            "tool_calls": tool_calls or [],
            "usage": usage or {},
            "timestamp": time.time(),
        }
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)
        self._enforce_lru()
        if self.persist_dir:
            self._save_to_disk()

    def _evict(self, key: str):
        """移除指定缓存项"""
        self._cache.pop(key, None)
        if key in self._access_order:
            self._access_order.remove(key)

    def _enforce_lru(self):
        """强制执行 LRU 淘汰"""
        while len(self._cache) > self.max_size:
            oldest = self._access_order.pop(0)
            self._cache.pop(oldest, None)

    def clear(self):
        """清空缓存"""
        self._cache.clear()
        self._access_order.clear()
        if self.persist_dir:
            for f in self.persist_dir.glob("*.json"):
                f.unlink()

    def stats(self) -> dict:
        """缓存统计"""
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "ttl_sec": self.ttl_sec,
            "persist_dir": str(self.persist_dir) if self.persist_dir else None,
        }

    def _save_to_disk(self):
        """保存缓存到磁盘"""
        for key, entry in self._cache.items():
            filepath = self.persist_dir / f"{key}.json"
            try:
                filepath.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    def _load_from_disk(self):
        """从磁盘加载缓存"""
        if not self.persist_dir.exists():
            return
        for filepath in self.persist_dir.glob("*.json"):
            try:
                key = filepath.stem
                entry = json.loads(filepath.read_text(encoding="utf-8"))
                # TTL 检查
                if time.time() - entry.get("timestamp", 0) <= self.ttl_sec:
                    self._cache[key] = entry
                    self._access_order.append(key)
            except Exception:
                pass
