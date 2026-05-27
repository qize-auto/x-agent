"""
智能模型路由 (Auto-Router)
===========================
根据任务类型、成本预算、模型可用性自动选择最优 LLM。

设计原则：
- 任务分类器：基于关键词快速分类（coding/reasoning/creative/search/simple）
- 路由策略：cost_first / quality_first / balanced
- 自动降级：主模型失败（rate limit / timeout）时切换到备选模型
- 成本追踪：每轮累计，超预算时强制降级到廉价模型
"""
from __future__ import annotations
import time
from typing import Callable
from dataclasses import dataclass, field


# 模型成本估算 ($/1M tokens) —— 仅用于路由决策，非精确计费
MODEL_PRICING = {
    # (input_price, output_price, quality_score 1-10)
    "openai/gpt-4o": (2.50, 10.00, 9),
    "anthropic/claude-3.5-sonnet": (3.00, 15.00, 10),
    "moonshot/kimi-k2.5": (0.60, 2.50, 8),
    "deepseek/deepseek-chat": (0.14, 0.28, 7),
    "deepseek/deepseek-r1": (0.55, 2.19, 8),
    "google/gemini-flash-1.5": (0.075, 0.30, 6),
    "openai/gpt-4o-mini": (0.15, 0.60, 6),
}


@dataclass
class RoutingDecision:
    """路由决策结果"""
    model_id: str
    provider: str
    reason: str
    estimated_cost: float
    quality_score: int


