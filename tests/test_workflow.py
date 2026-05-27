"""Workflow Engine 测试"""
from __future__ import annotations
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xagent.core.workflow import (
    Workflow, TaskNode, ConditionNode, EndNode,
    WorkflowContext, WorkflowParser, WorkflowEngine,
)


class TestWorkflowModels:
    def test_workflow_get_node(self):
        wf = Workflow(
            name="test",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": TaskNode(id="b", goal="do B", depends_on=["a"]),
            },
        )
        assert wf.get_node("a").id == "a"
        assert wf.get_node("c") is None

    def test_predecessors_successors(self):
        wf = Workflow(
            name="test",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": TaskNode(id="b", goal="do B", depends_on=["a"]),
                "c": TaskNode(id="c", goal="do C", depends_on=["a"]),
            },
        )
        assert wf.predecessors("b") == ["a"]
        assert set(wf.successors("a")) == {"b", "c"}

    def test_context_set_get(self):
        ctx = WorkflowContext()
        ctx.set("x", 42)
        assert ctx.get("x") == 42
        assert ctx.get("y", "default") == "default"


class TestWorkflowParser:
    def test_parse_simple_workflow(self):
        yaml_text = """
name: "Simple Test"
entry: start
nodes:
  start:
    type: task
    goal: "Start the process"
  check:
    type: condition
    condition: "all_ok"
    depends_on: [start]
    branches:
      true:
        next: end
      false:
        next: start
  end:
    type: end
    depends_on: [check]
"""
        wf = WorkflowParser.from_string(yaml_text)
        assert wf.name == "Simple Test"
        assert wf.entry == "start"
        assert len(wf.nodes) == 3
        assert isinstance(wf.get_node("start"), TaskNode)
        assert isinstance(wf.get_node("check"), ConditionNode)
        assert isinstance(wf.get_node("end"), EndNode)

    def test_parse_task_with_tools_and_retries(self):
        yaml_text = """
name: "Retry Test"
entry: t1
nodes:
  t1:
    type: task
    goal: "Run tests"
    tools: [shell, filesystem]
    retries: 2
"""
        wf = WorkflowParser.from_string(yaml_text)
        node = wf.get_node("t1")
        assert node.goal == "Run tests"
        assert node.tools == ["shell", "filesystem"]
        assert node.retries == 2

    def test_parse_from_file(self, tmp_path):
        f = tmp_path / "wf.yaml"
        f.write_text("name: FileTest\nentry: a\nnodes:\n  a:\n    type: end\n")
        wf = WorkflowParser.from_file(f)
        assert wf.name == "FileTest"


