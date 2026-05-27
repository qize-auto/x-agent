# X-Agent 系统运作说明

> 面向运维和深度使用者。解释系统从启动到完成一次任务的完整生命周期。

## 目录

- [1. 启动流程](#1-启动流程)
- [2. 配置加载与校验](#2-配置加载与校验)
- [3. 一次任务的生命周期](#3-一次任务的生命周期)
- [4. 错误处理与自愈](#4-错误处理与自愈)
- [5. 资源自适应动态调整](#5-资源自适应动态调整)
- [6. 自我改进触发时机](#6-自我改进触发时机)

---

## 1. 启动流程

```
python -m xagent.cli app
    ↓
XAgentConfig.__init__() → load()
    ↓
┌────────────────────────────────────────────┐
│ 1. 读取 ~/.xagent/config.json              │
│    - JSON 解析失败 → 回退 DEFAULT_CONFIG   │
│    - Schema 校验失败 → 备份 + 回退         │
│    - Import 测试失败 → 标记模块损坏        │
└────────────────────────────────────────────┘
    ↓
┌────────────────────────────────────────────┐
│ 2. 硬件检测（每次启动）                    │
│    - psutil 扫描 CPU/内存/频率             │
│    - 计算指纹 cpu=N|mem=X|freq=Y           │
│    - 与上次指纹对比                        │
│    - 变化 → 重新分档（low/mid/high）       │
│    - 应用 AdaptiveSettings                 │
└────────────────────────────────────────────┘
    ↓
┌────────────────────────────────────────────┐
│ 3. Prompt 进化加载                         │
│    - 扫描 ~/.xagent/prompt_evolution/      │
│    - 最新版本 vs 内置基线评分对比          │
│    - 通过格式校验 + 长度上限 → 替换运行时  │
└────────────────────────────────────────────┘
    ↓
AgentLoop / CacheFirstLoop 初始化
    ↓
等待用户输入
```

**关键设计**：硬件检测不是"只检测一次"。换机器、升级内存、虚拟机迁移都能自动重新分档。

---

## 2. 配置加载与校验

### 2.1 Schema 校验字段

```python
_validate_config() 检查以下字段：
- model.max_tokens        → int, ≥ 1
- adaptive.cpu_threshold  → float, 0-100
- adaptive.memory_threshold → float, 0-100
- swarm.workers           → int, ≥ 1
- self_improve.threshold  → int, ≥ 1
- swarm.task_timeout_sec  → float, ≥ 1
- workflow.max_parallel_nodes → int, ≥ 1
- routing.budget_usd_per_turn → float, ≥ 0
```

### 2.2 校验失败后的自动回退

```
校验失败
    ↓
备份原配置 → ~/.xagent/config.json.bak.{timestamp}
    ↓
回退到 DEFAULT_CONFIG（保留 _adaptive 硬件配置）
    ↓
记录到 ErrorLedger（指纹去重）
    ↓
CLI print 提醒（首次出现才打印，已提醒过的不重复）
    ↓
GUI 启动后 toast 展示未确认错误
    ↓
标记为已确认（acknowledged=True）
```

---

## 3. 一次任务的生命周期

以用户输入"帮我重构这个项目里的所有函数，添加类型注解"为例：

### 3.1 规划阶段（如果是复杂任务）

```
用户输入
    ↓
AgentLoop._detect_mode() → "plan"（长度>120 或含任务关键词）
    ↓
plan_task():
  1. _ensure_indexed() → 惰性索引代码库
  2. _build_repo_context() → 构建 Repo Map（最多 N 文件 × M 符号）
  3. TaskPlanner.plan() → LLM 分解为子任务计划
    ↓
返回 TaskPlan（Markdown 格式，需用户确认）
```

### 3.2 执行阶段

```
用户确认计划
    ↓
AgentLoop.run() 进入工具调用循环
    ↓
For iteration in range(max_tool_iterations):
  ├─ 总时间预算检查（超预算优雅终止）
  ├─ 意图锚定（每 5 轮提醒原始目标）
  ├─ LLM 调用（timeout=60s，网络层 120s）
  ├─ 修复流水线（ToolCallRepairPipeline）
  │   └─ JSON 截断修复 / Schema 扁平化
  ├─ 处理 tool_calls
  │   └─ 低配置串行，高配置并行（30s 单工具上限）
  └─ 如果 edit_file → compile() + lint 验证门禁
    ↓
无 tool_calls → 输出最终回复
```

### 3.3 收尾阶段

```
MemoryEngine.add() → 持久化对话记忆
Telemetry.finish_trace() → 导出 trace（如启用）
_failure_analyzer 分析失败 → 可能触发 Prompt 进化
```

---

## 4. 错误处理与自愈

### 4.1 四层错误体系

| 层级 | 示例 | 处理方式 |
|------|------|---------|
| **配置层** | Schema 类型错误、模块导入失败 | 自动回退到默认配置 + 备份 + 提醒 |
| **验证层** | edit_file 语法错误、lint 失败 | 拒绝写入，返回错误给 LLM |
| **工具层** | Shell 命令超时、文件不存在 | ToolRegistry 捕获异常，返回 `{"ok": False, "error": ...}` |
| **循环层** | LLM 网络超时、迭代超限 | ErrorLedger 记录 + GUI toast + 总时间预算优雅终止 |

### 4.2 ErrorLedger 工作原理

```python
# 记录（自动去重：7 天内相同指纹不重复）
fp = ledger.record("runtime", "Agent 执行异常", detail=err_msg)

# 获取未确认（GUI 启动时展示）
errors = ledger.get_unacknowledged(categories=["config_validation", "runtime"])

# 确认（展示后调用，下次不再提醒）
ledger.acknowledge(fp)
```

### 4.3 全链路超时保护

| 层级 | 超时 | 行为 |
|------|------|------|
| LLM HTTP 客户端 | 120s | 连接层总超时 |
| LLM 单次请求 | 60s | `create(timeout=60)` |
| 并行工具单任务 | 30s | `future.result(timeout=30)` |
| Workflow 节点 | 60s | `future.result(timeout=60)` |
| Agent 总时间 | 300/600/1800s | 优雅终止，返回阶段性成果 |

---

## 5. 资源自适应动态调整

### 5.1 启动时检测

```python
SystemProfiler.detect():
  cpu_count = psutil.cpu_count(logical=True)
  memory_gb = psutil.virtual_memory().total / (1024**3)
  cpu_freq_mhz = psutil.cpu_freq().max
  is_low_end = memory_gb < 4 or cpu_count <= 2
  is_virtual = cpu_freq_mhz > 0 and cpu_freq_mhz < 1500
```

### 5.2 档位推荐配置

| 参数 | low | mid | high |
|------|-----|-----|------|
| max_tokens | 2048 | 4096 | 8192 |
| max_tool_iterations | 5 | 10 | 15 |
| lint_timeout_sec | 30 | 60 | 120 |
| max_index_files | 100 | 500 | 2000 |
| repo_map_max_files | 20 | 30 | 50 |
| shell_default_timeout | 120 | None | None |
| max_total_time_sec | 300 | 600 | 1800 |
| enable_parallel | False | True | True |

### 5.3 运行时限流

```python
ResourceMonitor:
  后台线程，5s 间隔采样 CPU/内存
  
Throttler:
  check() → 高负载时跳过非关键任务
  wait_if_needed() → 工具调用间主动等待
```

---

## 6. 自我改进触发时机

### 6.1 触发条件

```python
# AgentLoop.run() 结束时的 _analyze_and_evolve()
if self_improve.enabled and error_msg:
    1. FailureClassifier.classify() 置信度 ≥ 0.7
    2. RootCauseAnalyzer.analyze()
    3. ExperienceBank.record()
    4. 检查 frequency >= threshold（默认 3）
    5. 如果 auto_apply=True → PromptEvolver.evolve()
    6. ShadowEval 评分 >= 基线 * 1.1 → 接受
```

### 6.2 用户可控

```bash
# 查看经验银行统计
xagent self-improve --status

# 查看高频失败历史
xagent self-improve --history

# 回滚 prompt（到上一进化版本或基线）
xagent self-improve --rollback

# 调整触发阈值
xagent self-improve --threshold 5
```

### 6.3 安全开关

- `self_improve.enabled=False`（默认）→ 只记录，不进化
- `self_improve.auto_apply=False`（默认）→ 达到阈值也不自动应用，需手动触发
