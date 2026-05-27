"""
记忆引擎
=======
基于 ChromaDB 的向量记忆系统。

设计参考：
- Hermes Agent: 周期性提示 + FTS5 会话搜索 + LLM 摘要
- OpenClaw: MEMORY.md + USER.md 文件式记忆
- Claude Code: 服务端隐式记忆

本实现：
- ChromaDB 持久化（纯本地，无需服务端）
- 自动嵌入（sentence-transformers）
- 对话历史 + 代码片段 + 错误模式 + 用户偏好 分类存储
"""
from __future__ import annotations
import json
import threading
import time
from pathlib import Path
from typing import Optional


class MemoryEngine:
    """
    向量记忆引擎
    
    封装 ChromaDB，提供简单的 add/recall 接口。
    如果 ChromaDB 不可用，降级为纯 JSON 存储。
    """

    def __init__(self, persist_dir: str, embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2", half_life_hours: float = 24.0):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model_name = embedding_model
        self.half_life = half_life_hours * 3600  # 转换为秒
        self._chroma_available = False
        self._client = None
        self._collection = None
        self._fallback: list[dict] = []  # 降级存储
        self._lock = threading.Lock()

        self._init_chroma()

    def _init_chroma(self):
        """初始化 ChromaDB"""
        try:
            import chromadb
            from chromadb.config import Settings

            self._client = chromadb.Client(Settings(
                persist_directory=str(self.persist_dir),
                anonymized_telemetry=False,
                is_persistent=True,
            ))
            self._collection = self._client.get_or_create_collection(
                name="xagent_memory",
                metadata={"hnsw:space": "cosine"},
            )
            self._chroma_available = True
        except Exception as e:
            # 降级：使用 JSON 文件
            self._chroma_available = False
            self._load_fallback()

    def _load_fallback(self):
        """加载降级存储"""
        fallback_file = self.persist_dir / "fallback_memory.jsonl"
        if fallback_file.exists():
            with open(fallback_file, "r", encoding="utf-8") as f:
                self._fallback = [json.loads(line) for line in f if line.strip()]

    def _save_fallback(self):
        """保存降级存储"""
        fallback_file = self.persist_dir / "fallback_memory.jsonl"
        with open(fallback_file, "w", encoding="utf-8") as f:
            for item in self._fallback:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def add(self, text: str, memory_type: str = "conversation", metadata: dict = None):
        """
        添加记忆
        
        Args:
            text: 记忆内容
            memory_type: 类型 (conversation/code/error/preference)
            metadata: 额外元数据
        """
        meta = metadata or {}
        meta["type"] = memory_type
        meta["timestamp"] = time.time()

        if self._chroma_available:
            try:
                doc_id = f"{memory_type}_{int(time.time() * 1000)}"
                self._collection.add(
                    documents=[text],
                    metadatas=[meta],
                    ids=[doc_id],
                )
            except Exception:
                self._chroma_available = False
                self._fallback_add(text, meta)
        else:
            self._fallback_add(text, meta)

    def _fallback_add(self, text: str, meta: dict):
        with self._lock:
            self._fallback.append({"text": text, "metadata": meta})
            if len(self._fallback) > 1000:
                self._fallback = self._fallback[-500:]  # 保留最近 500 条
            self._save_fallback()

    def recall(self, query: str, k: int = 5, memory_type: str = None) -> list[dict]:
        """
        语义检索相关记忆（带时间衰减）
        
        排序公式: score = semantic_score * recency_decay
        - semantic_score: 1 / (1 + distance)  (cosine distance 越小越相似)
        - recency_decay: exp(-age / half_life)
        
        Args:
            query: 查询文本
            k: 返回条数
            memory_type: 过滤类型（None=全部）
        
        Returns:
            [{"text": str, "metadata": dict, "score": float}]
        """
        now = time.time()
        items = []

        if self._chroma_available:
            try:
                where_filter = {"type": memory_type} if memory_type else None
                results = self._collection.query(
                    query_texts=[query],
                    n_results=k * 3,
                    where=where_filter,
                )
                for i, doc in enumerate(results["documents"][0]):
                    distance = results["distances"][0][i]
                    semantic_score = 1.0 / (1.0 + distance)
                    timestamp = results["metadatas"][0][i].get("timestamp", now)
                    age = now - timestamp
                    recency_decay = self._decay(age)
                    score = semantic_score * recency_decay
                    items.append({
                        "text": doc,
                        "metadata": results["metadatas"][0][i],
                        "score": score,
                        "distance": distance,
                    })
            except Exception:
                pass

        if not items:
            # 降级检索
            items = self._fallback_recall(query, k * 3, memory_type)
            # 为降级结果计算 score
            for item in items:
                timestamp = item["metadata"].get("timestamp", now)
                age = now - timestamp
                item["score"] = item.get("score", 1.0) * self._decay(age)

        # 按综合分数排序
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[:k]

    def _decay(self, age_seconds: float) -> float:
        """时间衰减函数"""
        import math
        if self.half_life <= 0:
            return 1.0
        return math.exp(-age_seconds / self.half_life)

    def _fallback_recall(self, query: str, k: int, memory_type: Optional[str]) -> list[dict]:
        """降级检索：关键词匹配 + 时间衰减"""
        query_words = query.lower().split()
        scored = []
        for item in self._fallback:
            if memory_type and item["metadata"].get("type") != memory_type:
                continue
            text = item["text"].lower()
            match_score = sum(1 for w in query_words if w in text)
            if match_score > 0:
                scored.append((match_score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"text": s[1]["text"], "metadata": s[1]["metadata"], "score": s[0], "distance": 1.0} for s in scored[:k]]

    def get_recent(self, n: int = 10, memory_type: str = None) -> list[dict]:
        """获取最近的记忆（按时间）"""
        if self._chroma_available:
            try:
                where = {"type": memory_type} if memory_type else None
                results = self._collection.get(where=where, limit=n)
                items = []
                for i, doc in enumerate(results["documents"]):
                    items.append({
                        "text": doc,
                        "metadata": results["metadatas"][i],
                    })
                # 按时间排序
                items.sort(key=lambda x: x["metadata"].get("timestamp", 0), reverse=True)
                return items[:n]
            except Exception:
                pass

        # 降级
        filtered = [item for item in self._fallback if not memory_type or item["metadata"].get("type") == memory_type]
        return [{"text": i["text"], "metadata": i["metadata"]} for i in filtered[-n:]]

    def summarize_conversation(self, llm_client) -> str:
        """
        压缩对话历史：把旧记忆摘要化（参考 Hermes 的 /compress）
        """
        recent = self.get_recent(n=30, memory_type="conversation")
        if len(recent) < 10:
            return ""

        text = "\n".join([f"[{r['metadata'].get('type', '?')}] {r['text'][:200]}" for r in recent])
        prompt = f"请用 3-5 句话总结以下对话的关键信息（决策、结论、待办）:\n\n{text}"

        try:
            resp = llm_client.chat([{"role": "user", "content": prompt}])
            return resp.content
        except Exception:
            return ""

    def forget(self, memory_type: str = None):
        """删除记忆"""
        with self._lock:
            if self._chroma_available:
                try:
                    if memory_type:
                        self._collection.delete(where={"type": memory_type})
                    else:
                        self._client.delete_collection("xagent_memory")
                        self._collection = self._client.get_or_create_collection(
                            name="xagent_memory",
                            metadata={"hnsw:space": "cosine"},
                        )
                except Exception:
                    pass
            if memory_type:
                self._fallback = [i for i in self._fallback if i["metadata"].get("type") != memory_type]
            else:
                self._fallback.clear()
            self._save_fallback()

    def search(self, query: str, k: int = 10, memory_type: str = None) -> list[dict]:
        """显式搜索记忆（供 CLI 使用）"""
        return self.recall(query, k=k, memory_type=memory_type)

    def stats(self) -> dict:
        """记忆统计"""
        result = {"half_life_hours": self.half_life / 3600}
        if self._chroma_available:
            try:
                count = self._collection.count()
                result.update({"total": count, "backend": "chroma"})
                return result
            except Exception:
                pass
        result.update({"total": len(self._fallback), "backend": "fallback"})
        return result
