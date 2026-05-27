"""
Agent Card
==========
A2A 协议中的能力声明文档。
描述 Agent 的名称、版本、技能、端点和认证要求。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentCard:
    """
    A2A Agent Card。

    参考: https://github.com/google/A2A/blob/main/specification.md
    """
    name: str
    description: str
    version: str = "1.0.0"
    url: str = ""
    skills: list[dict] = field(default_factory=list)
    authentication: dict = field(default_factory=dict)
    default_input_modes: list[str] = field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text"])

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "url": self.url,
            "skills": self.skills,
            "authentication": self.authentication,
            "defaultInputModes": self.default_input_modes,
            "defaultOutputModes": self.default_output_modes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentCard":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            url=data.get("url", ""),
            skills=data.get("skills", []),
            authentication=data.get("authentication", {}),
            default_input_modes=data.get("defaultInputModes", ["text"]),
            default_output_modes=data.get("defaultOutputModes", ["text"]),
        )
