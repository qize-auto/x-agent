# Socratic Clarification 深层架构 —— X-Agent 严谨整合评估

> **评估目标**: 判断八层深层架构（Layer 1~8）在 X-Agent 中的落地可行性，设计保守整合路线，避免"越改越错"。
> 
> **约束条件**: 98 测试全部通过（0.75s），向后兼容，Windows 环境，最小依赖，KISS 原则。
> 
> **评估日期**: 2026-05-26

---

## 一、执行摘要

| 结论 | 说明 |
|------|------|
| **Phase 1（立即）** | ✅ 仅改 Prompt + 配置开关，零代码侵入，风险为零 |
| **Phase 2（短期）** | 🟡 新增轻量 `ClarificationEngine` 模块，隔离于主循环外 |
| **Phase 3（中期）** | 🟡 `MemoryEngine` 扩展用户画像，需 2~3 天开发 |
| **Phase 4+（长期）** | 🔴 POMDP / EIG / 反事实引擎 / 自反监控 —— **当前不建议** |

**核心判断**: X-Agent 当前架构是"LLM-centric"的——推理、规划、判断都委托给 LLM。这是正确的工程选择。引入数学上严格但计算昂贵的 POMDP/EIG 层会与现有架构哲学冲突，且收益/成本比不成立。**应先穷尽 Prompt Engineering 的潜力，再考虑代码层增强。**

---

## 二、X-Agent 当前架构速览

```
┌─────────────────────────────────────────────────────────────┐
│  Entry Points: main.py (GUI) | python -m xagent chat (CLI)  │
│                               | python -m xagent server (HTTP)
├─────────────────────────────────────────────────────────────┤
│  AgentLoop                                                  │
│  ├── cache_mode="auto" → CacheFirstLoop (DeepSeek)         │
│  │   ├── ImmutablePrefix (system + tools, frozen)           │
│  │   ├── AppendOnlyLog (messages, append-only)              │
│  │   ├── VolatileScratch (per-turn temp state)              │
│  │   ├── ToolCallRepairPipeline (4-pass repair)             │
│  │   ├── CostController (flash→pro escalation)              │
│  │   └── ThoughtHarvester (R1 reasoning extract)            │
│  └── cache_mode="never" → Legacy Loop                       │
│      └── 标准 messages 列表                                  │
├─────────────────────────────────────────────────────────────┤
│  TaskPlanner ──→ TaskPlan (SubTask DAG) ──→ TaskExecutor   │
├─────────────────────────────────────────────────────────────┤
│  MemoryEngine (ChromaDB + sentence-transformers)            │
│  ToolRegistry (filesystem, shell, web_search, git)          │
│  ModelRouter (cost/quality/balance strategies)              │
├─────────────────────────────────────────────────────────────┤
│  XAgentConfig → ~/.xagent/config.json                       │
│  SettingsDialog (PySide6, 8 categories)                     │
└─────────────────────────────────────────────────────────────┘
```

**关键设计决策（已固化）**:
- System prompt 纯静态，动态内容（cwd/memory）全部下移到 enriched user message
- 单次 `run()` 内模型锁定（保护 KV cache）
- 回合结束清空 log（非 session_persist 模式）
- 工具结果直接 JSON dump 回传 LLM
- 危险命令通过 `confirm_callback` 半自动拦截

**已有的"澄清"基础**:
- `SYSTEM_PROMPT` 第 5 条: "If uncertain, ask the user rather than guessing."
- `TaskPlanner.PLANNING_PROMPT` 第 4 条: "If the goal is ambiguous, add a 'clarify' subtask first."
- `confirm_callback` 机制已支持人机交互确认

---

## 三、八层架构逐层落地评估

### 评估矩阵

| Layer | 名称 | 落地难度 | API 成本 | 对现有架构影响 | 推荐 | 理由 |
|-------|------|---------|---------|---------------|------|------|
| L1 | Dialogue Interface | ⭐ | 0 | 零 | ✅ **Phase 1** | 改 Prompt 即可 |
| L2 | Cognitive Trigger | ⭐ | +1 call/turn | 零 | ✅ **Phase 1** | Prompt 工程 |
| L3 | EIG Optimizer | ⭐⭐⭐⭐⭐ | +3~5 calls/turn | 高 | 🔴 **不建议** | 与缓存优化目标冲突 |
| L4 | POMDP Decision Core | ⭐⭐⭐⭐⭐ | N/A | 极高 | 🔴 **不建议** | 需要定义状态空间/转移概率 |
| L5 | Multi-layer Belief Net | ⭐⭐⭐ | +1 call/turn | 中 | 🟡 **Phase 3** | 可渐进实现 L1+L2 |
| L6 | Counterfactual Engine | ⭐⭐ | 0~1 call | 低 | 🟡 **Phase 2** | Prompt 模式切换即可 |
| L7 | Intent Evolution Tracker | ⭐⭐⭐ | +embedding | 中 | 🟡 **Phase 3** | 需对话状态追踪 |
| L8 | Reflexive Monitor | ⭐⭐⭐⭐ | +1~2 calls | 高 | 🔴 **不建议** | 无明确成功指标，可能过度思考 |

