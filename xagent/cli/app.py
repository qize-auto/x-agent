"""
X-Agent CLI
===========
终端交互入口。支持 chat / model / config 子命令。
"""
from __future__ import annotations
import argparse, sys, json
from pathlib import Path


def _get_agent(telemetry_enabled=None):
    """延迟初始化，避免未配置时导入失败"""
    from ..config import XAgentConfig
    from ..core.llm_client import LLMClient
    from ..core.tool_registry import ToolRegistry
    from ..core.memory_engine import MemoryEngine
    from ..core.agent_loop import AgentLoop
    from ..tools import register_all_tools

    config = XAgentConfig()
    client = LLMClient.from_config(config.model)
    tools = ToolRegistry()
    register_all_tools(tools, project_root=str(config.project_root))
    memory = MemoryEngine(config.memory.get("persist_dir", str(Path.home() / ".xagent" / "memory")))

    # Telemetry
    telemetry = None
    tel_cfg = config._data.get("telemetry", {})
    if telemetry_enabled is not None:
        tel_cfg = dict(tel_cfg)
        tel_cfg["enabled"] = telemetry_enabled
    if tel_cfg.get("enabled", False):
        from ..core.telemetry import TelemetryCollector
        telemetry = TelemetryCollector.from_config(tel_cfg)

    def confirm_dangerous(tool_name, args):
        print(f"\n[!] 危险操作: {tool_name}({json.dumps(args, ensure_ascii=False)})")
        resp = input("确认执行? [y/N]: ").strip().lower()
        return resp in ("y", "yes")

    def ask_user(question: str) -> str | None:
        print(f"\n❓ {question}")
        resp = input("你的回答 (直接回车=跳过): ").strip()
        return resp if resp else None

    # 自动加载配置的 MCP servers
    mcp_cfg = config._data.get("mcp", {})
    if mcp_cfg.get("enabled", False):
        from ..core.mcp.client import MCPClient, StdioTransport, HttpSseTransport
        from ..core.mcp.registry_adapter import MCPAdapter
        from ..core.mcp.security import MCPSecurityBundle
        for srv in mcp_cfg.get("servers", []):
            if not srv.get("name"):
                continue
            try:
                transport_type = srv.get("transport", "stdio")
                if transport_type == "stdio":
                    transport = StdioTransport(
                        command=srv["command"],
                        args=srv.get("args", []),
                        env=srv.get("env"),
                        cwd=srv.get("cwd"),
                    )
                elif transport_type == "http":
                    transport = HttpSseTransport(base_url=srv["url"])
                else:
                    continue
                client_mcp = MCPClient(transport, name=srv["name"])
                adapter = MCPAdapter(
                    server_name=srv["name"],
                    client=client_mcp,
                    trusted=srv.get("trusted", False),
                    security=MCPSecurityBundle(),
                )
                adapter.connect()
                discovered = adapter.discover_tools()
                for spec in discovered:
                    spec.func = adapter.make_handler(spec.name)
                    tools.register(
                        name=spec.name,
                        description=spec.description,
                        parameters=spec.parameters,
                        func=spec.func,
                        dangerous=spec.dangerous,
                        parallel_safe=spec.parallel_safe,
                    )
                tools._mcp_adapters[srv["name"]] = adapter
            except Exception as e:
                print(f"[!] MCP server '{srv.get('name')}' 加载失败: {e}")

    loop = AgentLoop(
        llm=client,
        tools=tools,
        memory=memory,
        project_root=str(config.project_root),
        confirm_callback=confirm_dangerous,
        router_config=config._data.get("routing"),
        ask_user_callback=ask_user,
        config=config._data,
        telemetry_collector=telemetry,
    )
    return loop, config


