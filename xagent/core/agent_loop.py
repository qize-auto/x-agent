"""
Agent 主循环
=========
感知 → 记忆检索 → LLM 推理 → 工具执行 → 结果反馈 → 记忆更新

参考设计：
- Claude Code: 极简循环，工具结果直接追加到上下文
- Hermes: 学习循环 + skills 创建
- OpenClaw: session model + 多 Agent 路由
- Reasonix: cache-first loop + tool-call repair
"""
from __future__ import annotations
import json
import time
from typing import Callable, Optional

from .llm_client import LLMClient, LLMResponse
from .tool_registry import ToolRegistry
from .memory_engine import MemoryEngine
from .planner import TaskPlanner
from .executor import TaskExecutor
from .task import TaskPlan
from .router import ModelRouter, RoutingDecision
from .cache_loop import CacheFirstLoop
from .requirement_contract import RequirementContract
import threading


SYSTEM_PROMPT = """You are X-Agent, a general-purpose AI agent that runs on the user's local machine.
You help with coding, file operations, shell commands, web searches, and general tasks.

## Core Protocol: THINK → VERIFY → ACT

For EVERY tool call, follow this protocol strictly:

### THINK (Mandatory — write your reasoning)
1. What is the user's ULTIMATE goal? (not just the immediate step)
2. What is the CURRENT state of the system?
3. What is the MINIMAL change needed?
4. What could go WRONG?

### VERIFY
5. Do you have ENOUGH information? If not, use read/analyze tools FIRST.
6. Does your plan EXACTLY match the user's intent?

### ACT
7. Execute ONE minimal change.
8. The system automatically verifies syntax and lint after edits. Do NOT manually call lint_code.
9. If verification fails, STOP and re-THINK. NEVER blindly retry the same action.

## Tool Rules
1. **Always use tools** for file operations, shell commands, and web searches. Do not hallucinate file contents.
2. **SEARCH/REPLACE** is the preferred way to edit code. For single changes, use `edit_file` with exact old_string/new_string. For multiple changes in one file, use the Aider multi-block format:
````
<<<<<<< SEARCH
old content 1
=======
new content 1
>>>>>>> REPLACE

<<<<<<< SEARCH
old content 2
=======
new content 2
>>>>>>> REPLACE
````
3. **Before editing unfamiliar code**, use `analyze_code` to understand its structure (functions, classes, imports, complexity).
4. **Dangerous commands** (rm, format, del, reg delete, etc.) require user confirmation — the system will block them automatically.
5. **Be concise** in responses. When using tools, explain what you're doing briefly.
6. **If uncertain**, ask the user rather than guessing.

## Anti-Patterns (NEVER do these)
- Do NOT make changes without understanding why
- Do NOT guess about code behavior
- Do NOT apply partial fixes and hope they work
- Do NOT lose sight of the original goal when iterating

## Tool Use Format
When you need to use a tool, respond with a JSON object:
```tool
{{"name": "tool_name", "arguments": {{"key": "value"}}}}
```
You can call multiple tools in sequence. After each tool result, you'll receive the output and can decide the next step.

## Context
- OS: {os_name}
- Project root: {project_root}
- Current directory: {cwd}
"""


