# X-Agent DeepSeek 缓存优化方案 (DeepCache Phase)

> 目标：将 Reasonix 的缓存优先架构迁移到 X-Agent，在保持多 Provider 通用能力的同时，为 DeepSeek 路径实现 90%+ 缓存命中率。
>
> 状态：方案阶段，未执行。

---

## 一、核心洞察：为什么 X-Agent 当前缓存命中率为零

### DeepSeek Prefix Caching 的硬性约束

| 约束 | 含义 |
|------|------|
| 字节级前缀匹配 | 从第 0 个 token 开始，完全相同的部分才能命中 |
| 64 tokens 缓存单元 | 不足 64 tokens 不进入缓存 |
| 最佳努力 | 不保证 100% 命中，但结构正确时命中率极高 |
| 计费差 10 倍 | hit: \$0.014/M, miss: \$0.14/M |

### X-Agent 的缓存破坏行为

当前 `AgentLoop.run()` 每次 tool-call 迭代都重新构建 `system_msg`，其中包含：
- 动态记忆检索结果（每轮不同）
- `cwd`（工具执行后可能改变）
- 工具 schema 每轮重新序列化（JSON key 顺序可能变化）

**结果**：单次 `run()` 内如果有 3 次 tool-call，system 内容每轮都在变 → 缓存前缀每轮都不同 → **命中率接近 0%**。

---

## 二、方案总览：三层架构

```
Layer 3: Cost Control（成本控制）
  - Flash-first 默认策略
  - 失败信号自动升级（flash -> pro）
  - Turn-end 上下文压缩
  - /pro 单次武装（用户手动触发，一回合后自动解除）

Layer 2: Tool-Call Repair（修复层）
  - Scavenge：从 R1 reasoning_content 回收遗漏的 tool calls
  - Truncation：JSON 截断自动修复（补全括号、去尾随逗号）
  - Flatten：深层 schema (>10 params / depth>2) 扁平化为 dot-notation
  - Storm：滑动窗口去重相同 (tool, args) 调用

Layer 1: Cache-First Context（核心）
  - ImmutablePrefix：session 开始时 hash 锁定，之后任何修改抛异常
  - AppendOnlyLog：只允许 append()，禁止 insert/update/delete
  - VolatileScratch：轮次级临时状态（R1 thought、记忆检索），轮末清空
  - 动态记忆从 system prompt 剥离，下移到 enriched user message
```

---

## 三、最关键的一个改动

**当前**：System prompt 每轮都在变（记忆检索结果不同）-> 缓存前缀每轮都不同 -> 命中率 ~0%。

**新方案**：System prompt 变为**绝对纯净**的静态角色定义，所有动态内容（记忆、cwd、项目信息）移到 user message 的前缀中：

```python
# Before（破坏缓存）
system_msg = {"role": "system", "content": SYSTEM_PROMPT + "\n## Relevant Memory\n[动态内容]"}

# After（缓存友好）
prefix = ImmutablePrefix(system_content=PURE_STATIC_PROMPT, tool_schemas=frozen_schemas)
user_msg = {
    "role": "user",
    "content": "## Context from Memory\n[动态记忆]\n\n## Current Directory\n{ cwd }\n\n## User Request\n{ input }"
}
```

虽然 user message 的内容每轮都在变，但 **system + 历史对话** 的前缀在多轮中是单调增长的，DeepSeek 可以逐级命中之前轮次的缓存。

---

## 四、六问题深度分析与推荐

### Q1: cwd 放在哪里？

| 维度 | A: user message（推荐） | B: system 锁定 | C: 冗余 |
|------|----------------------|--------------|--------|
| 前缀稳定性 | system 永远不变 | run()内稳定 | run()内稳定 |
| 跨run缓存连续 | 最佳 | system随cwd变 | system随cwd变 |
| 文件操作正确率 | 实时cwd | 可能出错 | 可能困惑 |
| 实现复杂度 | 简单 | 需延迟逻辑 | 复杂 |

**推荐 A**：system prompt 永远不变是缓存命中的根基。cwd 放在 user message 中不影响前缀缓存（user message 是后缀位置的新内容），且模型始终看到最新目录。

---

### Q2: Memory 放在 user message 的哪个位置？

