"""
PromptEvolver
=============
基于失败经验生成 prompt 补丁，通过 ShadowEval 评估后安全上线。

核心流程:
1. 从 ExperienceBank 读取高频失败
2. 生成 3 个候选 prompt 补丁
3. ShadowEval: 用历史失败样本重跑评估
4. 评分超过基线则接受，否则丢弃
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional

from ..llm_client import LLMClient


PATCH_GENERATOR_PROMPT = """You are an expert prompt engineer. Based on the following failure analysis, generate 3 improved versions of the system prompt.

Current System Prompt:
```
{current_prompt}
```

Failure Analysis:
- Type: {failure_type}
- Root Cause: {root_cause}
- Explanation: {explanation}
- Fix Category: {suggested_fix_category}

Requirements:
1. Each version should address the root cause specifically
2. Keep the prompt under 3000 tokens
3. Do not remove existing safety rules or core instructions
4. Add concrete examples if the fix category is "add_examples"
5. Simplify wording if the fix category is "clarify_prompt"
6. Add tool preconditions if the fix category is "add_precondition_check"

Output a JSON array of 3 objects:
[
  {{"version": "v1", "prompt": "...", "rationale": "..."}},
  {{"version": "v2", "prompt": "...", "rationale": "..."}},
  {{"version": "v3", "prompt": "...", "rationale": "..."}}
]
"""

SHADOW_EVAL_PROMPT = """Evaluate whether the following agent response successfully completes the task.

Task: {task}
Expected behavior: {expected_behavior}

Agent Response:
```
{response}
```

