"""A2A 数据模型"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import uuid


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass
class TextPart:
    type: str = "text"
    text: str = ""

    def to_dict(self):
        return {"type": self.type, "text": self.text}


@dataclass
class FilePart:
    type: str = "file"
    file_name: str = ""
    mime_type: str = ""
    bytes: str = ""  # base64

    def to_dict(self):
        return {"type": self.type, "file": {"name": self.file_name, "mimeType": self.mime_type, "bytes": self.bytes}}


Part = TextPart | FilePart


@dataclass
class Message:
    role: str  # user | agent
    parts: list[Part] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "role": self.role,
            "parts": [p.to_dict() for p in self.parts],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        parts = []
        for p in data.get("parts", []):
            if p.get("type") == "text":
                parts.append(TextPart(text=p.get("text", "")))
            elif p.get("type") == "file":
                f = p.get("file", {})
                parts.append(FilePart(file_name=f.get("name", ""), mime_type=f.get("mimeType", ""), bytes=f.get("bytes", "")))
        return cls(role=data.get("role", "user"), parts=parts, metadata=data.get("metadata", {}))


@dataclass
class Artifact:
    name: str = ""
    parts: list[Part] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "name": self.name,
            "parts": [p.to_dict() for p in self.parts],
            "metadata": self.metadata,
        }


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskStatus = TaskStatus.SUBMITTED
    message: Optional[Message] = None
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    history: list[Message] = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status.value,
            "message": self.message.to_dict() if self.message else None,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "metadata": self.metadata,
            "history": [m.to_dict() for m in self.history],
        }


@dataclass
class AgentSkill:
    id: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "description": self.description, "tags": self.tags, "examples": self.examples}


@dataclass
class AgentCard:
    name: str = ""
    description: str = ""
    url: str = ""
    version: str = "1.0"
    capabilities: dict = field(default_factory=lambda: {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    })
    skills: list[AgentSkill] = field(default_factory=list)
    default_input_modes: list[str] = field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text"])

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": [s.to_dict() for s in self.skills],
            "defaultInputModes": self.default_input_modes,
            "defaultOutputModes": self.default_output_modes,
        }
