# Socratic Clarification 深度整合方案 V2

> **核心变更**: L3 从"EIG 数学优化器"重定义为"需求契约引擎"——在任务执行前建立用户确认的需求规格，避免后期返工。
> 
> **约束**: 98 测试通过，向后兼容，不破坏 DeepSeek 缓存优化。

---

## 一、为什么 L3 必须重新做？

### 软件工程的基本定律

Barry Boehm 的软件工程经济学曲线：

```
修复成本倍数
  │
100├─────────────────────────────● 部署后
  │                          ╱
 10├────────────────────●───╱   编码阶段
  │                  ╱
  5├────────────●───╱           设计阶段
  │         ╱
  1├────●                       需求阶段
  └───────────────────────────────────
```

**X-Agent 当前的问题**：
- `TaskPlanner` 接收原始 `goal` 字符串直接分解
- 如果 `goal` 是"帮我设计一个系统"，生成的 TaskPlan 可能完全偏离用户预期
- 执行 5 个子任务后发现方向错了 → `replan()` 从头来过 → 大量 API call 浪费

**用户的反对是对的**：前期 1-2 次澄清 call 的成本，远低于后期 10+ 次返工 call 的成本。

### L3 的正确定义

| 我之前错的 | 修正后 |
|-----------|--------|
| EIG = 每轮对话的连续贝叶斯优化 | **需求契约 = 任务开始前的一次性规格确认** |
| 数学目标：最大化信息增益 | **工程目标：最小化返工概率** |
| 提问策略：实时计算最优问题 | **提问策略：基于任务类型的预设模板 + LLM 生成** |
| 与缓存冲突：+3~5 call/turn | **与缓存兼容：+1~2 call/task，且可用 cheap model** |

---

## 二、八层架构重新评估

| Layer | 名称 | 角色 | 决策 | 理由 |
|-------|------|------|------|------|
| **L1** | Dialogue Interface | Prompt 层澄清协议 | ✅ **Phase 1** | 零代码 |
| **L2** | Cognitive Trigger | 假设暴露 + 认知挑战 | ✅ **Phase 1** | Prompt 工程 |
| **L3** | **Requirement Contract Engine** | **任务前需求契约建立** | ✅ **Phase 2 核心** | **避免返工** |
| **L4** | POMDP Decision Core | — | 🔴 不做 | 过重的数学模型 |
| **L5** | Multi-layer Belief Net | 用户画像渐进积累 | 🟡 Phase 3 | 基于契约历史 |
| **L6** | Counterfactual Engine | 反事实验证真实偏好 | 🟡 Phase 2.5 | 特定场景模式 |
| **L7** | Intent Evolution Tracker | 意图漂移检测 | 🟡 Phase 3 | 需 session persist |
| **L8** | Reflexive Monitor | 自反监控 | 🔴 不做 | 无明确指标 |

---

## 三、L3 需求契约引擎：核心设计

### 3.1 核心概念

**RequirementContract（需求契约）**：一份用户和 Agent 在任务执行前共同确认的结构化规格。

```python
@dataclass
class RequirementContract:
    """需求契约——任务执行前的共同确认规格"""
    
    # 原始目标
    raw_goal: str
    
    # 澄清后的精确目标
    refined_goal: str
    
    # 约束条件（不可协商）
    hard_constraints: list[str] = field(default_factory=list)
    # 例: ["必须用 Python", "不能引入新依赖", "兼容 Windows"]
    
    # 偏好条件（可协商）
    soft_preferences: list[str] = field(default_factory=list)
    # 例: [" prefer 简洁实现", "希望有单元测试"]
    
    # 范围边界（明确不在范围内）
    out_of_scope: list[str] = field(default_factory=list)
    # 例: ["不做前端界面", "不处理并发"]
    
    # 验收标准
    acceptance_criteria: list[str] = field(default_factory=list)
    # 例: ["所有测试通过", "代码覆盖率 > 80%"]
    
    # 契约建立过程中的 Q&A
    clarifications: list[tuple[str, str]] = field(default_factory=list)
    # [("用什么语言？", "Python"), ("需要测试吗？", "需要")]
    
    # 契约版本（支持修订）
    version: int = 1
    
    # 用户确认状态
    confirmed: bool = False
    confirmed_at: float = 0.0
    
    def to_context_string(self) -> str:
        """转换为可注入 LLM 上下文的字符串"""
        lines = [
            "## Requirement Contract",
            f"Goal: {self.refined_goal}",
        ]
        if self.hard_constraints:
            lines.append("Hard Constraints:")
            for c in self.hard_constraints:
                lines.append(f"  - {c}")
        if self.soft_preferences:
            lines.append("Soft Preferences:")
            for p in self.soft_preferences:
                lines.append(f"  - {p}")
        if self.out_of_scope:
            lines.append("Out of Scope:")
            for o in self.out_of_scope:
                lines.append(f"  - {o}")
        if self.acceptance_criteria:
            lines.append("Acceptance Criteria:")
            for a in self.acceptance_criteria:
                lines.append(f"  - {a}")
        return "\n".join(lines)
```

