"""
X-Agent 配置管理
==============
配置文件: ~/.xagent/config.json
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".xagent"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "version": "0.1.0",
    # === 当前激活的模型预设 ===
    "active_model": "gpt-4o",
    # === 模型配置 ===
    "model": {
        "provider": "openrouter",   # openrouter / openai / anthropic / moonshot / deepseek / ollama
        "model_id": "openai/gpt-4o",
        "api_key": "",
        "base_url": "",
        "temperature": 0.7,
        "max_tokens": 4096,
    },
    # === 模型预设 ===
    "model_presets": {
        "gpt-4o": {"provider": "openrouter", "model_id": "openai/gpt-4o"},
        "claude-3-5": {"provider": "openrouter", "model_id": "anthropic/claude-3.5-sonnet"},
        "kimi-k2-5": {"provider": "openrouter", "model_id": "moonshot/kimi-k2.5"},
        "deepseek-chat": {"provider": "openrouter", "model_id": "deepseek/deepseek-chat"},
        "deepseek-r1": {"provider": "openrouter", "model_id": "deepseek/deepseek-r1"},
        "ollama-llama3": {"provider": "ollama", "model_id": "llama3.2"},
    },
    # === 安全 ===
    "safety": {
        "mode": "semi",              # auto / semi / manual
        "dangerous_commands": [
            "rm", "rmdir", "del", "format", "mkfs",
            "dd", "shutdown", "reboot", "reg delete",
        ],
        "allowed_dirs": [],          # Shell 命令只能操作这些目录（空=不限）
    },
    # === 记忆 ===
    "memory": {
        "enabled": True,
        "persist_dir": str(CONFIG_DIR / "memory"),
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "max_history": 50,
    },
    # === 工具 ===
    "tools": {
        "enabled": ["filesystem", "shell", "web_search", "git", "http", "api_test", "database", "docgen"],
        "web_search_engine": "duckduckgo",  # duckduckgo / searxng
    },
    # === MCP Servers ===
    "mcp": {
        "enabled": False,
        "servers": [
            # 示例配置（默认注释）：
            # {
            #     "name": "filesystem",
            #     "transport": "stdio",
            #     "command": "npx",
            #     "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
            #     "trusted": False,
            # },
            # {
            #     "name": "fetch",
            #     "transport": "stdio",
            #     "command": "uvx",
            #     "args": ["mcp-server-fetch"],
            #     "trusted": True,
            # },
        ],
    },
    # === GUI ===
    "gui": {
        "theme": "dark",
        "window_geometry": [80, 60, 1480, 900],
        "auto_start": True,
    },
    # === 智能路由 ===
    "routing": {
        "enabled": True,
        "default_strategy": "balanced",   # cost_first / quality_first / balanced
        "budget_usd_per_turn": 0.05,
        "strategies": {
            "coding": {"primary": "anthropic/claude-3.5-sonnet", "fallback": "openai/gpt-4o", "budget": 0.10},
            "reasoning": {"primary": "deepseek/deepseek-r1", "fallback": "anthropic/claude-3.5-sonnet", "budget": 0.05},
            "creative": {"primary": "openai/gpt-4o", "fallback": "moonshot/kimi-k2.5", "budget": 0.05},
            "search": {"primary": "deepseek/deepseek-chat", "fallback": "openai/gpt-4o-mini", "budget": 0.01},
            "simple": {"primary": "deepseek/deepseek-chat", "fallback": "openai/gpt-4o-mini", "budget": 0.01},
        },
    },
    # === 需求澄清 ===
    "clarification": {
        "enabled": False,                   # 默认关闭，向后兼容
        "mode": "standard",                 # standard / architect
        "max_questions_per_task": 3,        # 每个任务最多问几个问题
        "auto_skip_simple": True,           # 简单任务自动跳过
        "use_cheap_model": True,            # 澄清阶段用 cheap model
        "cheap_model_id": "deepseek/deepseek-chat",  # cheap model 指定
    },
    # === 资源自适应 ===
    "adaptive": {
        "enabled": True,           # 启动时自动根据硬件调整配置
        "auto_throttle": True,     # 运行时根据负载动态限流
        "cpu_threshold": 85.0,     # CPU 报警阈值 (%)
        "memory_threshold": 85.0,  # 内存报警阈值 (%)
    },
    # === 持久化 ===
    "persistence": {
        "enabled": True,           # 启用任务状态持久化
        "auto_checkpoint": True,   # 自动保存检查点
        "checkpoint_interval_sec": 300,
        "retention_days": 30,
    },
    # === 多 Agent 编排 (Swarm) ===
    "swarm": {
        "enabled": False,           # 默认关闭，零侵入
        "workers": 2,               # 进程数（Windows spawn 建议 <= CPU/2）
        "start_method": "spawn",    # Windows 只能用 spawn
        "preload_index": False,     # 是否每个 Worker 预加载代码索引
        "task_timeout_sec": 3600,   # 单任务超时（1小时，复杂任务不应被中断）
        "checkpoint": {
            "enabled": True,
            "dir": str(CONFIG_DIR / "swarm_checkpoints"),
            "redis_url": None,      # 可选，单机留空
        },
        "retry": {
            "max_retries": 3,
            "backoff_factor": 2.0,
        },
        "circuit_breaker": {
            "failure_threshold": 5,
            "recovery_timeout": 300,
        },
    },
    # === 视觉感知 ===
    "vision": {
        "enabled": True,
        "strategy": "auto",        # auto / a11y / multimodal / hybrid
        "screenshot_dir": str(CONFIG_DIR / "screenshots"),
        "ocr_language": "chi_sim+eng",
        "code_fusion_enabled": True,   # 视觉-代码融合 (VisualCodeFusion)
    },
    # === 代码智能 ===
    "code_intel": {
        "enabled": True,               # 是否启用代码索引
        "index_strategy": "lazy",      # lazy / eager / manual
        "repo_map_enabled": True,      # 符号图
        "semantic_edit_enabled": True, # 语义编辑
        "exclude_patterns": ["node_modules/", "__pycache__/", ".git/", "venv/", ".venv/"],
    },
    # === 工作流 ===
    "workflow": {
        "enabled": True,
        "default_dir": str(CONFIG_DIR / "workflows"),
        "max_parallel_nodes": 4,
        "default_timeout_sec": 3600,
    },
    # === 可观测性 ===
    "telemetry": {
        "enabled": False,
        "backend": "jsonl",          # jsonl | console | otel | combined
        "profile_dir": str(CONFIG_DIR / "profiles"),
        "sample_rate": 1.0,
        "verbose": False,
        "otel_endpoint": "",
        "otel_headers": {},
    },
    # === 缓存 ===
    "cache": {
        "enabled": False,
        "exact_match": {
            "enabled": True,
            "max_size": 1000,
            "ttl_sec": 3600,
        },
        "semantic": {
            "enabled": True,
            "threshold": 0.92,
            "ttl_sec": 3600,
            "max_entries": 500,
        },
        "prompt_prefix_optimize": True,
    },
    # === 自我改进 ===
    "self_improve": {
        "enabled": False,
        "auto_apply": False,
        "threshold": 3,            # 某类失败出现几次后触发进化
        "cheap_model_id": "deepseek/deepseek-chat",
        "max_prompt_versions": 5,  # 保留历史版本数
    },
    # === CLI ===
    "cli": {
        "streaming": True,
        "show_tool_calls": True,
    },
    # === 项目 ===
    "project_root": str(Path.home() / "kimi-workspace"),
    "first_run": True,
}


class XAgentConfig:
    """集中配置管理器"""

    def __init__(self):
        self._data: dict = {}
        self.load()

    def load(self):
        raw_data = None
        if CONFIG_PATH.exists():
            try:
                raw_data = CONFIG_PATH.read_text(encoding="utf-8")
                self._data = json.loads(raw_data)
                # 合并新版本默认值
                self._merge_defaults(DEFAULT_CONFIG, self._data)
            except Exception:
                self._data = self._deep_copy(DEFAULT_CONFIG)
        else:
            self._data = self._deep_copy(DEFAULT_CONFIG)

        # ── 启动完整性校验 ──
        validation_errors = self._validate_config()
        import_errors = self._validate_imports()

        if validation_errors or import_errors:
            # 备份损坏的配置
            backup_path = None
            if raw_data:
                backup_path = CONFIG_DIR / f"config.json.bak.{int(time.time())}"
                try:
                    backup_path.write_text(raw_data, encoding="utf-8")
                except Exception:
                    pass

            # 回退到默认配置，但保留有效的 _adaptive
            saved_adaptive = self._data.get("_adaptive")
            self._data = self._deep_copy(DEFAULT_CONFIG)
            if saved_adaptive and isinstance(saved_adaptive, dict):
                self._data["_adaptive"] = saved_adaptive

            # 记录到 ErrorLedger（带指纹去重）
            all_errors = validation_errors + import_errors
            error_summary = "; ".join(all_errors)
            try:
                from .error_ledger import ErrorLedger
                ledger = ErrorLedger()
                fp = ledger.record(
                    category="config_validation",
                    message="配置校验失败，已自动回退到默认配置",
                    detail=error_summary,
                    context={"backup_path": str(backup_path) if backup_path else None},
                )
                # 指纹变化才打印（避免每次启动重复提醒）
                last_fp = saved_adaptive.get("_last_error_fingerprint") if isinstance(saved_adaptive, dict) else None
                if fp != last_fp:
                    print("⚠️  配置校验失败，已自动回退到默认配置。详情：")
                    for err in all_errors:
                        print(f"   - {err}")
                    if backup_path:
                        print(f"   原配置已备份到: {backup_path}")
                    if isinstance(saved_adaptive, dict):
                        saved_adaptive["_last_error_fingerprint"] = fp
                        self._data["_adaptive"] = saved_adaptive
            except Exception:
                # ledger 不可用时的降级：直接打印
                print("⚠️  配置校验失败，已自动回退到默认配置。详情：")
                for err in all_errors:
                    print(f"   - {err}")
                if backup_path:
                    print(f"   原配置已备份到: {backup_path}")
            self.save()

        # 启动时检测硬件：首次启动或硬件发生变化时重新应用自适应配置
        adaptive_cfg = self._data.get("adaptive", {})
        if adaptive_cfg.get("enabled", True):
            try:
                from .resource_adaptive import SystemProfiler, apply_adaptive_config
                profile = SystemProfiler.detect()
                current_fp = profile.fingerprint()
                saved_fp = self._data.get("_adaptive", {}).get("hw_fingerprint")

                # 首次启动 或 硬件指纹变化 时重新应用
                if saved_fp != current_fp:
                    apply_adaptive_config(self._data, profile)
                    print(SystemProfiler.report())
                    if saved_fp is not None:
                        print(f"   检测到硬件变化: {saved_fp} → {current_fp}")
                    self.save()
            except Exception:
                pass

        if not CONFIG_PATH.exists():
            self.save()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _deep_copy(obj):
        return json.loads(json.dumps(obj))

    @classmethod
    def _merge_defaults(cls, defaults: dict, target: dict):
        for k, v in defaults.items():
            if k not in target:
                target[k] = cls._deep_copy(v)
            elif isinstance(v, dict) and isinstance(target[k], dict):
                cls._merge_defaults(v, target[k])

    def get(self, key: str, default=None) -> Any:
        """支持点号路径: config.get('model.provider')"""
        keys = key.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def _validate_config(self) -> list[str]:
        """配置 schema 校验：检查关键字段的类型和范围"""
        errors = []
        d = self._data

        def _check_int(path: str, min_val: int = None, max_val: int = None):
            keys = path.split(".")
            val = d
            for k in keys:
                if isinstance(val, dict) and k in val:
                    val = val[k]
                else:
                    return  # 字段不存在时跳过（merge_defaults 会补全）
            if not isinstance(val, int):
                errors.append(f"{path} 应为整数，当前类型: {type(val).__name__}")
                return
            if min_val is not None and val < min_val:
                errors.append(f"{path}={val} 低于最小值 {min_val}")
            if max_val is not None and val > max_val:
                errors.append(f"{path}={val} 超过最大值 {max_val}")

        def _check_float(path: str, min_val: float = None, max_val: float = None):
            keys = path.split(".")
            val = d
            for k in keys:
                if isinstance(val, dict) and k in val:
                    val = val[k]
                else:
                    return
            if not isinstance(val, (int, float)):
                errors.append(f"{path} 应为数值，当前类型: {type(val).__name__}")
                return
            if min_val is not None and val < min_val:
                errors.append(f"{path}={val} 低于最小值 {min_val}")
            if max_val is not None and val > max_val:
                errors.append(f"{path}={val} 超过最大值 {max_val}")

        _check_int("model.max_tokens", min_val=1)
        _check_float("adaptive.cpu_threshold", min_val=0, max_val=100)
        _check_float("adaptive.memory_threshold", min_val=0, max_val=100)
        _check_int("swarm.workers", min_val=1)
        _check_int("self_improve.threshold", min_val=1)
        _check_float("swarm.task_timeout_sec", min_val=1)
        _check_int("workflow.max_parallel_nodes", min_val=1)
        _check_float("routing.budget_usd_per_turn", min_val=0)

        return errors

    @staticmethod
    def _validate_imports() -> list[str]:
        """核心模块 import 测试：检测代码损坏/语法错误"""
        errors = []
        core_modules = [
            "xagent.core.agent_loop",
            "xagent.core.cache_loop",
            "xagent.tools.filesystem",
            "xagent.core.self_improve",
        ]
        for mod in core_modules:
            try:
                __import__(mod)
            except SyntaxError as e:
                errors.append(f"模块 {mod} 存在语法错误: {e}")
            except ImportError as e:
                errors.append(f"模块 {mod} 无法导入: {e}")
            except Exception as e:
                errors.append(f"模块 {mod} 加载异常: {e}")
        return errors

    def set(self, key: str, value: Any):
        keys = key.split(".")
        target = self._data
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value
        self.save()

    @property
    def model(self) -> dict:
        return self._data.get("model", {})

    def set_model_preset(self, preset_name: str):
        """切换模型预设"""
        presets = self._data.get("model_presets", {})
        if preset_name not in presets:
            raise ValueError(f"未知预设: {preset_name}。可用: {list(presets.keys())}")
        preset = presets[preset_name]
        self._data["model"].update(preset)
        self._data["active_model"] = preset_name
        self.save()

    def list_model_presets(self) -> list:
        return list(self._data.get("model_presets", {}).keys())

    @property
    def active_model(self) -> str:
        return self._data.get("active_model", "")

    @property
    def ui(self) -> dict:
        return self._data.get("gui", {})

    @property
    def safety(self) -> dict:
        return self._data.get("safety", {})

    @property
    def memory(self) -> dict:
        return self._data.get("memory", {})

    @property
    def project_root(self) -> Path:
        return Path(self._data.get("project_root", DEFAULT_CONFIG["project_root"]))
