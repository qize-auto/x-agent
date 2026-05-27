"""
Prompt Prefix 优化器
===================
根据 "Static first — Dynamic last" 原则重组 prompt，
最大化云厂商前缀缓存命中率。
"""
from __future__ import annotations
from typing import Any


class PromptPrefixOptimizer:
    """
    Prompt 前缀优化器。

    策略：
    1. System message 置顶
    2. 静态上下文（文档、规则、示例）紧随其后
    3. 动态用户 query 置底
    4. 避免在静态部分中间插入动态内容
    """

    def __init__(self, provider: str = ""):
        self.provider = provider.lower()

    def optimize(self, messages: list[dict]) -> list[dict]:
        """
        优化消息列表以最大化前缀缓存命中率。

        输入：任意顺序的 messages
        输出：按 [system, static_context, dynamic] 排序的 messages
        """
        system_msgs = [m for m in messages if m.get("role") == "system"]
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        tool_msgs = [m for m in messages if m.get("role") == "tool"]

        # 将 assistant/tool 消息中的静态内容（如文档块）提取到前面
        static_blocks = []
        dynamic_assistant = []
        for m in assistant_msgs:
            content = m.get("content", "")
            if self._is_static_block(content):
                static_blocks.append({"role": "assistant", "content": content})
            else:
                dynamic_assistant.append(m)

        # 最终顺序：system → static assistant → user → dynamic assistant → tool
        optimized = (
            system_msgs
            + static_blocks
            + user_msgs
            + dynamic_assistant
            + tool_msgs
        )
        return optimized

    @staticmethod
    def _is_static_block(content: str) -> bool:
        """启发式判断内容是否为静态文档块"""
        if not content:
            return False
        static_indicators = [
            "--- document ---",
            "## File:",
            "## Code:",
            "## Reference:",
            "```",
            "Context:",
            "Document:",
        ]
        content_lower = content.lower()
        return any(ind.lower() in content_lower for ind in static_indicators)

    def add_cache_control(self, messages: list[dict]) -> list[dict]:
        """
        为 Anthropic 的 prompt caching 添加 cache_control 标记。
        在 system message 和大型静态内容块上添加标记。

        注意：仅当 provider 为 anthropic 时有效。
        """
        if "anthropic" not in self.provider:
            return messages

        result = []
        for m in messages:
            new_m = dict(m)
            content = new_m.get("content", "")
            # 对 system message 或长度超过 1024 的静态内容添加 cache_control
            if m.get("role") == "system" or (len(content) > 1024 and self._is_static_block(content)):
                # Anthropic cache_control 标记格式（OpenAI SDK 兼容层）
                new_m["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            result.append(new_m)
        return result
