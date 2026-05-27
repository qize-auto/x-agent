"""
Clarification Engine — 澄清引擎
================================
判断是否需要澄清，以及生成澄清问题、构建需求契约。

设计原则：
- 轻量级：最多 1~2 次 LLM call
- 可扩展：支持不同任务类型的提问模板
- 无状态：每次调用独立，不依赖历史
- 可降级：LLM 失败时回退到模板问题
"""
from __future__ import annotations
import json
import re
from typing import TYPE_CHECKING

from .requirement_contract import RequirementContract

if TYPE_CHECKING:
    from .llm_client import LLMClient


# 任务类型 → 默认提问模板
CLARIFICATION_TEMPLATES: dict[str, dict] = {
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
    "data_cleaning": {
        "questions": [
            "What is the source format of the data (CSV, JSON, database)?",
            "Are there specific quality issues (missing values, duplicates, outliers)?",
            "What is the expected output format and schema?",
        ],
        "focus": "data quality and transformation",
    },
    "devops": {
        "questions": [
            "What is the target environment (cloud provider, on-premise, container)?",
            "Are there existing CI/CD pipelines or infrastructure-as-code setups?",
            "What are the uptime/SLA requirements?",
        ],
        "focus": "deployment and infrastructure constraints",
    },
    "writing": {
        "questions": [
            "Who is the target audience and what tone should be used?",
            "Are there length constraints or formatting requirements?",
            "Should the output reference specific sources, styles, or guidelines?",
        ],
        "focus": "content and style requirements",
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

# 内置歧义信号（正则模式）
AMBIGUITY_PATTERNS = [
    r"帮我.*",
    r"给我.*",
    r"做[一个种].*",
    r"设计[一个种].*",
    r"优化.*",
    r"处理.*",
    r"实现.*",
    r"写[一个种].*",
    r"最好",
    r"最合适",
    r"最优",
    r"等等",
    r"之类的",
    r"差不多",
]

# 简单任务前缀（无需澄清）
SIMPLE_PREFIXES = [
    r"^(read|show|cat|ls|dir|grep|find|run|exec|git\s+(status|log|diff|branch|add|commit))\b",
    r"^(读|读取|查看|显示|搜索|运行|执行)\s*",
]


class ClarificationEngine:
    """澄清引擎"""

    def __init__(
        self,
        llm: "LLMClient",
        max_questions: int = 3,
        cheap_model_id: str | None = None,
    ):
        self.llm = llm
        self.max_questions = max(1, min(max_questions, 5))  # 限制 1~5
        self.cheap_model_id = cheap_model_id
        self._simple_pattern = re.compile(
            "|".join(f"({p})" for p in SIMPLE_PREFIXES),
            re.IGNORECASE,
        )
        self._ambiguity_patterns = [re.compile(p) for p in AMBIGUITY_PATTERNS]

    # ── Public API ──

    def needs_clarification(self, goal: str) -> bool:
        """
        快速判断是否需要澄清。

        三层判断：
        1. 规则层：简单任务直接跳过
        2. 歧义信号：中文模糊表达匹配
        3. 长度启发式：过短的目标通常不明确
        """
        if not goal or not isinstance(goal, str):
            return False

        stripped = goal.strip()
        if not stripped:
            return False

        # 层1：简单任务
        if self._is_simple_goal(stripped):
            return False

        # 层2：歧义信号匹配
        for pat in self._ambiguity_patterns:
            if pat.search(stripped):
                return True

        # 层3：长度启发式（少于 8 个字符的目标通常不明确）
        if len(stripped) < 8:
            return True

        return False

    def generate_questions(
        self,
        goal: str,
        task_type: str = "default",
        historical_hints: list[str] | None = None,
        mode: str = "standard",
    ) -> list[str]:
        """
        生成澄清问题。

        策略：
        1. 基于任务类型选择模板
        2. 用 LLM 根据具体 goal 定制问题
        3. 返回最多 max_questions 个问题
        """
        template = CLARIFICATION_TEMPLATES.get(
            task_type, CLARIFICATION_TEMPLATES["default"]
        )

        prompt = (
            f"Given the user's goal, generate up to {self.max_questions} "
            f"concise clarifying questions.\n\n"
            f"Goal: {goal}\n"
            f"Task type: {task_type}\n"
            f"Focus: {template['focus']}\n"
        )
        if historical_hints:
            prompt += (
                f"\nHistorical constraints from previous tasks:\n"
                + "\n".join(f"- {h}" for h in historical_hints)
                + "\nIf these constraints still apply, do NOT ask about them again.\n"
            )
        if mode == "architect":
            prompt += (
                "\nMode: ARCHITECT (counterfactual reasoning)\n"
                "Ask at least ONE counterfactual question that challenges "
                "the user's hidden assumptions or reveals unstated constraints.\n"
                "Examples of counterfactual questions:\n"
                "- 'If you had to cut the scope in half, what would you keep?'\n"
                "- 'What constraint, if removed, would make this trivial?'\n"
                "- 'What would you do if the opposite of your goal were required?'\n"
            )
        prompt += (
            f"\nReference questions for this task type:\n"
            + "\n".join(f"- {q}" for q in template["questions"])
            + "\n\nRules:\n"
            "1. Each question should be ONE sentence\n"
            "2. Questions should reveal hard constraints (not soft preferences)\n"
            "3. Return ONLY the questions, one per line\n"
            f"4. Do NOT ask more than {self.max_questions} questions\n"
        )

        try:
            resp = self.llm.chat(
                [{"role": "user", "content": prompt}],
                model_id=self.cheap_model_id,
            )
            questions = [
                q.strip("- ").strip()
                for q in resp.content.strip().split("\n")
                if q.strip()
            ]
            return questions[: self.max_questions]
        except Exception:
            # 降级：返回模板问题
            return template["questions"][: self.max_questions]

    def build_contract(
        self,
        goal: str,
        questions: list[str],
        answers: list[str],
        task_type: str = "default",
        historical_hints: list[str] | None = None,
        mode: str = "standard",
    ) -> RequirementContract:
        """
        基于 Q&A 构建结构化契约。

        使用 LLM 将非结构化的 Q&A 提取为结构化字段。
        失败时降级为包含原始 Q&A 的基本契约。
        """
        qa_text = "\n".join(
            f"Q: {q}\nA: {a}" for q, a in zip(questions, answers)
        )

        prompt = (
            f"Extract a structured requirement contract from the following Q&A.\n\n"
            f"Goal: {goal}\n"
            f"Task type: {task_type}\n"
        )
        if historical_hints:
            prompt += (
                f"\nHistorical constraints from previous tasks (retain if still relevant):\n"
                + "\n".join(f"- {h}" for h in historical_hints)
            )
        prompt += (
            f"\nQ&A:\n{qa_text}\n\n"
            f'Respond with a JSON object:\n'
            f'{{\n'
            f'  "refined_goal": "precise description of what needs to be done",\n'
            f'  "hard_constraints": ["list of non-negotiable constraints"],\n'
            f'  "soft_preferences": ["list of nice-to-have preferences"],\n'
            f'  "out_of_scope": ["list of what is explicitly NOT included"],\n'
            f'  "acceptance_criteria": ["list of how to verify completion"]\n'
            f'}}\n\n'
            f'If a field has no relevant information, return an empty array.\n'
        )

        try:
            resp = self.llm.chat(
                [{"role": "user", "content": prompt}],
                model_id=self.cheap_model_id,
            )
            content = resp.content.strip()

            # 提取 JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

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
            # 降级：返回基本契约（仅保存 Q&A）
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

        # 单文件操作（read/show/cat + 文件名）
        if re.match(
            r"^(read|show|cat)\s+\S+\.(py|js|ts|json|md|txt|yaml|yml|toml)$",
            stripped,
            re.IGNORECASE,
        ):
            return True

        return False
