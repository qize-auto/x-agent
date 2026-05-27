"""
TaskPlanner
===========
将用户目标分解为可执行的子任务序列。

设计原则：
- 使用 LLM 做高层分解，但用模板约束输出格式
- 对于常见任务类型（编码、重构、调研），提供领域特定的分解模板
- 子任务粒度：能在 1-3 次工具调用内完成
"""
from __future__ import annotations
import json
from .llm_client import LLMClient
from .task import TaskPlan, SubTask
from .requirement_contract import RequirementContract


PLANNING_PROMPT = """You are a Task Planning Expert. Decompose the user's goal into a sequence of small, actionable subtasks.

## Rules
1. Each subtask should be completable in 1-3 tool calls.
2. Mark dependencies clearly — a subtask can only start after its dependencies are done.
3. Use tool hints to suggest the most appropriate tool for each step.
4. If the goal is ambiguous, add a "clarify" subtask first.
5. For coding tasks, prefer this pattern: read → edit → verify → commit.

## Output Format
Respond with a JSON array ONLY:
```json
[
  {{"id": "1", "description": "Read the current implementation", "tool_hint": "read_file", "dependencies": []}},
  {{"id": "2", "description": "Edit the function to handle edge case", "tool_hint": "edit_file", "dependencies": ["1"]}},
  {{"id": "3", "description": "Run tests to verify", "tool_hint": "run_command", "dependencies": ["2"]}}
]
```

## Context
- OS: {os_name}
- Project root: {project_root}
- Current directory: {cwd}
"""


class TaskPlanner:
    """任务规划器"""

    def __init__(self, llm: LLMClient, code_indexer=None):
        self.llm = llm
        self.code_indexer = code_indexer

    def replan(self, plan: TaskPlan, failed_subtask, error_info: str) -> list:
        """
        为失败的子任务生成替代方案
        
        Returns:
            新的 SubTask 列表（替代 failed_subtask 的方案）
        """
        context = plan.to_markdown()
        prompt = (
            f"The following subtask failed:\n"
            f"ID: {failed_subtask.id}\n"
            f"Description: {failed_subtask.description}\n"
            f"Error: {error_info}\n\n"
            f"Original plan:\n{context}\n\n"
            f"Please provide an alternative approach. Respond with a JSON array of replacement subtasks:"
        )
        try:
            resp = self.llm.chat([{"role": "user", "content": prompt}])
            subtasks = self._parse_plan(resp.content)
            # 标记为替代任务
            for st in subtasks:
                st.dependencies = [d for d in st.dependencies if d != failed_subtask.id]
                # 如果没有依赖了，依赖原失败任务的前置依赖
                if not st.dependencies and failed_subtask.dependencies:
                    st.dependencies = list(failed_subtask.dependencies)
            return subtasks
        except Exception:
            # 降级：拆分为更细的步骤
            return [
                SubTask(
                    id=f"{failed_subtask.id}_alt",
                    description=f"(替代方案) {failed_subtask.description}",
                    tool_hint=failed_subtask.tool_hint,
                    dependencies=failed_subtask.dependencies,
                )
            ]

    def plan(self, goal: str, contract: RequirementContract = None,
             os_name: str = "", project_root: str = "", cwd: str = "",
             vision_context: str = None) -> TaskPlan:
        """
        将用户目标分解为子任务计划
        
        Args:
            goal: 用户原始目标
            os_name, project_root, cwd: 上下文信息
            vision_context: 视觉感知上下文（UI 状态、截图分析等）
        
        Returns:
            TaskPlan 对象
        """
        plan = TaskPlan(goal=goal, contract=contract)
        plan.update_status("planning")

        # 简单目标：如果只有一步就能完成，跳过复杂规划
        if self._is_simple_goal(goal):
            plan.subtasks.append(SubTask(
                id="1",
                description=goal,
                tool_hint=None,
                dependencies=[],
            ))
            plan.update_status("executing")
            return plan

        # 构建用户消息（注入契约约束 + 代码上下文）
        user_content = f"Goal: {goal}"
        if contract is not None:
            contract_ctx = contract.to_context_string()
            if contract_ctx:
                user_content += f"\n\n{contract_ctx}"

        # Phase 7: 注入视觉上下文（UI 状态、截图分析等）
        if vision_context:
            user_content += f"\n\n## UI / Visual Context\n{vision_context}"

        # 代码智能：为 coding 任务附加仓库地图
        if self._is_coding_goal(goal) and project_root and self.code_indexer:
            try:
                from .code_intel.repo_map import RepoMapBuilder
                self.code_indexer.index_all()
                repo_map = RepoMapBuilder(indexer=self.code_indexer, max_total_chars=4000).build()
                if repo_map:
                    user_content += f"\n\n## Project Structure\n```\n{repo_map}\n```"
            except Exception:
                pass

        # 调用 LLM 做任务分解
        messages = [
            {"role": "system", "content": PLANNING_PROMPT.format(
                os_name=os_name,
                project_root=project_root,
                cwd=cwd,
            )},
            {"role": "user", "content": f"{user_content}\n\nPlease decompose into subtasks."},
        ]

        try:
            resp = self.llm.chat(messages)
            subtasks = self._parse_plan(resp.content)
        except Exception as e:
            # LLM 规划失败 → 降级为单任务
            subtasks = [SubTask(id="1", description=goal, tool_hint=None, dependencies=[])]

        plan.subtasks = subtasks
        plan.update_status("executing")
        return plan

    @staticmethod
    def _is_coding_goal(goal: str) -> bool:
        """判断是否为编码相关目标"""
        coding_keywords = [
            "code", "implement", "fix", "refactor", "build", "write",
            "function", "class", "module", "test", "bug", "error",
            "实现", "编写", "修复", "重构", "构建", "函数", "类",
        ]
        goal_lower = goal.lower()
        return any(k in goal_lower for k in coding_keywords)

    def _is_simple_goal(self, goal: str) -> bool:
        """判断是否为简单目标（无需分解）"""
        simple_prefixes = (
            "读", "读取", "read", "查看", "show", "cat ", "ls ", "dir ",
            "搜索", "search", "find ", "grep ",
            "运行", "执行", "run ", "exec ",
            "git ", "git",
        )
        lower = goal.lower().strip()
        # 如果是一句话且包含简单前缀
        if any(lower.startswith(p.lower()) for p in simple_prefixes):
            return True
        # 如果包含问号（通常是问答，不是执行）
        if "?" in goal or "？" in goal:
            return True
        return False

    def _parse_plan(self, content: str) -> list[SubTask]:
        """从 LLM 输出解析子任务列表"""
        # 提取 JSON 代码块
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        else:
            json_str = content.strip()

        data = json.loads(json_str)
        if not isinstance(data, list):
            raise ValueError("Plan must be a JSON array")

        subtasks = []
        for item in data:
            subtasks.append(SubTask(
                id=str(item.get("id", len(subtasks) + 1)),
                description=item.get("description", ""),
                tool_hint=item.get("tool_hint") or None,
                dependencies=[str(d) for d in item.get("dependencies", [])],
            ))
        return subtasks