def cmd_chat(args):
    loop, config = _get_agent(telemetry_enabled=getattr(args, "profile", False))
    print("=" * 50)
    print("🤖 X-Agent 终端模式")
    print(f"模型: {config.model.get('model_id', '?')}")
    print("命令: /exit | /model | /task <目标> | /memory | /route | /forget <type> | /cache | /pro | /session reset | /screenshot")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input == "/exit":
            print("再见！")
            break
        if user_input == "/memory":
            stats = loop.memory.stats()
            print("\n记忆统计:")
            for k, v in stats.items():
                print(f"  {k}: {v}")
            continue
        if user_input.startswith("/forget "):
            mtype = user_input[8:].strip()
            loop.memory.forget(memory_type=mtype if mtype != "all" else None)
            print(f"已删除记忆: {mtype}")
            continue
        if user_input == "/route":
            if loop.router:
                s = loop.router.summary()
                print("\n路由统计:")
                for k, v in s.items():
                    print(f"  {k}: {v}")
            else:
                print("\n路由功能未启用")
            continue
        if user_input.startswith("/task "):
            goal = user_input[6:].strip()
            print(f"\n📋 任务模式: {goal}")
            try:
                # 1. 生成计划
                plan = loop.plan_task(goal)
                print("\n" + "=" * 50)
                print(plan.to_markdown())
                print("=" * 50)

                # 2. 显式确认
                while True:
                    action = input("[执行(e) / 删除子任务(d) / 取消(c)]: ").strip().lower()
                    if action in ("c", "cancel", "取消"):
                        print("已取消")
                        break
                    if action in ("e", "exec", "执行", ""):
                        plan = loop.execute_plan(plan)
                        print("\n" + "=" * 50)
                        print(plan.to_markdown())
                        print("=" * 50)
                        print(f"状态: {plan.status} | 进度: {plan.done_count()}/{plan.total_count()}")
                        break
                    if action.startswith("d"):
                        # 删除子任务，如 d 2 3
                        parts = action.split()
                        if len(parts) >= 2:
                            removed = []
                            for idx_str in parts[1:]:
                                try:
                                    idx = int(idx_str) - 1
                                    if 0 <= idx < len(plan.subtasks):
                                        removed.append(plan.subtasks[idx].description)
                                        plan.subtasks[idx].status = "skipped"
                                except ValueError:
                                    pass
                            print(f"已跳过: {', '.join(removed)}")
                        else:
                            print("用法: d <编号1> <编号2> ...")
                        continue
                    print("无效输入")
            except Exception as e:
                print(f"\n❌ 错误: {e}")
            continue
        if user_input == "/cache":
            if loop._cache_loop:
                stats = loop._cache_loop.get_stats()
                print("\n缓存与成本统计:")
                print(f"  回合数: {stats['turns']}")
                print(f"  总成本: ${stats['total_cost_usd']}")
                print(f"  缓存命中: {stats['cache_hit_tokens']} tokens")
                print(f"  缓存未命中: {stats['cache_miss_tokens']} tokens")
                print(f"  命中率: {stats['cache_hit_rate']}%")
                print(f"  前缀指纹: {stats['prefix_fingerprint']}")
                print(f"  预热状态: {'完成' if stats['warmup_done'] else '未预热'}")
                print(f"  Session 持久化: {'开启' if stats['session_persist'] else '关闭'}")
                print(f"  Session 长度: {stats['session_length']} 条消息")
                cc_stats = stats.get("cost_control", {})
                print(f"  Preset: {cc_stats.get('preset', '?')}")
                print(f"  本回合失败信号: {cc_stats.get('failure_count_this_turn', 0)}")
            else:
                print("\n缓存优化未启用（当前使用 legacy 模式）")
            continue
        if user_input == "/pro":
            if loop._cache_loop:
                loop._cache_loop.cost_controller.arm_pro()
                print("\n⇧ /pro 已武装 — 下一回合将使用 pro 模型")
            else:
                print("\n成本控制在 legacy 模式下不可用")
            continue
        if user_input == "/session reset":
            if loop._cache_loop:
                loop._cache_loop.reset_session()
                print("\nSession 已重置")
            else:
                print("\nSession 管理在 legacy 模式下不可用")
            continue
        if user_input == "/model":
            presets = config.list_model_presets()
            for i, p in enumerate(presets, 1):
                mark = " [active]" if p == config.active_model else ""
                print(f"  {i}. {p}{mark}")
            choice = input("选择模型编号: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(presets):
                    config.set_model_preset(presets[idx])
                    print(f"已切换至: {presets[idx]}")
                    # 重建 loop 以使用新模型
                    loop, config = _get_agent()
            except ValueError:
                pass
            continue

        print("\n🧠 思考中…")
        try:
            result = loop.run(user_input)
            print("\n🤖", result)
        except Exception as e:
            print(f"\n❌ 错误: {e}")

    # 会话结束：显示成本摘要
    if loop._telemetry:
        stats = loop._telemetry.get_stats()
        print("\n" + "=" * 50)
        print("💰 本次会话成本摘要")
        print(f"  LLM 调用: {stats['total_llm_calls']} 次")
        print(f"  工具调用: {stats['total_tool_calls']} 次")
        print(f"  总成本: ${stats['total_cost_usd']:.6f}")
        print("=" * 50)
    elif loop._cache_loop:
        stats = loop._cache_loop.get_stats()
        print("\n" + "=" * 50)
        print("💰 本次会话成本摘要")
        print(f"  总成本: ${stats['total_cost_usd']}")
        print("=" * 50)


def cmd_model(args):
    from ..config import XAgentConfig
    config = XAgentConfig()
    if args.set:
        try:
            config.set_model_preset(args.set)
            print(f"已切换模型预设: {args.set}")
        except ValueError as e:
            print(f"错误: {e}")
            sys.exit(1)
    else:
        print("当前预设:", config.active_model)
        print("可用预设:")
        for p in config.list_model_presets():
            mark = " [active]" if p == config.active_model else ""
            print(f"  - {p}{mark}")


def cmd_config(args):
    from ..config import XAgentConfig, CONFIG_PATH
    if args.open:
        print("配置文件路径:", CONFIG_PATH)
        return
    config = XAgentConfig()
    if args.edit:
        # 简单键值设置，如 --edit iframe_url=https://kimi.moonshot.cn
        for kv in args.edit:
            if "=" not in kv:
                print(f"忽略无效键值: {kv}")
                continue
            key, value = kv.split("=", 1)
            if key.startswith("model."):
                sub = key.split(".", 1)[1]
                config.model[sub] = value
            elif key.startswith("ui."):
                sub = key.split(".", 1)[1]
                config.ui[sub] = value
            else:
                config.__dict__.setdefault("_raw", {})[key] = value
        config.save()
        print("配置已更新")
    else:
        print(json.dumps(config.model, indent=2, ensure_ascii=False))


def cmd_route(args):
    from ..config import XAgentConfig
    from ..core.router import ModelRouter
    config = XAgentConfig()
    router_cfg = config._data.get("routing", {})
    if args.strategy:
        router_cfg["default_strategy"] = args.strategy
        config._data["routing"] = router_cfg
        config.save()
        print(f"已切换路由策略: {args.strategy}")
        return
    router = ModelRouter(router_cfg)
    print("路由配置:")
    for k, v in router.summary().items():
        print(f"  {k}: {v}")
    print("\n任务分类示例:")
    samples = [
        "帮我写一个快速排序",
        "分析一下这个架构",
        "搜索最新的AI新闻",
        "hello",
    ]
    for s in samples:
        d = router.decide(s)
        print(f"  '{s}' -> {d.model_id} ({d.reason})")


def cmd_server(args):
    from ..server import AgentServer
    server = AgentServer(host=args.host, port=args.port)
    server.start()


def cmd_memory(args):
    from ..config import XAgentConfig
    from ..core.memory_engine import MemoryEngine
    config = XAgentConfig()
    memory = MemoryEngine(config.memory.get("persist_dir"))

    if args.forget:
        memory.forget(memory_type=args.forget if args.forget != "all" else None)
        print(f"已删除记忆类型: {args.forget}")
        return

    if args.search:
        results = memory.search(args.search, k=args.limit or 10, memory_type=args.type)
        print(f"搜索: {args.search} (返回 {len(results)} 条)")
        for i, r in enumerate(results, 1):
            meta = r["metadata"]
            mtype = meta.get("type", "?")
            score = r.get("score", 0)
            text = r["text"][:120].replace("\n", " ")
            print(f"  {i}. [{mtype}] (score:{score:.3f}) {text}...")
        return

    # 默认显示统计
    stats = memory.stats()
    print("记忆统计:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


def cmd_task(args):
    """任务管理子命令"""
    from pathlib import Path
    loop, config = _get_agent()

    if args.list:
        tasks = loop.list_tasks(status=args.status)
        if not tasks:
            print("暂无持久化任务")
            return
        print(f"{'任务ID':<12} {'状态':<10} {'进度':<8} {'目标'}")
        print("-" * 70)
        for t in tasks:
            prog = f"{t['progress']}%"
            goal = t['goal'][:40] + "..." if len(t['goal']) > 40 else t['goal']
            print(f"{t['task_id']:<12} {t['status']:<10} {prog:<8} {goal}")
        return

    if args.resume:
        task_id = args.resume
        print(f"🔄 恢复任务 {task_id}...")
        plan = loop.resume_task(task_id)
        if plan:
            print(f"✅ 任务完成: {plan.status}")
            print(plan.to_markdown())
        else:
            print("❌ 恢复失败")
        return

    if args.abort:
        task_id = args.abort
        if loop.delete_task(task_id):
            print(f"🗑️ 已删除任务 {task_id}")
        else:
            print(f"❌ 删除失败 {task_id}")
        return

    if args.export:
        task_id = args.export
        from ..core.persistence.task_store import TaskStore
        store = TaskStore()
        plan = store.load_plan(task_id)
        if plan:
            md = plan.to_markdown()
            if args.output:
                Path(args.output).write_text(md, encoding="utf-8")
                print(f"📄 已导出到 {args.output}")
            else:
                print(md)
        else:
            print(f"❌ 任务 {task_id} 不存在")
        return

    if args.background:
        goal = args.background
        print(f"⏳ 创建后台任务: {goal}")
        plan = loop.run_task(goal, mode="background")
        print(f"📋 任务ID: {plan.id}")
        print(f"   状态: {plan.status}")
        print(f"   子任务: {len(plan.subtasks)} 个")
        print("提示: 使用 `xagent task --resume {}` 恢复执行".format(plan.id))
        return

    # 默认行为
    print("用法: xagent task [--list|--resume ID|--abort ID|--export ID|--background GOAL]")


def cmd_schedule(args):
    """后台调度子命令"""
    import time
    from pathlib import Path
    from ..config import CONFIG_DIR

    SCHEDULE_PATH = CONFIG_DIR / "schedule.json"

    def _load_queue():
        if SCHEDULE_PATH.exists():
            return json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
        return []

    def _save_queue(queue):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SCHEDULE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.list:
        queue = _load_queue()
        if not queue:
            print("调度队列为空")
            return
        now = time.time()
        print(f"{'任务ID':<16} {'策略':<10} {'目标'}")
        print("-" * 60)
        for item in queue:
            eta = "立即" if item.get("execute_after", 0) <= now else time.strftime(
                "%m-%d %H:%M", time.localtime(item["execute_after"])
            )
            goal = item["goal"][:35] + "..." if len(item["goal"]) > 35 else item["goal"]
            print(f"{item['task_id']:<16} {item['strategy']:<10} {goal} (ETA: {eta})")
        return

    if args.add:
        queue = _load_queue()
        strategy = args.strategy or "immediate"
        execute_after = 0.0
        if strategy == "night":
            import datetime
            now = datetime.datetime.now()
            night_start = 23
            target = now + datetime.timedelta(days=1) if now.hour >= night_start else now
            target = target.replace(hour=night_start, minute=0, second=0, microsecond=0)
            execute_after = target.timestamp()
        elif strategy == "interval":
            execute_after = time.time() + 1800

        task_id = f"sch_{int(time.time() * 1000)}"
        queue.append({
            "task_id": task_id,
            "goal": args.add,
            "strategy": strategy,
            "execute_after": execute_after,
        })
        _save_queue(queue)
        print(f"✅ 已添加调度任务: {task_id}")
        print(f"   目标: {args.add}")
        print(f"   策略: {strategy}")
        if execute_after:
            print(f"   预计执行: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(execute_after))}")
        return

    if args.cancel:
        queue = _load_queue()
        new_queue = [q for q in queue if q["task_id"] != args.cancel]
        if len(new_queue) < len(queue):
            _save_queue(new_queue)
            print(f"🗑️ 已取消任务 {args.cancel}")
        else:
            print(f"❌ 任务 {args.cancel} 不存在")
        return

    if args.tick:
        queue = _load_queue()
        if not queue:
            print("调度队列为空")
            return
        now = time.time()
        due = [q for q in queue if q.get("execute_after", 0) <= now]
        remaining = [q for q in queue if q.get("execute_after", 0) > now]
        if not due:
            print("没有到期的任务")
            return

        loop, config = _get_agent()
        launched = []
        for item in due:
            print(f"🚀 启动任务: {item['goal']}")
            try:
                plan = loop.run_task(item["goal"], mode="background")
                launched.append(item["task_id"])
                print(f"   任务ID: {plan.id}")
            except Exception as e:
                print(f"   ❌ 失败: {e}")
        _save_queue(remaining)
        print(f"\n✅ 已启动 {len(launched)} 个任务，剩余 {len(remaining)} 个等待中")
        return

    # 默认行为
    print("用法: xagent schedule [--list|--add GOAL --strategy immediate|night|interval|--cancel ID|--tick]")


def cmd_workflow(args):
    """工作流子命令"""
    from pathlib import Path
    from ..core.workflow import WorkflowParser, WorkflowEngine
    from ..config import XAgentConfig

    config = XAgentConfig()
    workflow_dir = Path(config._data.get("workflow", {}).get("default_dir", str(Path.home() / ".xagent" / "workflows")))
    workflow_dir.mkdir(parents=True, exist_ok=True)

    if args.list:
        files = sorted(workflow_dir.glob("*.yaml")) + sorted(workflow_dir.glob("*.yml"))
        if not files:
            print("未找到工作流文件 (.yaml / .yml)")
            return
        print(f"{'工作流文件':<40} {'名称'}")
        print("-" * 60)
        for f in files:
            try:
                wf = WorkflowParser.from_file(f)
                print(f"{f.name:<40} {wf.name}")
            except Exception:
                print(f"{f.name:<40} [解析失败]")
        return

    if args.validate:
        path = Path(args.validate)
        if not path.exists():
            print(f"❌ 文件不存在: {path}")
            return
        try:
            wf = WorkflowParser.from_file(path)
            print(f"✅ 验证通过: {wf.name}")
            print(f"   入口: {wf.entry}")
            print(f"   节点数: {len(wf.nodes)}")
            for nid, node in wf.nodes.items():
                deps = f" (依赖: {', '.join(node.depends_on)})" if node.depends_on else ""
                print(f"   - [{node.node_type}] {nid}{deps}")
        except Exception as e:
            print(f"❌ 验证失败: {e}")
        return

    if args.run:
        path = Path(args.run)
        if not path.exists():
            print(f"❌ 文件不存在: {path}")
            return
        try:
            wf = WorkflowParser.from_file(path)
            print(f"🚀 执行工作流: {wf.name}")
            print(f"   节点数: {len(wf.nodes)} | 入口: {wf.entry}")
            print("=" * 50)

            if args.dry_run:
                # 仅打印执行计划，不实际运行
                engine = WorkflowEngine()
                # 利用拓扑排序展示执行批次
                batches = engine._topological_sort(wf)
                for i, batch in enumerate(batches, 1):
                    print(f"批次 {i}: {', '.join(batch)}")
                return

            loop, cfg = _get_agent(telemetry_enabled=getattr(args, "profile", False))
            swarm_cfg = cfg._data.get("swarm", {})
            executor = None
            swarm_controller = None
            if getattr(args, "swarm_workers", 0) > 0:
                from ..core.swarm import SwarmController, SwarmExecutor
                swarm_controller = SwarmController(
                    num_workers=args.swarm_workers,
                    config=cfg._data,
                    project_root=str(cfg.project_root),
                    checkpoint_dir=Path(swarm_cfg.get("checkpoint", {}).get("dir", str(Path.home() / ".xagent" / "swarm_checkpoints"))),
                    enabled=True,
                )
                executor = SwarmExecutor(swarm_controller)
                print(f"🐝 Swarm 模式: {args.swarm_workers} workers")

            ctx = loop.run_workflow(wf, executor=executor)
            if swarm_controller:
                swarm_controller.shutdown()

            print("\n" + "=" * 50)
            print("✅ 工作流执行完成")
            print(f"   执行节点: {len(ctx.executed_nodes)}")
            print(f"   失败节点: {len(ctx.failed_nodes)}")
            for nid, res in ctx.node_results.items():
                status = res.get("status", "?")
                icon = "✅" if status == "completed" else "❌"
                print(f"   {icon} {nid}: {status}")
        except Exception as e:
            print(f"\n❌ 执行失败: {e}")
        return

    # 默认行为
    print("用法: xagent workflow [--list|--validate FILE|--run FILE|--dry-run]")


def cmd_profile(args):
    """查看 Telemetry 性能分析数据"""
    from pathlib import Path
    import json

    profile_dir = Path(args.dir) if args.dir else Path.home() / ".xagent" / "profiles"
    if not profile_dir.exists():
        print(f"Profile 目录不存在: {profile_dir}")
        return

    files = sorted(profile_dir.glob("*.jsonl"))
    if not files:
        print("暂无 profile 数据")
        return

    if args.stats:
        total_traces = 0
        total_cost = 0.0
        total_tokens = 0
        total_llm_calls = 0
        total_tool_calls = 0
        total_latency = 0.0
        for f in files:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    total_traces += 1
                    total_cost += data.get("total_cost_usd", 0)
                    total_tokens += data.get("total_tokens", 0)
                    total_llm_calls += len(data.get("llm_calls", []))
                    total_tool_calls += len(data.get("tool_calls", []))
                    total_latency += data.get("total_latency_ms", 0)
                except json.JSONDecodeError:
                    continue
        print("=" * 50)
        print("Telemetry 聚合统计")
        print("=" * 50)
        print(f"  Trace 总数:    {total_traces}")
        print(f"  总成本:        ${total_cost:.6f}")
        print(f"  总 Token 数:   {total_tokens}")
        print(f"  LLM 调用:      {total_llm_calls}")
        print(f"  工具调用:      {total_tool_calls}")
        print(f"  总延迟:        {total_latency:.0f} ms")
        if total_traces:
            print(f"  平均延迟:      {total_latency / total_traces:.0f} ms")
            print(f"  平均成本:      ${total_cost / total_traces:.6f}")
        return

    if args.latest:
        latest = files[-1]
        lines = latest.read_text(encoding="utf-8").strip().split("\n")
        if lines:
            try:
                data = json.loads(lines[-1])
                print(f"最新 Trace ({data.get('trace_id', '?')})")
                print(f"  输入: {data.get('user_input', '')[:80]}")
                print(f"  延迟: {data.get('total_latency_ms', 0):.0f} ms")
                print(f"  成本: ${data.get('total_cost_usd', 0):.6f}")
                print(f"  Token: {data.get('total_tokens', 0)}")
                print(f"  LLM 调用: {len(data.get('llm_calls', []))}")
                for lc in data.get("llm_calls", []):
                    print(f"    - {lc.get('model', '?')}: {lc.get('prompt_tokens', 0)} in / {lc.get('completion_tokens', 0)} out ({lc.get('total_latency_ms', 0):.0f}ms)")
                print(f"  工具调用: {len(data.get('tool_calls', []))}")
                for tc in data.get("tool_calls", []):
                    print(f"    - {tc.get('tool_name', '?')}: {tc.get('latency_ms', 0):.0f}ms")
            except json.JSONDecodeError:
                print("解析失败")
        return

    print(f"Profile 文件 ({len(files)} 个):")
    for f in files:
        size_kb = f.stat().st_size / 1024
        print(f"  {f.name} ({size_kb:.1f} KB)")
    print("提示: 使用 --latest 查看最新 trace，--stats 查看聚合统计")


def cmd_self_improve(args):
    """自我改进系统 CLI"""
    import time
    from ..config import XAgentConfig
    from ..core.self_improve import ExperienceBank, PromptEvolver
    from pathlib import Path

    config = XAgentConfig()
    si_cfg = config._data.get("self_improve", {})
    db_path = Path.home() / ".xagent" / "experience_bank.db"
    bank = ExperienceBank(str(db_path))

    if args.threshold is not None:
        threshold = max(1, args.threshold)
        # 存入配置文件中
        config._data["self_improve"] = dict(config._data.get("self_improve", {}))
        config._data["self_improve"]["threshold"] = threshold
        config.save()
        print(f"触发阈值已设置为: {threshold}")
        return

    if args.rollback:
        from ..core.llm_client import LLMClient
        llm = LLMClient.from_config(config.model)
        evolver = PromptEvolver(llm, experience_bank=bank)
        rolled = evolver.rollback()
        if rolled:
            print("✅ Prompt 已回滚到上一版本")
        else:
            print("⚠️ 无可回滚版本")
        return

    if args.history:
        threshold = si_cfg.get("threshold", 3)
        hot = bank.get_frequent(min_frequency=threshold, limit=20)
        if not hot:
            print("暂无高频失败记录（当前低于阈值）")
            return
        print("=" * 50)
        print(f"高频失败历史 (阈值 >= {threshold})")
        print("=" * 50)
        for rec in hot:
            ts = rec.last_seen
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "N/A"
            print(f"  [{rec.failure_type}] 频次: {rec.frequency}, 最近: {ts_str}")
            if rec.root_cause:
                print(f"    根因: {rec.root_cause}")
        return

    # --status (默认)
    stats = bank.stats()
    threshold = si_cfg.get("threshold", 3)
    hot = bank.get_frequent(min_frequency=threshold, limit=20)
    print("=" * 50)
    print("自我改进系统状态")
    print("=" * 50)
    print(f"  总失败记录:   {stats['total_records']}")
    print(f"  失败类型数:   {len(stats['type_distribution'])}")
    print(f"  高频热点数:   {len(hot)} (阈值={threshold})")
    if stats['type_distribution']:
        print("  类型分布:")
        for t, info in stats['type_distribution'].items():
            print(f"    - {t}: 记录={info['records']}, 总频次={info['total_freq']}")
    print(f"  自动应用:     {'开启' if si_cfg.get('auto_apply') else '关闭'}")
    print(f"  数据库:       {db_path}")


def cmd_mcp(args):
    """MCP Server 管理"""
    from ..config import XAgentConfig
    from ..core.mcp.client import MCPClient, StdioTransport, HttpSseTransport
    from ..core.mcp.registry_adapter import MCPAdapter
    from ..core.mcp.security import MCPSecurityBundle
    import json

    config = XAgentConfig()
    mcp_cfg = config._data.get("mcp", {})

    if args.list:
        servers = mcp_cfg.get("servers", [])
        if not servers:
            print("未配置 MCP servers。请在 config.json 的 mcp.servers 中添加。")
            return
        print(f"{'名称':<16} {'传输':<8} {'命令/URL':<30} {'可信'}")
        print("-" * 70)
        for s in servers:
            transport = s.get("transport", "stdio")
            endpoint = s.get("command", "") or s.get("url", "")
            trusted = "是" if s.get("trusted") else "否"
            print(f"{s.get('name', '?'):<16} {transport:<8} {endpoint:<30} {trusted}")
        return

    if args.connect:
        name = args.connect
        srv = None
        for s in mcp_cfg.get("servers", []):
            if s.get("name") == name:
                srv = s
                break
        if not srv:
            print(f"未找到 MCP server: {name}")
            return
        try:
            transport_type = srv.get("transport", "stdio")
            if transport_type == "stdio":
                transport = StdioTransport(
                    command=srv["command"],
                    args=srv.get("args", []),
                )
            elif transport_type == "http":
                transport = HttpSseTransport(base_url=srv["url"])
            else:
                print(f"不支持的传输类型: {transport_type}")
                return

            client = MCPClient(transport, name=name)
            adapter = MCPAdapter(name, client, security=MCPSecurityBundle())
            info = adapter.connect()
            tools = adapter.discover_tools()
            print(f"✅ 连接成功: {name}")
            print(f"   Server info: {info.get('serverInfo', {})}")
            print(f"   工具数量: {len(tools)}")
            for t in tools:
                print(f"   - {t.name}: {t.description[:60]}...")
            adapter.disconnect()
        except Exception as e:
            print(f"❌ 连接失败: {e}")
        return

    if args.call:
        tool_ref = args.call[0]
        args_json = args.call[1]
        if "." not in tool_ref:
            print("工具引用格式错误，应为: server_name.tool_name")
            return
        srv_name, raw_tool_name = tool_ref.split(".", 1)
        try:
            arguments = json.loads(args_json)
        except json.JSONDecodeError:
            print("参数解析失败，应为 JSON 字符串")
            return

        # 复用 _get_agent 中的逻辑（简化版）
        srv = None
        for s in mcp_cfg.get("servers", []):
            if s.get("name") == srv_name:
                srv = s
                break
        if not srv:
            print(f"未找到 MCP server: {srv_name}")
            return
        try:
            transport_type = srv.get("transport", "stdio")
            if transport_type == "stdio":
                transport = StdioTransport(command=srv["command"], args=srv.get("args", []))
            elif transport_type == "http":
                transport = HttpSseTransport(base_url=srv["url"])
            else:
                print(f"不支持的传输类型: {transport_type}")
                return
            client = MCPClient(transport, name=srv_name)
            adapter = MCPAdapter(srv_name, client, security=MCPSecurityBundle())
            adapter.connect()
            result = client.call_tool(raw_tool_name, arguments)
            print(json.dumps(result.content, ensure_ascii=False, indent=2))
            adapter.disconnect()
        except Exception as e:
            print(f"❌ 调用失败: {e}")
        return

    if args.install:
        from ..core.mcp.manager import MCPServerManager
        name = args.install[0]
        if not args.command:
            print("--install 需要指定 NAME，例如: --install filesystem --command npx --args '-y,@modelcontextprotocol/server-filesystem,/tmp'")
            return
        if not args.command:
            print("--install 需要 --command 参数")
            return
        install_args = args.args.split(",") if args.args else []
        manager = MCPServerManager()
        ok = manager.install(
            name=name,
            transport=args.transport,
            command=args.command,
            args=install_args,
            trusted=args.trusted,
        )
        if ok:
            print(f"✅ 已安装 MCP server: {name}")
            # 尝试自动启动
            started = manager.start(name)
            if started:
                print(f"🚀 已自动启动: {name}")
        else:
            print(f"❌ 安装失败: {name}")
        return

    if args.uninstall:
        from ..core.mcp.manager import MCPServerManager
        name = args.uninstall
        manager = MCPServerManager()
        ok = manager.uninstall(name)
        if ok:
            print(f"✅ 已卸载 MCP server: {name}")
        else:
            print(f"❌ 卸载失败: {name}")
        return

    if args.start:
        from ..core.mcp.manager import MCPServerManager
        name = args.start
        manager = MCPServerManager()
        ok = manager.start(name)
        if ok:
            print(f"🚀 已启动 MCP server: {name}")
        else:
            print(f"❌ 启动失败: {name}")
        return

    if args.stop:
        from ..core.mcp.manager import MCPServerManager
        name = args.stop
        manager = MCPServerManager()
        ok = manager.stop(name)
        if ok:
            print(f"🛑 已停止 MCP server: {name}")
        else:
            print(f"❌ 停止失败: {name}")
        return

    if args.status:
        from ..core.mcp.manager import MCPServerManager
        name = args.status
        manager = MCPServerManager()
        servers = manager.list_servers()
        srv = next((s for s in servers if s["name"] == name), None)
        if not srv:
            print(f"未找到 MCP server: {name}")
            return
        status_map = {0: "未启动", 1: "运行中", 2: "已断开", 3: "错误"}
        print(f"📊 MCP Server: {name}")
        print(f"   传输: {srv.get('transport', 'stdio')}")
        print(f"   命令: {srv.get('command', '')} {' '.join(srv.get('args', []))}")
        print(f"   状态: {status_map.get(srv.get('status', 0), '未知')}")
        print(f"   可信: {'是' if srv.get('trusted') else '否'}")
        print(f"   描述: {srv.get('description', '')}")
        return

    print("用法: xagent mcp [--list|--connect NAME|--call SERVER.TOOL ARGS_JSON|--install NAME --command CMD|--uninstall NAME|--start NAME|--stop NAME|--status NAME]")



def cmd_benchmark(args):
    """运行 SWE-bench 基准测试"""
    from ..eval.swe_bench import SWEBenchDataset
    from ..eval.runner import EvalRunner
    from ..eval.report import ReportGenerator
    from ..config import XAgentConfig
    from ..core.llm_client import LLMClient
    from ..core.agent_loop import AgentLoop
    from ..core.tool_registry import ToolRegistry
    from ..core.memory_engine import MemoryEngine
    from ..tools import register_all_tools

    if args.dry_run:
        dataset = SWEBenchDataset.from_jsonl(args.dataset)
        instances = dataset.instances[:args.limit] if args.limit else dataset.instances
        print(f"干运行模式 — 将评估 {len(instances)} 个实例:")
        for inst in instances:
            print(f"  - {inst.instance_id} ({inst.repo})")
        return

    # 初始化 Agent
    config = XAgentConfig()
    client = LLMClient.from_config(config.model)
    tools = ToolRegistry()
    register_all_tools(tools, project_root=str(config.project_root))
    memory = MemoryEngine(config.memory.get("persist_dir", str(Path.home() / ".xagent" / "memory")))
    loop = AgentLoop(llm=client, tools=tools, memory=memory, project_root=str(config.project_root))

    # 加载数据集
    dataset = SWEBenchDataset.from_jsonl(args.dataset)
    instances = dataset.instances[:args.limit] if args.limit else dataset.instances
    print(f"开始评估 {len(instances)} 个实例...")

    # 运行评估
    runner = EvalRunner(agent_loop=loop, max_workers=args.workers or 1)
    results = runner.run(dataset, instance_filter=lambda i: i.instance_id in {inst.instance_id for inst in instances},
                         progress_callback=lambda cur, total, inst_id: print(f"[{cur}/{total}] {inst_id}"))

    # 生成报告
    gen = ReportGenerator(results)
    gen.print_summary()

    if args.output:
        if args.output.endswith('.json'):
            gen.to_json(args.output)
        else:
            Path(args.output).write_text(gen.to_markdown(), encoding='utf-8')
        print(f"\n报告已保存: {args.output}")



def cmd_a2a(args):
    """A2A Agent-to-Agent 协议"""
    from ..core.a2a import A2AServer, A2AClient, AgentCard, Task, TextPart, Message, TaskStatus

    if args.serve:
        card = AgentCard(
            name=args.name or "x-agent",
            description=args.description or "X-Agent A2A endpoint",
            url=f"http://{args.host}:{args.port}",
            skills=[],
        )

        def handler(task: Task) -> Task:
            # 默认处理：将消息内容作为任务结果返回
            text = ""
            if task.message:
                for p in task.message.parts:
                    if hasattr(p, "text"):
                        text += p.text + " "
            task.status = TaskStatus.COMPLETED
            task.artifacts.append({"name": "result", "parts": [TextPart(text=text or "OK")]})
            return task

        server = A2AServer(card, host=args.host, port=args.port, task_handler=handler)
        server.start()
        print(f"🌐 A2A Server 启动: {server.url}")
        print(f"   Agent Card: {server.url}/agent-card")
        print("按 Ctrl+C 停止...")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("停止 A2A Server")
            server.stop()
        return

    if args.card:
        client = A2AClient(args.card)
        card = client.get_agent_card()
        if card:
            print(f"Agent: {card.name}")
            print(f"Description: {card.description}")
            print(f"URL: {card.url}")
            print(f"Capabilities: {card.capabilities}")
        else:
            print("❌ 无法获取 Agent Card")
        client.close()
        return

    if args.send:
        if len(args.send) < 2:
            print("用法: --send URL \"message\"")
            return
        url, msg_text = args.send[0], args.send[1]
        client = A2AClient(url)
        task = client.send_task(text=msg_text)
        if task:
            print(f"Task ID: {task.id}")
            print(f"Status: {task.status.value}")
            for a in task.artifacts:
                for p in a.parts:
                    if hasattr(p, "text"):
                        print(f"Result: {p.text}")
        else:
            print("❌ 发送失败")
        client.close()
        return

    print("用法: xagent a2a [--serve | --card URL | --send URL \"message\"]")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="xagent", description="X-Agent — Multi-model AI Agent")
    sub = parser.add_subparsers(dest="command")

    p_chat = sub.add_parser("chat", help="启动终端对话")
    p_chat.add_argument("--profile", action="store_true", help="启用 Telemetry 性能分析")
    p_chat.set_defaults(func=cmd_chat)

    p_model = sub.add_parser("model", help="查看或切换模型")
    p_model.add_argument("--set", help="切换至指定预设")
    p_model.set_defaults(func=cmd_model)

    p_config = sub.add_parser("config", help="配置管理")
    p_config.add_argument("--open", action="store_true", help="显示配置文件路径")
    p_config.add_argument("--edit", nargs="+", help="设置键值，如 --edit ui.theme=light")
    p_config.set_defaults(func=cmd_config)

    p_mem = sub.add_parser("memory", help="记忆管理")
    p_mem.add_argument("--search", help="搜索记忆内容")
    p_mem.add_argument("--type", help="过滤记忆类型 (conversation/code/error/preference)")
    p_mem.add_argument("--limit", type=int, help="返回条数")
    p_mem.add_argument("--forget", help="删除指定类型记忆 (all=全部)")
    p_mem.set_defaults(func=cmd_memory)

    p_route = sub.add_parser("route", help="智能路由统计与配置")
    p_route.add_argument("--strategy", choices=["cost_first", "quality_first", "balanced"], help="切换路由策略")
    p_route.set_defaults(func=cmd_route)

    p_task = sub.add_parser("task", help="任务管理 (list/resume/abort/export/background)")
    p_task.add_argument("-l", "--list", action="store_true", help="列出持久化任务")
    p_task.add_argument("--status", help="按状态过滤 (planning/executing/completed/failed)")
    p_task.add_argument("-r", "--resume", help="恢复指定任务")
    p_task.add_argument("-a", "--abort", help="删除/中止指定任务")
    p_task.add_argument("-e", "--export", help="导出任务为 markdown")
    p_task.add_argument("-o", "--output", help="导出文件路径 (默认 stdout)")
    p_task.add_argument("-b", "--background", help="后台创建任务（仅保存计划）")
    p_task.set_defaults(func=cmd_task)

    p_sch = sub.add_parser("schedule", help="后台任务调度")
    p_sch.add_argument("-l", "--list", action="store_true", help="列出调度队列")
    p_sch.add_argument("--add", help="添加调度任务")
    p_sch.add_argument("--strategy", choices=["immediate", "night", "interval"], help="调度策略")
    p_sch.add_argument("--cancel", help="取消指定调度任务")
    p_sch.add_argument("--tick", action="store_true", help="手动触发调度检查")
    p_sch.set_defaults(func=cmd_schedule)

    p_wf = sub.add_parser("workflow", help="工作流管理 (run/validate/list)")
    p_wf.add_argument("-l", "--list", action="store_true", help="列出工作流文件")
    p_wf.add_argument("--validate", help="验证工作流文件")
    p_wf.add_argument("-r", "--run", help="执行工作流文件")
    p_wf.add_argument("--dry-run", action="store_true", help="仅打印执行计划")
    p_wf.add_argument("--swarm-workers", type=int, default=0, help="启用 Swarm 模式，指定 Worker 进程数 (0=禁用)")
    p_wf.add_argument("--profile", action="store_true", help="启用 Telemetry 性能分析")
    p_wf.set_defaults(func=cmd_workflow)

    p_server = sub.add_parser("server", help="启动 HTTP API 服务器 (供 VS Code 插件使用)")
    p_server.add_argument("--host", default="127.0.0.1", help="绑定地址")
    p_server.add_argument("--port", type=int, default=7727, help="端口")
    p_server.set_defaults(func=cmd_server)

    p_profile = sub.add_parser("profile", help="查看 Telemetry 性能分析数据")
    p_profile.add_argument("--latest", action="store_true", help="显示最新 trace 摘要")
    p_profile.add_argument("--stats", action="store_true", help="显示聚合统计")
    p_profile.add_argument("--dir", help="指定 profile 目录")
    p_profile.set_defaults(func=cmd_profile)

    p_si = sub.add_parser("self-improve", help="自我改进系统 (status/history/rollback)")
    p_si.add_argument("--status", action="store_true", help="显示经验银行统计")
    p_si.add_argument("--history", action="store_true", help="显示高频失败历史")
    p_si.add_argument("--rollback", action="store_true", help="回滚 prompt 到上一版本")
    p_si.add_argument("--threshold", type=int, help="设置触发进化的频率阈值")
    p_si.set_defaults(func=cmd_self_improve)

    p_mcp = sub.add_parser("mcp", help="MCP Server 管理 (list/connect/call/install/uninstall/start/stop/status)")
    p_mcp.add_argument("--list", action="store_true", help="列出已配置的 MCP servers")
    p_mcp.add_argument("--connect", help="连接并测试指定 MCP server")
    p_mcp.add_argument("--call", nargs=2, metavar=("SERVER.TOOL", "ARGS_JSON"), help="调用 MCP 工具")
    p_mcp.add_argument("--install", nargs='+', metavar="ARGS", help="安装 MCP server: --install NAME --command CMD [--args ARGS] [--transport stdio|http]")
    p_mcp.add_argument("--uninstall", help="卸载指定 MCP server")
    p_mcp.add_argument("--start", help="启动指定 MCP server")
    p_mcp.add_argument("--stop", help="停止指定 MCP server")
    p_mcp.add_argument("--status", help="查看指定 MCP server 状态")
    p_mcp.add_argument("--command", help="Server 命令（配合 --install）")
    p_mcp.add_argument("--args", help="Server 参数，逗号分隔（配合 --install）")
    p_mcp.add_argument("--transport", choices=["stdio", "http"], default="stdio", help="传输类型（配合 --install）")
    p_mcp.add_argument("--trusted", action="store_true", help="标记为可信（配合 --install）")
    p_mcp.set_defaults(func=cmd_mcp)

    p_a2a = sub.add_parser("a2a", help="A2A Agent-to-Agent 协议 (serve/card/send)")
    p_a2a.add_argument("--serve", action="store_true", help="启动 A2A Server")
    p_a2a.add_argument("--host", default="127.0.0.1", help="A2A Server 绑定地址")
    p_a2a.add_argument("--port", type=int, default=7728, help="A2A Server 端口")
    p_a2a.add_argument("--name", help="Agent 名称")
    p_a2a.add_argument("--description", help="Agent 描述")
    p_a2a.add_argument("--card", help="获取远端 Agent Card")
    p_a2a.add_argument("--send", nargs=2, metavar=("URL", "MESSAGE"), help="发送任务到远端 Agent")
    p_a2a.set_defaults(func=cmd_a2a)

    p_bench = sub.add_parser("benchmark", help="运行 SWE-bench 基准测试")
    p_bench.add_argument("--dataset", required=True, help="SWE-bench JSONL 数据集路径")
    p_bench.add_argument("--output", help="报告输出路径 (.md 或 .json)")
    p_bench.add_argument("--limit", type=int, help="限制评估实例数")
    p_bench.add_argument("--workers", type=int, default=1, help="并行 Worker 数")
    p_bench.add_argument("--dry-run", action="store_true", help="仅显示将要评估的实例")
    p_bench.set_defaults(func=cmd_benchmark)

    args = parser.parse_args(argv)
    if not args.command:
        # 默认进入 chat
        cmd_chat(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