class TestWorkflowEngine:
    def test_run_linear_workflow(self):
        wf = Workflow(
            name="linear",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": TaskNode(id="b", goal="do B", depends_on=["a"]),
                "c": TaskNode(id="c", goal="do C", depends_on=["b"]),
            },
        )
        engine = WorkflowEngine()
        ctx = engine.run(wf)
        assert "a" in ctx.executed_nodes
        assert "b" in ctx.executed_nodes
        assert "c" in ctx.executed_nodes
        assert len(ctx.failed_nodes) == 0

    def test_run_parallel_workflow(self):
        wf = Workflow(
            name="parallel",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": TaskNode(id="b", goal="do B", depends_on=["a"]),
                "c": TaskNode(id="c", goal="do C", depends_on=["a"]),
                "d": TaskNode(id="d", goal="do D", depends_on=["b", "c"]),
            },
        )
        engine = WorkflowEngine()
        ctx = engine.run(wf)
        assert set(ctx.executed_nodes) == {"a", "b", "c", "d"}

    def test_run_condition_branch_true(self):
        wf = Workflow(
            name="branch",
            entry="start",
            nodes={
                "start": TaskNode(id="start", goal="start"),
                "check": ConditionNode(
                    id="check", condition="pass",
                    depends_on=["start"],
                    branches={"true": {"next": "success"}, "false": {"next": "fail"}},
                ),
                "success": TaskNode(id="success", goal="success", depends_on=["check"]),
                "fail": TaskNode(id="fail", goal="fail", depends_on=["check"]),
            },
        )
        engine = WorkflowEngine()
        ctx = WorkflowContext()
        ctx.set("pass", True)
        ctx = engine.run(wf, ctx)
        assert "success" in ctx.executed_nodes
        assert "fail" not in ctx.executed_nodes
        assert ctx.node_results["check"]["result"] is True

    def test_run_condition_branch_false(self):
        wf = Workflow(
            name="branch",
            entry="start",
            nodes={
                "start": TaskNode(id="start", goal="start"),
                "check": ConditionNode(
                    id="check", condition="pass",
                    depends_on=["start"],
                    branches={"true": {"next": "success"}, "false": {"next": "fail"}},
                ),
                "success": TaskNode(id="success", goal="success", depends_on=["check"]),
                "fail": TaskNode(id="fail", goal="fail", depends_on=["check"]),
            },
        )
        engine = WorkflowEngine()
        ctx = WorkflowContext()
        ctx.set("pass", False)
        ctx = engine.run(wf, ctx)
        assert "fail" in ctx.executed_nodes
        assert "success" not in ctx.executed_nodes

    def test_run_end_node_stops(self):
        wf = Workflow(
            name="stop",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": EndNode(id="b", depends_on=["a"]),
                "c": TaskNode(id="c", goal="do C", depends_on=["b"]),
            },
        )
        engine = WorkflowEngine()
        ctx = engine.run(wf)
        assert "a" in ctx.executed_nodes
        assert "b" in ctx.executed_nodes
        # end 节点后不应继续执行 c
        assert "c" not in ctx.executed_nodes

    def test_run_missing_entry_raises(self):
        wf = Workflow(name="bad", entry="")
        engine = WorkflowEngine()
        with pytest.raises(ValueError):
            engine.run(wf)

    def test_task_retry(self):
        wf = Workflow(
            name="retry",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A", retries=2),
            },
        )
        engine = WorkflowEngine()
        ctx = engine.run(wf)
        assert "a" in ctx.executed_nodes

    def test_topological_sort(self):
        wf = Workflow(
            name="topo",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": TaskNode(id="b", goal="do B", depends_on=["a"]),
                "c": TaskNode(id="c", goal="do C", depends_on=["a"]),
                "d": TaskNode(id="d", goal="do D", depends_on=["b", "c"]),
            },
        )
        engine = WorkflowEngine()
        batches = engine._topological_sort(wf)
        # a 在第一批次，b/c 在第二批次（无互相依赖），d 在第三批次
        assert batches[0] == ["a"]
        assert set(batches[1]) == {"b", "c"}
        assert batches[2] == ["d"]

    def test_topological_sort_with_condition(self):
        wf = Workflow(
            name="topo-cond",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": ConditionNode(id="b", condition="x", depends_on=["a"], branches={}),
                "c": TaskNode(id="c", goal="do C", depends_on=["b"]),
            },
        )
        engine = WorkflowEngine()
        batches = engine._topological_sort(wf)
        assert batches[0] == ["a"]
        assert batches[1] == ["b"]
        assert batches[2] == ["c"]

    def test_agent_loop_run_workflow(self):
        """测试 AgentLoop.run_workflow 集成"""
        from xagent.core.agent_loop import AgentLoop
        from xagent.core.llm_client import LLMClient
        from xagent.core.tool_registry import ToolRegistry
        from xagent.core.memory_engine import MemoryEngine

        wf = Workflow(
            name="integration",
            entry="a",
            nodes={
                "a": TaskNode(id="a", goal="do A"),
                "b": TaskNode(id="b", goal="do B", depends_on=["a"]),
            },
        )

        # Mock 依赖
        mock_llm = MagicMock()
        mock_llm.model_id = "gpt-4o"
        mock_llm.provider = "openai"
        mock_tools = ToolRegistry()
        mock_memory = MagicMock()

        loop = AgentLoop(llm=mock_llm, tools=mock_tools, memory=mock_memory, config={})
        ctx = loop.run_workflow(wf)
        assert "a" in ctx.executed_nodes
        assert "b" in ctx.executed_nodes
