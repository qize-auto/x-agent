"""
缓存优先的 Agent 循环 (CacheFirstLoop)
========================================
围绕 DeepSeek prefix caching 设计的 Agent 主循环。

核心不变量：
1. ImmutablePrefix 在 session 开始时构造，之后永不修改
2. AppendOnlyLog 只追加，不修改
3. VolatileScratch 每轮清空，内容不直接进入 API 请求
4. 单次 run() 内不切换模型（保护 KV cache）
5. 支持预热请求（Q3 决策：启动时建立缓存）

参考：DeepSeek-Reasonix 的 Pillar 1 — Cache-First Loop
"""
from __future__ import annotations
import json
import os
import sys
import threading
import time
from typing import Callable, Optional

from .llm_client import LLMClient, LLMResponse
from .tool_registry import ToolRegistry
from .memory_engine import MemoryEngine
from .cache_context import ImmutablePrefix, AppendOnlyLog, VolatileScratch
from .router import ModelRouter, RoutingDecision
from .repair_pipeline import ToolCallRepairPipeline, SchemaFlattener
from .cost_control import CostController, CostControlConfig, ContextCompressor
from .thought_harvester import ThoughtHarvester
from .requirement_contract import RequirementContract


# 纯静态的 System Prompt（不含任何动态变量）
# 动态内容（cwd、记忆、项目信息）全部下移到 enriched user message
PURE_SYSTEM_PROMPT = """You are X-Agent, a general-purpose AI agent that runs on the user's local machine.
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
{"name": "tool_name", "arguments": {"key": "value"}}
```
You can call multiple tools in sequence. After each tool result, you'll receive the output and can decide the next step.
"""


