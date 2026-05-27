"""
Tests for LLM Cache system
"""
import time
import pytest
from pathlib import Path

from xagent.core.cache.exact_cache import ExactMatchCache
from xagent.core.cache.semantic_cache import SemanticCache
from xagent.core.cache.prompt_optimizer import PromptPrefixOptimizer


class TestExactMatchCache:
    def test_basic_get_put(self):
        cache = ExactMatchCache(max_size=10, ttl_sec=60)
        messages = [{"role": "user", "content": "hello"}]
        cache.put("gpt-4o", messages, 0.7, content="hi there")
        result = cache.get("gpt-4o", messages, 0.7)
        assert result is not None
        assert result["content"] == "hi there"

    def test_miss_different_model(self):
        cache = ExactMatchCache(max_size=10, ttl_sec=60)
        messages = [{"role": "user", "content": "hello"}]
        cache.put("gpt-4o", messages, 0.7, content="hi")
        result = cache.get("claude-3.5", messages, 0.7)
        assert result is None

    def test_miss_different_temperature(self):
        cache = ExactMatchCache(max_size=10, ttl_sec=60)
        messages = [{"role": "user", "content": "hello"}]
        cache.put("gpt-4o", messages, 0.7, content="hi")
        result = cache.get("gpt-4o", messages, 0.5)
        assert result is None

    def test_ttl_expiration(self):
        cache = ExactMatchCache(max_size=10, ttl_sec=0.1)
        messages = [{"role": "user", "content": "hello"}]
        cache.put("gpt-4o", messages, 0.7, content="hi")
        time.sleep(0.15)
        result = cache.get("gpt-4o", messages, 0.7)
        assert result is None

    def test_lru_eviction(self):
        cache = ExactMatchCache(max_size=2, ttl_sec=60)
        for i in range(3):
            cache.put("gpt-4o", [{"role": "user", "content": str(i)}], 0.7, content=str(i))
        # 0 应该被淘汰
        assert cache.get("gpt-4o", [{"role": "user", "content": "0"}], 0.7) is None
        assert cache.get("gpt-4o", [{"role": "user", "content": "1"}], 0.7) is not None
        assert cache.get("gpt-4o", [{"role": "user", "content": "2"}], 0.7) is not None

    def test_persistence(self, tmp_path):
        cache = ExactMatchCache(max_size=10, ttl_sec=60, persist_dir=str(tmp_path))
        messages = [{"role": "user", "content": "persist"}]
        cache.put("gpt-4o", messages, 0.7, content="yes")

        # 重新加载
        cache2 = ExactMatchCache(max_size=10, ttl_sec=60, persist_dir=str(tmp_path))
        result = cache2.get("gpt-4o", messages, 0.7)
        assert result is not None
        assert result["content"] == "yes"

    def test_stats(self):
        cache = ExactMatchCache(max_size=100, ttl_sec=3600)
        stats = cache.stats()
        assert stats["size"] == 0
        assert stats["max_size"] == 100


class TestSemanticCache:
    def test_basic_get_put(self):
        cache = SemanticCache(similarity_threshold=0.85, ttl_sec=60, max_entries=10)
        if cache._embedding_fn is None:
            pytest.skip("sentence-transformers not available")
        cache.put("what is python?", "gpt-4o", "Python is a programming language.")
        result = cache.get("what is python?", model="gpt-4o")
        assert result is not None
        assert result["content"] == "Python is a programming language."

    def test_similar_query_match(self):
        cache = SemanticCache(similarity_threshold=0.85, ttl_sec=60, max_entries=10)
        cache.put("what is python?", "gpt-4o", "Python is a programming language.")
        result = cache.get("explain python", model="gpt-4o")
        # 语义相似度应足够高
        if cache._embedding_fn is not None:
            assert result is not None
            assert result["content"] == "Python is a programming language."
            assert "similarity" in result
        else:
            pytest.skip("sentence-transformers not available")

    def test_different_query_no_match(self):
        cache = SemanticCache(similarity_threshold=0.95, ttl_sec=60, max_entries=10)
        cache.put("what is python?", "gpt-4o", "Python is a programming language.")
        result = cache.get("how to cook pasta", model="gpt-4o")
        if cache._embedding_fn is not None:
            assert result is None
        else:
            pytest.skip("sentence-transformers not available")

    def test_ttl_expiration(self):
        cache = SemanticCache(similarity_threshold=0.85, ttl_sec=0.1, max_entries=10)
        cache.put("hello", "gpt-4o", "hi")
        time.sleep(0.15)
        result = cache.get("hello", model="gpt-4o")
        assert result is None

    def test_stats(self):
        cache = SemanticCache()
        stats = cache.stats()
        assert stats["entries"] == 0
        assert stats["threshold"] > 0


class TestPromptPrefixOptimizer:
    def test_optimize_order(self):
        opt = PromptPrefixOptimizer()
        messages = [
            {"role": "user", "content": "help"},
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "some result"},
            {"role": "tool", "content": "tool out"},
        ]
        result = opt.optimize(messages)
        assert result[0]["role"] == "system"
        assert result[-1]["role"] == "tool"

    def test_static_blocks_first(self):
        opt = PromptPrefixOptimizer()
        messages = [
            {"role": "assistant", "content": "## File: main.py\n```python\nprint(1)\n```"},
            {"role": "assistant", "content": "ok done"},
            {"role": "user", "content": "fix it"},
        ]
        result = opt.optimize(messages)
        # 静态文档块应在动态内容之前
        assert "## File:" in result[0]["content"] or result[0]["role"] == "system"

    def test_anthropic_cache_control(self):
        opt = PromptPrefixOptimizer(provider="anthropic")
        messages = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ]
        result = opt.add_cache_control(messages)
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0].get("cache_control", {}).get("type") == "ephemeral"
        # user message 不应被修改
        assert result[1]["content"] == "hi"

    def test_non_anthropic_no_cache_control(self):
        opt = PromptPrefixOptimizer(provider="openai")
        messages = [
            {"role": "system", "content": "you are helpful"},
        ]
        result = opt.add_cache_control(messages)
        assert result[0]["content"] == "you are helpful"


class TestCacheIntegration:
    def test_llm_client_with_cache(self):
        from xagent.core.llm_client import LLMClient
        client = LLMClient(
            provider="openrouter",
            model_id="gpt-4o",
            cache_config={
                "enabled": True,
                "exact_match": {"enabled": True},
                "semantic": {"enabled": False},
            },
        )
        assert client._exact_cache is not None
        assert client._semantic_cache is None

    def test_llm_client_cache_hit(self):
        from xagent.core.llm_client import LLMClient
        client = LLMClient(
            provider="openrouter",
            model_id="openai/gpt-4o",
            cache_config={
                "enabled": True,
                "exact_match": {"enabled": True},
                "semantic": {"enabled": False},
            },
        )
        messages = [{"role": "user", "content": "cache me"}]
        client._exact_cache.put("openai/gpt-4o", messages, 0.7, content="cached result")
        cached = client._exact_cache.get("openai/gpt-4o", messages, 0.7)
        assert cached is not None
        assert cached["content"] == "cached result"
