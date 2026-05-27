"""
R1 Thought Harvesting
=====================
从 DeepSeek R1 的 reasoning_content 中提取结构化计划状态。

DeepSeek 官方建议不要把 reasoning_content 喂回模型。
但 reasoning trace 包含了丰富的子目标、假设、不确定性和已排除路径信息。
Harvesting 将这些非结构化思考提炼为结构化状态，供后续决策参考。

参考：DeepSeek-Reasonix 的 Thought Harvesting
"""
from __future__ import annotations
import json
import re
from typing import Optional

from .llm_client import LLMClient, LLMResponse


class ThoughtHarvester:
    """
    思考 harvester。

    用法:
        harvester = ThoughtHarvester(llm_client)
        state = harvester.harvest(reasoning_content)
        # state = {
        #   "subgoals": [...],
        #   "hypotheses": [...],
        #   "uncertainties": [...],
        #   "rejected_paths": [...],
        # }
    """

    _EXTRACTION_PROMPT = """Analyze the following reasoning trace and extract structured plan state.

Reasoning trace:
```
{reasoning}
```

Extract these fields as JSON:
- "subgoals": list of concrete intermediate objectives mentioned
- "hypotheses": list of candidate approaches being weighed
- "uncertainties": list of things flagged as unclear or needing verification
- "rejected_paths": list of approaches considered and abandoned

If a field has no entries, return an empty list. Respond ONLY with valid JSON."""

    def __init__(self, llm: LLMClient, model_id: Optional[str] = None):
        self.llm = llm
        # 默认使用便宜的 flash 模型做 harvesting
        self.model_id = model_id or "deepseek-v4-flash"

    def harvest(self, reasoning_content: str) -> dict:
        """
        从 reasoning_content 中提取结构化状态。

        Args:
            reasoning_content: R1 的思考过程文本

        Returns:
            结构化状态字典。如果提取失败，返回空字段的字典。
        """
        if not reasoning_content or len(reasoning_content) < 50:
            return self._empty_state()

        try:
            prompt = self._EXTRACTION_PROMPT.format(reasoning=reasoning_content[:8000])
            resp = self.llm.chat(
                [{"role": "user", "content": prompt}],
                model_id=self.model_id,
                max_tokens=1024,
            )
            return self._parse_harvest(resp.content)
        except Exception:
            # 提取失败时优雅降级
            return self._empty_state()

    def harvest_fast(self, reasoning_content: str) -> dict:
        """
        快速提取（规则基础，无 LLM 调用）。

        用 regex 和启发式规则从 reasoning 中提取关键信息。
        不消耗 API token，但准确率低于 LLM-based harvesting。
        """
        if not reasoning_content:
            return self._empty_state()

        state = self._empty_state()
        text = reasoning_content

        # 提取 "subgoal" / "goal" / "step" 后面的内容
        subgoal_patterns = [
            r'(?:subgoal|goal|objective|step)\s*\d*[:.)\s]+([^\n]+)',
            r'(?:首先|第一步|接下来|然后|最后)[:：，,\s]+([^。\n]+)',
        ]
        for pattern in subgoal_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                item = match.group(1).strip()
                if item and item not in state["subgoals"]:
                    state["subgoals"].append(item)

        # 提取 "hypothesis" / "approach" / "option"
        hypo_patterns = [
            r'(?:hypothesis|approach|option|candidate|method)\s*\d*[:.)\s]+([^\n]+)',
            r'(?:方案|方法|思路)\s*\d*[:：，,\s]+([^\n]+)',
        ]
        for pattern in hypo_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                item = match.group(1).strip()
                if item and item not in state["hypotheses"]:
                    state["hypotheses"].append(item)

        # 提取 "uncertain" / "unclear" / "not sure"
        uncert_patterns = [
            r'(?:uncertain|unclear|not sure|unknown|question|doubt)[:：，,\s]+([^\n]+)',
            r'(?:不确定|不清楚|疑问)[:：，,\s]+([^\n]+)',
        ]
        for pattern in uncert_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                item = match.group(1).strip()
                if item and item not in state["uncertainties"]:
                    state["uncertainties"].append(item)

        # 提取 "rejected" / "not work" / "abandoned"
        reject_patterns = [
            r'(?:rejected|abandoned|not work|doesn\'t work|ruled out)[:：，,\s]+([^\n]+)',
            r'(?:排除|放弃|不行|无效)[:：，,\s]+([^\n]+)',
        ]
        for pattern in reject_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                item = match.group(1).strip()
                if item and item not in state["rejected_paths"]:
                    state["rejected_paths"].append(item)

        return state

    @staticmethod
    def _parse_harvest(content: str) -> dict:
        """解析 LLM 返回的 JSON"""
        # 尝试提取 JSON 代码块
        if "```json" in content:
            match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                content = match.group(1)
        elif "```" in content:
            match = re.search(r'```\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                content = match.group(1)

        content = content.strip()
        try:
            data = json.loads(content)
            return {
                "subgoals": data.get("subgoals", []),
                "hypotheses": data.get("hypotheses", []),
                "uncertainties": data.get("uncertainties", []),
                "rejected_paths": data.get("rejected_paths", []),
            }
        except json.JSONDecodeError:
            return ThoughtHarvester._empty_state()

    @staticmethod
    def _empty_state() -> dict:
        return {
            "subgoals": [],
            "hypotheses": [],
            "uncertainties": [],
            "rejected_paths": [],
        }
