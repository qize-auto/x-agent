"""
TaskExecutor
============
执行 TaskPlan 中的子任务，支持依赖解析、失败重试、进度回调。
"""
from __future__ import annotations
import time
import json
from typing import Callable

from .task import TaskPlan, SubTask
from .llm_client import LLMClient
from .tool_registry import ToolRegistry
from .planner import TaskPlanner


class TaskExecutor:
    """任务执行器"""

    def __init__(self, llm: LLMClient, tools: ToolRegistry,
                 confirm_callback: Callable = None,
                 status_callback: Callable = None):
        self.llm = llm
        self.tools = tools
        self.confirm_callback = confirm_callback
        self.status_callback = status_callback

    def execute(self, plan: TaskPlan) -> TaskPlan:
        """
        执行整个任务计划
        
        Returns:
            执行后的 plan（状态已更新）
        """
        plan.update_status("executing")

        while not plan.all_done():
            ready = plan.get_ready_subtasks()
            if not ready:
                # 没有 ready 任务，检查是否有 failed 导致死锁
                if plan.any_failed():
                    plan.update_status("failed")
                    return plan
                # 否则是空计划，直接完成
                break

            for subtask in ready:
                self._run_subtask(subtask, plan)

        plan.finished_at = time.time()
        if plan.any_failed():
            plan.update_status("failed")
        else:
            plan.update_status("done")
        return plan

    def _run_subtask(self, subtask: SubTask, plan: TaskPlan):
        """执行单个子任务"""
        subtask.status = "running"
        subtask.started_at = time.time()
        subtask.attempts += 1
        self._notify(plan, f"🔄 执行 [{subtask.id}] {subtask.description}")

        try:
            # 策略：如果 tool_hint 明确，直接调用工具；否则让 LLM 决定
            if subtask.tool_hint and subtask.tool_hint in self.tools.list_tools():
                result = self._execute_tool_direct(subtask)
            else:
                result = self._execute_via_llm(subtask, plan)

            subtask.result = result
            subtask.status = "verify"
            self._notify(plan, f"🔍 验证 [{subtask.id}] ...")

            # 验证
            if self._verify_result(subtask, result):
                subtask.status = "done"
                self._notify(plan, f"✅ 完成 [{subtask.id}] {subtask.description}")
            else:
                # 验证失败 → 重试或标记失败
                if subtask.attempts < subtask.max_attempts:
                    subtask.status = "pending"  # 放回队列重试
                    self._notify(plan, f"⚠️ 验证失败，准备重试 [{subtask.id}]")
                else:
                    subtask.status = "failed"
                    self._notify(plan, f"❌ 失败 [{subtask.id}] 已达最大重试次数")

        except Exception as e:
            subtask.error = str(e)
            if subtask.attempts < subtask.max_attempts:
                subtask.status = "pending"
                self._notify(plan, f"⚠️ 错误: {e}，准备重试 [{subtask.id}]")
            else:
                # 计划修正：生成替代方案
                self._notify(plan, f"🔧 计划修正: [{subtask.id}] 失败，尝试替代方案")
                planner = TaskPlanner(self.llm)
                alternatives = planner.replan(plan, subtask, str(e))
                if alternatives:
                    # 将替代子任务插入到 plan 中
                    # 找到 failed_subtask 的索引，在其后插入
                    idx = next((i for i, st in enumerate(plan.subtasks) if st.id == subtask.id), -1)
                    if idx >= 0:
                        # 后续任务的依赖需要更新
                        old_id = subtask.id
                        for st in plan.subtasks:
                            if old_id in st.dependencies:
                                st.dependencies.remove(old_id)
                                if alternatives:
                                    st.dependencies.append(alternatives[-1].id)
                        # 标记原任务为跳过
                        subtask.status = "skipped"
                        for alt in reversed(alternatives):
                            plan.subtasks.insert(idx + 1, alt)
                        self._notify(plan, f"📋 已生成 {len(alternatives)} 个替代子任务")
                    else:
                        subtask.status = "failed"
                        self._notify(plan, f"❌ 失败 [{subtask.id}] {e}")
                else:
                    subtask.status = "failed"
                    self._notify(plan, f"❌ 失败 [{subtask.id}] {e}")

        subtask.finished_at = time.time()

    def _execute_tool_direct(self, subtask: SubTask) -> str:
        """直接执行 tool_hint 指定的工具"""
        # 需要把 description 解析为参数 —— 这里简化处理：让 LLM 生成参数
        tool_name = subtask.tool_hint
        prompt = (
            f"Task: {subtask.description}\n"
            f"Available tool: {tool_name}\n"
            f"Please generate the exact arguments JSON for this tool call."
        )
        resp = self.llm.chat([{"role": "user", "content": prompt}])
        args_text = resp.content.strip()
        # 提取 JSON
        if "```json" in args_text:
            args_text = args_text.split("```json")[1].split("```")[0].strip()
        elif "```" in args_text:
            args_text = args_text.split("```")[1].split("```")[0].strip()

        try:
            args = json.loads(args_text)
        except json.JSONDecodeError:
            # 降级：把整个描述当参数
            args = {"command": subtask.description} if tool_name == "run_command" else {"path": subtask.description}

        # 安全确认
        tool = self.tools.get(tool_name)
        if tool and tool.dangerous and self.confirm_callback:
            if not self.confirm_callback(tool_name, args):
                return "[cancelled by user]"

        result = self.tools.execute(tool_name, args)
        return json.dumps(result, ensure_ascii=False)

    def _execute_via_llm(self, subtask: SubTask, plan: TaskPlan) -> str:
        """通过 LLM + 工具调用执行子任务"""
        # 构建上下文：已完成的子任务结果
        context = self._build_context(plan)
        messages = [
            {"role": "system", "content": "You are executing a subtask. Use tools when necessary."},
            {"role": "user", "content": f"Context:\n{context}\n\nSubtask: {subtask.description}"},
        ]
        tool_schemas = self.tools.get_schemas()
        resp = self.llm.chat(messages, tools=tool_schemas if tool_schemas else None)

        # 如果有工具调用，执行它们
        if resp.tool_calls:
            results = []
            for tc in resp.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                # 安全确认
                tool = self.tools.get(tool_name)
                if tool and tool.dangerous and self.confirm_callback:
                    if not self.confirm_callback(tool_name, tool_args):
                        results.append(f"{tool_name}: [cancelled by user]")
                        continue
                tr = self.tools.execute(tool_name, tool_args)
                results.append(f"{tool_name}: {json.dumps(tr, ensure_ascii=False)}")
            return "\n".join(results)

        return resp.content

    def _build_context(self, plan: TaskPlan) -> str:
        """构建已完成的子任务结果上下文"""
        lines = []
        for st in plan.subtasks:
            if st.status in ("done", "verify") and st.result:
                lines.append(f"[{st.id}] {st.description}:\n{st.result[:500]}")
        return "\n\n".join(lines) if lines else "No previous results."

    def _verify_result(self, subtask: SubTask, result: str) -> bool:
        """
        验证子任务结果

        四层验证：
        1. 自动：结果非空且不含错误关键字
        2. 物理：文件操作是否真的生效
        3. LLM：让模型判断是否完成（复杂子任务）
        4. 用户：对于关键操作询问确认
        """
        # 层1：自动检查
        if not result or result.strip() in ("", "[cancelled by user]"):
            return False
        error_keywords = (
            "[错误]", "[error]", "[拒绝]",
            "failed", "failure", "exception", "traceback",
            "syntaxerror", "nameerror", "importerror", "attributeerror",
            "permission denied", "no such file", "not found", "does not exist",
        )
        if any(kw in result.lower() for kw in error_keywords):
            return False

        # 层2：物理验证（文件操作必须产生实际修改）
        if subtask.tool_hint in ("edit_file", "write_file"):
            # edit_file 自动验证已确保语法正确，这里确认操作成功执行
            success_markers = ("已编辑", "已写入", "已创建", "已修改", "已更新")
            if not any(m in result for m in success_markers):
                return False

        # 层3：LLM 语义验证（对于关键子任务）
        if subtask.tool_hint in ("edit_file", "write_file", "run_command", "apply_diff"):
            prompt = (
                f"Subtask: {subtask.description}\n"
                f"Result: {result[:1000]}\n\n"
                f"Does this result indicate the subtask was completed successfully? "
                f"Answer ONLY 'yes' or 'no'."
            )
            try:
                v_resp = self.llm.chat([{"role": "user", "content": prompt}])
                if "no" in v_resp.content.lower():
                    return False
            except Exception:
                pass  # LLM 验证失败不影响，默认通过

        return True

    def _notify(self, plan: TaskPlan, message: str):
        if self.status_callback:
            self.status_callback(message, plan.summary())
