"""Workflow 执行引擎"""
from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional

from .models import Workflow, WorkflowContext, WorkflowNode, TaskNode, ConditionNode, EndNode


class WorkflowEngine:
    """
    工作流执行引擎。

    支持：
    - 从 entry 节点开始的图遍历
    - 并行执行（依赖已满足的无依赖节点同时运行）
    - 条件分支（condition 节点根据运行时结果选择路径）
    - 错误处理（重试、跳过）
    """

    def __init__(self, agent_loop=None, executor=None):
        self.agent_loop = agent_loop
        self._lock = threading.Lock()
        self._executor = executor  # 可插拔执行器（SwarmExecutor 或 ThreadPoolExecutor 包装）

    def run(self, workflow: Workflow, context: WorkflowContext = None) -> WorkflowContext:
        """
        执行完整工作流。

        执行逻辑：
        1. 从 entry 节点开始
        2. 维护一个就绪队列（依赖已满足的节点）
        3. 每轮并行执行所有就绪节点
        4. 条件节点决定下一个走向
        5. 重复直到没有可执行节点或遇到 end
        """
        ctx = context or WorkflowContext()
        if not workflow.entry:
            raise ValueError("Workflow entry node not defined")

        ready: list[str] = [workflow.entry]
        pending: set[str] = set()

        while ready or pending:
            # 筛选出依赖已满足的节点
            can_run = []
            for nid in list(ready):
                node = workflow.get_node(nid)
                if node is None:
                    ready.remove(nid)
                    continue
                if all(dep in ctx.executed_nodes for dep in node.depends_on):
                    can_run.append(nid)
                    ready.remove(nid)

            if not can_run and not pending:
                # 无节点可执行且无 pending，结束
                break

            if not can_run:
                # 等待 pending 完成（不应该发生，除非有 bug）
                continue

            # 并行执行 can_run 中的节点
            futures: dict[str, Future] = {}
            if self._executor is not None:
                # 使用外部执行器（如 SwarmExecutor）
                try:
                    swarm_results = self._execute_with_external(can_run, workflow, ctx)
                    for nid, result in swarm_results.items():
                        pending.discard(nid)
                        if result.get("status") == "failed":
                            ctx.node_results[nid] = result
                            ctx.failed_nodes.add(nid)
                        else:
                            ctx.node_results[nid] = result
                            ctx.executed_nodes.add(nid)
                            node = workflow.get_node(nid)
                            if isinstance(node, EndNode):
                                return ctx
                            if isinstance(node, ConditionNode):
                                next_id = result.get("next")
                                if next_id and next_id not in ctx.executed_nodes and next_id not in ready:
                                    ready.append(next_id)
                            else:
                                for succ in workflow.successors(nid):
                                    if succ not in ctx.executed_nodes and succ not in ready and succ not in pending:
                                        ready.append(succ)
                    continue
                except Exception:
                    # 外部执行器失败，降级到 ThreadPoolExecutor
                    pass

            with ThreadPoolExecutor(max_workers=max(1, len(can_run))) as pool:
                for nid in can_run:
                    node = workflow.get_node(nid)
                    futures[nid] = pool.submit(self._execute_node_safe, node, ctx)
                    pending.add(nid)

            # 收集结果，并将后继节点加入就绪队列
            for nid, future in futures.items():
                pending.remove(nid)
                try:
                    result = future.result(timeout=60)
                    ctx.node_results[nid] = result
                    ctx.executed_nodes.add(nid)

                    node = workflow.get_node(nid)
                    if isinstance(node, EndNode):
                        return ctx

                    if isinstance(node, ConditionNode):
                        # 条件节点：按结果选择分支
                        next_id = result.get("next")
                        if next_id and next_id not in ctx.executed_nodes and next_id not in ready:
                            ready.append(next_id)
                    else:
                        # 普通节点：将所有后继节点加入就绪队列
                        for succ in workflow.successors(nid):
                            if succ not in ctx.executed_nodes and succ not in ready and succ not in pending:
                                ready.append(succ)
                except TimeoutError:
                    ctx.node_results[nid] = {"status": "timeout", "error": "节点执行超过 60 秒超时"}
                    ctx.failed_nodes.add(nid)
                except Exception as e:
                    ctx.node_results[nid] = {"status": "error", "error": str(e)}
                    ctx.failed_nodes.add(nid)

        return ctx

    def _topological_sort(self, workflow: Workflow) -> list[list[str]]:
        """
        对工作流节点进行拓扑排序，返回按批次组织的节点 ID 列表。
        同一批次的节点之间没有依赖关系，可以并行执行。
        """
        in_degree = {nid: 0 for nid in workflow.nodes}
        for nid, node in workflow.nodes.items():
            for dep in node.depends_on:
                if dep in in_degree:
                    in_degree[nid] = in_degree.get(nid, 0) + 1

        batches = []
        remaining = set(workflow.nodes.keys())
        while remaining:
            ready = [nid for nid in remaining if in_degree[nid] == 0]
            if not ready:
                raise ValueError("Circular dependency detected in workflow")
            batches.append(ready)
            for nid in ready:
                remaining.remove(nid)
                for succ in workflow.successors(nid):
                    in_degree[succ] -= 1
        return batches

    def _execute_node_safe(self, node: WorkflowNode, ctx: WorkflowContext):
        """安全执行节点，支持重试"""
        last_error = None
        max_retries = getattr(node, "retries", 0) if isinstance(node, TaskNode) else 0
        for attempt in range(max_retries + 1):
            try:
                return self._execute_node(node, ctx)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    continue
        raise last_error

    def _execute_with_external(self, node_ids: list[str], workflow: Workflow, ctx: WorkflowContext) -> dict:
        """使用外部执行器（如 SwarmExecutor）并行执行节点"""
        if self._executor is None:
            raise RuntimeError("No external executor configured")
        nodes = [workflow.get_node(nid) for nid in node_ids]
        return self._executor(nodes, ctx)

    def _execute_node(self, node: WorkflowNode, ctx: WorkflowContext) -> dict:
        """执行单个节点"""
        if isinstance(node, TaskNode):
            return self._execute_task(node, ctx)
        elif isinstance(node, ConditionNode):
            return self._execute_condition(node, ctx)
        elif isinstance(node, EndNode):
            return {"status": "completed", "node_type": "end"}
        else:
            return {"status": "unknown", "node_type": node.node_type}

    def _execute_task(self, node: TaskNode, ctx: WorkflowContext) -> dict:
        """执行任务节点"""
        if self.agent_loop:
            plan = self.agent_loop.run_task(node.goal, mode="interactive")
            return {
                "status": plan.status,
                "node_type": "task",
                "goal": node.goal,
                "plan_id": plan.id,
            }
        return {
            "status": "completed",
            "node_type": "task",
            "goal": node.goal,
        }

    def _execute_condition(self, node: ConditionNode, ctx: WorkflowContext) -> dict:
        """执行条件节点"""
        # 优先从上下文中读取条件变量
        result = ctx.get(node.condition, None)
        if result is None:
            # 简单启发式
            cond_lower = node.condition.lower()
            if cond_lower in ("true", "yes", "1"):
                result = True
            elif cond_lower in ("false", "no", "0"):
                result = False
            else:
                # 默认 true
                result = True

        branch_key = "true" if result else "false"
        branch = node.branches.get(branch_key, {})
        next_node = branch.get("next") if isinstance(branch, dict) else None

        return {
            "status": "completed",
            "node_type": "condition",
            "condition": node.condition,
            "result": result,
            "next": next_node,
        }