@dataclass
class CostTracker:
    """成本追踪器"""
    session_total: float = 0.0
    turn_total: float = 0.0
    history: list[dict] = field(default_factory=list)

    def add(self, model_id: str, prompt_tokens: int, completion_tokens: int):
        price = MODEL_PRICING.get(model_id, (0.5, 0.5))
        cost = (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000
        self.turn_total += cost
        self.session_total += cost
        self.history.append({
            "model_id": model_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(cost, 6),
            "timestamp": time.time(),
        })

    def reset_turn(self):
        self.turn_total = 0.0

    def summary(self) -> dict:
        return {
            "session_total_usd": round(self.session_total, 4),
            "turn_total_usd": round(self.turn_total, 4),
            "call_count": len(self.history),
        }


class TaskClassifier:
    """任务分类器 —— 基于规则快速分类"""

    CATEGORIES = {
        "coding": {
            "keywords": [
                "代码", "编程", "bug", "fix", "refactor", "函数", "类", "模块",
                "排序", "算法", "数据结构", "接口", "实现", "调用",
                "code", "programming", "function", "class", "import", "def ",
                "error", "exception", "traceback", "lint", "test", "debug",
                "git", "commit", "merge", "pull request", "pr ",
            ],
            "file_exts": [".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp", ".c", ".h"],
        },
        "reasoning": {
            "keywords": [
                "分析", "比较", "评估", "为什么", "原因", "逻辑",
                "analyze", "compare", "evaluate", "why", "reason", "logic",
                "architecture", "design pattern", "trade-off", "pros and cons",
            ],
        },
        "creative": {
            "keywords": [
                "写", "创作", "生成", "故事", "文案", "诗歌",
                "write", "create", "generate", "story", "poem", "draft",
                "email", "letter", "blog", "article", "content",
            ],
        },
        "search": {
            "keywords": [
                "搜索", "查找", "最新", "新闻", "资料",
                "search", "find", "lookup", "latest", "news", "current",
                "what is", "who is", "how to", "tutorial", "documentation",
            ],
        },
    }

    def classify(self, text: str) -> str:
        """
        分类用户输入
        Returns: 'coding' | 'reasoning' | 'creative' | 'search' | 'simple'
        """
        text_lower = text.lower()
        scores = {cat: 0 for cat in self.CATEGORIES}

        for cat, data in self.CATEGORIES.items():
            weight = 2 if cat == "coding" else 1  # coding 关键词权重更高
            for kw in data.get("keywords", []):
                if kw.lower() in text_lower:
                    scores[cat] += weight

        # 文件扩展名加权
        for cat, data in self.CATEGORIES.items():
            for ext in data.get("file_exts", []):
                if ext in text_lower:
                    scores[cat] += 2

        if max(scores.values(), default=0) == 0:
            return "simple"
        return max(scores, key=scores.get)


class ModelRouter:
    """
    智能模型路由器

    用法:
        router = ModelRouter(config)
        decision = router.decide("帮我写一个快速排序")
        # → RoutingDecision(model_id="deepseek/deepseek-chat", reason="coding任务，cost_first策略")

        # 执行失败时获取备选
        fallback = router.get_fallback(decision)
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.classifier = TaskClassifier()
        self.tracker = CostTracker()
        self._last_error: str = ""

        # 默认路由策略
        self._strategies = self.config.get("strategies", {
            "coding": {"primary": "anthropic/claude-3.5-sonnet", "fallback": "openai/gpt-4o", "budget": 0.10},
            "reasoning": {"primary": "deepseek/deepseek-r1", "fallback": "anthropic/claude-3.5-sonnet", "budget": 0.05},
            "creative": {"primary": "openai/gpt-4o", "fallback": "moonshot/kimi-k2.5", "budget": 0.05},
            "search": {"primary": "deepseek/deepseek-chat", "fallback": "openai/gpt-4o-mini", "budget": 0.01},
            "simple": {"primary": "deepseek/deepseek-chat", "fallback": "openai/gpt-4o-mini", "budget": 0.01},
        })
        self._default_strategy = self.config.get("default_strategy", "balanced")
        self._budget_per_turn = self.config.get("budget_usd_per_turn", 0.05)

    def decide(self, user_input: str, usage_hint: dict = None) -> RoutingDecision:
        """
        为用户输入选择最优模型

        Args:
            user_input: 用户原始输入
            usage_hint: 可选的使用提示，如 {"preferred_model": "..."}
        """
        category = self.classifier.classify(user_input)
        strategy = self._strategies.get(category, self._strategies["simple"])

        # 检查本轮预算是否已超支
        if self.tracker.turn_total >= self._budget_per_turn:
            # 强制使用最便宜的模型
            cheapest = self._find_cheapest()
            return RoutingDecision(
                model_id=cheapest,
                provider="openrouter",
                reason=f"预算已超 ({self.tracker.turn_total:.4f} USD)，强制降级到最廉价模型",
                estimated_cost=0.0001,
                quality_score=MODEL_PRICING.get(cheapest, (0, 0, 5))[2],
            )

        primary = strategy["primary"]
        fallback = strategy["fallback"]
        budget = strategy.get("budget", self._budget_per_turn)

        # 如果之前刚失败过，直接使用 fallback
        if self._last_error:
            return RoutingDecision(
                model_id=fallback,
                provider="openrouter",
                reason=f"上次调用失败 ({self._last_error})，使用备选模型 [{category}]",
                estimated_cost=self._estimate_cost(fallback),
                quality_score=MODEL_PRICING.get(fallback, (0, 0, 5))[2],
            )

        # 根据全局策略微调
        if self._default_strategy == "cost_first":
            # 选择 category 内最便宜且质量 >= 6 的
            primary = self._find_best_in_budget(category, budget)
        elif self._default_strategy == "quality_first":
            # 始终使用 primary，不考虑成本
            pass
        # balanced: 默认策略，使用 primary

        return RoutingDecision(
            model_id=primary,
            provider="openrouter",
            reason=f"{category} 任务，{self._default_strategy} 策略",
            estimated_cost=self._estimate_cost(primary),
            quality_score=MODEL_PRICING.get(primary, (0, 0, 5))[2],
        )

    def get_fallback(self, decision: RoutingDecision) -> RoutingDecision:
        """获取当前决策的备选模型"""
        # 找到当前 category 的 fallback
        for cat, strat in self._strategies.items():
            if strat["primary"] == decision.model_id:
                fb = strat["fallback"]
                return RoutingDecision(
                    model_id=fb,
                    provider="openrouter",
                    reason=f"备选模型 (原 {decision.model_id} 失败)",
                    estimated_cost=self._estimate_cost(fb),
                    quality_score=MODEL_PRICING.get(fb, (0, 0, 5))[2],
                )
        # 通用 fallback
        return RoutingDecision(
            model_id="deepseek/deepseek-chat",
            provider="openrouter",
            reason="通用备选模型",
            estimated_cost=0.0002,
            quality_score=7,
        )

    def report_error(self, error: str):
        """报告模型调用失败，影响下一次路由决策"""
        self._last_error = error

    def reset_error(self):
        """清除错误状态（成功调用后）"""
        self._last_error = ""

    def _estimate_cost(self, model_id: str, prompt_tokens: int = 1000, completion_tokens: int = 500) -> float:
        price = MODEL_PRICING.get(model_id, (0.5, 0.5))
        return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000

    def _find_cheapest(self) -> str:
        """找到已知模型中最便宜的"""
        cheapest = min(MODEL_PRICING.items(), key=lambda x: x[1][0] + x[1][1])
        return cheapest[0]

    def _find_best_in_budget(self, category: str, budget: float) -> str:
        """在预算内找到性价比最高的模型"""
        strategy = self._strategies.get(category, self._strategies["simple"])
        candidates = [strategy["primary"], strategy["fallback"]]
        # 按 (quality / cost) 排序
        scored = []
        for m in candidates:
            p = MODEL_PRICING.get(m, (1, 1, 5))
            cost = p[0] + p[1]
            score = p[2] / max(cost, 0.001)
            scored.append((score, m))
        scored.sort(reverse=True)
        return scored[0][1] if scored else candidates[0]

    def summary(self) -> dict:
        return {
            "strategy": self._default_strategy,
            "budget_per_turn": self._budget_per_turn,
            "cost_tracker": self.tracker.summary(),
        }