### 3.2 契约建立流程（Clarification Pipeline）

```
用户输入: "帮我设计一个系统"
        │
        ▼
┌─────────────────────────────────────┐
│ Step 1: Ambiguity Detection         │
│ 轻量规则 + 单次 LLM 分类            │
│ 输出: needs_clarification = True    │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Step 2: Requirement Elicitation     │
│ 根据任务类型（编码/架构/调研）      │
│ 选择提问模板，LLM 生成 1-3 个问题   │
│ 输出: ["什么语言？", "规模多大？"]   │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Step 3: User Response Collection    │
│ GUI/CLI 交互收集答案                │
│ 输出: {"语言": "Python", "规模": "小"}│
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Step 4: Contract Generation         │
│ LLM 将 Q&A 结构化为契约             │
│ 输出: RequirementContract 对象      │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Step 5: User Confirmation           │
│ 展示契约摘要，用户确认或继续澄清    │
│ 输出: confirmed = True/False        │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Step 6: Contract Binding            │
│ 契约注入 TaskPlan，成为执行约束     │
│ TaskPlanner.plan(contract=contract) │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ 执行阶段                            │
│ 如果执行偏离契约 → 触发 Contract    │
│ Revision 而非 full replan           │
└─────────────────────────────────────┘
```

### 3.3 与现有架构的无缝集成

**集成点 1：AgentLoop.run_task()**

当前流程：
```python
def run_task(self, goal: str) -> TaskPlan:
    plan = self.plan_task(goal)      # 直接规划
    return self.execute_plan(plan)    # 直接执行
```

新流程（契约启用时）：
```python
def run_task(self, goal: str) -> TaskPlan:
    # 1. 需求澄清阶段（可选，由配置控制）
    if self.config.get("clarification.enabled") and not self._is_simple_goal(goal):
        contract = self._establish_contract(goal)
        if not contract.confirmed:
            # 用户取消了契约 → 返回空 plan
            return TaskPlan(goal=goal, status="cancelled")
    else:
        contract = None
    
    # 2. 任务规划（契约作为约束输入）
    plan = self.plan_task(goal, contract=contract)
    
    # 3. 执行
    return self.execute_plan(plan)
```

**集成点 2：TaskPlanner.plan()**

```python
def plan(self, goal: str, contract: RequirementContract = None, ...) -> TaskPlan:
    # ...
    # 构建用户消息时，如果存在契约，将其作为约束注入
    user_content = f"Goal: {goal}"
    if contract:
        user_content += f"\n\n{contract.to_context_string()}"
    
    messages = [
        {"role": "system", "content": PLANNING_PROMPT.format(...)},
        {"role": "user", "content": user_content},
    ]
    # ...
```

**集成点 3：TaskPlan 数据模型**

```python
@dataclass
class TaskPlan:
    goal: str
    contract: RequirementContract = None  # 新增
    subtasks: list[SubTask] = field(default_factory=list)
    # ...
```

**集成点 4：TaskExecutor 执行时契约检查**

