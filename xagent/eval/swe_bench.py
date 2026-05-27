"""SWE-bench 数据集加载与管理"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SWEBenchInstance:
    """单个 SWE-bench 实例"""
    instance_id: str
    repo: str
    base_commit: str
    patch: str = ""          # 正确修复 patch（golden）
    test_patch: str = ""     # 测试 patch
    problem_statement: str = ""
    hints_text: str = ""
    created_at: str = ""
    version: str = ""
    
    # 环境信息
    environment_setup_commit: str = ""
    
    @property
    def repo_name(self) -> str:
        """返回 repo 名称，如 django/django"""
        return self.repo
    
    @property
    def short_id(self) -> str:
        """返回简写 ID"""
        return self.instance_id.split("-")[-1] if "-" in self.instance_id else self.instance_id


class SWEBenchDataset:
    """SWE-bench 数据集加载器"""

    def __init__(self, instances: list[SWEBenchInstance] = None):
        self.instances = instances or []

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SWEBenchDataset":
        """从 JSONL 文件加载"""
        instances = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                instances.append(SWEBenchInstance(
                    instance_id=data.get("instance_id", ""),
                    repo=data.get("repo", ""),
                    base_commit=data.get("base_commit", ""),
                    patch=data.get("patch", ""),
                    test_patch=data.get("test_patch", ""),
                    problem_statement=data.get("problem_statement", ""),
                    hints_text=data.get("hints_text", ""),
                    created_at=data.get("created_at", ""),
                    version=data.get("version", ""),
                    environment_setup_commit=data.get("environment_setup_commit", ""),
                ))
        return cls(instances)

    @classmethod
    def from_json(cls, path: str | Path) -> "SWEBenchDataset":
        """从 JSON 文件加载（数组格式）"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("instances", [])
        instances = []
        for item in data:
            instances.append(SWEBenchInstance(
                instance_id=item.get("instance_id", ""),
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                patch=item.get("patch", ""),
                test_patch=item.get("test_patch", ""),
                problem_statement=item.get("problem_statement", ""),
                hints_text=item.get("hints_text", ""),
                created_at=item.get("created_at", ""),
                version=item.get("version", ""),
                environment_setup_commit=item.get("environment_setup_commit", ""),
            ))
        return cls(instances)

    @classmethod
    def from_list(cls, data: list[dict]) -> "SWEBenchDataset":
        """从字典列表加载"""
        instances = []
        for item in data:
            instances.append(SWEBenchInstance(
                instance_id=item.get("instance_id", ""),
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                patch=item.get("patch", ""),
                test_patch=item.get("test_patch", ""),
                problem_statement=item.get("problem_statement", ""),
            ))
        return cls(instances)

    def __len__(self) -> int:
        return len(self.instances)

    def __iter__(self):
        return iter(self.instances)

    def filter_by_repo(self, repo: str) -> "SWEBenchDataset":
        """按仓库过滤"""
        return SWEBenchDataset([i for i in self.instances if i.repo == repo])

    def sample(self, n: int, seed: int = 42) -> "SWEBenchDataset":
        """随机采样 n 个实例"""
        import random
        rng = random.Random(seed)
        sampled = rng.sample(self.instances, min(n, len(self.instances)))
        return SWEBenchDataset(sampled)
