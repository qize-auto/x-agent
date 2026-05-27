# AGENTS.md —— X-Agent 接手指南

> 如果你是另一个 AI Agent，需要修改、扩展或维护 X-Agent，请完整阅读本文档后再动手。

## 目录

- [1. 项目概览](#1-项目概览)
- [2. 编码规范](#2-编码规范)
- [3. 测试规范](#3-测试规范)
- [4. 常见修改模式](#4-常见修改模式)
- [5. 文件修改清单](#5-文件修改清单)
- [6. 避坑指南](#6-避坑指南)

---

## 1. 项目概览

**技术栈**：Python 3.14+, PySide6, tree-sitter, jedi, psutil, sentence-transformers, chromadb, pytest
**测试基线**：462 passed, 3 skipped（零回归是铁律）
**进程启动方式**：Windows 强制 spawn（Swarm 多进程已固化）
**配置文件**：`~/.xagent/config.json`
**状态目录**：`~/.xagent/`（经验银行、prompt 进化、错误台账、审计日志）

**双循环架构**：
- `AgentLoop`（Legacy）：通用 ReAct，所有模型可用
- `CacheFirstLoop`：DeepSeek 前缀缓存优化，`ImmutablePrefix` 保护 KV cache

**核心设计原则**：
1. 精准度优先（不因时间/资源紧跳过验证）
2. 降速不降质（低配置减少并行，不砍验证流水线）
3. 所有限流决策可覆盖，不强制

---

## 2. 编码规范

### 2.1 配置访问

**正确**：
```python
# 点号路径支持
self.config.get("a.b")

# 嵌套配置安全访问
self.config.get("section", {}).get("key")
```

**错误**：
```python
self.config["section"]["key"]  # 可能 KeyError
```

### 2.2 MockLLM 规范

测试中的 Mock 响应必须使用 `type()` 动态创建：

```python
# 正确
resp = type("FakeResp", (), {})()
resp.content = "ok"
resp.tool_calls = []
resp.usage = {}

# 错误
dict(content="ok")  # LLMClient 期望属性访问，不是 dict
```

### 2.3 超时参数

Shell 默认 `timeout=None`（复杂任务不应被中断），未传入时读取自适应配置：

```python
# shell.py
def run_command(command, cwd="", timeout=None):
    if timeout is None:
        try:
            timeout = XAgentConfig()._data.get("_adaptive", {}).get("shell_default_timeout")
        except Exception:
            pass
```

### 2.4 新增字段到 AdaptiveSettings

如果新增资源相关字段，必须：
1. 在 `AdaptiveSettings` dataclass 中定义
2. 在 `SystemProfiler.recommend()` 的 low/high 中赋值
3. 在 `apply_adaptive_config()` 中导出到 `_adaptive`
4. 在使用处通过 `self.config.get("_adaptive", {}).get("key")` 读取

---

## 3. 测试规范

### 3.1 运行测试

```bash
cd ~/kimi-workspace/x-agent
python -m pytest tests/ -q
```

### 3.2 零回归原则

任何代码修改后，**462 passed, 3 skipped** 必须保持不变。如果测试失败：
1. 先检查是否是你的修改引入了回归
2. 如果是 Mock 相关，检查是否遵循 MockLLM 规范
3. 如果是导入相关，检查是否有循环依赖

### 3.3 新增测试位置

| 模块 | 测试文件 |
|------|---------|
| self_improve | `tests/test_self_improve.py` |
| resource_adaptive | `tests/test_resource_adaptive.py` |
| code_intel | `tests/test_code_intel.py`, `tests/test_code_intel_advanced.py` |
| vision | `tests/test_vision.py` |
| swarm | `tests/swarm_poc/` |

---

## 4. 常见修改模式

### 4.1 新增工具

```python
# 1. 创建 xagent/tools/my_tool.py
def my_function(param: str) -> str:
    return f"result: {param}"

def register_my_tools(registry):
    registry.register(
        name="my_tool",
        description="Do something useful",
        parameters={
            "type": "object",
            "properties": {
                "param": {"type": "string", "description": "Input parameter"}
            },
            "required": ["param"]
        },
        func=my_function,
        dangerous=False,
        parallel_safe=True,
    )

# 2. 修改 xagent/tools/__init__.py
from .my_tool import register_my_tools

def register_all_tools(registry, project_root="."):
    ...
    register_my_tools(registry)
```

### 4.2 修改 System Prompt

直接修改 `agent_loop.py` 顶部的 `SYSTEM_PROMPT` 常量即可。PromptEvolver 会在启动时自动加载进化版本（如果评分更高）。

如果修改涉及占位符，必须确保：
```python
# 修改后验证
SYSTEM_PROMPT.format(os_name="test", project_root="/tmp", cwd="/tmp")
```

### 4.3 修改自适应配置

```python
# resource_adaptive.py
@dataclass
class AdaptiveSettings:
    ...
    my_new_field: int = 100

class SystemProfiler:
    @classmethod
    def recommend(cls, profile):
        if tier == "low":
            return AdaptiveSettings(my_new_field=50, ...)
        if tier == "high":
            return AdaptiveSettings(my_new_field=200, ...)

def apply_adaptive_config(config_data, profile=None):
    ...
    d["_adaptive"]["my_new_field"] = settings.my_new_field
```

### 4.4 修改 GUI

```python
# main_window.py
# 使用 _push_panel_js() 与前端通信
self._push_panel_js('showToast("消息")')
self._push_panel_js('setStatus("thinking", "思考中…")')
self._push_panel_js(f'appendAgentMessage({json.dumps(text)})')
```

前端 JS 函数（定义在 `web_ui/index.html`）：
- `showToast(msg)` —— 轻量提示
- `setStatus(state, label)` —— 状态栏
- `appendAgentMessage(text)` —— 追加回复
- `appendError(text)` —— 追加错误
- `appendToolCall(name)` —— 显示工具调用

---

## 5. 文件修改清单

当你需要实现某类功能时，参考下表确定需要修改的文件：

| 功能类型 | 必须修改的文件 | 可能需要修改的文件 |
|---------|--------------|------------------|
| 新增工具 | `tools/my_tool.py`, `tools/__init__.py` | `tests/test_*.py` |
| 修改 prompt | `core/agent_loop.py`, `core/cache_loop.py` | `core/self_improve/prompt_evolver.py` |
| 新增失败类型 | `core/self_improve/failure_classifier.py` | `core/self_improve/experience_bank.py` |
| 修改自适应配置 | `core/resource_adaptive.py` | `core/agent_loop.py`, `tools/shell.py`, `core/code_intel/indexer.py` |
| 修改 GUI | `gui/main_window.py`, `gui/bridge.py` | `web_ui/index.html` |
| 新增工作流节点 | `core/workflow/models.py`, `core/workflow/engine.py` | `tests/test_workflow*.py` |
| 修改启动流程 | `config.py` | `core/resource_adaptive.py` |

---

## 6. 避坑指南

### 6.1 Windows 路径

Windows 下使用 `Path` 对象，不要硬编码反斜杠：

```python
# 正确
from pathlib import Path
p = Path.home() / ".xagent" / "config.json"

# 错误
"C:\\Users\\pc\\.xagent\\config.json"
```

### 6.2 循环依赖

`config.py` 和 `resource_adaptive.py` 之间容易形成循环导入：

```python
# 安全：在函数内部延迟导入
 def load(self):
     from .resource_adaptive import SystemProfiler, apply_adaptive_config
```

### 6.3 LLM 客户端超时

`LLMClient` 初始化时设置了 `timeout=120`（HTTP 层），单次 `create()` 设置 `timeout=60`。修改时不要遗漏 `chat_stream_events()` 中的流式调用。

### 6.4 Prompt 格式占位符

`SYSTEM_PROMPT` 和 `PURE_SYSTEM_PROMPT` 必须包含 `{os_name}`、`{project_root}`、`{cwd}`。`PromptEvolver.load_best_prompt()` 会验证这些占位符，缺失则拒绝加载进化版本。

### 6.5 模块级常量修改

`AgentLoop.__init__` 中通过 `import xagent.core.agent_loop as _al; _al.SYSTEM_PROMPT = best_prompt` 替换模块级常量。这是单进程安全的，但如果未来支持多进程 Agent，需要改为实例级存储。

### 6.6 ErrorLedger 写入

`ErrorLedger` 使用 JSONL 追加写入，理论上不会阻塞。但在高频异常场景下（如无限循环产生异常），可能积累大量记录。`get_unacknowledged()` 会自动去重（同指纹只返回最新），但文件会增长。当前无自动清理机制。

### 6.7 配置文件回退陷阱

`config.py` 的 `_validate_config()` 只检查字段类型和范围。如果用户把 `model.max_tokens` 从 `4096` 改成 `"4096"`（字符串），校验会失败并回退到默认配置。但用户自定义的其他合法字段也会丢失。**不要过度依赖自动回退**——它是最坏情况的保险，不是正常操作流程。