```python
def _run_subtask(self, subtask: SubTask, plan: TaskPlan):
    # ...
    # 如果子任务可能违反契约约束，提前警告
    if plan.contract and self._might_violate_contract(subtask, plan.contract):
        self._notify(plan, f"⚠️ 注意: [{subtask.id}] 可能违反契约约束")
    # ...
```

### 3.4 DeepSeek 缓存兼容性

**澄清阶段**:
- 是独立的 `run()` 调用，不共享主任务的 prefix
- 可以使用 cheap model（deepseek-chat / gpt-4o-mini），成本极低
- 澄清阶段的 Q&A 历史不进入主任务的 AppendOnlyLog

**主任务阶段**:
- 契约内容作为静态字符串注入 enriched user message
- 契约内容在 `ImmutablePrefix` 之外（动态内容），符合现有架构
- 如果契约内容较长，可被 `ContextCompressor` 自动压缩

**缓存收益**:
- 契约建立后，主任务的 prefix（system + tools）完全不变
- 契约内容作为 user message 的一部分，不影响 prefix 缓存命中率

### 3.5 返工成本对比

**场景**: 用户说"帮我设计一个缓存系统"

| 方案 | 前期澄清 | 执行中发现问题 | 返工成本 | 总 API Call |
|------|---------|--------------|---------|------------|
| **无契约（当前）** | 0 | 第 4 个子任务发现"原来要分布式" | replan + 重做 4 步 | ~15 calls |
| **有契约（新方案）** | 2 calls（"单节点还是分布式？""用什么语言？"） | 无（契约已明确） | 0 | ~8 calls |
| **净节省** | +2 | -4 返工 | — | **-7 calls** |

---

## 四、完整模块设计

### 4.1 新增文件

```
xagent/core/requirement_contract.py      # RequirementContract 数据模型
xagent/core/clarification_engine.py      # 澄清引擎（歧义检测 + 提问生成）
xagent/core/contract_builder.py          # 契约构建器（Q&A → 结构化契约）
```

### 4.2 requirement_contract.py

```python
"""
Requirement Contract — 需求契约
===============================
任务执行前与用户共同确认的结构化规格。

设计原则：
- 纯数据类，无外部依赖
- 支持版本化修订
- 可序列化（存入 TaskPlan/MemoryEngine）
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import json
import time
from typing import Callable


@dataclass
class RequirementContract:
    """需求契约"""
    
    raw_goal: str
    refined_goal: str = ""
    hard_constraints: list[str] = field(default_factory=list)
    soft_preferences: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    clarifications: list[dict] = field(default_factory=list)
    version: int = 1
    confirmed: bool = False
    confirmed_at: float = 0.0
    
    def confirm(self):
        """用户确认契约"""
        self.confirmed = True
        self.confirmed_at = time.time()
    
    def revise(self, new_clarifications: list[dict]) -> "RequirementContract":
        """基于新的澄清修订契约"""
        new_contract = RequirementContract(
            raw_goal=self.raw_goal,
            refined_goal=self.refined_goal,
            hard_constraints=list(self.hard_constraints),
            soft_preferences=list(self.soft_preferences),
            out_of_scope=list(self.out_of_scope),
            acceptance_criteria=list(self.acceptance_criteria),
            clarifications=self.clarifications + new_clarifications,
            version=self.version + 1,
        )
        return new_contract
    
    def to_context_string(self) -> str:
        """转换为 LLM 上下文字符串"""
        lines = ["## Requirement Contract"]
        if self.refined_goal:
            lines.append(f"Refined Goal: {self.refined_goal}")
        else:
            lines.append(f"Goal: {self.raw_goal}")
        
        sections = [
            ("Hard Constraints", self.hard_constraints),
            ("Soft Preferences", self.soft_preferences),
            ("Out of Scope", self.out_of_scope),
            ("Acceptance Criteria", self.acceptance_criteria),
        ]
        for title, items in sections:
            if items:
                lines.append(f"\n{title}:")
                for item in items:
                    lines.append(f"  - {item}")
        
        return "\n".join(lines)
    
    def to_markdown(self) -> str:
        """生成人类可读的契约摘要"""
        lines = ["### 📋 Requirement Contract", ""]
        lines.append(f"**Goal**: {self.refined_goal or self.raw_goal}")
        
        if self.hard_constraints:
            lines.append("\n**Hard Constraints** (不可协商):")
            for c in self.hard_constraints:
                lines.append(f"- {c}")
        
        if self.soft_preferences:
            lines.append("\n**Soft Preferences** (可协商):")
            for p in self.soft_preferences:
                lines.append(f"- {p}")
        
        if self.out_of_scope:
            lines.append("\n**Out of Scope**:")
            for o in self.out_of_scope:
                lines.append(f"- {o}")
        
        if self.acceptance_criteria:
            lines.append("\n**Acceptance Criteria**:")
            for a in self.acceptance_criteria:
                lines.append(f"- {a}")
        
        if self.clarifications:
            lines.append("\n**Clarification History**:")
            for qa in self.clarifications:
                lines.append(f"- Q: {qa.get('question', '')}")
                lines.append(f"  A: {qa.get('answer', '')}")
        
        lines.append(f"\n*Version {self.version} | {'✅ Confirmed' if self.confirmed else '⏳ Draft'}*")
        return "\n".join(lines)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "RequirementContract":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def __repr__(self):
        status = "confirmed" if self.confirmed else "draft"
        return f"RequirementContract(v{self.version}, {status}, constraints={len(self.hard_constraints)})"
```