Output JSON: {{"success": true/false, "issues": ["..."], "score": 0-100}}
"""


class PromptEvolver:
    """
    Prompt 进化器。

    用法:
        evolver = PromptEvolver(llm_client, experience_bank)
        result = evolver.evolve(current_prompt, experience_record)
        if result["accepted"]:
            new_prompt = result["best_prompt"]
    """

    def __init__(self, llm_client: LLMClient, experience_bank=None,
                 cheap_model_id: str = None, prompt_dir: str = None):
        self.llm = llm_client
        self.experience_bank = experience_bank
        self.cheap_model_id = cheap_model_id or "deepseek/deepseek-chat"
        self.prompt_dir = Path(prompt_dir) if prompt_dir else Path.home() / ".xagent" / "prompt_evolution"
        self.prompt_dir.mkdir(parents=True, exist_ok=True)

    def evolve(self, current_prompt: str, experience: dict) -> dict:
        """
        基于一条失败经验尝试进化 prompt。

        Args:
            current_prompt: 当前 system prompt
            experience: ExperienceRecord 的字典表示

        Returns:
            {"accepted": bool, "best_prompt": str, "score": float, "baseline_score": float}
        """
        failure_type = experience.get("failure_type", "UNKNOWN")
        root_cause = experience.get("root_cause", "")
        explanation = experience.get("explanation", "")
        fix_category = experience.get("suggested_fix_category", "other")

        # Step 1: 生成候选补丁
        candidates = self._generate_candidates(
            current_prompt, failure_type, root_cause, explanation, fix_category
        )
        if not candidates:
            return {"accepted": False, "best_prompt": current_prompt, "score": 0, "baseline_score": 0}

        # Step 2: ShadowEval（简化版：用 cheap model 评估 prompt 质量）
        baseline_score = self._eval_prompt_quality(current_prompt, experience)
        best_candidate = None
        best_score = baseline_score

        for cand in candidates:
            score = self._eval_prompt_quality(cand["prompt"], experience)
            cand["score"] = score
            if score > best_score:
                best_score = score
                best_candidate = cand

        # Step 3: 决策（阈值：比基线高 10%）
        if best_candidate and best_score >= baseline_score * 1.1:
            self._save_prompt_version(best_candidate["prompt"], best_candidate["rationale"], experience)
            if self.experience_bank:
                self.experience_bank.increment_hit(experience.get("id", 0))
            return {
                "accepted": True,
                "best_prompt": best_candidate["prompt"],
                "score": best_score,
                "baseline_score": baseline_score,
                "rationale": best_candidate["rationale"],
            }

        return {
            "accepted": False,
            "best_prompt": current_prompt,
            "score": best_score,
            "baseline_score": baseline_score,
        }

    def _generate_candidates(self, current_prompt: str, failure_type: str,
                             root_cause: str, explanation: str, fix_category: str) -> list[dict]:
        """生成 3 个候选 prompt"""
        prompt = PATCH_GENERATOR_PROMPT.format(
            current_prompt=current_prompt,
            failure_type=failure_type,
            root_cause=root_cause,
            explanation=explanation,
            suggested_fix_category=fix_category,
        )
        try:
            resp = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model_id=self.cheap_model_id,
            )
            content = resp.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            candidates = json.loads(content)
            if isinstance(candidates, list) and len(candidates) > 0:
                return candidates
        except Exception:
            pass
        return []

    def _eval_prompt_quality(self, prompt_text: str, experience: dict = None) -> float:
        """
        简化评估：用启发式给 prompt 打分。
        experience 为 None 时做通用评分。
        """
        experience = experience or {}
        score = 50.0  # 基线

        # 1. 长度适中（1000-3000 字符）
        length = len(prompt_text)
        if 1000 <= length <= 3000:
            score += 10
        elif length > 5000:
            score -= 10
        # 硬上限：超过 8000 直接判负分
        if length > 8000:
            score -= 50

        # 2. 包含具体示例（fix_category 相关）
        if experience.get("suggested_fix_category") == "add_examples" and "```" in prompt_text:
            score += 15

        # 3. 包含前置条件检查
        if experience.get("suggested_fix_category") == "add_precondition_check" and "before" in prompt_text.lower():
            score += 15

        # 4. 清晰性指标：避免模糊词汇
        vague_words = ["maybe", "perhaps", "sometimes", "try to", "should"]
        vague_count = sum(prompt_text.lower().count(w) for w in vague_words)
        score -= vague_count * 3

        # 5. 安全性保留检查
        if "dangerous" in prompt_text.lower() and "confirmation" in prompt_text.lower():
            score += 10

        return max(0.0, min(100.0, score))

    def _save_prompt_version(self, prompt_text: str, rationale: str, experience: dict):
        """保存 prompt 版本到磁盘"""
        timestamp = time.time_ns()
        filename = self.prompt_dir / f"prompt_v{timestamp}.json"
        data = {
            "timestamp": timestamp,
            "prompt": prompt_text,
            "rationale": rationale,
            "triggered_by": {
                "failure_type": experience.get("failure_type"),
                "root_cause": experience.get("root_cause"),
            },
        }
        try:
            filename.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def list_versions(self) -> list[dict]:
        """列出所有保存的 prompt 版本"""
        versions = []
        for f in sorted(self.prompt_dir.glob("prompt_v*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["filename"] = f.name
                versions.append(data)
            except Exception:
                pass
        return versions

    def save_baseline(self, baseline_prompt: str):
        """保存出厂基线 prompt，用于回退"""
        baseline_file = self.prompt_dir / "prompt_baseline.json"
        try:
            baseline_file.write_text(
                json.dumps({"prompt": baseline_prompt, "saved_at": time.time()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def load_baseline(self) -> Optional[str]:
        """加载出厂基线 prompt"""
        baseline_file = self.prompt_dir / "prompt_baseline.json"
        if baseline_file.exists():
            try:
                data = json.loads(baseline_file.read_text(encoding="utf-8"))
                return data.get("prompt")
            except Exception:
                pass
        return None

    def _check_prompt_format(self, prompt_text: str) -> bool:
        """检查 prompt 是否包含必要的格式占位符，确保 .format() 不会失败"""
        required_placeholders = ["{os_name}", "{project_root}", "{cwd}"]
        for ph in required_placeholders:
            if ph not in prompt_text:
                return False
        # 尝试 format 验证
        try:
            prompt_text.format(os_name="test", project_root="/tmp", cwd="/tmp")
            return True
        except Exception:
            return False

    def load_best_prompt(self, baseline_prompt: str) -> str:
        """
        加载最佳 prompt：如果保存的版本优于基线且通过校验则使用，否则使用基线。
        同时保存基线（首次调用时）。
        """
        self.save_baseline(baseline_prompt)

        versions = self.list_versions()
        if not versions:
            return baseline_prompt

        latest = versions[0]
        latest_prompt = latest.get("prompt", "")

        # 硬上限：超过 8000 字符直接拒绝并清理
        if len(latest_prompt) > 8000:
            try:
                Path(self.prompt_dir / latest["filename"]).unlink()
            except Exception:
                pass
            return baseline_prompt

        # 格式兼容性检查
        if not self._check_prompt_format(latest_prompt):
            return baseline_prompt

        # 评分对比
        baseline_score = self._eval_prompt_quality(baseline_prompt)
        latest_score = self._eval_prompt_quality(latest_prompt)

        if latest_score >= baseline_score * 1.05:
            return latest_prompt
        return baseline_prompt

    def rollback(self) -> Optional[str]:
        """
        回滚到上一个可用版本。如果没有进化版本，回退到基线。
        Returns: 回滚后的 prompt 文本，或 None。
        """
        versions = self.list_versions()
        if len(versions) >= 2:
            # 删除最新版本，返回上一个
            latest = versions[0]
            try:
                Path(self.prompt_dir / latest["filename"]).unlink()
            except Exception:
                pass
            return versions[1].get("prompt")
        # 没有进化版本或只剩一个，回退到基线
        return self.load_baseline()