class AgentLoop:
    """
    Agent 主循环
    
    核心流程：
    1. 接收用户输入
    2. 检索相关记忆 → 注入上下文
    3. LLM 推理（可能输出工具调用）
    4. 执行工具 → 结果回传 LLM
    5. 迭代直到 LLM 输出最终回复
    6. 记录到记忆
    """

    def __init__(self, llm: LLMClient, tools: ToolRegistry, memory: MemoryEngine,
                 project_root: str = "", confirm_callback: Callable = None,
                 status_callback: Callable = None, router_config: dict = None,
                 cache_mode: str = "auto", session_persist: bool = False,
                 enable_thought_harvest: bool = False,
                 ask_user_callback: Callable = None, config: dict = None,
                 code_indexer=None, telemetry_collector=None):
        # 持久化层（可选）
        try:
            from .persistence import TaskStore, CheckpointManager
            self._task_store = TaskStore()
            self._checkpoint_mgr = CheckpointManager(self._task_store)
        except Exception:
            self._task_store = None
            self._checkpoint_mgr = None
        """
        Args:
            cache_mode: "auto" - 自动检测 DeepSeek 时启用 CacheFirstLoop
                        "always" - 始终启用
                        "never" - 禁用，保持原有行为
            session_persist: 是否跨 run() 保留对话上下文
            enable_thought_harvest: 是否启用 R1 Thought Harvesting
            ask_user_callback: 交互式提问回调，签名: f(question: str) -> str | None
            config: XAgentConfig 配置字典
        """
        self.llm = llm
        self.tools = tools
        self.memory = memory
        self.project_root = project_root
        self.confirm_callback = confirm_callback
        self.status_callback = status_callback
        self.ask_user_callback = ask_user_callback
        self.config = config or {}
        self.router = ModelRouter(router_config) if router_config and router_config.get("enabled") else None
        self.messages: list[dict] = []
        self._lock = threading.Lock()
        self.max_tool_iterations = 10
        self._os_name = __import__("sys").platform
        self._cwd = __import__("os").getcwd()

        # Telemetry（可选）
        self._telemetry = telemetry_collector

        # 自我改进（可选）
        self._failure_analyzer = None
        self._prompt_evolver = None
        si_cfg = self.config.get("self_improve", {})
        if si_cfg.get("enabled", False):
            try:
                from .self_improve import FailureClassifier, ExperienceBank, RootCauseAnalyzer, PromptEvolver
                self._failure_analyzer = {
                    "classifier": FailureClassifier(self.tools),
                    "bank": ExperienceBank(),
                    "analyzer": RootCauseAnalyzer(self.llm, cheap_model_id=si_cfg.get("cheap_model_id")),
                    "evolver": PromptEvolver(self.llm, experience_bank=None,
                                              cheap_model_id=si_cfg.get("cheap_model_id")),
                    "config": si_cfg,
                }
                # 将 experience_bank 绑定到 evolver
                self._failure_analyzer["evolver"].experience_bank = self._failure_analyzer["bank"]
            except Exception:
                pass

        # Prompt 进化加载：尝试加载已保存的最佳 prompt（与 self_improve 开关独立）
        try:
            from .self_improve import PromptEvolver
            evolver = PromptEvolver(self.llm, cheap_model_id=si_cfg.get("cheap_model_id"))
            best_prompt = evolver.load_best_prompt(SYSTEM_PROMPT)
            if best_prompt != SYSTEM_PROMPT:
                # 替换模块级常量（单进程安全）
                import xagent.core.agent_loop as _al
                _al.SYSTEM_PROMPT = best_prompt
        except Exception:
            pass

        # Cache mode 配置
        self.cache_mode = cache_mode
        self.session_persist = session_persist
        self.enable_thought_harvest = enable_thought_harvest
        self._cache_loop: Optional[CacheFirstLoop] = None

        # 意图漂移追踪（Phase 4）
        try:
            from .intent_tracker import IntentTracker
            self.intent_tracker = IntentTracker()
        except Exception:
            self.intent_tracker = None

        # 代码智能（可选，由配置开关控制）
        self._code_indexer = code_indexer
        ci_cfg = self.config.get("code_intel", {})
        if self._code_indexer is None and project_root and ci_cfg.get("enabled", True):
            try:
                from .code_intel.indexer import CodeIndexer
                max_files = self.config.get("_adaptive", {}).get("max_index_files")
                self._code_indexer = CodeIndexer(project_root, max_files=max_files)
            except Exception:
                pass

        # 持久化配置
        self._persist_enabled = self.config.get("persistence", {}).get("auto_checkpoint", True)

        # 资源自适应监控
        self.throttler = None
        adaptive_cfg = self.config.get("adaptive", {})
        if adaptive_cfg.get("auto_throttle", True):
            try:
                from .resource_adaptive import ResourceMonitor, Throttler
                cpu_th = adaptive_cfg.get("cpu_threshold", 85.0)
                mem_th = adaptive_cfg.get("memory_threshold", 85.0)
                monitor = ResourceMonitor(cpu_threshold=cpu_th, memory_threshold=mem_th)
                monitor.start()
                self.throttler = Throttler(monitor)
            except Exception:
                self.throttler = None

        # 视觉感知层（Phase 7）
        self._vision = None
        self._vision_fusion = None
        vision_cfg = self.config.get("vision", {})
        if vision_cfg.get("enabled", False):
            try:
                from .vision.perceptor import VisionPerceptor
                self._vision = VisionPerceptor(
                    llm_client=self.llm,
                    strategy=vision_cfg.get("strategy", "auto"),
                    config=vision_cfg,
                )
            except Exception:
                pass
            # 视觉-代码融合（独立开关）
            if vision_cfg.get("code_fusion_enabled", True) and self._code_indexer:
                try:
                    from .vision.code_fusion import VisualCodeFusion
                    self._vision_fusion = VisualCodeFusion(indexer=self._code_indexer)
                except Exception:
                    pass

    def _should_use_cache_mode(self) -> bool:
        """判断是否应使用 CacheFirstLoop"""
        if self.cache_mode == "never":
            return False
        if self.cache_mode == "always":
            return True
        # auto: 检测 provider 或 model_id 是否包含 deepseek
        model = (self.llm.model_id or "").lower()
        provider = (self.llm.provider or "").lower()
        return "deepseek" in model or "deepseek" in provider
    def _ensure_indexed(self):
        """惰性索引：确保 CodeIndexer 已构建项目索引，不卡死主流程"""
        if not self._code_indexer:
            return
        if self._code_indexer._files:
            return  # 已索引

        # 读取自适应配置
        index_strategy = "lazy"
        max_files = 500
        try:
            index_strategy = self.config.get("_adaptive", {}).get("index_strategy", "lazy")
            max_files = self.config.get("_adaptive", {}).get("max_index_files", 500)
        except Exception:
            pass

        # partial 策略：低配置不索引全项目
        if index_strategy == "partial":
            try:
                self._status("🔍 正在构建轻量代码索引（partial 模式）...")
                count = 0
                for file_path in list(self._code_indexer._walk_files())[:max_files]:
                    try:
                        file_index = self._code_indexer.index_file(file_path)
                        if file_index:
                            self._code_indexer._files[str(file_path)] = file_index
                            count += 1
                    except Exception:
                        pass
                self._status(f"🔍 轻量索引完成: {count} 个文件")
            except Exception:
                pass
            return

        # eager / lazy 策略：全量索引
        try:
            self._status("🔍 正在构建代码索引...")
            self._code_indexer.index_all()
            count = len(self._code_indexer._files)
            self._status(f"🔍 代码索引完成: {count} 个文件")
        except Exception:
            pass  # 索引失败不影响主流程

    def _build_repo_context(self) -> str:
        """
        构建轻量 Repo Map 供 LLM 参考。
        大小根据硬件档位动态调整（高配置更全，低配置精简但不缺失关键信息）。
        """
        if not self._code_indexer:
            return ""

        # 读取自适应配置
        max_files = 30
        max_symbols = 5
        try:
            max_files = self.config.get("_adaptive", {}).get("repo_map_max_files", 30)
            max_symbols = self.config.get("_adaptive", {}).get("repo_map_max_symbols_per_file", 5)
        except Exception:
            pass

        try:
            from .code_intel.indexer import SymbolKind
            symbols = self._code_indexer.list_all_symbols()
            if not symbols:
                return ""
            by_file = {}
            for sym in symbols:
                if sym.kind in (SymbolKind.CLASS, SymbolKind.FUNCTION, SymbolKind.METHOD):
                    by_file.setdefault(sym.file_path, []).append(sym)
            if not by_file:
                return ""
            lines = ["## Repo Map (auto-generated from codebase)"]
            for fp, syms in sorted(by_file.items())[:max_files]:
                rel = fp
                try:
                    rel = str(Path(fp).relative_to(self.project_root))
                except ValueError:
                    pass
                lines.append(f"- {rel}")
                for s in syms[:max_symbols]:
                    icon = "📦" if s.kind == SymbolKind.CLASS else "🔧"
                    lines.append(f"  {icon} {s.name}{s.signature}")
            return "\n".join(lines)
        except Exception:
            return ""

    def run(self, user_input: str) -> str:
        """
        执行一次完整的 Agent 循环
        
        Returns:
            最终回复文本
        """
        # 记录用户意图（用于漂移检测）
        if self.intent_tracker:
            self.intent_tracker.record(user_input)

        # Cache mode 分支
        if self._should_use_cache_mode():
            if self._cache_loop is None:
                self._cache_loop = CacheFirstLoop(
                    llm=self.llm,
                    tools=self.tools,
                    memory=self.memory,
                    project_root=self.project_root,
                    confirm_callback=self.confirm_callback,
                    status_callback=self.status_callback,
                    router_config=getattr(self.router, 'config', None) if self.router else None,
                    session_persist=self.session_persist,
                    enable_thought_harvest=self.enable_thought_harvest,
                    throttler=self.throttler,
                )
            return self._cache_loop.run(user_input)

        # Legacy 模式（原有逻辑）
        self._status(f"🧠 正在思考: {user_input[:50]}...")

        # Telemetry: 开始 Trace
        trace = None
        if self._telemetry:
            trace = self._telemetry.start_trace(user_input)

        # 1. 检索相关记忆
        relevant = self.memory.recall(user_input, k=5)
        memory_context = self._format_memory(relevant)

        # 2. 构建 Repo Map（自动检索相关文件结构）
        self._ensure_indexed()
        repo_context = self._build_repo_context()

        # 3. 构建系统提示
        system_msg = {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                os_name=self._os_name,
                project_root=self.project_root,
                cwd=self._cwd,
            ) + (f"\n\n## Relevant Memory\n{memory_context}" if memory_context else "")
            + (f"\n\n{repo_context}" if repo_context else ""),
        }

        # 4. 添加用户消息
        with self._lock:
            self.messages.append({"role": "user", "content": user_input})

        # 4. Agent 推理循环
        final_response = ""
        error_msg = None
        start_time = time.time()
        max_total_time = self.config.get("_adaptive", {}).get("max_total_time_sec", 600)
        for iteration in range(self.max_tool_iterations):
            # 总时间预算检查：防止网络慢或工具卡住导致用户干等
            if time.time() - start_time > max_total_time:
                final_response = (
                    f"[系统提示] 总执行时间超过预算（{max_total_time} 秒），"
                    "返回当前阶段性成果。请简化请求或检查是否有长时间运行的操作。"
                )
                break
            # 构建完整上下文
            context = [system_msg] + self.messages

            # 意图锚定：每 5 轮提醒一次原始目标，防止上下文膨胀导致偏离
            if iteration > 0 and iteration % 5 == 0:
                context.append({
                    "role": "system",
                    "content": (
                        f"【意图锚定】原始目标: {user_input[:200]}。"
                        f"当前第 {iteration + 1} 轮工具调用。"
                        "请确认你的行动仍直接服务于原始目标，"
                        "没有偏离或陷入局部优化。如已偏离，请 STOP 并重新规划。"
                    ),
                })

            # 获取工具 schema
            tool_schemas = self.tools.get_schemas()

            # 调用 LLM（支持智能路由）
            resp = self._chat_with_routing(context, tools=tool_schemas if tool_schemas else None, trace=trace)

            # 记录 assistant 消息
            with self._lock:
                self.messages.append({
                    "role": "assistant",
                    "content": resp.content,
                })

            # 处理工具调用
            if resp.tool_calls:
                for tc in resp.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]

                    self._status(f"🔧 执行工具: {tool_name}({json.dumps(tool_args)[:60]}...)")

                    # Telemetry: 记录工具调用
                    import time as _time
                    _t0 = _time.time()
                    # 半自动安全：危险命令确认
                    if self._is_dangerous(tool_name, tool_args):
                        self._status(f"⚠️ 危险操作: {tool_name}")
                        if self.confirm_callback:
                            if not self.confirm_callback(tool_name, tool_args):
                                tool_result = {"ok": False, "error": "用户取消了危险操作"}
                            else:
                                tool_result = self.tools.execute(tool_name, tool_args)
                        else:
                            tool_result = {"ok": False, "error": "危险操作被阻止（无确认回调）"}
                    else:
                        tool_result = self.tools.execute(tool_name, tool_args)
                    _t1 = _time.time()

                    if self._telemetry and trace:
                        from .telemetry import ToolCallSpan
                        result_preview = json.dumps(tool_result, ensure_ascii=False)[:200]
                        tspan = ToolCallSpan(
                            tool_name=tool_name,
                            arguments=tool_args,
                            result_preview=result_preview,
                            latency_ms=(_t1 - _t0) * 1000,
                        )
                        self._telemetry.record_tool(trace, tspan)

                    # 将工具结果追加到对话
                    result_text = json.dumps(tool_result, ensure_ascii=False)
                    with self._lock:
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": result_text,
                        })

                    self._status(f"📋 结果: {result_text[:100]}...")

                # 继续循环，让 LLM 处理工具结果
                continue

            # 没有工具调用，输出最终回复
            final_response = resp.content
            break
        else:
            error_msg = "工具调用次数达到上限"

        # Telemetry: 结束 Trace
        if self._telemetry and trace:
            self._telemetry.finish_trace(trace, final_response=final_response, error=error_msg)

        # Self-Improvement: 分析失败并尝试进化 prompt
        if self._failure_analyzer and error_msg:
            self._analyze_and_evolve(error_msg, trace)

        # 5. 记录到记忆
        self.memory.add(f"User: {user_input}\nAgent: {final_response}", memory_type="conversation")

        # 迭代超限保护
        if not final_response and iteration == self.max_tool_iterations - 1:
            final_response = "[系统提示] 工具调用次数达到上限，未能生成最终回复。请简化请求或检查工具链循环。"

        return final_response

    def plan_task(self, goal: str, contract: RequirementContract = None) -> TaskPlan:
        """仅生成任务计划，不执行"""
        self._status(f"📋 开始任务规划: {goal[:60]}...")

        # Phase 7: 自动检测 UI/视觉相关任务并注入上下文
        vision_context = ""
        if self._vision and self._is_vision_related_goal(goal):
            try:
                perception = self._vision.perceive("screen")
                if perception and perception.elements:
                    vision_context = perception.to_context_string(max_elements=20)
                    self._status("👁️ 已捕获 UI 状态")
                    # 如果同时有代码索引器，尝试关联到代码
                    if self._vision_fusion and self._code_indexer:
                        locs = self._vision_fusion.trace_ui_to_code(perception, keyword=goal)
                        if locs:
                            vision_context += "\n\n--- 关联代码位置 ---\n"
                            for loc in locs[:5]:
                                vision_context += f"- {loc.file_path}:{loc.line_start} ({loc.confidence:.0%} confidence)\n"
            except Exception:
                pass

        self._ensure_indexed()
        planner = TaskPlanner(self.llm, code_indexer=self._code_indexer)
        plan = planner.plan(
            goal=goal,
            contract=contract,
            os_name=self._os_name,
            project_root=self.project_root,
            cwd=self._cwd,
            vision_context=vision_context if vision_context else None,
        )
        self._status(f"📋 计划已生成: {plan.total_count()} 个子任务")
        self._status("\n" + plan.to_markdown())
        return plan

    @staticmethod
    def _is_vision_related_goal(goal: str) -> bool:
        """检测目标是否涉及 UI/视觉/界面"""
        keywords = [
            "截图", "屏幕", "界面", "ui", "button", "按钮", "页面", "page",
            "样式", "css", "布局", "layout", "外观", "look", "visual",
            "图标", "icon", "菜单", "menu", "对话框", "dialog", "窗口", "window",
            "颜色", "color", "字体", "font", "显示", "display", "render",
        ]
        g = goal.lower()
        return any(kw in g for kw in keywords)

    def execute_plan(self, plan: TaskPlan) -> TaskPlan:
        """执行已有任务计划（带自动 checkpoint）"""
        executor = TaskExecutor(
            llm=self.llm,
            tools=self.tools,
            confirm_callback=self.confirm_callback,
            status_callback=self.status_callback,
        )
        # 包装执行器以注入 checkpoint
        if self._checkpoint_mgr and self._persist_enabled:
            original_execute = executor.execute
            def executing_with_checkpoint(plan: TaskPlan) -> TaskPlan:
                result = original_execute(plan)
                self._checkpoint_mgr.checkpoint(plan.id, plan, force=True)
                if self._task_store:
                    self._task_store.save_plan(result)
                return result
            executor.execute = executing_with_checkpoint

        plan = executor.execute(plan)
        # 记录到记忆
        summary = plan.to_markdown()
        self.memory.add(f"Task: {plan.goal}\n{summary}", memory_type="conversation")
        return plan

    def list_tasks(self, status: str = None) -> list[dict]:
        """列出持久化的任务"""
        if not self._task_store:
            return []
        summaries = self._task_store.list_tasks(status_filter=status)
        return [
            {
                "task_id": s.task_id,
                "goal": s.goal,
                "status": s.status,
                "progress": s.progress,
                "updated_at": s.updated_at,
            }
            for s in summaries
        ]

    def resume_task(self, task_id: str) -> TaskPlan | None:
        """恢复并继续执行指定任务"""
        if not self._task_store:
            self._status("❌ 持久化未启用")
            return None
        plan = self._task_store.load_plan(task_id)
        if plan is None:
            self._status(f"❌ 任务 {task_id} 不存在")
            return None
        self._status(f"🔄 恢复任务: {plan.goal}")
        return self.execute_plan(plan)

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        if not self._task_store:
            return False
        return self._task_store.delete_task(task_id)

    def perceive_ui(self, target: str = "screen", **kwargs) -> "UIPerception | None":
        """
        执行 UI 视觉感知。
        Returns None if vision layer is not enabled or unavailable.
        """
        if self._vision is None:
            return None
        try:
            return self._vision.perceive(target, **kwargs)
        except Exception:
            return None

    def trace_ui_to_code(self, perception: "UIPerception", keyword: str = "") -> list[dict]:
        """
        将 UI 感知结果映射到代码位置。
        Returns list of dicts with file_path, line_start, confidence, snippet.
        """
        if self._vision_fusion is None:
            return []
        try:
            locs = self._vision_fusion.trace_ui_to_code(perception, keyword=keyword)
            return [
                {
                    "file_path": loc.file_path,
                    "line_start": loc.line_start,
                    "line_end": loc.line_end,
                    "confidence": loc.confidence,
                    "snippet": loc.snippet,
                    "match_reason": loc.match_reason,
                }
                for loc in locs
            ]
        except Exception:
            return []

    def run_task(self, goal: str, contract: RequirementContract = None,
                 mode: str = "interactive", task_id: str = None) -> TaskPlan:
        """
        Clarify → Plan → Execute → Verify 任务循环

        Args:
            mode: "interactive" — 立即执行（默认）
                  "background" — 后台异步执行
            task_id: 用于 resume 的任务 ID（恢复模式时传入）
        """
        # 恢复模式：加载已有计划
        if task_id and self._task_store:
            loaded = self._task_store.load_plan(task_id)
            if loaded and loaded.status in ("executing", "planning"):
                self._status(f"🔄 恢复任务 {task_id}")
                return self.execute_plan(loaded)

        # 0. 意图漂移检测（Phase 4）
        if self.intent_tracker and len(self.intent_tracker.history) >= 2:
            try:
                drift = self.intent_tracker.detect_drift(goal)
                if drift > 0.5:
                    self._status(f"⚠️ 检测到意图漂移 ({drift:.0%})，目标与近期对话方向差异较大")
            except Exception:
                pass

        # 0.5 资源限流：高负载时主动等待
        if self.throttler:
            self.throttler.wait_if_needed(base_delay=0.5)

        # 1. 需求澄清阶段（可选，由配置控制）
        clarification_cfg = self.config.get("clarification", {})
        if contract is None and clarification_cfg.get("enabled", False):
            if not self._is_simple_goal(goal):
                contract = self._establish_contract(goal)
                if contract is None:
                    # 用户取消或无法交互
                    plan = TaskPlan(goal=goal, status="cancelled")
                    self._status("⏭️ 用户跳过了需求澄清")
                    return plan
        
        # 2. 任务规划（契约作为约束输入）
        plan = self.plan_task(goal, contract=contract)

        # 保存初始计划
        if self._task_store:
            self._task_store.save_plan(plan)

        # 后台模式：仅保存计划并返回
        if mode == "background":
            self._status(f"⏳ 任务已加入后台队列: {plan.id}")
            return plan

        # 3. 执行
        executed_plan = self.execute_plan(plan)

        # 最终保存
        if self._task_store:
            self._task_store.save_plan(executed_plan)
        return executed_plan

    def _chat_with_routing(self, messages: list[dict], tools: list[dict] = None, trace=None) -> LLMResponse:
        """
        带智能路由的 LLM 调用
        
        如果 router 存在，根据用户输入自动选择模型；
        如果调用失败，自动降级到备选模型。
        """
        from .llm_client import LLMResponse
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break

        def _record_llm(resp, model_used):
            if self._telemetry and trace and resp:
                from .telemetry import LLMCallSpan
                usage = resp.usage or {}
                span = LLMCallSpan(
                    model=model_used or self.llm.model_id,
                    provider=self.llm.provider,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    cached_tokens=usage.get("cached_tokens", 0) or usage.get("prompt_cache_hit_tokens", 0),
                    ttft_ms=resp.ttft_ms,
                    total_latency_ms=resp.latency_ms,
                    cost_usd=self.llm.get_cost_estimate(usage),
                )
                self._telemetry.record_llm(trace, span)

        if self.router and user_text:
            decision = self.router.decide(user_text)
            self._status(f"🎯 路由: {decision.model_id} ({decision.reason})")
            try:
                resp = self.llm.chat(messages, tools=tools, model_id=decision.model_id)
                self.router.reset_error()
                if resp.usage:
                    self.router.tracker.add(
                        decision.model_id,
                        resp.usage.get("prompt_tokens", 0),
                        resp.usage.get("completion_tokens", 0),
                    )
                _record_llm(resp, decision.model_id)
                return resp
            except Exception as e:
                self.router.report_error(str(e))
                fb = self.router.get_fallback(decision)
                self._status(f"⚠️ {decision.model_id} 失败，降级到 {fb.model_id}")
                resp = self.llm.chat(messages, tools=tools, model_id=fb.model_id)
                self.router.reset_error()
                if resp.usage:
                    self.router.tracker.add(
                        fb.model_id,
                        resp.usage.get("prompt_tokens", 0),
                        resp.usage.get("completion_tokens", 0),
                    )
                _record_llm(resp, fb.model_id)
                return resp
        else:
            resp = self.llm.chat(messages, tools=tools)
            _record_llm(resp, self.llm.model_id)
            return resp

    def _format_memory(self, memories: list[dict]) -> str:
        """格式化记忆为上下文字符串"""
        if not memories:
            return ""
        lines = []
        for m in memories:
            text = m["text"][:300]  # 截断
            meta = m.get("metadata", {})
            mtype = meta.get("type", "?")
            lines.append(f"[{mtype}] {text}")
        return "\n".join(lines)

    def _is_dangerous(self, tool_name: str, args: dict) -> bool:
        """判断工具调用是否包含危险操作"""
        if tool_name == "run_command":
            command = args.get("command", "")
            # 复用 tool_registry 中的危险检测
            dangerous_list = getattr(self.tools, "_dangerous_list", [])
            is_dangerous_fn = getattr(self.tools, "_is_dangerous", None)
            if is_dangerous_fn:
                return is_dangerous_fn(command, dangerous_list)
        return False

    def _establish_contract(self, goal: str) -> RequirementContract | None:
        """
        建立需求契约。
        
        流程：
        1. 检测是否需要澄清
        2. 生成澄清问题
        3. 通过 ask_user_callback 收集回答
        4. 构建结构化契约
        5. 用户确认
        
        Returns:
            RequirementContract — 用户确认的契约
            None — 用户取消、跳过或无法交互
        """
        from .clarification_engine import ClarificationEngine
        
        clarification_cfg = self.config.get("clarification", {})
        max_q = clarification_cfg.get("max_questions_per_task", 3)
        mode = clarification_cfg.get("mode", "standard")
        cheap_model = clarification_cfg.get("cheap_model_id") if \
            clarification_cfg.get("use_cheap_model", True) else None
        
        engine = ClarificationEngine(
            self.llm,
            max_questions=max_q,
            cheap_model_id=cheap_model,
        )
        
        # 0. 召回历史契约，通过 UserProfile 聚合用户偏好模式
        historical_hints = []
        if self.memory:
            try:
                from .user_profile import UserProfile
                import json as _json
                
                # 召回更多契约用于画像聚合（k=5 做统计，k=2 做直接提示）
                past_contracts = self.memory.recall(goal, k=5, memory_type="contract")
                
                # 用 UserProfile 聚合高频约束（出现 >= 2 次视为画像）
                profile = UserProfile()
                profile.ingest_contracts(past_contracts)
                profile_hints = profile.get_profile_hints(min_occurrences=2)
                
                # 同时保留最近契约的直接约束作为补充提示
                recent_hints = []
                for item in past_contracts[:2]:
                    meta = item.get("metadata", {})
                    hc_raw = meta.get("hard_constraints", "[]")
                    if isinstance(hc_raw, str):
                        try:
                            hc_raw = _json.loads(hc_raw)
                        except Exception:
                            continue
                    if isinstance(hc_raw, list):
                        recent_hints.extend(hc_raw)
                
                # 合并：画像约束优先，最近约束补充
                seen = set()
                for h in profile_hints:
                    seen.add(h)
                    historical_hints.append(h)
                for h in recent_hints:
                    if h not in seen:
                        historical_hints.append(h)
            except Exception:
                pass
        
        # 1. 检测是否需要澄清
        if not engine.needs_clarification(goal):
            return RequirementContract(raw_goal=goal, refined_goal=goal, confirmed=True)
        
        # 2. 生成问题（传入历史约束，避免重复提问）
        self._status("🔍 正在分析需求，生成澄清问题...")
        questions = engine.generate_questions(goal, historical_hints=historical_hints or None, mode=mode)
        if not questions:
            return RequirementContract(raw_goal=goal, refined_goal=goal, confirmed=True)
        
        # 3. 收集回答
        if self.ask_user_callback is None:
            # 无交互能力，跳过澄清
            self._status("ℹ️ 未配置交互回调，跳过需求澄清")
            return None
        
        answers = []
        for q in questions:
            answer = self.ask_user_callback(q)
            if answer is None:  # 用户取消
                self._status("⏭️ 用户取消了需求澄清")
                return None
            answers.append(answer)
        
        # 4. 构建契约
        self._status("📝 正在构建需求契约...")
        contract = engine.build_contract(goal, questions, answers, historical_hints=historical_hints or None)
        
        # 5. 用户确认契约
        self._status("\n" + contract.to_markdown())
        if self.confirm_callback:
            confirmed = self.confirm_callback("confirm_contract", {
                "contract": contract.to_markdown(),
                "message": "请确认以上需求契约是否正确。如不正确，将重新澄清。",
            })
            if confirmed:
                contract.confirm()
            else:
                # 用户不确认，递归重新澄清（最多重试 2 次）
                self._status("🔄 用户未确认契约，重新澄清...")
                # 简化处理：返回 None，让调用方处理
                return None
        else:
            # 无确认回调，自动确认（主要用于测试/CLI）
            contract.confirmed = True
        
        self._status("✅ 需求契约已确认")
        
        # 6. 将确认的契约存入记忆，供后续任务复用
        if contract.confirmed and self.memory:
            try:
                import json as _json
                self.memory.add(
                    contract.to_context_string(),
                    memory_type="contract",
                    metadata={
                        "raw_goal": contract.raw_goal,
                        "refined_goal": contract.refined_goal,
                        "hard_constraints": _json.dumps(contract.hard_constraints),
                        "soft_preferences": _json.dumps(contract.soft_preferences),
                        "out_of_scope": _json.dumps(contract.out_of_scope),
                        "acceptance_criteria": _json.dumps(contract.acceptance_criteria),
                    }
                )
            except Exception:
                pass  # 记忆存储失败不应影响任务执行
        
        return contract
    
    def _is_simple_goal(self, goal: str) -> bool:
        """判断是否为简单目标（无需澄清）"""
        from .clarification_engine import ClarificationEngine
        engine = ClarificationEngine(llm=self.llm)
        return not engine.needs_clarification(goal)

    def _status(self, message: str):
        if self.status_callback:
            self.status_callback(message)

    def run_workflow(self, workflow: "Workflow", executor=None) -> "WorkflowContext":
        """执行工作流，自动注入 agent_loop 到工作流引擎"""
        from .workflow.engine import WorkflowEngine
        engine = WorkflowEngine(agent_loop=self, executor=executor)
        return engine.run(workflow)

    def _analyze_and_evolve(self, error_msg: str, trace):
        """
        分析失败并尝试进化 prompt。
        仅在 self_improve.enabled=True 时调用。
        """
        if not self._failure_analyzer:
            return
        try:
            classifier = self._failure_analyzer["classifier"]
            bank = self._failure_analyzer["bank"]
            analyzer = self._failure_analyzer["analyzer"]
            evolver = self._failure_analyzer["evolver"]
            si_cfg = self._failure_analyzer["config"]

            # 从 trace 提取信息
            llm_spans = trace.llm_spans if trace else []
            tool_spans = trace.tool_spans if trace else []

            # 构造 tool_results
            tool_results = []
            for ts in tool_spans:
                preview = ts.result_preview
                ok = not ts.error
                tool_results.append({
                    "name": ts.tool_name,
                    "ok": ok,
                    "error": ts.error or "",
                })

            # 构造模拟 LLMResponse（用于分类器）
            from .llm_client import LLMResponse
            last_resp = LLMResponse(content="", tool_calls=[])
            if llm_spans:
                last_resp = LLMResponse(content="", tool_calls=[],
                                        usage=llm_spans[-1].to_dict())

            result = classifier.classify(last_resp, tool_results)
            ftype = result["type"]
            evidence = result["evidence"]

            # 如果置信度足够高，记录经验
            if result["confidence"] >= 0.7:
                # 根因分析
                root = analyzer.analyze(
                    failure_type=ftype.name,
                    evidence=evidence,
                    system_prompt=SYSTEM_PROMPT[:500],
                )

                record_id = bank.record(
                    failure_type=ftype.name,
                    root_cause=root.get("root_cause", ""),
                    evidence=evidence,
                    trace_id=trace.trace_id if trace else "",
                    original_prompt=SYSTEM_PROMPT[:1000],
                    suggested_fix=root.get("suggested_fix_category", ""),
                )

                # 检查是否达到进化阈值
                threshold = si_cfg.get("threshold", 3)
                frequent = bank.get_frequent(failure_type=ftype.name, min_frequency=threshold, limit=1)
                if frequent and si_cfg.get("auto_apply", False):
                    exp = frequent[0]
                    evolve_result = evolver.evolve(SYSTEM_PROMPT, {
                        "id": exp.id,
                        "failure_type": exp.failure_type,
                        "root_cause": exp.root_cause,
                        "explanation": exp.evidence,
                        "suggested_fix_category": exp.suggested_fix,
                    })
                    if evolve_result.get("accepted"):
                        self._status(f"🔄 Prompt 已进化（+{evolve_result['score'] - evolve_result['baseline_score']:.1f} 分）")
        except Exception:
            pass

    def delegate(self, task_input: str, target_agent_url: str = "",
                 target_agent_card: dict = None) -> dict:
        """
        将任务委托给远程 A2A Agent（预留接口）。

        Args:
            task_input: 任务描述
            target_agent_url: 远程 Agent 的端点 URL
            target_agent_card: 远程 Agent 的 Agent Card 字典

        Returns:
            {"status": str, "result": str, "error": str|None}
        """
        from .a2a.agent_card import AgentCard
        from .a2a.task_client import A2ATaskClient

        if not target_agent_url and not target_agent_card:
            return {"status": "failed", "result": "", "error": "未指定目标 Agent"}

        card = None
        if target_agent_card:
            card = AgentCard.from_dict(target_agent_card)
        elif target_agent_url:
            card = AgentCard(name="remote", description="Remote A2A agent", url=target_agent_url)

        client = A2ATaskClient(card)
        result = client.send_task(task_input)

        return {
            "status": result.get("status", "unknown"),
            "result": json.dumps(result.get("artifacts", [])),
            "error": result.get("error"),
            "note": "A2A delegate 当前为预留接口，尚未实现真实网络通信。",
        }

    def reset(self):
        """重置会话上下文"""
        with self._lock:
            self.messages.clear()

    def get_usage_estimate(self) -> dict:
        """估算本次会话的 token 消耗"""
        with self._lock:
            total_chars = sum(len(m.get("content", "")) for m in self.messages)
            msg_count = len(self.messages)
        return {
            "estimated_tokens": total_chars // 4,
            "message_count": msg_count,
        }

    def __repr__(self):
        return f"AgentLoop(llm={self.llm}, tools={len(self.tools.list_tools())}, memory={self.memory.stats()})"