### 4.3 clarification_engine.py

```python
"""
Clarification Engine — 澄清引擎
================================
判断是否需要澄清，以及生成澄清问题。

设计原则：
- 轻量级：最多 1 次 LLM call
- 可扩展：支持不同任务类型的提问模板
- 无状态：每次调用独立，不依赖历史
"""
from __future__ import annotations
import re
from typing import Callable
from .llm_client import LLMClient
from .requirement_contract import RequirementContract


# 任务类型 → 默认提问模板
CLARIFICATION_TEMPLATES = {
    "coding": {
        "questions": [
            "What programming language should be used?",
            "Are there any existing dependencies or frameworks to consider?",
            "What is the expected input/output format?",
        ],
        "focus": "implementation details",
    },
    "architecture": {
        "questions": [
            "What is the expected scale (users, requests per second)?",
            "Are there specific tech stack constraints?",
            "What are the key non-functional requirements (latency, availability)?",
        ],
        "focus": "system constraints",
    },
    "refactoring": {
        "questions": [
            "What is the primary motivation (performance, readability, bug fix)?",
            "Are there parts of the code that must NOT be changed?",
            "What testing coverage is expected after refactoring?",
        ],
        "focus": "change boundaries",
    },
    "debugging": {
        "questions": [
            "What is the expected behavior vs actual behavior?",
            "Have you identified any specific error messages or logs?",
            "What environment does this occur in (OS, versions)?",
        ],
        "focus": "symptom details",
    },
    "default": {
        "questions": [
            "What is the primary goal or desired outcome?",
            "Are there any constraints or limitations I should know about?",
            "What would constitute success for this task?",
        ],
        "focus": "general requirements",
    },
}


class ClarificationEngine:
    """澄清引擎"""
    
    def __init__(self, llm: LLMClient, max_questions: int = 3):
        self.llm = llm
        self.max_questions = max_questions
        self._simple_pattern = re.compile(
            r"^(read|show|cat|ls|dir|grep|find|run|exec|git\s+(status|log|diff|branch))\b",
            re.I,
        )
    
    # ── Public API ──
    
    def needs_clarification(self, goal: str) -> bool:
        """
        快速判断是否需要澄清。
        
        两层判断：
        1. 规则层：简单任务直接跳过
        2. LLM 层：模糊表达分类
        """
        # 层1：简单任务
        if self._is_simple_goal(goal):
            return False
        
        # 层2：歧义信号
        ambiguity_signals = [
            r"帮我.*",
            r"给我.*",
            r"做[一个种].*",
            r"设计[一个种].*",
            r"优化.*",
            r"处理.*",
            r"最好",
            r"最合适",
            r"等等",
            r"之类的",
        ]
        if any(re.search(p, goal) for p in ambiguity_signals):
            return True
        
        # 层3：LLM 分类（可选，默认不开）
        return False
    
    def generate_questions(self, goal: str, task_type: str = "default") -> list[str]:
        """
        生成澄清问题。
        
        策略：
        1. 基于任务类型选择模板
        2. 用 LLM 根据具体 goal 定制问题
        3. 返回最多 max_questions 个问题
        """
        template = CLARIFICATION_TEMPLATES.get(task_type, CLARIFICATION_TEMPLATES["default"])
        
        prompt = f"""Given the user's goal, generate up to {self.max_questions} concise clarifying questions.

Goal: {goal}
Task type: {task_type}
Focus: {template['focus']}

Reference questions for this task type:
{chr(10).join(f"- {q}" for q in template['questions'])}

Rules:
1. Each question should be ONE sentence
2. Questions should reveal hard constraints (not soft preferences)
3. Return ONLY the questions, one per line
4. Do NOT ask more than {self.max_questions} questions
"""
        try:
            resp = self.llm.chat([{"role": "user", "content": prompt}])
            questions = [q.strip("- ").strip() for q in resp.content.strip().split("\n") if q.strip()]
            return questions[:self.max_questions]
        except Exception:
            # 降级：返回模板问题
            return template["questions"][:self.max_questions]
    
    def build_contract(
        self,
        goal: str,
        questions: list[str],
        answers: list[str],
        task_type: str = "default",
    ) -> RequirementContract:
        """
        基于 Q&A 构建结构化契约。
        
        使用 LLM 将非结构化的 Q&A 提取为结构化字段。
        """
        qa_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in zip(questions, answers))
        
        prompt = f"""Extract a structured requirement contract from the following Q&A.

Goal: {goal}
Task type: {task_type}

Q&A:
{qa_text}

Respond with a JSON object:
{{
  "refined_goal": "precise description of what needs to be done",
  "hard_constraints": ["list of non-negotiable constraints"],
  "soft_preferences": ["list of nice-to-have preferences"],
  "out_of_scope": ["list of what is explicitly NOT included"],
  "acceptance_criteria": ["list of how to verify completion"]
}}

If a field has no relevant information, return an empty array.
"""
        try:
            resp = self.llm.chat([{"role": "user", "content": prompt}])
            content = resp.content.strip()
            # 提取 JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            import json
            data = json.loads(content)
            
            return RequirementContract(
                raw_goal=goal,
                refined_goal=data.get("refined_goal", goal),
                hard_constraints=data.get("hard_constraints", []),
                soft_preferences=data.get("soft_preferences", []),
                out_of_scope=data.get("out_of_scope", []),
                acceptance_criteria=data.get("acceptance_criteria", []),
                clarifications=[
                    {"question": q, "answer": a}
                    for q, a in zip(questions, answers)
                ],
            )
        except Exception:
            # 降级：返回基本契约
            return RequirementContract(
                raw_goal=goal,
                refined_goal=goal,
                clarifications=[
                    {"question": q, "answer": a}
                    for q, a in zip(questions, answers)
                ],
            )
    
    # ── Private ──
    
    def _is_simple_goal(self, goal: str) -> bool:
        """判断是否为简单目标（无需澄清）"""
        stripped = goal.strip().lower()
        # 简单命令前缀
        if self._simple_pattern.match(stripped):
            return True
        # 纯问句（问答类，不是执行类）
        if stripped.endswith("?") or stripped.endswith("？"):
            return True
        # 单文件操作
        if re.match(r"^(read|show|cat)\s+\S+\.(py|js|ts|json|md|txt)$", stripped, re.I):
            return True
        return False
```

