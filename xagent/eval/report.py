"""评估报告生成器"""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .runner import EvalResult


class ReportGenerator:
    """生成 SWE-bench 评估报告"""

    def __init__(self, results: list[EvalResult]):
        self.results = results

    def summary(self) -> dict:
        """返回统计摘要"""
        total = len(self.results)
        resolved = sum(1 for r in self.results if r.status == "resolved")
        failed = sum(1 for r in self.results if r.status == "failed")
        errors = sum(1 for r in self.results if r.status == "error")
        skipped = sum(1 for r in self.results if r.status == "skipped")
        total_time = sum(r.duration_sec for r in self.results)

        return {
            "total": total,
            "resolved": resolved,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "resolution_rate": round(resolved / total, 4) if total > 0 else 0.0,
            "avg_duration_sec": round(total_time / total, 2) if total > 0 else 0.0,
            "total_duration_sec": round(total_time, 2),
        }

    def to_markdown(self) -> str:
        """生成 Markdown 格式报告"""
        stats = self.summary()
        lines = [
            "# SWE-bench 评估报告",
            "",
            "## 统计摘要",
            "",
            f"- **总实例数**: {stats['total']}",
            f"- **已修复 (resolved)**: {stats['resolved']} ({stats['resolution_rate']:.1%})",
            f"- **失败 (failed)**: {stats['failed']}",
            f"- **错误 (error)**: {stats['errors']}",
            f"- **跳过 (skipped)**: {stats['skipped']}",
            f"- **平均耗时**: {stats['avg_duration_sec']}s",
            f"- **总耗时**: {stats['total_duration_sec']}s",
            "",
            "## 详细结果",
            "",
            "| 实例 ID | 状态 | 耗时 (s) | 错误 |",
            "|---------|------|----------|------|",
        ]

        for r in self.results:
            err = r.error[:40] + "..." if len(r.error) > 40 else r.error
            lines.append(f"| {r.instance_id} | {r.status} | {r.duration_sec:.1f} | {err} |")

        lines.append("")
        return "\n".join(lines)

    def to_json(self, path: Optional[str] = None) -> str:
        """生成 JSON 格式报告"""
        data = {
            "summary": self.summary(),
            "results": [asdict(r) for r in self.results],
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if path:
            Path(path).write_text(text, encoding="utf-8")
        return text

    def print_summary(self):
        """打印摘要到控制台"""
        stats = self.summary()
        print("=" * 50)
        print("SWE-bench 评估摘要")
        print("=" * 50)
        print(f"总实例数:   {stats['total']}")
        print(f"已修复:     {stats['resolved']} ({stats['resolution_rate']:.1%})")
        print(f"失败:       {stats['failed']}")
        print(f"错误:       {stats['errors']}")
        print(f"平均耗时:   {stats['avg_duration_sec']}s")
        print("=" * 50)
