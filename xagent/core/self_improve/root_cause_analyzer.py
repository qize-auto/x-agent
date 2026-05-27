"""
RootCauseAnalyzer
=================
用 cheap model 分析失败根因，生成结构化 critique。

设计原则：
- 仅在高频失败或规则分类器无法确定时调用
- 使用配置中的 cheap_model_id（默认 deepseek-chat）
- 输出结构化，便于 PromptEvolver 消费
"""
from __future__ import annotations
import json
from typing import Optional

from ..llm_client import LLMClient


ROOT_CAUSE_PROMPT = """You are an expert Agent debugger. Analyze the following failure and identify the root cause.

Failure Type: {failure_type}
Evidence: {evidence}
Tool Schema (relevant):
{tool_schema}

Original System Prompt (first 500 chars):
{system_prompt_preview}

Recent Conversation Context:
{context_preview}

Output a JSON object with:
- "root_cause": one of ["prompt_ambiguity", "schema_too_complex", "missing_examples", "tool_description_unclear", "context_window_issue", "model_capability_limit", "environment_state", "other"]
- "explanation": brief explanation in 1-2 sentences
- "suggested_fix_category": one of ["clarify_prompt", "simplify_schema", "add_examples", "improve_tool_desc", "compress_context", "upgrade_model", "add_precondition_check", "other"]
- "confidence": 0.0-1.0

Be concise."""


class RootCauseAnalyzer:
    """
    根因分析器。

    用法:
        analyzer = RootCauseAnalyzer(llm_client)
        result = analyzer.analyze(failure_type="TOOL_SCHEMA_MISMATCH", evidence="...")
        # result: {"root_cause": "schema_too_complex", "explanation": "...", ...}
    """

    def __init__(self, llm_client: LLMClient, cheap_model_id: str = None):
        self.llm = llm_client
        self.cheap_model_id = cheap_model_id or "deepseek/deepseek-chat"

    def analyze(self, failure_type: str, evidence: str,
                tool_schema: dict = None, system_prompt: str = "",
                context: list[dict] = None) -> dict:
        """
        分析失败根因。

        Args:
            failure_type: FailureType.name
            evidence: 分类器提供的证据
            tool_schema: 相关工具的 schema（可选）
            system_prompt: 当前 system prompt（可选）
            context: 最近几轮对话（可选）

        Returns:
            {"root_cause", "explanation", "suggested_fix_category", "confidence"}
        """
        tool_schema_str = json.dumps(tool_schema, ensure_ascii=False)[:800] if tool_schema else "N/A"
        context_preview = ""
        if context:
            for m in context[-4:]:
                role = m.get("role", "?")
                content = m.get("content", "")[:150]
                context_preview += f"{role}: {content}\n"

        prompt = ROOT_CAUSE_PROMPT.format(
            failure_type=failure_type,
            evidence=evidence[:500],
            tool_schema=tool_schema_str,
            system_prompt_preview=system_prompt[:500],
            context_preview=context_preview or "N/A",
        )

        try:
            resp = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model_id=self.cheap_model_id,
            )
            content = resp.content.strip()
            # 提取 JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            result = json.loads(content)
            # 校验字段
            return {
                "root_cause": result.get("root_cause", "other"),
                "explanation": result.get("explanation", ""),
                "suggested_fix_category": result.get("suggested_fix_category", "other"),
                "confidence": float(result.get("confidence", 0.5)),
            }
        except Exception:
            # 降级：返回通用根因
            return {
                "root_cause": "other",
                "explanation": f"Analysis failed. Failure type: {failure_type}, evidence: {evidence[:200]}",
                "suggested_fix_category": "other",
                "confidence": 0.3,
            }