### 4.4 修改现有文件

**config.py** — 新增配置：

```python
"clarification": {
    "enabled": False,              # 默认关闭
    "mode": "standard",            # standard / minimal / thorough
    "max_questions_per_task": 3,   # 每个任务最多问几个问题
    "auto_skip_simple": True,      # 简单任务自动跳过
    "use_cheap_model": True,       # 澄清阶段用 cheap model
    "cheap_model_id": "deepseek/deepseek-chat",  # cheap model 指定
}
```

**task.py** — TaskPlan 扩展：

```python
@dataclass
class TaskPlan:
    goal: str
    contract: RequirementContract = None  # 新增
    subtasks: list[SubTask] = field(default_factory=list)
    # ...
```

**agent_loop.py** — run_task() 扩展：

```python
def run_task(self, goal: str) -> TaskPlan:
    """
    Plan → Clarify → Execute 任务循环
    
    如果 clarification.enabled=True 且目标非简单：
    1. 先建立需求契约
    2. 用户确认后绑定到 TaskPlan
    3. 再执行
    """
    clarification_enabled = self.config.get("clarification.enabled", False) if hasattr(self, 'config') else False
    
    contract = None
    if clarification_enabled and not self._is_simple_goal(goal):
        self._status("🔍 正在分析需求...")
        contract = self._establish_contract(goal)
        if contract is None:
            # 用户跳过或取消
            plan = TaskPlan(goal=goal, status="cancelled")
            self._status("⏭️ 用户跳过了需求澄清")
            return plan
    
    plan = self.plan_task(goal, contract=contract)
    return self.execute_plan(plan)

def _establish_contract(self, goal: str) -> RequirementContract | None:
    """建立需求契约，返回 None 表示用户取消"""
    from .clarification_engine import ClarificationEngine
    
    engine = ClarificationEngine(self.llm, max_questions=3)
    
    # 1. 检测是否需要澄清
    if not engine.needs_clarification(goal):
        return RequirementContract(raw_goal=goal, refined_goal=goal, confirmed=True)
    
    # 2. 生成问题
    questions = engine.generate_questions(goal)
    if not questions:
        return RequirementContract(raw_goal=goal, refined_goal=goal, confirmed=True)
    
    # 3. 收集回答（通过 confirm_callback 或专门的 ask_user）
    answers = []
    for q in questions:
        answer = self._ask_user(q)
        if answer is None:  # 用户取消
            return None
        answers.append(answer)
    
    # 4. 构建契约
    contract = engine.build_contract(goal, questions, answers)
    
    # 5. 用户确认契约
    self._status("\n" + contract.to_markdown())
    if self.confirm_callback:
        confirmed = self.confirm_callback("confirm_contract", {
            "contract": contract.to_markdown(),
            "message": "请确认以上需求契约是否正确。",
        })
        if confirmed:
            contract.confirm()
    else:
        # 无回调，自动确认（CLI 模式）
        contract.confirmed = True
    
    return contract

def _ask_user(self, question: str) -> str | None:
    """向用户提问并收集回答"""
    # GUI 模式：通过 confirm_callback 或专门的 ask_callback
    if hasattr(self, 'ask_callback') and self.ask_callback:
        return self.ask_callback(question)
    
    # CLI 模式：直接输入
    self._status(f"❓ {question}")
    # 注：CLI 模式下需要阻塞读取输入
    # 这部分在 CLI app 中实现
    return None  # 占位
```

