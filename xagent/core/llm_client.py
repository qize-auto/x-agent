"""
LLM 客户端抽象层
==============
支持多 provider: OpenRouter / OpenAI / Anthropic / Moonshot / DeepSeek / Ollama
统一接口，一键切换模型。
"""
from __future__ import annotations
import json
import os
import time
from typing import Iterator, Optional, Callable
from openai import OpenAI


class LLMResponse:
    """LLM 响应封装"""
    def __init__(self, content: str = "", reasoning: str = "", tool_calls: list = None, usage: dict = None,
                 latency_ms: float = 0.0, ttft_ms: float = 0.0):
        self.content = content
        self.reasoning = reasoning
        self.tool_calls = tool_calls or []
        self.usage = usage or {}
        self.latency_ms = latency_ms
        self.ttft_ms = ttft_ms

    def __repr__(self):
        return f"LLMResponse(content={self.content[:50]}..., tool_calls={len(self.tool_calls)})"


class LLMClient:
    """
    通用 LLM 客户端
    
    通过 OpenAI 兼容 API 接入绝大多数模型：
    - OpenRouter: https://openrouter.ai/api/v1
    - OpenAI: https://api.openai.com/v1
    - DeepSeek: https://api.deepseek.com/v1
    - Moonshot(Kimi): https://api.moonshot.cn/v1
    - Ollama: http://localhost:11434/v1
    """

    PROVIDER_DEFAULTS = {
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        "anthropic": {
            "base_url": "https://api.anthropic.com/v1",  # 注意：Anthropic 原生 API 不兼容 OpenAI
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        "moonshot": {
            "base_url": "https://api.moonshot.cn/v1",
            "api_key_env": "MOONSHOT_API_KEY",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "ollama": {
            "base_url": "http://localhost:11434/v1",
            "api_key_env": None,
        },
    }

    def __init__(self, provider: str = "openrouter", model_id: str = "", api_key: str = "",
                 base_url: str = "", temperature: float = 0.7, max_tokens: int = 4096,
                 cache_config: dict = None):
        self.provider = provider.lower()
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._cache_config = cache_config or {}

        # 解析 base_url 和 api_key
        defaults = self.PROVIDER_DEFAULTS.get(self.provider, {})
        self.base_url = base_url or defaults.get("base_url", "")
        self.api_key = api_key or self._resolve_api_key(defaults.get("api_key_env"))

        if not self.base_url:
            raise ValueError(f"未知 provider: {provider}，请提供 base_url")

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "sk-no-key",  # Ollama 不需要 key
            timeout=120,  # HTTP 层总超时 120s，防止网络挂死
        )

        # 初始化缓存层
        self._exact_cache = None
        self._semantic_cache = None
        self._prompt_optimizer = None
        if self._cache_config.get("enabled", False):
            from .cache import ExactMatchCache, SemanticCache, PromptPrefixOptimizer
            em_cfg = self._cache_config.get("exact_match", {})
            if em_cfg.get("enabled", True):
                self._exact_cache = ExactMatchCache(
                    max_size=em_cfg.get("max_size", 1000),
                    ttl_sec=em_cfg.get("ttl_sec", 3600),
                )
            sem_cfg = self._cache_config.get("semantic", {})
            if sem_cfg.get("enabled", True):
                self._semantic_cache = SemanticCache(
                    similarity_threshold=sem_cfg.get("threshold", 0.92),
                    ttl_sec=sem_cfg.get("ttl_sec", 3600),
                    max_entries=sem_cfg.get("max_entries", 500),
                )
            if self._cache_config.get("prompt_prefix_optimize", True):
                self._prompt_optimizer = PromptPrefixOptimizer(provider=self.provider)

    def _resolve_api_key(self, env_var: Optional[str]) -> str:
        if env_var and env_var in os.environ:
            return os.environ[env_var]
        # 尝试通用环境变量
        for key in ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                    "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY"]:
            if key in os.environ:
                return os.environ[key]
        return ""

    def chat(self, messages: list[dict], tools: list[dict] = None, stream: bool = False, model_id: str = None) -> LLMResponse:
        """
        发送聊天请求
        
        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": str}]
            tools: 工具定义列表（OpenAI function calling 格式）
            stream: 是否流式返回
            model_id: 临时覆盖模型 ID（用于路由切换）
        """
        m_id = model_id or self.model_id

        # Prompt prefix 优化
        optimized_messages = messages
        if self._prompt_optimizer:
            optimized_messages = self._prompt_optimizer.optimize(messages)
            if "anthropic" in self.provider:
                optimized_messages = self._prompt_optimizer.add_cache_control(optimized_messages)

        # 缓存检查（仅非流式、无 tools 时）
        if not stream and not tools:
            # Exact match
            if self._exact_cache:
                cached = self._exact_cache.get(m_id, optimized_messages, self.temperature)
                if cached:
                    return LLMResponse(
                        content=cached["content"],
                        tool_calls=cached.get("tool_calls", []),
                        usage=cached.get("usage", {}),
                        latency_ms=0.0,
                        ttft_ms=0.0,
                    )
            # Semantic match
            if self._semantic_cache:
                user_text = ""
                for m in reversed(optimized_messages):
                    if m.get("role") == "user":
                        user_text = m.get("content", "")
                        break
                if user_text:
                    sem_cached = self._semantic_cache.get(user_text, model=m_id)
                    if sem_cached:
                        return LLMResponse(
                            content=sem_cached["content"],
                            tool_calls=sem_cached.get("tool_calls", []),
                            usage=sem_cached.get("usage", {}),
                            latency_ms=0.0,
                            ttft_ms=0.0,
                        )

        kwargs = {
            "model": m_id,
            "messages": optimized_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if stream:
            return self._chat_stream(**kwargs)

        start = time.time()
        resp = self.client.chat.completions.create(**kwargs, timeout=60)
        latency_ms = (time.time() - start) * 1000
        result = self._parse_response(resp)
        result.latency_ms = latency_ms

        # 写入缓存
        if not stream and not tools:
            if self._exact_cache:
                self._exact_cache.put(
                    m_id, optimized_messages, self.temperature,
                    content=result.content,
                    tool_calls=result.tool_calls,
                    usage=result.usage,
                )
            if self._semantic_cache:
                user_text = ""
                for m in reversed(optimized_messages):
                    if m.get("role") == "user":
                        user_text = m.get("content", "")
                        break
                if user_text:
                    self._semantic_cache.put(
                        query_text=user_text,
                        model=m_id,
                        content=result.content,
                        tool_calls=result.tool_calls,
                        usage=result.usage,
                    )

        return result

    def _chat_stream(self, **kwargs) -> Iterator[str]:
        """流式返回文本片段（向后兼容）"""
        kwargs["stream"] = True
        for chunk in self.client.chat.completions.create(**kwargs, timeout=60):
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    def chat_stream_events(self, messages: list[dict], tools: list[dict] = None, model_id: str = None):
        """
        流式对话，返回文本增量和工具调用事件
        
        Yields:
            {"type": "text", "content": str}
            {"type": "tool_call", "id": str, "name": str, "arguments": dict}
            {"type": "done", "usage": dict | None}
        """
        kwargs = {
            "model": model_id or self.model_id,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        tool_call_chunks: dict[int, dict] = {}
        usage = None

        for chunk in self.client.chat.completions.create(**kwargs, timeout=60):
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta.content:
                yield {"type": "text", "content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc.id:
                        tool_call_chunks[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        tool_call_chunks[idx]["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_call_chunks[idx]["arguments"] += tc.function.arguments

            if finish_reason == "tool_calls" or (finish_reason and tool_call_chunks):
                for idx in sorted(tool_call_chunks.keys()):
                    tc = tool_call_chunks[idx]
                    if tc["name"]:
                        try:
                            args = json.loads(tc["arguments"])
                        except json.JSONDecodeError:
                            args = {"_raw": tc["arguments"]}
                        yield {"type": "tool_call", "id": tc["id"], "name": tc["name"], "arguments": args}

            if chunk.usage:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }

        yield {"type": "done", "usage": usage}

    def _parse_response(self, resp) -> LLMResponse:
        """解析 OpenAI 兼容响应"""
        choice = resp.choices[0]
        message = choice.message

        content = message.content or ""
        reasoning = ""
        tool_calls = []

        # 提取 reasoning（DeepSeek R1 等思考模型）
        if hasattr(message, "reasoning_content") and message.reasoning_content:
            reasoning = message.reasoning_content

        # 提取工具调用
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        usage = {}
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
            # DeepSeek 缓存命中字段（可选，其他 provider 可能不存在）
            if hasattr(resp.usage, "prompt_cache_hit_tokens"):
                usage["prompt_cache_hit_tokens"] = resp.usage.prompt_cache_hit_tokens
            if hasattr(resp.usage, "prompt_cache_miss_tokens"):
                usage["prompt_cache_miss_tokens"] = resp.usage.prompt_cache_miss_tokens
            # OpenAI 标准缓存字段
            if hasattr(resp.usage, "cached_tokens"):
                usage["cached_tokens"] = resp.usage.cached_tokens

        return LLMResponse(content=content, reasoning=reasoning, tool_calls=tool_calls, usage=usage)

    def get_cost_estimate(self, usage: dict) -> float:
        """
        估算 token 成本（美元）
        注意：这只是粗略估算，OpenRouter 实际价格可能不同
        """
        prices = {
            # OpenAI
            "gpt-4o": (2.50, 10.00),
            "gpt-4.1": (2.00, 8.00),
            "gpt-4.1-mini": (0.40, 1.60),
            "o4-mini": (1.10, 4.40),
            "o3": (10.00, 40.00),
            # Anthropic
            "claude-3.5-sonnet": (3.00, 15.00),
            "claude-4-sonnet": (3.00, 15.00),
            "claude-4-opus": (15.00, 75.00),
            # Moonshot
            "kimi-k2.5": (0.60, 2.50),
            # DeepSeek
            "deepseek-chat": (0.14, 0.28),
            "deepseek-r1": (0.55, 2.19),
            "deepseek-v4-flash": (0.05, 0.20),
            "deepseek-v4-pro": (0.50, 2.00),
            "deepseek-v3.2": (0.10, 0.50),
            # Google
            "gemini-2.5-flash": (0.15, 0.60),
            "gemini-2.5-pro": (1.25, 10.00),
        }
        # 尝试匹配模型名
        for key, (input_price, output_price) in prices.items():
            if key in self.model_id.lower():
                prompt = usage.get("prompt_tokens", 0)
                completion = usage.get("completion_tokens", 0)
                return (prompt * input_price + completion * output_price) / 1_000_000
        return 0.0

    @classmethod
    def from_config(cls, config: dict) -> "LLMClient":
        """从配置字典创建客户端"""
        cache_cfg = config.get("cache", {}) if isinstance(config, dict) else {}
        return cls(
            provider=config.get("provider", "openrouter"),
            model_id=config.get("model_id", ""),
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", 4096),
            cache_config=cache_cfg,
        )

    def __repr__(self):
        return f"LLMClient(provider={self.provider}, model={self.model_id})"
