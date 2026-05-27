"""
Semantic Cache
==============
基于 Embedding 相似度的缓存。
使用 sentence-transformers 生成 query 向量，
用 cosine similarity 判断语义等价性。
"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from typing import Optional


class SemanticCache:
    """
    语义缓存。

    缓存结构：
        {
            "embedding_key": {
                "embedding": list[float],
                "response": {"content", "tool_calls", "usage"},
                "timestamp": float,
                "model": str,
            }
        }

    查找时：计算 query embedding → 与所有缓存项比较 cosine similarity →
           超过 threshold 则返回对应 response。
    """

    def __init__(self, similarity_threshold: float = 0.92, ttl_sec: float = 3600,
                 max_entries: int = 500, persist_dir: str = None,
                 embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.threshold = similarity_threshold
        self.ttl_sec = ttl_sec
        self.max_entries = max_entries
        self._entries: list[dict] = []
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self._embedding_model = embedding_model
        self._embedding_fn = None
        self._init_embedding()
        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    def _init_embedding(self):
        """延迟初始化 embedding 模型"""
        try:
            from sentence_transformers import SentenceTransformer
            self._embedding_fn = SentenceTransformer(self._embedding_model)
        except Exception:
            self._embedding_fn = None

    def _embed(self, text: str) -> Optional[list[float]]:
        if self._embedding_fn is None:
            return None
        try:
            vec = self._embedding_fn.encode(text, convert_to_numpy=True)
            return vec.tolist()
        except Exception:
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def get(self, query_text: str, model: str = "") -> Optional[dict]:
        """查找语义相似的缓存结果"""
        query_vec = self._embed(query_text)
        if query_vec is None:
            return None

        best_match = None
        best_score = -1.0
        now = time.time()

        for entry in list(self._entries):
            if now - entry["timestamp"] > self.ttl_sec:
                continue
            score = self._cosine_similarity(query_vec, entry["embedding"])
            if score > best_score:
                best_score = score
                best_match = entry

        if best_match and best_score >= self.threshold:
            return {
                "content": best_match["response"]["content"],
                "tool_calls": best_match["response"].get("tool_calls", []),
                "usage": best_match["response"].get("usage", {}),
                "similarity": round(best_score, 4),
            }
        return None

    def put(self, query_text: str, model: str, content: str,
            tool_calls: list = None, usage: dict = None):
        """存入语义缓存"""
        vec = self._embed(query_text)
        if vec is None:
            return

        self._entries.append({
            "embedding": vec,
            "response": {
                "content": content,
                "tool_calls": tool_calls or [],
                "usage": usage or {},
            },
            "timestamp": time.time(),
            "model": model,
        })

        # 强制执行容量限制（淘汰最旧的）
        self._enforce_capacity()
        if self.persist_dir:
            self._save_to_disk()

    def _enforce_capacity(self):
        now = time.time()
        # 先淘汰过期的
        self._entries = [e for e in self._entries if now - e["timestamp"] <= self.ttl_sec]
        # 再按数量淘汰
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def clear(self):
        self._entries.clear()
        if self.persist_dir:
            for f in self.persist_dir.glob("*.jsonl"):
                f.unlink()

    def stats(self) -> dict:
        return {
            "entries": len(self._entries),
            "max_entries": self.max_entries,
            "threshold": self.threshold,
            "ttl_sec": self.ttl_sec,
            "embedding_model": self._embedding_model,
            "embedding_available": self._embedding_fn is not None,
        }

    def _save_to_disk(self):
        filepath = self.persist_dir / "semantic_cache.jsonl"
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _load_from_disk(self):
        filepath = self.persist_dir / "semantic_cache.jsonl"
        if not filepath.exists():
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if time.time() - entry.get("timestamp", 0) <= self.ttl_sec:
                        self._entries.append(entry)
        except Exception:
            pass