**planner.py** — plan() 接收契约：

```python
def plan(self, goal: str, contract: RequirementContract = None, 
         os_name: str = "", project_root: str = "", cwd: str = "") -> TaskPlan:
    plan = TaskPlan(goal=goal, contract=contract)  # 绑定契约
    # ...
    user_content = f"Goal: {goal}"
    if contract:
        user_content += f"\n\n{contract.to_context_string()}"
    # ...
```

---

## 五、与 DeepSeek 缓存的协同

### 澄清阶段用 Cheap Model

```python
# 在 clarification_engine.py 中
class ClarificationEngine:
    def __init__(self, llm: LLMClient, max_questions: int = 3, 
                 cheap_model_id: str = None):
        self.llm = llm
        self.cheap_model_id = cheap_model_id or llm.model_id
    
    def generate_questions(self, goal: str, ...) -> list[str]:
        # 使用 cheap model 生成问题
        resp = self.llm.chat(
            messages, 
            model_id=self.cheap_model_id  # 可能用 deepseek-chat 而非 r1
        )
```

**成本对比**:
- deepseek-v4-flash: $0.14/M input tokens
- deepseek-chat: $0.14/M input tokens（价格相同，但速度更快）
- gpt-4o-mini: $0.15/M input tokens

澄清阶段总成本通常 < $0.001（几百 tokens）。

