# Swarm 多进程执行

X-Agent Swarm 模块提供单机多进程并行执行能力，兼容 Windows `spawn` 模式。

## 何时使用 Swarm

| 场景 | 建议 |
|------|------|
| 轻量 Mock/本地任务 | **单线程**（默认，更快） |
| 真实 LLM API 并发调用 | **Swarm**（突破 GIL，多 Key 并行） |
| 长时间运行的分析任务 | **Swarm**（故障隔离，超时自动重试） |
| CPU 密集型代码分析 | **Swarm**（多核并行） |

> 基准测试：7 节点 Mock 工作流，单线程 0.001s，Swarm 2 Workers 0.85s。  
> Swarm 的价值在**真实 LLM 调用**和**长任务**中体现，轻量任务建议用默认单线程。

## 快速开始

### CLI

```bash
# 默认单线程
xagent workflow --run pipeline.yaml

# Swarm 模式（2 Workers）
xagent workflow --run pipeline.yaml --swarm-workers 2
```

### Python API

```python
from xagent.core.swarm import SwarmController, SwarmExecutor
from xagent.core.workflow.engine import WorkflowEngine

# 创建 Swarm 控制器
controller = SwarmController(
    num_workers=4,
    config={"swarm": {"enabled": True}},
    enabled=True,
)

# 作为 WorkflowEngine 的可插拔执行器
executor = SwarmExecutor(controller)
engine = WorkflowEngine(executor=executor)
engine.run(workflow)
```

## 配置

`~/.xagent/config.json` 中的 `swarm` 块：

```json
{
  "swarm": {
    "enabled": false,
    "workers": 2,
    "start_method": "spawn",
    "preload_index": false,
    "task_timeout_sec": 300,
    "checkpoint": {
      "enabled": true,
      "dir": "~/.xagent/swarm_checkpoints",
      "redis_url": null
    },
    "retry": {
      "max_retries": 3,
      "backoff_factor": 2.0
    },
    "circuit_breaker": {
      "failure_threshold": 5,
      "recovery_timeout": 30
    }
  }
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 是否启用 Swarm | `false`（默认关闭，零侵入） |
| `workers` | Worker 进程数 | `2`（建议 ≤ CPU/2） |
| `preload_index` | 预加载代码索引到共享内存 | `false` |
| `task_timeout_sec` | 单任务超时 | `300` |
| `max_retries` | 失败重试次数 | `3` |
| `backoff_factor` | 指数退避基数 | `2.0` |
| `failure_threshold` | 熔断器失败阈值 | `5` |
| `recovery_timeout` | 熔断器恢复时间 | `30` |

## 共享内存优化（Windows）

Windows `spawn` 模式下，每个 Worker 是全新 Python 解释器，需 0.8s 初始化 + 53MB 内存。  
开启 `preload_index: true` 后：

1. 主进程构建代码索引，序列化到 `multiprocessing.shared_memory`
2. Worker 进程直接从共享内存恢复索引，**避免重复构建**
3. 主进程关闭时自动清理共享内存

```bash
# 开启共享内存索引优化
xagent workflow --run big_project.yaml --swarm-workers 4
# 配置中设置 "preload_index": true
```

## 故障排查

### Worker 启动慢（>5s）

- **原因**：spawn 模式下每个 Worker 需重新 import 所有模块
- **缓解**：减少 `workers` 数量，或开启 `preload_index`

### 内存占用高

- **原因**：每个 Worker 独立持有 AgentLoop（~53MB）
- **缓解**：减少 `workers`，或按需启用 Swarm（只在 LLM 并发场景使用）

### Checkpoint 堆积

- **原因**：长期运行产生大量 checkpoint 文件
- **清理**：调用 `SwarmController.cleanup(max_age_sec=86400)` 或手动删除 `~/.xagent/swarm_checkpoints/*.json`

### pickle 错误

- **原因**：尝试传递不可序列化的对象给 Worker
- **解决**：确保只传递 `dict` / `str` / `int` 等基本类型，不传递 `AgentLoop` 实例

## Web UI 监控

GUI 模式下点击 🐝 按钮展开 Swarm 面板，实时显示：
- Worker 数量与状态
- 待处理 / 运行中 / 已完成 / 失败 任务数
- 最近 10 条 Checkpoint 记录

面板每 5 秒自动刷新。