| 维度 | A: 最前面（推荐） | B: 最后面 | C: 单独消息 |
|------|----------------|----------|-----------|
| 模型关注优先级 | 记忆优先 | 请求优先 | 请求优先 |
| 记忆被截断风险 | 中等 | 高（在末尾） | 中等 |
| 缓存帮助 | 固定header | 无 | 多一条消息开销 |
| 结构清晰度 | 合并 | 合并 | 分离 |

**推荐 A**：固定 header `## Context from Memory\n` 可能增加前缀匹配长度；记忆作为上下文前置符合认知逻辑。

---

### Q3: 跨 run() 的缓存连续性？

| 维度 | A: 重置（推荐先这样做） | B: 保留log | C: 预热 |
|------|----------------------|----------|--------|
| 长会话缓存命中 | 只有system | 几乎全部 | 只有system |
| 实现复杂度 | 简单 | 需session管理 | 简单 |
| 内存/上下文管理 | 无负担 | 需压缩和重置 | 无负担 |
| 与当前X-Agent兼容 | 完全兼容 | 需改动 | 可选添加 |

**推荐 先A后B**：当前 `run()` 语义是独立调用，保留 log 需要 session 生命周期管理。Phase 4 再做 session 持久化 + 预热。

---

### Q4: Context Compaction 触发时机？

| 维度 | A: turn-end | B: turn-end + 40%（推荐） | C: 双阈值 |
|------|------------|-------------------------|----------|
| 防止上下文膨胀 | 回合内可能超限 | 预防性 | 最强 |
| 实现复杂度 | 简单 | 中等 | 复杂 |
| 信息丢失风险 | 最低 | 低 | 紧急时可能丢 |

**推荐 B**：40% proactive 预防回合中间累积过长内容。DeepSeek 上下文窗口 1M，40% = 400K，一般任务不会触发，只在工具结果特别长时处理。

---

### Q5: Parallel Tool Dispatch 优先级？

| 维度 | A: 不做 | B: 标记metadata（推荐） | C: 完整实现 |
|------|--------|---------------------|-----------|
| 性能提升 | 无 | 无 | 显著 |
| 实现复杂度 | 无 | 中等 | 高 |
| 并发风险 | 无 | 无 | 有 |
| 缓存稳定性 | 最佳 | 好 | 需验证 |

**推荐 B**：先标记 `parallel_safe` 元数据，建立分组逻辑，实际执行仍串行。为后续并发做准备，但不引入并发风险。

---

### Q6: 非 DeepSeek Provider 的 fallback？

| 维度 | A: 回退legacy | B: 统一结构（推荐） | C: 简化版 |
|------|------------|------------------|----------|
| 代码维护成本 | 两套路径 | 一套路径 | 有开关 |
| Repair收益利用 | 无 | 全部 | 部分 |
| 工程实践价值 | 无 | 高 | 中等 |

**推荐 B**：Repair Pipeline（JSON截断修复、重复调用去重）对所有模型都有价值。AppendOnlyLog + ImmutablePrefix 是好的软件工程实践，不依赖 DeepSeek 特有机制。

---

## 五、六问题决策总结

| 问题 | 推荐 | 核心理由 |
|------|------|---------|
| Q1 cwd | **A (user message)** | system永远纯净，模型实时看到最新cwd |
| Q2 Memory | **A (最前面)** | 固定header增加前缀匹配，记忆前置 |
| Q3 连续性 | **先A，Phase4做B** | 兼容当前run()语义 |
| Q4 Compaction | **B (turn-end + 40%)** | 预防性压缩，复杂度适中 |
| Q5 Parallel | **B (标记+串行)** | 先建基础设施，并发后续验证 |
| Q6 fallback | **B (统一结构)** | 代码统一，Repair通用 |

---

## 六、实施路线图

### Phase 1: 基础缓存结构（最小可行）
- ImmutablePrefix dataclass
- AppendOnlyLog 替换 messages list
- System prompt 去动态化，记忆移到 user message
- LLMClient 解析 prompt_cache_hit_tokens
- 可选 cache_mode 配置

### Phase 2: 修复流水线
- ToolCallRepairPipeline 框架
- Scavenge / TruncationFix / StormDedup / FlattenSchema

### Phase 3: 成本控制
- CostController + preset 系统
- ContextCompressor turn-end compaction
- /pro 单次武装 + 失败信号自动升级

### Phase 4: 高级优化
- Parallel tool dispatch
- Session 持久化
- Cache warmup 预热

---

> 本方案为设计文档，未进入实现阶段。
