"""CLI 测试"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestCLIParser:
    """测试 argparse 配置"""

    def test_task_subcommand_exists(self):
        from xagent.cli.app import main
        with pytest.raises(SystemExit) as exc:
            main(["task", "--help"])
        assert exc.value.code == 0

    def test_schedule_subcommand_exists(self):
        from xagent.cli.app import main
        with pytest.raises(SystemExit) as exc:
            main(["schedule", "--help"])
        assert exc.value.code == 0


class TestScheduleCommand:
    """测试 schedule 子命令（不依赖 AgentLoop）"""

    def test_schedule_list_empty(self, tmp_path, monkeypatch):
        from xagent.cli.app import cmd_schedule
        import xagent.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

        args = MagicMock(list=True, add=None, strategy=None, cancel=None, tick=False)
        cmd_schedule(args)

    def test_schedule_add_and_list(self, tmp_path, monkeypatch, capsys):
        from xagent.cli.app import cmd_schedule
        import xagent.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

        # add
        args = MagicMock(list=False, add="run benchmark", strategy="immediate",
                         cancel=None, tick=False)
        cmd_schedule(args)
        captured = capsys.readouterr()
        assert "已添加调度任务" in captured.out

        # list
        args = MagicMock(list=True, add=None, strategy=None, cancel=None, tick=False)
        cmd_schedule(args)
        captured = capsys.readouterr()
        assert "run benchmark" in captured.out

    def test_schedule_cancel(self, tmp_path, monkeypatch, capsys):
        from xagent.cli.app import cmd_schedule
        import xagent.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

        # add
        args = MagicMock(list=False, add="test task", strategy="immediate",
                         cancel=None, tick=False)
        cmd_schedule(args)
        captured = capsys.readouterr()
        task_id = captured.out.split("已添加调度任务:")[1].strip().split()[0]

        # cancel
        args = MagicMock(list=False, add=None, strategy=None, cancel=task_id, tick=False)
        cmd_schedule(args)
        captured = capsys.readouterr()
        assert "已取消" in captured.out

        # list empty
        args = MagicMock(list=True, add=None, strategy=None, cancel=None, tick=False)
        cmd_schedule(args)
        captured = capsys.readouterr()
        assert "调度队列为空" in captured.out


class TestTaskCommand:
    """测试 task 子命令"""

    def test_task_list(self, capsys):
        from xagent.cli.app import cmd_task

        mock_loop = MagicMock()
        mock_loop.list_tasks.return_value = [
            {"task_id": "abc123", "status": "completed", "progress": 100, "goal": "test goal"}
        ]

        with patch("xagent.cli.app._get_agent", return_value=(mock_loop, MagicMock())):
            args = MagicMock(list=True, status=None, resume=None, abort=None,
                           export=None, output=None, background=None)
            cmd_task(args)

        captured = capsys.readouterr()
        assert "abc123" in captured.out
        assert "test goal" in captured.out

    def test_task_abort(self, capsys):
        from xagent.cli.app import cmd_task

        mock_loop = MagicMock()
        mock_loop.delete_task.return_value = True

        with patch("xagent.cli.app._get_agent", return_value=(mock_loop, MagicMock())):
            args = MagicMock(list=False, status=None, resume=None, abort="abc123",
                           export=None, output=None, background=None)
            cmd_task(args)

        captured = capsys.readouterr()
        assert "已删除" in captured.out

    def test_task_export_stdout(self, capsys, tmp_path, monkeypatch):
        from xagent.cli.app import cmd_task
        from xagent.core.task import TaskPlan, SubTask

        plan = TaskPlan(goal="test export")
        plan.subtasks = [SubTask(id="s1", description="step 1", status="completed")]

        mock_store = MagicMock()
        mock_store.load_plan.return_value = plan

        with patch("xagent.core.persistence.task_store.TaskStore", return_value=mock_store):
            with patch("xagent.cli.app._get_agent", return_value=(MagicMock(), MagicMock())):
                args = MagicMock(list=False, status=None, resume=None, abort=None,
                               export="abc123", output=None, background=None)
                cmd_task(args)

        captured = capsys.readouterr()
        assert "test export" in captured.out


class TestWorkflowCommand:
    def test_workflow_subcommand_exists(self):
        from xagent.cli.app import main
        with pytest.raises(SystemExit) as exc:
            main(["workflow", "--help"])
        assert exc.value.code == 0

    def test_workflow_validate(self, tmp_path, capsys):
        from xagent.cli.app import cmd_workflow
        wf_file = tmp_path / "test.yaml"
        wf_file.write_text("name: TestWF\nentry: start\nnodes:\n  start:\n    type: end\n")

        args = MagicMock(list=False, validate=str(wf_file), run=None, dry_run=False)
        cmd_workflow(args)
        captured = capsys.readouterr()
        assert "验证通过" in captured.out
        assert "TestWF" in captured.out

    def test_workflow_list_empty(self, capsys, tmp_path, monkeypatch):
        from xagent.cli.app import cmd_workflow
        import xagent.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)

        args = MagicMock(list=True, validate=None, run=None, dry_run=False)
        cmd_workflow(args)
        captured = capsys.readouterr()
        assert "未找到" in captured.out or "用法" in captured.out

    def test_workflow_dry_run(self, tmp_path, capsys):
        from xagent.cli.app import cmd_workflow
        wf_file = tmp_path / "test.yaml"
        wf_file.write_text("""
name: DryRun
entry: a
nodes:
  a:
    type: task
    goal: do A
  b:
    type: task
    goal: do B
    depends_on: [a]
""")
        args = MagicMock(list=False, validate=None, run=str(wf_file), dry_run=True)
        cmd_workflow(args)
        captured = capsys.readouterr()
        assert "批次" in captured.out