### 主任务阶段缓存保护

```python
# CacheFirstLoop.run()
def run(self, user_input: str, contract: RequirementContract = None) -> str:
    # ...
    # enriched user message 包含契约
    enriched_content = self._build_enriched_user_message(user_input, contract)
    # ...

def _build_enriched_user_message(self, user_input: str, contract=None) -> str:
    parts = []
    if contract and contract.confirmed:
        parts.append(contract.to_context_string())  # 契约作为上下文
    # ... 记忆、cwd 等
    parts.append(f"## User Request\n{user_input}")
    return "\n\n".join(parts)
```

契约内容在 enriched user message 中，不影响 ImmutablePrefix 的缓存。

---

## 六、测试策略

### 6.1 单元测试

```python
# tests/test_requirement_contract.py

def test_contract_to_context_string():
    c = RequirementContract(
        raw_goal="设计系统",
        refined_goal="设计一个单节点缓存系统",
        hard_constraints=["必须用 Python"],
        soft_preferences=["希望有单元测试"],
    )
    s = c.to_context_string()
    assert "单节点缓存系统" in s
    assert "必须用 Python" in s
    assert "单元测试" in s

def test_contract_revision():
    c = RequirementContract(raw_goal="设计系统", refined_goal="设计缓存系统")
    c.confirm()
    c2 = c.revise([{"question": "QPS？", "answer": "1000"}])
    assert c2.version == 2
    assert c2.confirmed is False  # 修订后需重新确认

def test_contract_markdown():
    c = RequirementContract(
        raw_goal="设计系统",
        hard_constraints=["Python"],
        confirmed=True,
    )
    md = c.to_markdown()
    assert "✅ Confirmed" in md
    assert "Python" in md

# tests/test_clarification_engine.py

def test_simple_goal_no_clarification():
    engine = ClarificationEngine(llm=mock_llm)
    assert not engine.needs_clarification("ls -la")
    assert not engine.needs_clarification("git status")
    assert not engine.needs_clarification("Read file.py")

def test_ambiguous_goal_needs_clarification():
    engine = ClarificationEngine(llm=mock_llm)
    assert engine.needs_clarification("帮我设计一个系统")
    assert engine.needs_clarification("给我做个工具")
    assert engine.needs_clarification("优化一下代码")

def test_max_questions_respected():
    engine = ClarificationEngine(llm=mock_llm, max_questions=2)
    qs = engine.generate_questions("设计微服务", task_type="architecture")
    assert len(qs) <= 2

def test_contract_building():
    engine = ClarificationEngine(llm=mock_llm)
    contract = engine.build_contract(
        "设计系统",
        ["什么语言？", "规模多大？"],
        ["Python", "小"],
    )
    assert contract.raw_goal == "设计系统"
    assert len(contract.clarifications) == 2
```

### 6.2 集成测试

```python
def test_run_task_with_contract():
    """验证 run_task 在启用澄清时先建立契约"""
    # ...

def test_task_plan_includes_contract():
    """验证 TaskPlan 包含契约"""
    # ...

def test_planner_uses_contract_constraints():
    """验证规划器将契约作为约束输入"""
    # ...
```

### 6.3 回归测试

```bash
python -m pytest tests/ -q
# 必须保持: 98 passed in < 1s
```

---

## 七、实施路线图

### Phase 1: 基础设施（本周）

1. **新增 `requirement_contract.py`** — 纯数据类，零依赖
2. **新增 `clarification_engine.py`** — 澄清引擎，< 300 行
3. **修改 `config.py`** — 新增 clarification 配置块
4. **修改 `task.py`** — TaskPlan 增加 contract 字段
5. **测试** — 新增 8~10 个单元测试

**验收标准**: `pytest tests/` 通过，无回归。

### Phase 2: 集成到主循环（下周）