class CacheFirstLoop:
    """
    缓存优先的 Agent 循环。

    用法:
        loop = CacheFirstLoop(llm=client, tools=registry, memory=memory)
        loop.warmup()  # 可选：预热缓存
        response = loop.run("帮我修改这个 bug")
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        memory: MemoryEngine,
        system_prompt: Optional[str] = None,
        project_root: str = "",
        confirm_callback: Callable = None,
        status_callback: Callable = None,
        router_config: dict = None,
        session_persist: bool = False,
        enable_thought_harvest: bool = False,
        throttler=None,
    ):
        self.llm = llm
        self.tools = tools
        self.memory = memory
        self.project_root = project_root
        self.confirm_callback = confirm_callback
        self.status_callback = status_callback
        self._throttler = throttler

        # Router（可选）
        self.router = ModelRouter(router_config) if router_config and router_config.get("enabled") else None

        # Layer 2: 修复流水线
        self.repair_pipeline = ToolCallRepairPipeline(
            tools=tools,
            enable_flatten=True,
            storm_window=10,
        )

        # Layer 3: 成本控制
        self.cost_controller = CostController(
            config=CostControlConfig(),
            status_callback=status_callback,
        )
        self.context_compressor = ContextCompressor(llm, threshold_tokens=3000)

        # Layer 4: Thought Harvesting（可选）
        self.thought_harvester = ThoughtHarvester(llm) if enable_thought_harvest else None
        self._thought_harvest_enabled = enable_thought_harvest

        # Session 持久化配置
        self.session_persist = session_persist

        # 尝试加载进化后的最佳 prompt（仅在未显式传入时）
        if system_prompt is None:
            try:
                from ..config import XAgentConfig
                cfg = XAgentConfig()
                cheap_model_id = cfg._data.get("self_improve", {}).get("cheap_model_id")
                from .self_improve import PromptEvolver
                evolver = PromptEvolver(self.llm, cheap_model_id=cheap_model_id)
                best = evolver.load_best_prompt(PURE_SYSTEM_PROMPT)
                if best != PURE_SYSTEM_PROMPT:
                    system_prompt = best
            except Exception:
                pass

        # Layer 1: 三区域上下文
        self.prefix = self._build_prefix(system_prompt or PURE_SYSTEM_PROMPT)
        self.log = AppendOnlyLog()
        self.scratch = VolatileScratch()

        # 预热状态
        self._warmup_done: bool = False
        self._warmup_model_id: Optional[str] = None

        # 模型锁定：单次 run() 内保持同一模型
        self._locked_model_id: Optional[str] = None

        # 成本与缓存监控
        self.cache_stats = {
            "total_hit_tokens": 0,
            "total_miss_tokens": 0,
            "total_cost_usd": 0.0,
            "turns": 0,
        }

        # 通用参数
        self.max_tool_iterations = 10
        self._os_name = sys.platform
        self._cwd = os.getcwd()

    def _build_prefix(self, system_prompt: str) -> ImmutablePrefix:
        """构造不可变前缀。只在初始化时调用一次。"""
        # 如果启用了 flatten，使用扁平化后的 schemas
        if self.repair_pipeline.flattener:
            tool_schemas = self.repair_pipeline.flattener.get_schemas_for_model()
        else:
            tool_schemas = self.tools.get_schemas() or []

        # 关键：按工具名排序，保证序列化一致性
        sorted_schemas = tuple(
            sorted(tool_schemas, key=lambda s: s.get("function", {}).get("name", ""))
        )
        return ImmutablePrefix(
            system_content=system_prompt,
            tool_schemas=sorted_schemas,
            few_shots=(),
        )

    def warmup(self, model_id: Optional[str] = None) -> bool:
        """
        预热缓存。

        发送一次只包含 prefix（system + tools）的请求，让 DeepSeek 服务端
        将这部分内容缓存起来。后续请求可以从预热状态开始。

        Args:
            model_id: 指定预热用的模型，默认使用 llm.model_id

        Returns:
            是否预热成功
        """
        if self._warmup_done:
            return True

        target_model = model_id or self.llm.model_id
        if not target_model:
            self._status("⚠️ 未配置模型，跳过预热")
            return False

        # 只预热 DeepSeek 模型
        if "deepseek" not in target_model.lower():
            self._status("ℹ️ 非 DeepSeek 模型，跳过预热")
            return False

        self._status(f"🔥 预热缓存: {target_model}")
        try:
            # 预热请求：只有 system prompt，无用户消息
            # 用一条简单的 user message 触发 completion，但内容极短
            warmup_messages = self.prefix.to_messages() + [
                {"role": "user", "content": "Hello."}
            ]
            resp = self.llm.chat(
                warmup_messages,
                tools=self.prefix.to_api_tools(),
                model_id=target_model,
            )
            self._warmup_done = True
            self._warmup_model_id = target_model

            # 记录预热结果中的缓存统计
            if resp.usage:
                hit = resp.usage.get("prompt_cache_hit_tokens", 0)
                miss = resp.usage.get("prompt_cache_miss_tokens", 0)
                self._status(f"🔥 预热完成 | hit: {hit} | miss: {miss}")

            return True
        except Exception as e:
            self._status(f"⚠️ 预热失败: {e}")
            return False

    def run(self, user_input: str, contract: RequirementContract = None) -> str:
        """
        执行一次完整的用户请求。

        流程：
        1. 检索记忆 -> 放入 scratch（不放入 prefix）
        2. 构建 enriched user message（动态内容都在这里）
        3. 追加到 log
        4. 进入 tool-call 循环（模型锁定）
        5. 每轮结束后清空 scratch
        6. 记录成本和缓存统计
        7. 回合结束：清空 log（Q3: 预热模式，每次 run() 独立）
        """
        self._status(f"🧠 正在思考: {user_input[:50]}...")

        # 自动预热（如果未预热且是 DeepSeek）
        if not self._warmup_done:
            self.warmup()

        # 1. 检索记忆（放入 scratch，不污染 prefix）
        self.scratch.memory_results = self.memory.recall(user_input, k=5)
        self.scratch.current_cwd = self._cwd

        # 2. 构建 enriched user message（动态内容都在这里）
        enriched_content = self._build_enriched_user_message(user_input, contract)
        user_msg = {"role": "user", "content": enriched_content}

        # 3. 追加到 log
        self.log.append(user_msg)

        # 4. 模型锁定：本 run() 内使用同一模型
        self._locked_model_id = None

        # 5. 重置成本控制状态
        self.cost_controller.reset_turn()

        # 6. Tool-call 循环
        final_response = ""
        iteration = 0
        start_time = time.time()
        max_total_time = 600
        try:
            from ..config import XAgentConfig
            max_total_time = XAgentConfig()._data.get("_adaptive", {}).get("max_total_time_sec", 600)
        except Exception:
            pass
        for iteration in range(self.max_tool_iterations):
            # 总时间预算检查：防止网络慢或工具卡住导致用户干等
            if time.time() - start_time > max_total_time:
                final_response = (
                    f"[系统提示] 总执行时间超过预算（{max_total_time} 秒），"
                    "返回当前阶段性成果。请简化请求或检查是否有长时间运行的操作。"
                )
                break
            # 构建 API 请求：prefix + log snapshot
            api_messages = self.prefix.to_messages() + self.log.snapshot()

            # 意图锚定：每 5 轮提醒一次原始目标
            if iteration > 0 and iteration % 5 == 0:
                api_messages.append({
                    "role": "system",
                    "content": (
                        f"【意图锚定】原始目标: {user_input[:200]}。"
                        f"当前第 {iteration + 1} 轮工具调用。"
                        "请确认你的行动仍直接服务于原始目标，"
                        "没有偏离或陷入局部优化。"
                    ),
                })

            tool_schemas = self.prefix.to_api_tools()

            # 调用 LLM（带模型锁定）
            resp = self._chat_with_cache_affinity(api_messages, tools=tool_schemas)

            # 修复流水线（Layer 2）
            resp = self.repair_pipeline.repair(resp)

            # Thought Harvesting（Layer 4，可选）
            if self.thought_harvester and resp.reasoning:
                harvest = self.thought_harvester.harvest_fast(resp.reasoning)
                if any(harvest.values()):
                    self.scratch.plan_state = harvest
                    self._status(f"🌾 Harvested: {len(harvest['subgoals'])} subgoals, "
                                 f"{len(harvest['uncertainties'])} uncertainties")

            # 记录 assistant 消息到 log
            assistant_msg = {"role": "assistant", "content": resp.content}
            if resp.reasoning:
                self.scratch.reasoning_content = resp.reasoning
            self.log.append(assistant_msg)

            # 处理工具调用（Phase 4: 分组逻辑，当前仍串行执行）
            if resp.tool_calls:
                # 资源限流：高负载时降低工具执行频率
                if hasattr(self, '_throttler') and self._throttler:
                    self._throttler.wait_if_needed(base_delay=0.3)
                groups = self._group_tool_calls(resp.tool_calls)
                for group in groups:
                    # 低配置禁用并行，避免资源争抢导致卡死
                    tier = "mid"
                    try:
                        from ..config import XAgentConfig
                        tier = XAgentConfig()._data.get("_adaptive", {}).get("tier", "mid")
                    except Exception:
                        pass

                    # 判断是否可以并行执行
                    can_parallel = (
                        tier != "low" and
                        len(group) > 1 and
                        all(self._is_parallel_safe(tc) for tc in group)
                    )

                    if can_parallel:
                        results = self._execute_group_parallel(group)
                    else:
                        results = self._execute_group_serial(group)

                    # 将所有结果追加到 log
                    for tc, tool_result in results:
                        result_text = json.dumps(tool_result, ensure_ascii=False)
                        self.log.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": result_text,
                        })
                        self._status(f"📋 结果: {result_text[:100]}...")

                # 清空 scratch，进入下一轮
                self.scratch.clear()
                continue

            # 无工具调用，获得最终回复
            final_response = resp.content
            break

        # 6. 记录记忆
        if final_response:
            self.memory.add(f"User: {user_input}\nAgent: {final_response}", memory_type="conversation")

        # 7. 迭代超限保护
        if not final_response and iteration == self.max_tool_iterations - 1:
            final_response = "[系统提示] 工具调用次数达到上限，未能生成最终回复。请简化请求或检查工具链循环。"

        # 8. 回合结束：上下文压缩（如果启用）
        if self.cost_controller.config.turn_end_compaction:
            estimated = self.context_compressor.estimate_tokens(
                self.prefix.to_messages() + self.log.snapshot()
            )
            compact_level = self.cost_controller.should_compact(estimated)
            if compact_level != "none":
                self._status(f"📦 Context compaction triggered ({compact_level}): {estimated} tokens")
                # Note: compaction 在 log 上执行，但 log 即将被清空
                # 实际效果在保留 log 的模式（Phase 4 session）时更明显

        # 9. 回合结束：清空或保留 log
        self.scratch.clear()
        self.repair_pipeline.reset_turn()
        self.cost_controller.reset_turn()

        if not self.session_persist:
            # 预热模式：每次 run() 独立，清空 log
            self.log.clear()

        return final_response

    def _build_enriched_user_message(self, user_input: str, contract: RequirementContract = None) -> str:
        """构建包含动态内容的 enriched user message。"""
        parts = []

        # 需求契约（如有）放在最前面，作为最高优先级约束
        if contract is not None and contract.confirmed:
            parts.append(contract.to_context_string())

        # 动态记忆（Q2: 放在 user content 最前面）
        if self.scratch.memory_results:
            memory_text = self._format_memory(self.scratch.memory_results)
            parts.append(f"## Context from Memory\n{memory_text}")

        # 当前工作目录（Q1: 放在 user message 中，实时反映）
        parts.append(f"## Current Directory\n{self._cwd}")

        # 操作系统和项目根目录（静态信息，但放在 user message 中保持 system 纯净）
        parts.append(f"## Environment\n- OS: {self._os_name}\n- Project: {self.project_root or '(none)'}")

        # 原始用户输入
        parts.append(f"## User Request\n{user_input}")

        return "\n\n".join(parts)

    def _format_memory(self, memories: list[dict]) -> str:
        """格式化记忆为上下文字符串。"""
        if not memories:
            return ""
        lines = []
        for m in memories:
            text = m.get("text", "")[:300]  # 截断
            meta = m.get("metadata", {})
            mtype = meta.get("type", "?")
            lines.append(f"[{mtype}] {text}")
        return "\n".join(lines)

    def _chat_with_cache_affinity(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        """
        带缓存亲和性的 LLM 调用。

        关键行为：
        1. 如果已锁定模型，复用锁定模型（保护 KV cache）
        2. 如果未锁定，根据 router + cost_controller 选择模型
        3. 失败后尝试 fallback，但会显式警告缓存将失效
        """
        if self._locked_model_id:
            return self.llm.chat(messages, tools=tools, model_id=self._locked_model_id)

        # 使用 cost_controller 选择模型（flash-first + 升级逻辑）
        estimated_tokens = ContextCompressor.estimate_tokens(messages)
        cost_model = self.cost_controller.select_model(context_length=estimated_tokens)

        if self.router:
            # 从最近的 user message 中提取文本用于分类
            user_text = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_text = m.get("content", "")
                    break

            decision = self.router.decide(user_text)
            self._status(f"🎯 路由: {decision.model_id} ({decision.reason})")

            # 成本控制器可能要求使用不同模型
            if cost_model and decision.model_id != cost_model:
                self._status(f"💰 Cost control: override to {cost_model}")
                decision.model_id = cost_model

            # 缓存亲和性：长上下文时优先 DeepSeek
            if self._should_prefer_deepseek_for_cache(messages):
                if "deepseek" not in decision.model_id.lower():
                    ds_alt = self._find_deepseek_alternative(decision)
                    if ds_alt:
                        self._status(f"🔄 长上下文，切换至 DeepSeek 以利用缓存")
                        decision = ds_alt

            self._locked_model_id = decision.model_id
            try:
                resp = self.llm.chat(messages, tools=tools, model_id=decision.model_id)
                self._record_usage(resp)
                return resp
            except Exception as e:
                self._status(f"⚠️ {decision.model_id} 失败，降级到 fallback（缓存将失效）")
                fb = self.router.get_fallback(decision)
                self._locked_model_id = fb.model_id
                resp = self.llm.chat(messages, tools=tools, model_id=fb.model_id)
                self._record_usage(resp)
                return resp
        else:
            # 无 router，直接使用 cost_controller 选择的模型
            model_id = cost_model or self.llm.model_id
            self._locked_model_id = model_id
            resp = self.llm.chat(messages, tools=tools, model_id=model_id)
            self._record_usage(resp)
            return resp

    def _should_prefer_deepseek_for_cache(self, messages: list[dict]) -> bool:
        """判断是否应优先 DeepSeek 以获取缓存收益。"""
        # 简单启发式：消息列表长度 > 5 且总字符数 > 4000
        if len(messages) < 5:
            return False
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars > 4000

    def _find_deepseek_alternative(self, decision: RoutingDecision) -> Optional[RoutingDecision]:
        """寻找 DeepSeek 备选模型。"""
        # 简单策略：检查所有策略中是否有 DeepSeek fallback
        for strat in self.router._strategies.values():
            if "deepseek" in strat.get("fallback", "").lower():
                from dataclasses import replace
                return replace(
                    decision,
                    model_id=strat["fallback"],
                    reason=f"{decision.reason} [缓存亲和性: 切换至 DeepSeek]",
                )
        return None

    def _record_usage(self, resp: LLMResponse) -> None:
        """记录 token 使用和缓存统计。"""
        if not resp.usage:
            return

        hit = resp.usage.get("prompt_cache_hit_tokens", 0)
        miss = resp.usage.get("prompt_cache_miss_tokens", 0)
        completion = resp.usage.get("completion_tokens", 0)

        self.cache_stats["total_hit_tokens"] += hit
        self.cache_stats["total_miss_tokens"] += miss
        self.cache_stats["turns"] += 1

        # 成本估算（DeepSeek 缓存价格）
        cost = self._estimate_cost_with_cache(hit, miss, completion)
        self.cache_stats["total_cost_usd"] += cost

        # 状态显示
        total_prompt = hit + miss
        hit_rate = (hit / total_prompt * 100) if total_prompt > 0 else 0
        self._status(f"💰 Turn: ${cost:.6f} | Cache: {hit_rate:.1f}% hit ({hit}/{total_prompt})")

    def _estimate_cost_with_cache(self, hit: int, miss: int, completion: int) -> float:
        """使用 DeepSeek 缓存价格估算成本（$/token）。"""
        # DeepSeek v4-flash: hit $0.014/M, miss $0.14/M, completion $0.28/M
        input_cost = (hit * 0.014 + miss * 0.14) / 1_000_000
        output_cost = completion * 0.28 / 1_000_000
        return input_cost + output_cost

    def _group_tool_calls(self, tool_calls: list[dict]) -> list[list[dict]]:
        """
        将 tool_calls 按 parallel_safe 分组。

        策略：
        - parallel_safe=True 且非危险的工具合并到同一组（并行执行）
        - 其他工具独立成组（串行执行）
        """
        parallel_group = []
        serial_groups = []
        for tc in tool_calls:
            if self._is_parallel_safe(tc) and not self._is_dangerous(tc.get("name", ""), tc.get("arguments", {})):
                parallel_group.append(tc)
            else:
                # 先刷新并行组
                if parallel_group:
                    serial_groups.append(parallel_group)
                    parallel_group = []
                serial_groups.append([tc])
        if parallel_group:
            serial_groups.append(parallel_group)
        return serial_groups

    def _is_parallel_safe(self, tc: dict) -> bool:
        """检查工具调用是否可并行执行"""
        tool_name = tc.get("name", "")
        tool_spec = self.tools.get(tool_name)
        if tool_spec is None:
            return False
        return getattr(tool_spec, "parallel_safe", False)

    def _execute_group_serial(self, group: list[dict]) -> list[tuple[dict, dict]]:
        """串行执行工具组，返回 (tool_call, result) 列表"""
        results = []
        for tc in group:
            tool_name = tc.get("name", "")
            tool_args = tc.get("arguments", {})
            self._status(f"🔧 执行工具: {tool_name}({json.dumps(tool_args)[:60]}...)")
            tool_result = self._execute_single_tool(tool_name, tool_args)
            results.append((tc, tool_result))
        return results

    def _execute_group_parallel(self, group: list[dict]) -> list[tuple[dict, dict]]:
        """并行执行工具组（读操作），返回 (tool_call, result) 列表"""
        from concurrent.futures import ThreadPoolExecutor
        self._status(f"🔧 并行执行 {len(group)} 个工具")
        with ThreadPoolExecutor(max_workers=len(group)) as executor:
            futures = {
                executor.submit(self._execute_single_tool, tc.get("name", ""), tc.get("arguments", {})): tc
                for tc in group
            }
            results = []
            for future in futures:
                tc = futures[future]
                try:
                    tool_result = future.result(timeout=30)
                except TimeoutError:
                    tool_result = {"ok": False, "error": "[工具执行超时] 并行组内某工具超过 30 秒未完成"}
                except Exception as e:
                    tool_result = {"ok": False, "error": str(e)}
                results.append((tc, tool_result))
        return results

    def _execute_single_tool(self, tool_name: str, tool_args: dict) -> dict:
        """执行单个工具"""
        if self._is_dangerous(tool_name, tool_args):
            self._status(f"⚠️ 危险操作: {tool_name}")
            if self.confirm_callback:
                if not self.confirm_callback(tool_name, tool_args):
                    return {"ok": False, "error": "用户取消了危险操作"}
                return self.tools.execute(tool_name, tool_args)
            return {"ok": False, "error": "危险操作被阻止（无确认回调）"}
        return self.tools.execute(tool_name, tool_args)

    def _is_dangerous(self, tool_name: str, args: dict) -> bool:
        """判断工具调用是否包含危险操作。"""
        if tool_name == "run_command":
            command = args.get("command", "")
            dangerous_list = getattr(self.tools, "_dangerous_list", [])
            is_dangerous_fn = getattr(self.tools, "_is_dangerous", None)
            if is_dangerous_fn:
                return is_dangerous_fn(command, dangerous_list)
        return False

    def _status(self, message: str) -> None:
        if self.status_callback:
            self.status_callback(message)

    def reset_session(self):
        """手动重置 session（清空 log 和统计）。"""
        self.log.clear()
        self.cache_stats = {
            "total_hit_tokens": 0,
            "total_miss_tokens": 0,
            "total_cost_usd": 0.0,
            "turns": 0,
        }
        self._warmup_done = False
        self._warmup_model_id = None

    def get_session_messages(self) -> list[dict]:
        """获取当前 session 的完整消息（包含 prefix）。"""
        return self.prefix.to_messages() + self.log.snapshot()

    def get_stats(self) -> dict:
        """获取缓存和成本统计。"""
        total_hit = self.cache_stats["total_hit_tokens"]
        total_miss = self.cache_stats["total_miss_tokens"]
        total = total_hit + total_miss
        return {
            "turns": self.cache_stats["turns"],
            "total_cost_usd": round(self.cache_stats["total_cost_usd"], 6),
            "cache_hit_tokens": total_hit,
            "cache_miss_tokens": total_miss,
            "cache_hit_rate": round(total_hit / total * 100, 2) if total > 0 else 0,
            "prefix_fingerprint": self.prefix.fingerprint,
            "warmup_done": self._warmup_done,
            "warmup_model": self._warmup_model_id,
            "session_persist": self.session_persist,
            "session_length": len(self.log),
            "cost_control": self.cost_controller.get_stats(),
        }

    def __repr__(self):
        return (
            f"CacheFirstLoop(llm={self.llm}, tools={len(self.tools.list_tools())}, "
            f"prefix={self.prefix.fingerprint}, warmup={self._warmup_done}, "
            f"persist={self.session_persist})"
        )
