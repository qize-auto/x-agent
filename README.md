# X-Agent

> 一个具备完整自我进化闭环的本地开源 AI Agent —— 从失败中学习，动态适应硬件，在资源受限时仍保持精准。

[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-462%20passed-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

## 一句话定位

X-Agent 是唯一具备**完整自我进化闭环**（失败分类 → 经验沉淀 → 根因分析 → Prompt 进化 → 安全上线）的本地开源 Agent，在低端笔记本到高端工作站上都能自动调整运行参数，不因资源紧张而牺牲任务准确度。

## 核心特性

| 特性 | 说明 |
|------|------|
| **自我进化闭环** | FailureClassifier（6 类失败模式）→ ExperienceBank（SQLite 持久化）→ RootCauseAnalyzer → PromptEvolver（ShadowEval 评分 + 安全上线） |
| **动态资源自适应** | 启动时检测 CPU/内存/频率，自动分档（low/mid/high），lint 超时、索引粒度、并行度随硬件动态调整 |
| **硬件指纹追踪** | 换机器/升级硬件时自动重新检测，不因旧配置拖累新环境 |
| **错误台账** | ErrorLedger 统一记录系统级错误，指纹去重，已提醒不再骚扰 |
| **代码验证门禁** | `edit_file` 写入前自动 `compile()` + `ruff/flake8/py_compile` 检查，失败拒绝写入 |
| **双循环架构** | Legacy（ReAct）+ CacheFirst（DeepSeek 前缀缓存优化），按模型自动切换 |
| **Repo Map 自动注入** | 惰性索引代码库，构建轻量符号图注入 LLM 上下文 |
| **意图锚定** | 每 5 轮工具调用提醒原始目标，防止上下文膨胀导致漂移 |
| **全链路超时保护** | LLM 网络 60s、并行工具 30s、Workflow 节点 60s、Agent 循环总时间预算按档位分配 |
| **MCP Client 生产化** | 支持 stdio/http 传输，工具自动发现/注册，安全扫描，心跳保活，CLI 安装/管理 |
| **Aider 风格 Diff 编辑** | `edit_file` 默认 SEARCH/REPLACE，支持多块原子编辑，失败自动回滚，语法/lint 门禁 |
| **成本监控** | Token 使用跟踪，美元成本估算，回合结束自动压缩，/pro 单次升级 |
| **GUI 截图感知** | 5 个感知器（a11y/OCR/multimodal/code_fusion/auto），`/screenshot` CLI 一键截图 |
| **A2A 协议** | Google A2A 最小实现，Agent Card / Task / Artifact，server/client 双模式 |
| **Swarm 分布式** | 单机多进程并行，Checkpoint 持久化，Consensus 共识，CLI `--swarm-workers` 一键启用 |
| **性能基准** | SWE-bench 评估流水线，HumanEval 支持，Markdown/JSON 报告生成 |

## 快速开始

```bash
# 克隆
git clone https://github.com/YOURNAME/x-agent.git
cd x-agent

# 安装依赖
pip install -e ".[dev]"

# 首次运行（自动检测硬件并生成配置）
python -m xagent.cli app
```

首次启动会在 `~/.xagent/config.json` 生成配置，自动填入硬件档位推荐参数。你只需要填入 LLM API key。

## 使用方式

### CLI
```bash
# 对话模式
python -m xagent.cli app

# MCP Server 管理
python -m xagent.cli mcp --list
python -m xagent.cli mcp --install filesystem --command npx --args "-y,@modelcontextprotocol/server-filesystem,/tmp"
python -m xagent.cli mcp --start filesystem

# GUI 截图感知
python -m xagent.cli app
# 在对话中输入: /screenshot

# A2A 协议
python -m xagent.cli a2a --serve --name my-agent
python -m xagent.cli a2a --card http://localhost:7728
python -m xagent.cli a2a --send http://localhost:7728 "Hello!"

# 性能基准
python -m xagent.cli benchmark --dataset swe-bench-lite.jsonl --limit 10 --dry-run

# 自我改进系统状态
python -m xagent.cli self-improve --status

# 回滚 prompt 到上一版本
python -m xagent.cli self-improve --rollback

# Swarm 工作流
python -m xagent.cli workflow --run workflow.yaml --swarm-workers 4
```

### GUI
```bash
python -m xagent.gui
```

PySide6 + QWebEngineView 双面板架构：左侧 iframe（默认 Kimi Web），右侧 Agent 控制台。

## 架构速览

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│   User      │────▶│  AgentMainWindow │────▶│ AgentWorker │
│  (GUI/CLI)  │     │   (PySide6)      │     │  (QThread)  │
└─────────────┘     └─────────────────┘     └──────┬──────┘
                                                   │
              ┌────────────────────────────────────┘
              ▼
    ┌──────────────────┐    ┌──────────────────┐
    │   AgentLoop      │    │  CacheFirstLoop  │
    │   (Legacy ReAct) │    │  (DeepSeek缓存)  │
    └────────┬─────────┘    └────────┬─────────┘
             │                       │
             └───────────┬───────────┘
                         ▼
              ┌─────────────────────┐
              │    ToolRegistry     │
              │  filesystem / shell │
              │  web / git / http   │
              │  browser / database │
              └─────────────────────┘
```

完整架构文档见 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)。

## 测试

```bash
pytest tests/ -q
```

基线：**462 passed, 3 skipped**。所有修改必须通过完整测试套件。

## 状态迁移

跨设备继承进度见 [`archive/README.md`](../archive/README.md)。

## 许可证

MIT