---

### Layer 1: Dialogue Interface ✅ Phase 1

**当前状态**: 已有基础（"If uncertain, ask"），但不够结构化。

**问题**:
- LLM 对"何时不确定"的判断不稳定
- 没有区分"语法歧义"和"语义歧义"
- 没有限制澄清问题的数量（可能问 10 个问题惹恼用户）

**Phase 1 方案**:
在 `PURE_SYSTEM_PROMPT` 中增加结构化指令块（见第五部分）。不新增任何代码，仅改字符串常量。

**风险**: 零。

---

### Layer 2: Cognitive Trigger ✅ Phase 1

**当前状态**: 未实现。

**理论**: 认知失调驱动 + 元认知激活 + 心智理论。

**Phase 1 方案**:
在 system prompt 中增加"假设挑战"指令：

```
Before answering, list 3 implicit assumptions in the user's request.
If any assumption has >50% chance of being wrong, ask a clarifying question.
```

**验证方法**: 用 10 个模糊任务测试，统计澄清率 + 最终任务完成准确率。

**风险**: 极低（仅增加 token 消耗，约 +50~100 tokens/turn）。

---

### Layer 3: EIG Optimizer 🔴 不建议

**理论**: 贝叶斯实验设计，最大化期望信息增益。

**反对理由**:

1. **与 CacheFirstLoop 目标冲突**: CacheFirstLoop 的核心 KPI 是"最小化 API call 次数"（保护缓存、降低成本）。EIG 需要为每个候选问题计算 EIG（至少 3~5 次 LLM call），直接违背这一目标。

2. **无现成实现**: BED-LLM 论文中的方法需要：
   - 假设空间采样（多次 LLM 生成 + 过滤）
   - 联合模型 p(θ, y | x) 的估计
   - 蒙特卡洛 EIG 近似
   每轮对话增加 3~5 秒延迟。

3. **收益不确定**: BED-LLM 在 20-Questions（名人猜测）上从 14%→91%，但这是因为状态空间极小（固定名人列表）。开放域软件开发任务的假设空间是无限的，EIG 近似质量会急剧下降。

**如果非要做的替代方案**:
用一个简单的启发式规则替代 EIG：
```python
def should_ask_clarification(user_input: str) -> bool:
    """轻量级歧义检测"""
    ambiguity_signals = [
        "帮我", "给我", "做一个", "设计一个",  # 动词过于宽泛
        "最好", "最优", "最合适",  # 价值判断无标准
        "等等", "之类的", "差不多",  # 边界模糊
    ]
    return any(s in user_input for s in ambiguity_signals)
```
成本：零 API call，O(1) 时间。

---

### Layer 4: POMDP Decision Core 🔴 不建议

**理论**: 将对话建模为部分可观察马尔可夫决策过程。

**反对理由**:

1. **维度灾难**: POMDP 求解复杂度 O(|S|² × |A| × |O|)。对话的"状态"包括用户意图、情绪、知识水平、上下文——状态空间至少是 10⁴ 量级，精确求解不可行。

2. **与架构哲学冲突**: X-Agent 的设计哲学是"LLM 作为通用推理引擎"，不是"符号状态机 + 手工规则"。POMDP 要求精确定义状态空间、观察函数、转移概率——这会把系统变成 90 年代的语音对话系统。

3. **已有更好的替代**: `ModelRouter` + `CostController` 已经是基于启发式的策略选择器。它们更简单、更可解释、更易调试。

---

### Layer 5: Multi-layer Belief Net 🟡 Phase 3

**理论**: 四层信念（显式 → 隐式 → 上下文 → 元偏好）。

**评估**: 这是一个**好想法**，但不应该一次性实现全部四层。

**渐进方案**:

```
Step 1（Phase 2）: 显式信念层
  └─ 已存在：user_input 就是显式信念

Step 2（Phase 3）: 隐式假设层  
  └─ 新增 MemoryEngine 的 "user_profile" collection
  └─ 每次澄清对话后，提取一个 "assumption: correction" pair
  └─ 例：用户说"用 Python" → 记录 {"assumption": "tech_stack", "value": "python"}

Step 3（Phase 4）: 上下文约束层
  └─ 从项目结构、git history、过往任务推断约束
  └─ 需要更复杂的项目感知能力

Step 4（远期）: 元偏好层
  └─ 用户如何做权衡（性能 vs 可读性 vs 交付速度）
  └─ 需要长期积累（数十次交互后才有统计意义）
```

**实现成本**: Step 2 约 2 天（扩展 MemoryEngine + 提取逻辑）。

---

### Layer 6: Counterfactual Engine 🟡 Phase 2

**理论**: Judea Pearl 因果阶梯第三层，用"如果 X 不存在会怎样"揭示真实偏好。

**评估**: **可以在特定场景下作为 Prompt 模式实现**，无需代码层改动。

**Phase 2 方案**:
新增一个 `SocraticMode`（枚举值），在 `AgentLoop.run()` 入口根据任务类型切换 system prompt：

```python
class SocraticMode(Enum):
    OFF = "off"           # 默认，直接回答
    STANDARD = "standard" # 基础澄清（Layer 1+2）
    ARCHITECT = "architect" # 架构场景：启用反事实提问
    DEBUG = "debug"       # 调试场景：启用假设挑战
```

`ARCHITECT` 模式的额外 prompt:
```
When helping with system design, after gathering requirements,
ask ONE counterfactual question: "If [key_constraint] were removed,
how would your approach change?" This reveals hidden priorities.
```

**成本**: 每个受影响场景 +1 LLM call（反事实提问本身），但只在 Architect 模式下启用。

---

### Layer 7: Intent Evolution Tracker 🟡 Phase 3

**理论**: 用户意图在对话中动态演化，需要追踪意图轨迹。

**评估**: 有价值，但当前 X-Agent 的 `session_persist=False` 默认模式意味着每次 `run()` 是独立的——**没有多轮对话来演化意图**。

**先决条件**: 必须先提升 `session_persist=True` 的使用率，让用户体验到连续对话的价值。

**实现方案**:
```python
# 在 VolatileScratch 中增加意图追踪
class IntentTracker:
    def detect_drift(self, current_input: str, session_history: list) -> float:
        """返回 0~1 的意图漂移分数"""
        # 用 embedding 相似度 + LLM 分类
        ...
```

---

### Layer 8: Reflexive Monitor 🔴 不建议

**理论**: Agent 监控自己的提问行为，防止过度澄清。

**反对理由**:

1. **没有明确的成功指标**: "过度澄清"的定义是什么？3 个问题？5 个问题？这取决于用户耐心、任务复杂度、时间压力——无法统一量化。

2. **可能产生元循环**: Agent 问"我应该问这个问题吗？"→ 然后问"我刚才的自我质疑合理吗？"→ 延迟爆炸。

3. **已有更简单的解决方案**: 直接限制澄清问题数量（max 3 个），或给用户一个"跳过澄清，直接执行"的选项。

---

## 四、风险评估与回退策略

### 风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Prompt 增强导致 LLM 过度澄清，用户体验下降 | 中 | 高 | Feature flag + 用户可随时 `/skip` |
| 新增模块破坏现有测试 | 低 | 极高 | 每次变更前跑 `pytest -q`，红则回退 |
| CacheFirstLoop 前缀因 prompt 增大而失效 | 低 | 中 | Prompt 变化后跑 `test_cache_context.py` |
| 配置项过多导致用户困惑 | 中 | 中 | 默认全部关闭，高级用户手动开启 |
| 引入外部依赖（numpy/scipy for EIG） | 低 | 中 | **坚决不引入**，纯 Python 实现 |

### 回退策略

**Phase 1 回退**:
```bash
# 如果新 prompt 导致问题，直接恢复 git 中的旧 prompt
# 零状态副作用（prompt 是 Stateless 的）
git checkout xagent/core/agent_loop.py xagent/core/cache_loop.py
```

**Phase 2 回退**:
```python
# ClarificationEngine 完全隔离，开关控制
if not config.get("clarification.enabled", False):
    return user_input  # 透传，零影响
```

