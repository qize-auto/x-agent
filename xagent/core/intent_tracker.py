"""
Intent Tracker — 意图漂移检测
===============================
连续对话中检测用户意图变化。

设计原则：
- 轻量：延迟加载 embedding 模型
- 可降级：无 sentence-transformers 时使用字符频率向量
- 无侵入：AgentLoop 可选集成
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
import time


@dataclass
class IntentSnapshot:
    text: str
    embedding: list[float]
    timestamp: float = field(default_factory=time.time)


class IntentTracker:
    """
    意图追踪器。

    维护最近的用户输入 snapshot，计算当前输入与历史的语义漂移。
    漂移分数 0.0 ~ 1.0：0 表示完全一致，1 表示完全无关。
    """

    def __init__(
        self,
        max_history: int = 10,
        embed_fn: Callable[[str], list[float]] | None = None,
    ):
        self.history: list[IntentSnapshot] = []
        self.max_history = max_history
        self._embed_fn = embed_fn
        self._embedder = None  # 延迟初始化（sentence-transformers）

    def _get_embedder(self):
        """延迟初始化 sentence-transformers embedder"""
        if self._embedder is None and self._embed_fn is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2"
                )
            except Exception:
                pass
        return self._embedder

    def _embed(self, text: str) -> list[float]:
        """获取文本的 embedding（优先外部函数 -> sentence-transformers -> 降级）"""
        if self._embed_fn:
            return self._embed_fn(text)

        embedder = self._get_embedder()
        if embedder is not None:
            return embedder.encode(text).tolist()

        # 降级：基于字符 ord 的 128 维频率向量
        return self._fallback_embed(text)

    @staticmethod
    def _fallback_embed(text: str) -> list[float]:
        """降级 embedding——字符频率向量（无需外部依赖）"""
        vec = [0.0] * 128
        for c in text:
            vec[ord(c) % 128] += 1.0
        total = sum(v * v for v in vec) ** 0.5
        if total > 0:
            vec = [v / total for v in vec]
        return vec

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def record(self, text: str):
        """记录用户输入到意图历史"""
        emb = self._embed(text)
        self.history.append(IntentSnapshot(text=text, embedding=emb))
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]

    def detect_drift(self, current: str) -> float:
        """
        检测当前输入与近期历史的意图漂移。

        Returns:
            0.0 ~ 1.0 的漂移分数。
        """
        if len(self.history) < 2:
            return 0.0

        current_emb = self._embed(current)
        if not current_emb:
            return 0.0

        recent = self.history[-3:]
        recent_embs = [s.embedding for s in recent if s.embedding]
        if not recent_embs:
            return 0.0

        dim = len(recent_embs[0])
        avg_emb = [
            sum(e[i] for e in recent_embs) / len(recent_embs) for i in range(dim)
        ]

        similarity = self._cosine_similarity(current_emb, avg_emb)
        return max(0.0, min(1.0, 1.0 - similarity))

    def is_drift_significant(self, current: str, threshold: float = 0.5) -> bool:
        """判断当前输入是否发生显著漂移"""
        return self.detect_drift(current) >= threshold
