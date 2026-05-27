"""
Resource Adaptive — 资源自适应引擎
==================================
1. 启动时检测硬件配置，自动调整运行参数
2. 运行时监控 CPU/内存，动态限流防止卡死

设计原则：
- 跨平台（Windows/Linux/macOS）
- psutil 不可用时优雅降级
- 监控线程低频率（5 秒间隔），自身几乎不耗资源
- 所有限流决策可覆盖，不强制
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SystemProfile:
    """系统硬件画像"""
    cpu_count: int = 1
    memory_gb: float = 4.0
    cpu_freq_mhz: float = 0.0
    is_low_end: bool = False
    is_virtual: bool = False

    def tier(self) -> str:
        """返回设备档位: low / mid / high"""
        if self.memory_gb < 4 or self.cpu_count <= 2 or self.is_low_end:
            return "low"
        if self.memory_gb >= 16 and self.cpu_count >= 8:
            return "high"
        return "mid"

    def fingerprint(self) -> str:
        """硬件指纹：基于物理特征生成稳定标识，用于检测硬件变化"""
        return f"cpu={self.cpu_count}|mem={self.memory_gb:.1f}|freq={self.cpu_freq_mhz:.0f}"


@dataclass
class AdaptiveSettings:
    """根据硬件档位推荐的设置 —— 精准度优先的分层降级策略"""
    # ── LLM 推理 ──
    max_tokens: int = 4096
    max_tool_iterations: int = 10
    # ── 缓存与上下文 ──
    session_persist: bool = True
    enable_thought_harvest: bool = True
    enable_warmup: bool = True
    cache_mode: str = "auto"
    context_compaction_threshold: int = 3000
    max_context_history: int = 20
    # ── 需求澄清 ──
    max_questions_per_task: int = 3
    # ── 验证策略（精准度底线） ──
    lint_timeout_sec: int = 60
    enable_lint: bool = True
    enable_typecheck: bool = False
    # ── 索引与代码智能 ──
    index_strategy: str = "lazy"  # eager / lazy / partial
    max_index_files: int = 500
    # ── Repo Map 上下文 ──
    repo_map_max_files: int = 30
    repo_map_max_symbols_per_file: int = 5
    # ── Agent 行为 ──
    intent_anchor_interval: int = 5
    # ── 网络与内存 ──
    request_timeout: int = 600
    memory_limit_mb: int = 512
    # ── Shell ──
    shell_default_timeout: int | None = None
    # ── 总时间预算 ──
    max_total_time_sec: int = 600


class SystemProfiler:
    """启动时系统配置检测器"""

    @classmethod
    def detect(cls) -> SystemProfile:
        """检测当前系统配置"""
        try:
            import psutil
            cpu_count = psutil.cpu_count(logical=True) or 1
            memory_gb = psutil.virtual_memory().total / (1024 ** 3)
            cpu_freq = psutil.cpu_freq()
            cpu_freq_mhz = cpu_freq.max if cpu_freq else 0

            # 判断是否为低端设备
            is_low_end = memory_gb < 4 or cpu_count <= 2

            # 判断是否为虚拟机（部分虚拟机会报告较低频率）
            is_virtual = cpu_freq_mhz > 0 and cpu_freq_mhz < 1500

            return SystemProfile(
                cpu_count=cpu_count,
                memory_gb=round(memory_gb, 1),
                cpu_freq_mhz=round(cpu_freq_mhz, 0),
                is_low_end=is_low_end,
                is_virtual=is_virtual,
            )
        except Exception:
            # psutil 不可用时降级为保守估计
            return SystemProfile(cpu_count=2, memory_gb=4.0, is_low_end=True)

    @classmethod
    def recommend(cls, profile: SystemProfile | None = None) -> AdaptiveSettings:
        """根据系统画像生成推荐配置"""
        if profile is None:
            profile = cls.detect()

        tier = profile.tier()
        if tier == "low":
            # 低配置：精简但不牺牲精准度底线（语法检查必须，lint 保持但超时短）
            return AdaptiveSettings(
                max_tokens=2048,
                max_tool_iterations=5,
                session_persist=False,
                enable_thought_harvest=False,
                enable_warmup=False,
                cache_mode="never",
                context_compaction_threshold=1500,
                max_context_history=10,
                max_questions_per_task=2,
                lint_timeout_sec=30,
                enable_lint=True,
                enable_typecheck=False,
                index_strategy="partial",
                max_index_files=100,
                repo_map_max_files=20,
                repo_map_max_symbols_per_file=3,
                intent_anchor_interval=3,
                shell_default_timeout=120,
                max_total_time_sec=300,
                request_timeout=300,
                memory_limit_mb=256,
            )
        if tier == "high":
            # 高配置：全功能运行，追求最高精度
            return AdaptiveSettings(
                max_tokens=8192,
                max_tool_iterations=15,
                session_persist=True,
                enable_thought_harvest=True,
                enable_warmup=True,
                cache_mode="auto",
                context_compaction_threshold=5000,
                max_context_history=50,
                max_questions_per_task=5,
                lint_timeout_sec=120,
                enable_lint=True,
                enable_typecheck=True,
                index_strategy="eager",
                max_index_files=2000,
                repo_map_max_files=50,
                repo_map_max_symbols_per_file=10,
                intent_anchor_interval=8,
                shell_default_timeout=None,
                max_total_time_sec=1800,
                request_timeout=120,
                memory_limit_mb=1024,
            )
        # mid
        return AdaptiveSettings()

    @classmethod
    def report(cls) -> str:
        """生成人类可读的系统报告"""
        p = cls.detect()
        tier = p.tier()
        tier_emoji = {"low": "🐢", "mid": "🚗", "high": "🚀"}.get(tier, "?")
        lines = [
            f"{tier_emoji} 系统档位: {tier.upper()}",
            f"   CPU: {p.cpu_count} 核心 @ {p.cpu_freq_mhz:.0f} MHz",
            f"   内存: {p.memory_gb:.1f} GB",
        ]
        if p.is_virtual:
            lines.append("   检测到虚拟机环境")
        return "\n".join(lines)


class ResourceMonitor:
    """
    运行时资源监控器。

    在后台线程中定期采样 CPU/内存使用率，
    提供 should_throttle() 供主循环查询。
    """

    def __init__(
        self,
        cpu_threshold: float = 85.0,
        memory_threshold: float = 85.0,
        interval: float = 5.0,
    ):
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.interval = interval
        self._cpu_percent = 0.0
        self._memory_percent = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_sample_time = 0.0

    def start(self):
        """启动后台监控线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监控"""
        self._running = False

    def _loop(self):
        """后台采样循环"""
        while self._running:
            try:
                import psutil
                with self._lock:
                    self._cpu_percent = psutil.cpu_percent(interval=1)
                    self._memory_percent = psutil.virtual_memory().percent
                    self._last_sample_time = time.time()
            except Exception:
                pass
            time.sleep(max(0, self.interval - 1))

    def current_load(self) -> dict[str, float]:
        """返回当前负载 {cpu, memory}"""
        with self._lock:
            return {
                "cpu": self._cpu_percent,
                "memory": self._memory_percent,
            }

    def is_overloaded(self) -> bool:
        """当前是否超过负载阈值"""
        load = self.current_load()
        return load["cpu"] > self.cpu_threshold or load["memory"] > self.memory_threshold

    def should_throttle(self, throttle_cpu: float = 70.0, throttle_memory: float = 75.0) -> bool:
        """
        是否建议限流（比 is_overloaded 更早触发，用于预防性降速）。

        Returns:
            True — 建议暂停/降速非关键任务
        """
        load = self.current_load()
        return load["cpu"] > throttle_cpu or load["memory"] > throttle_memory


class Throttler:
    """
    运行时动态限流器。

    根据 ResourceMonitor 的状态，对关键操作进行限流：
    - 高负载时暂停非关键后台任务
    - 延长请求间隔
    - 触发上下文压缩
    """

    def __init__(self, monitor: ResourceMonitor | None = None):
        self.monitor = monitor or ResourceMonitor()
        self._throttle_count = 0

    def check(self, action: str = "") -> bool:
        """
        检查当前是否允许执行某操作。

        Returns:
            True — 允许执行
            False — 建议跳过（系统高负载）
        """
        if self.monitor is None:
            return True
        if not self.monitor.should_throttle():
            return True
        self._throttle_count += 1
        return False

    def wait_if_needed(self, base_delay: float = 0.5):
        """
        如果系统高负载，主动等待一段时间让资源恢复。

        用于工具调用之间、重试之前等场景。
        """
        if self.monitor and self.monitor.should_throttle():
            time.sleep(base_delay)

    def status(self) -> str:
        """返回限流器状态摘要"""
        if self.monitor is None:
            return "监控未启用"
        load = self.monitor.current_load()
        return (
            f"CPU {load['cpu']:.0f}% | "
            f"Memory {load['memory']:.0f}% | "
            f"Throttled {self._throttle_count} times"
        )


def apply_adaptive_config(config_data: dict, profile: SystemProfile | None = None) -> dict:
    """
    将自适应配置应用到现有配置字典。

    Args:
        config_data: 现有配置（会被修改）
        profile: 系统画像（None 时自动检测）

    Returns:
        修改后的配置字典
    """
    if profile is None:
        profile = SystemProfiler.detect()
    settings = SystemProfiler.recommend(profile)
    d = config_data

    d.setdefault("model", {})
    d["model"]["max_tokens"] = settings.max_tokens

    d.setdefault("cache", {})
    d["cache"]["mode"] = settings.cache_mode
    d["cache"]["session_persist"] = settings.session_persist
    d["cache"]["enable_thought_harvest"] = settings.enable_thought_harvest
    d["cache"]["warmup"] = settings.enable_warmup

    d.setdefault("cost_control", {})
    d["cost_control"]["compaction_threshold_tokens"] = settings.context_compaction_threshold

    d.setdefault("clarification", {})
    d["clarification"]["max_questions_per_task"] = settings.max_questions_per_task

    # 保存完整的自适应策略，供各模块动态读取
    d.setdefault("_adaptive", {})
    d["_adaptive"]["tier"] = profile.tier()
    d["_adaptive"]["applied_at"] = time.time()
    # ── 验证策略 ──
    d["_adaptive"]["lint_timeout_sec"] = settings.lint_timeout_sec
    d["_adaptive"]["enable_lint"] = settings.enable_lint
    d["_adaptive"]["enable_typecheck"] = settings.enable_typecheck
    # ── 索引与上下文策略 ──
    d["_adaptive"]["index_strategy"] = settings.index_strategy
    d["_adaptive"]["max_index_files"] = settings.max_index_files
    d["_adaptive"]["repo_map_max_files"] = settings.repo_map_max_files
    d["_adaptive"]["repo_map_max_symbols_per_file"] = settings.repo_map_max_symbols_per_file
    d["_adaptive"]["max_context_history"] = settings.max_context_history
    # ── Agent 行为策略 ──
    d["_adaptive"]["intent_anchor_interval"] = settings.intent_anchor_interval
    d["_adaptive"]["shell_default_timeout"] = settings.shell_default_timeout
    d["_adaptive"]["max_total_time_sec"] = settings.max_total_time_sec
    d["_adaptive"]["hw_fingerprint"] = profile.fingerprint()

    return d
