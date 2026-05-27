# X-Agent 技术架构文档

> 供开发者和接管 Agent 阅读。涵盖模块划分、数据流、关键类设计、扩展点。

## 目录

- [1. 项目结构](#1-项目结构)
- [2. 核心循环](#2-核心循环)
- [3. 工具层](#3-工具层)
- [4. 自我改进系统](#4-自我改进系统)
- [5. 资源自适应](#5-资源自适应)
- [6. 代码智能](#6-代码智能)
- [7. 扩展点](#7-扩展点)

---

## 1. 项目结构

```
xagent/
├── core/                          # 核心引擎
│   ├── agent_loop.py              # Legacy ReAct 主循环
│   ├── cache_loop.py              # CacheFirst 循环（DeepSeek 优化）
│   ├── tool_registry.py           # 工具注册中心
│   ├── llm_client.py              # 多 Provider LLM 客户端
│   ├── router.py                  # 智能模型路由
│   ├── memory_engine.py           # 向量记忆（sentence-transformers）
│   ├── planner.py                 # 任务规划器
│   ├── executor.py                # 任务执行器
│   ├── workflow/                  # 工作流引擎
│   │   └── engine.py
│   ├── swarm/                     # 多 Agent Swarm（进程池）
│   │   ├── controller.py
│   │   └── shared_index.py
│   ├── self_improve/              # 自我改进系统
│   │   ├── failure_classifier.py  # 6 类失败模式分类器
│   │   ├── experience_bank.py     # SQLite 经验银行
│   │   ├── root_cause_analyzer.py # 根因分析器（LLM 驱动）
│   │   └── prompt_evolver.py      # Prompt 进化器（ShadowEval）
│   ├── code_intel/                # 代码智能
│   │   ├── indexer.py             # tree-sitter AST 索引
│   │   └── repo_map.py            # 仓库符号图构建
│   ├── telemetry/                 # 可观测性
│   │   ├── collector.py
│   │   ├── exporter.py
│   │   └── spans.py
│   ├── resource_adaptive.py       # 动态资源自适应引擎
│   └── error_ledger.py            # 系统错误台账（JSONL）
├── tools/                         # 工具实现
│   ├── filesystem.py              # 文件操作（含 lint 验证门禁）
│   ├── shell.py                   # Shell 执行
│   ├── web.py                     # 网页搜索
│   ├── git_tool.py                # Git 操作
│   ├── browser.py                 # 浏览器自动化
│   ├── http.py                    # HTTP 请求
│   ├── database.py                # SQL 数据库
│   ├── docgen.py                  # 文档生成
│   ├── api_test.py                # API 测试
│   └── code_quality.py            # 代码质量检查
├── gui/                           # PySide6 GUI
│   ├── main_window.py             # 主窗口
│   ├── bridge.py                  # JS ↔ Python 桥接
│   ├── worker.py                  # Agent 后台线程
│   └── settings_dialog.py         # 设置对话框
├── cli/                           # 命令行入口
│   └── app.py
├── config.py                      # 集中配置管理器
├── web_ui/                        # 前端面板（HTML/JS）
└── tests/                         # 测试套件（462 个用例）
```

---

## 2. 核心循环

### 2.1 AgentLoop（Legacy 模式）

**文件**：`core/agent_loop.py`

**核心流程**：
```python
def run(self, user_input: str) -> str:
    1. 检索记忆 (MemoryEngine.recall)
    2. 构建 Repo Map (_build_repo_context)
    3. 构造 system_msg (SYSTEM_PROMPT + 记忆 + Repo Map)
    4. for iteration in range(max_tool_iterations):
         a. 总时间预算检查
         b. 意图锚定（每 5 轮）
         c. LLM 调用 (_chat_with_routing)
         d. 处理 tool_calls / 返回最终回复
    5. Telemetry 结束 trace
    6. Self-Improvement 分析失败
```

**关键常量**：
- `SYSTEM_PROMPT`：模块级字符串，含 `{os_name}` `{project_root}` `{cwd}` 占位符，`.format()` 动态注入
- `max_tool_iterations`：按档位（low=5, mid=10, high=15）

**Prompt 进化加载**：
`__init__` 初始化时调用 `PromptEvolver.load_best_prompt(SYSTEM_PROMPT)`，如果进化版本评分优于基线 5%，替换模块级常量。

### 2.2 CacheFirstLoop（缓存优先模式）

**文件**：`core/cache_loop.py`

**触发条件**：`AgentLoop._should_use_cache_mode()` 检测到 DeepSeek 模型时自动切换。

**核心不变量**：
1. `ImmutablePrefix`：session 开始时构造，永不修改（保护 KV cache）
2. `AppendOnlyLog`：只追加，不修改
3. `VolatileScratch`：每轮清空

**并行执行**：
- 读操作工具可并行（`can_parallel`）
- 低配置（tier=low）禁用并行，避免资源争抢
- 并行组内单个工具 30s 超时

---

## 3. 工具层

### 3.1 ToolRegistry

**文件**：`core/tool_registry.py`

```python
class ToolRegistry:
    def register(name, description, parameters, func, dangerous=False, parallel_safe=False)
    def execute(name, arguments, confirm_callback=None) -> {"ok": bool, "result": Any, "error": str}
```

- 支持内置工具 + MCP 适配器动态工具
- 危险操作需 `confirm_callback` 确认
- 审计日志（AuditLog）记录每次调用

### 3.2 edit_file 验证门禁

**文件**：`tools/filesystem.py`

写入前强制验证：
1. `compile()` 语法检查（Python）
2. `ruff check` / `flake8` / `py_compile` 静态分析
3. lint 超时按档位动态（low=30s, mid=60s, high=120s）
4. 超时 ≠ 通过，返回 `"[lint 检查超时] 文件语法正确，但静态分析未完成，状态未知"`

---

## 4. 自我改进系统

**目录**：`core/self_improve/`

### 4.1 数据流

```
AgentLoop.run() 失败（迭代超限 / tool 错误）
    ↓
FailureClassifier.classify()  →  FailureType + 置信度
    ↓
ExperienceBank.record()         →  SQLite 持久化，frequency++
    ↓
（frequency >= threshold）触发进化
    ↓
RootCauseAnalyzer.analyze()     →  root_cause + suggested_fix_category
    ↓
PromptEvolver.evolve()          →  生成 3 个候选 prompt
    ↓
ShadowEval：评分 vs 基线
    ↓
评分 >= 基线 * 1.1 → 接受 → 保存到 prompt_evolution/
    ↓
下次启动时 load_best_prompt() 加载到运行时
```

### 4.2 PromptEvolver 安全机制

- **长度硬上限**：>8000 字符直接拒绝并删除
- **格式校验**：必须包含 `{os_name}` `{project_root}` `{cwd}` 占位符
- **评分对比**：优于基线 5% 才使用
- **基线保存**：`save_baseline()` 保存出厂 prompt，`rollback()` 可回到基线

---

## 5. 资源自适应

**文件**：`core/resource_adaptive.py`

### 5.1 档位定义

| 档位 | 条件 | 典型配置 |
|------|------|---------|
| low | 内存 < 4GB 或 CPU ≤ 2核 | lint_timeout=30s, max_files=100, 串行工具, 总时间=300s |
| mid | 其他 | lint_timeout=60s, max_files=500, 允许并行, 总时间=600s |
| high | 内存 ≥ 16GB 且 CPU ≥ 8核 | lint_timeout=120s, max_files=2000, 全并行, 总时间=1800s |

### 5.2 硬件指纹

`SystemProfile.fingerprint()` = `"cpu=N|mem=X.X|freq=YYYY"`

每次启动重新检测，指纹变化则重新应用配置。解决换机器/升级硬件后仍用旧配置的问题。

---

## 6. 代码智能

**目录**：`core/code_intel/`

### 6.1 CodeIndexer

- tree-sitter AST 解析（Python / JS / TS）
- jedi Python 增强（类型推断）
- 惰性索引：`AgentLoop._ensure_indexed()` 首次调用时触发
- 按档位限制文件数：`max_index_files`（low=100, mid=500, high=2000）

### 6.2 Repo Map

`_build_repo_context()` 构建轻量符号图：
- 最多 N 个文件（按档位）
- 每文件最多 M 个符号（按档位）
- 只注入类/函数/方法，不读文件内容

---

## 7. 扩展点

### 7.1 新增工具

```python
# xagent/tools/my_tool.py
def register_my_tools(registry):
    registry.register(
        name="my_tool",
        description="...",
        parameters={"type": "object", "properties": {...}},
        func=my_function,
        dangerous=False,
        parallel_safe=True,
    )

# xagent/tools/__init__.py
from .my_tool import register_my_tools

def register_all_tools(registry, project_root="."):
    ...
    register_my_tools(registry)
```

### 7.2 新增失败类型

```python
# core/self_improve/failure_classifier.py
class FailureType(Enum):
    TOOL_PARSE_ERROR = auto()
    ...
    MY_NEW_FAILURE = auto()   # ← 新增
```

### 7.3 自定义自适应配置

```python
# resource_adaptive.py 中 SystemProfiler.recommend()
# 添加新的 AdaptiveSettings 字段
```

---

## 关键数据持久化位置

| 数据 | 路径 | 格式 |
|------|------|------|
| 配置 | `~/.xagent/config.json` | JSON |
| 记忆 | `~/.xagent/memory/` | SQLite + 向量索引 |
| 经验银行 | `~/.xagent/experience_bank.db` | SQLite |
| Prompt 进化 | `~/.xagent/prompt_evolution/` | JSON |
| 错误台账 | `~/.xagent/error_ledger.jsonl` | JSONL |
| 工作流 | `~/.xagent/workflows/` | JSON |
| 审计日志 | `~/.xagent/audit/` | 按日期分文件 |