**Phase 3 回退**:
```python
# MemoryEngine 扩展不影响已有 collection
# 删除 user_profile collection 即可回退
```

---

## 五、Phase 1 落地方案（立即执行）

### 5.1 配置扩展

在 `DEFAULT_CONFIG` 中新增：

```python
"clarification": {
    "enabled": False,           # 默认关闭，向后兼容
    "mode": "standard",         # off / standard / architect / debug
    "max_questions_per_turn": 3, # 防止过度澄清
    "auto_skip_if_simple": True, # 简单任务自动跳过
}
```

在 `SettingsDialog` 的"模型"或"界面"页增加一个开关（Phase 1 先不新增 UI 页，避免过度设计）。

### 5.2 System Prompt 增强

`PURE_SYSTEM_PROMPT` 末尾增加（仅当 `clarification.enabled=True` 时注入）：

```
## Clarification Protocol (only when enabled)
Before taking action on ambiguous requests, follow this protocol:
1. Identify up to 3 implicit assumptions in the user's request.
2. For each assumption, estimate confidence (high/medium/low).
3. If any medium/low confidence assumption is critical to the outcome,
   ask ONE concise clarifying question. Never ask more than {max_questions} questions.
4. If the request contains these simple prefixes, skip clarification:
   read, show, ls, cat, grep, find, run, git status, git log
5. If the user says "/skip" or "/execute", bypass all clarification and proceed.
```

### 5.3 简单任务自动跳过

在 `AgentLoop.run()` 入口增加：

```python
def _is_simple_request(self, user_input: str) -> bool:
    """简单任务无需澄清"""
    simple_patterns = [
        r"^(read|show|cat|ls|dir|grep|find|run|exec|git\s+status|git\s+log)\b",
        r"^[\w\s]+\?$",  # 纯问句
    ]
    return any(re.match(p, user_input.strip(), re.I) for p in simple_patterns)
```

### 5.4 `/skip` 命令支持

在 CLI 和 GUI 中识别 `/skip` 指令，立即跳过当前澄清流程。

---

## 六、Phase 2 落地方案（短期）

### 6.1 新增 `ClarificationEngine`

```
xagent/core/clarification_engine.py
├── class ClarificationEngine
│   ├── should_clarify(user_input: str, context: dict) -> bool
│   ├── generate_questions(user_input: str, context: dict) -> list[str]
│   ├── is_answer_sufficient(questions: list, answers: list) -> bool
│   └── _is_simple_request(user_input: str) -> bool   # 规则引擎
```

**设计原则**:
- 纯 Python，无外部依赖
- 单文件 < 300 行
- 所有判断支持 override（用户配置）

### 6.2 集成点

在 `AgentLoop.run()` 和 `CacheFirstLoop.run()` 的入口增加：

```python
# 在构建 enriched user message 之前
if clarification_enabled and not self._is_simple_request(user_input):
    questions = self.clarification_engine.generate_questions(user_input, context)
    if questions:
        # 通过 confirm_callback 或专门的 ask_user 机制提问
        answers = self._ask_clarifying_questions(questions)
        # 将 Q&A 合并到 enriched user message
        user_input = self._merge_clarifications(user_input, questions, answers)
```

### 6.3 反事实模式

当 `clarification.mode == "architect"` 时，在标准澄清之后追加一个反事实问题：

```python
counterfactual = (
    "Quick counterfactual: If you had to remove one key constraint "
    "from this project, which would you drop first? This reveals priorities."
)
```

---

## 七、Phase 3 落地方案（中期）

### 7.1 MemoryEngine 扩展

新增 `user_profile` collection，存储结构化偏好：

```python
@dataclass
class UserPreference:
    key: str           # "tech_stack", "style_guide", "test_preference"
    value: str         # "python", "google_style", "pytest"
    confidence: float  # 0~1，基于确认次数
    source: str        # "explicit" | "inferred" | "clarified"
    updated_at: float
```

### 7.2 意图漂移检测

在 `VolatileScratch` 中增加：

```python
class IntentSnapshot:
    def __init__(self, user_input: str, embedding: list[float]):
        self.text = user_input
        self.embedding = embedding
        self.timestamp = time.time()

class IntentTracker:
    def __init__(self, memory_engine: MemoryEngine):
        self.history: list[IntentSnapshot] = []
    
    def detect_drift(self, current: str) -> float:
        """返回 0~1 的漂移分数"""
        if len(self.history) < 2:
            return 0.0
        current_emb = self._embed(current)
        recent_avg = np.mean([s.embedding for s in self.history[-3:]], axis=0)
        similarity = cosine_similarity(current_emb, recent_avg)
        return 1.0 - similarity  # 越不相似，漂移越大
```

