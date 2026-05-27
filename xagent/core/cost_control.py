"""
成本控制层 (Cost Control)
=========================
核心原则：默认用最便宜的模型，只在必要时升级。

四个互补机制：
1. Tiered defaults (flash-first)
2. Turn-end auto-compaction
3. /pro single-turn arming
4. Failure-signal auto-escalation

参考：DeepSeek-Reasonix 的 Pillar 3 — Cost Control
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class CostControlConfig:
    """成本控制配置"""
    default_preset: str = "flash"          # flash / auto / pro
    auto_escalation: bool = True
    escalation_threshold: int = 3           # 失败信号达到此阈值自动升级
    turn_end_compaction: bool = True
    compaction_threshold_tokens: int = 3000  # token 数超过此值触发压缩
    context_window_tokens: int = 128000     # 上下文窗口大小（用于比例计算）
    context_ratio_proactive: float = 0.4    # 40% 上下文比例时主动压缩
    context_ratio_emergency: float = 0.8    # 80% 时紧急压缩
    pro_single_turn: bool = True            # /pro 是否只生效一回合


class CostController:
    """
    成本控制器。

    Preset 系统：
    - flash: 始终使用 v4-flash（最便宜）
    - auto:  默认 flash，困难任务自动升级到 pro
    - pro:   始终使用 v4-pro（最贵）
    """

    PRESETS = {
        "flash": {"model": "deepseek-v4-flash", "reasoning_effort": "high"},
        "auto":  {"model": "deepseek-v4-flash", "reasoning_effort": "high", "escalate_on_hard": True},
        "pro":   {"model": "deepseek-v4-pro", "reasoning_effort": "max"},
    }

    # 失败信号集合
    FAILURE_SIGNALS = {
        "search_not_found",   # edit_file SEARCH 未找到
        "tool_call_repair",   # 修复流水线触发
        "edit_rejected",      # 编辑被拒绝
        "test_failure",       # 测试失败
        "shell_error",        # shell 命令执行失败
        "lint_error",         # lint 检查失败
    }

    def __init__(self, config: Optional[CostControlConfig] = None,
                 status_callback: Callable = None):
        self.config = config or CostControlConfig()
        self.status_callback = status_callback
        self.current_preset = self.config.default_preset
        self.failure_count_this_turn = 0
        self.pro_armed = False  # /pro 武装状态
        self.pro_escalated = False  # 失败信号触发的自动升级

    def select_model(self, task_type: str = "", context_length: int = 0) -> str:
        """
        为当前回合选择模型。

        优先级：
        1. /pro 武装状态（用户手动触发）
        2. 失败信号自动升级
        3. 当前 preset 对应的模型
        """
        # /pro 武装状态：下一回合强制用 pro
        if self.pro_armed:
            self.pro_armed = False
            if self.config.pro_single_turn:
                self._status("⇧ pro armed — 单次升级已触发")
            else:
                self._status("⇧ pro armed")
            return self.PRESETS["pro"]["model"]

        # 失败信号自动升级
        if self.config.auto_escalation and self.failure_count_this_turn >= self.config.escalation_threshold:
            if not self.pro_escalated:
                self.pro_escalated = True
                self._status(f"⇧ pro escalated — 失败信号 {self.failure_count_this_turn} 次，自动升级")
            return self.PRESETS["pro"]["model"]

        # 默认 preset
        preset = self.PRESETS.get(self.current_preset, self.PRESETS["flash"])
        return preset["model"]

    def record_failure_signal(self, signal_type: str) -> bool:
        """
        记录失败信号。返回是否触发了自动升级。
        """
        if signal_type not in self.FAILURE_SIGNALS:
            return False

        self.failure_count_this_turn += 1

        # 如果达到阈值且未升级，立即通知
        if (self.config.auto_escalation and
            self.failure_count_this_turn >= self.config.escalation_threshold and
            not self.pro_escalated):
            return True
        return False

    def reset_turn(self):
        """回合边界：重置计数器和状态"""
        self.failure_count_this_turn = 0
        self.pro_escalated = False

    def arm_pro(self) -> bool:
        """用户手动武装 /pro。返回是否成功。"""
        self.pro_armed = True
        self._status("⇧ pro armed — 下一回合将使用 pro 模型")
        return True

    def set_preset(self, preset: str) -> bool:
        """切换 preset。返回是否成功。"""
        if preset not in self.PRESETS:
            return False
        self.current_preset = preset
        self._status(f"Preset switched to: {preset}")
        return True

    def should_compact(self, estimated_tokens: int) -> str:
        """
        判断是否需要上下文压缩。

        Returns:
            "none" | "proactive" | "emergency"
        """
        if not self.config.turn_end_compaction:
            return "none"

        ratio = estimated_tokens / self.config.context_window_tokens

        if ratio >= self.config.context_ratio_emergency:
            return "emergency"
        if ratio >= self.config.context_ratio_proactive:
            return "proactive"
        return "none"

    def get_stats(self) -> dict:
        """获取当前状态统计"""
        return {
            "preset": self.current_preset,
            "pro_armed": self.pro_armed,
            "pro_escalated": self.pro_escalated,
            "failure_count_this_turn": self.failure_count_this_turn,
            "escalation_threshold": self.config.escalation_threshold,
        }

    def _status(self, message: str):
        if self.status_callback:
            self.status_callback(message)


class ContextCompressor:
    """
    上下文压缩器。

    问题：工具结果（如 read_file 返回的大文件内容）会累积在上下文中。
    策略：将超过阈值的长工具结果替换为摘要。
    """

    def __init__(self, llm_client, threshold_tokens: int = 3000):
        self.llm = llm_client
        self.threshold = threshold_tokens

    def compact_messages(self, messages: list[dict]) -> list[dict]:
        """
        压缩消息列表中的长工具结果。
        返回压缩后的新列表（不修改原始列表）。
        """
        compacted = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                # 简单 token 估算：字符数 / 4
                estimated_tokens = len(content) // 4

                if estimated_tokens > self.threshold:
                    summary = self._summarize(content)
                    compacted_msg = dict(msg)  # 浅拷贝
                    compacted_msg["content"] = (
                        f"[COMPACTED] Original: {estimated_tokens} tokens\n"
                        f"Summary: {summary}\n"
                        f"[Use read_file to retrieve full content if needed]"
                    )
                    compacted.append(compacted_msg)
                else:
                    compacted.append(dict(msg))
            else:
                compacted.append(dict(msg))

        return compacted

    def _summarize(self, content: str) -> str:
        """生成内容摘要。

        简化实现：返回前 200 字符 + 省略号。
        未来可替换为 LLM 摘要（但会增加 API 调用）。
        """
        lines = content.strip().split("\n")
        if len(lines) <= 3:
            preview = content[:200]
        else:
            preview = "\n".join(lines[:3])

        if len(content) > 200:
            preview += " ..."

        return preview

    @staticmethod
    def estimate_tokens(messages: list[dict]) -> int:
        """估算消息列表的 token 数（粗略：字符数 / 4）"""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return total_chars // 4