1. **修改 `agent_loop.py`** — `run_task()` 增加契约建立阶段
2. **修改 `planner.py`** — `plan()` 接收并注入契约
3. **修改 `cache_loop.py`** — `_build_enriched_user_message` 支持契约
4. **CLI 支持** — `xagent chat` 模式下支持交互式澄清
5. **测试** — 新增 3~5 个集成测试

**验收标准**: 
- 简单任务（`ls`, `git status`）不受影响
- 模糊任务（"帮我设计系统"）触发澄清流程
- 用户可 `/skip` 跳过澄清

### Phase 3: GUI 与高级功能（下月）

1. **SettingsDialog** — 新增"需求澄清"配置页
2. **反事实模式** — `clarification.mode = "architect"` 启用反事实提问
3. **契约历史** — 将确认的契约存入 MemoryEngine，供后续任务复用
4. **契约修订** — 执行中发现偏离契约时，提示用户修订而非完全重规划

### Phase 4: 长期演进

1. **用户画像层** — 从契约历史中提取硬约束模式（"用户总是用 Python"）
2. **意图漂移检测** — 连续对话中检测用户意图变化
3. **领域模板扩展** — 为更多任务类型（数据清洗、DevOps、写作）定制提问模板

---

## 八、风险评估与回退

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 澄清流程打断用户心流 | 中 | 高 | 简单任务自动跳过；用户可随时 `/skip` |
| 契约生成失败（LLM 输出格式错误） | 低 | 中 | 降级：返回基本契约（仅保存 Q&A） |
| 增加 `run_task()` 延迟（+1~2 LLM call） | 高 | 低 | 用 cheap model；可配置关闭 |
| TaskPlan 序列化兼容性问题 | 低 | 高 | `contract` 字段有默认值 `None`，不影响旧数据 |
| 破坏现有测试 | 低 | 极高 | 每次变更后跑 `pytest -q`，红则回退 |

**回退策略**:
```python
# 完全关闭澄清
cconfig.set("clarification.enabled", False)

# 代码回退：ClarificationEngine 完全隔离
# 删除 engine 后，所有调用点 fallback 到无契约路径
```

---

## 九、关键设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 契约放在 TaskPlan 还是 AgentLoop？ | TaskPlan | 契约是任务属性，应随任务生命周期管理 |
| 契约内容放入 prefix 还是 user message？ | user message | prefix 必须静态，契约是动态内容 |
| 澄清用 cheap model 还是主 model？ | cheap model | 澄清是"理解"而非"推理"，不需要最强模型 |
| 契约确认阻塞还是非阻塞？ | 阻塞 | 契约是执行的前提，必须先确认 |
| 偏离契约时 replan 还是 revise？ | revise | 保留已完成工作，只调整相关部分 |

---

## 十、总结

**L3 的核心价值**：把"模糊目标 → 直接执行 → 发现问题 → 返工重规划"的低效循环，转化为"模糊目标 → 建立契约 → 用户确认 → 定向执行 → 按契约验收"的高效流水线。

**与 X-Agent 现有架构的关系**：
- 不是替代，是**增强**：TaskPlanner 还是 TaskPlanner，只是多了一层输入约束
- 不是侵入，是**扩展**：新增两个文件（`requirement_contract.py`, `clarification_engine.py`），修改三个入口（`run_task`, `plan`, `_build_enriched_user_message`）
- 不是破坏，是**保护**：默认关闭，向后兼容；开启后反而减少返工

**与 DeepSeek 缓存的关系**：
- 澄清阶段独立执行，不影响主任务的 prefix caching
- 契约内容作为 user message 注入，不污染 ImmutablePrefix
- 用 cheap model 做澄清，主 model 的 KV cache 完全保护

---

> **作者注**: 这份方案的核心修正在于——把 L3 从"数学优化"重新定义为"工程契约"。需求澄清不是对话中的连续贝叶斯更新，而是任务执行前的一次性规格确认。这个修正使得 L3 与 X-Agent 的现有架构完全兼容，且直接解决了一个真实的工程问题（返工成本）。