> **注意**: `numpy` 是 `sentence-transformers` 的依赖，已存在，无需新增。

---

## 八、测试策略

### 8.1 Phase 1 测试

无需新增测试——Phase 1 只改 Prompt，通过手动验证：

```python
# 验证脚本（非 pytest）
ambiguous_inputs = [
    "帮我设计一个系统",
    "给我做个网站",
    "优化一下这段代码",
    "帮我处理这个数据",
]

for inp in ambiguous_inputs:
    # 启用 clarification
    result = agent.run(inp)
    print(f"Input: {inp}")
    print(f"Contains '?': {'?' in result}")  # 是否包含澄清问题
    print()
```

### 8.2 Phase 2 测试

```python
# tests/test_clarification_engine.py
def test_should_not_clarify_simple_request():
    engine = ClarificationEngine()
    assert not engine.should_clarify("ls -la")
    assert not engine.should_clarify("git status")
    assert not engine.should_clarify("Read file.py")

def test_should_clarify_ambiguous_request():
    engine = ClarificationEngine()
    assert engine.should_clarify("帮我设计一个系统")
    assert engine.should_clarify("给我做个工具")

def test_max_questions_respected():
    engine = ClarificationEngine(max_questions=3)
    questions = engine.generate_questions("帮我设计一个微服务架构")
    assert len(questions) <= 3

def test_merge_clarifications():
    engine = ClarificationEngine()
    merged = engine.merge_clarifications(
        "帮我设计系统",
        ["用什么语言？"],
        ["Python"]
    )
    assert "Python" in merged
    assert "帮我设计系统" in merged
```

### 8.3 回归测试

每次变更后必须执行：

```bash
cd ~/kimi-workspace/x-agent
python -m pytest tests/ -q
# 期望: 98 passed in < 1s
```

---

## 九、最终建议

### 立即执行（今天）

1. **合并 Phase 1 Prompt 增强**: 仅修改 `PURE_SYSTEM_PROMPT` 和 `SYSTEM_PROMPT`，增加澄清协议块
2. **新增配置项**: `clarification.enabled` 等 4 个配置，默认 `False`
3. **简单任务跳过**: 在 `AgentLoop`/`CacheFirstLoop` 入口增加 `_is_simple_request` 判断
4. **手动验证**: 用 10 个模糊/清晰任务各测 3 次，记录澄清率和完成率

### 本周执行

5. **新增 `ClarificationEngine`**: 隔离模块，< 300 行
6. **集成到主循环**: 在 `run()` 入口调用，不影响 tool-call 循环
7. **补充测试**: `test_clarification_engine.py`，至少 4 个用例

### 本月执行

8. **用户画像层**: 扩展 `MemoryEngine`
9. **意图漂移检测**: 轻量 embedding 相似度
10. **SettingsDialog 扩展**: 新增"澄清"配置页

### 永远不做（当前阶段）

- ❌ 引入 POMDP 求解器
- ❌ 引入 EIG 蒙特卡洛近似
- ❌ 引入显式贝叶斯信念更新
- ❌ 引入因果推断引擎（Judea Pearl 全套）
- ❌ 引入自反元监控（避免元循环）

---

## 十、参考资料

1. `socratic-ai-prompt-skill` (roy-reshef) — Prompt 层实现参考
2. `STaR-GATE` (Andukuri et al., 2024) — 自我改进提问策略
3. `BED-LLM` (Rainforth et al., 2025) — 贝叶斯实验设计（理论参考，不建议落地）
4. `Uncertainty Quantification for LLM Agents` (2024) — 不确定性分层理论
5. X-Agent 当前架构: `xagent/core/agent_loop.py`, `xagent/core/cache_loop.py`

---

> **作者注**: 这份评估的核心立场是"保守演进"。DeepSeek 缓存优化（4 层架构）之所以成功，是因为它解决了明确的工程问题（prefix caching、tool-call repair、成本控制），且每个模块都有清晰的输入/输出契约和测试覆盖。苏格拉底澄清是一个**更模糊**的能力边界——"更好的提问"难以量化。因此，我们应该用最低成本的方式（Prompt + 轻量规则）先验证价值，再决定是否投入工程资源构建深层架构。
