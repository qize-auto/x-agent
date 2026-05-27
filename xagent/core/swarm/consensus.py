"""共识引擎

多 Agent 决策仲裁机制。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

from .synthesizer import AgentRole


@dataclass
class AgentProposal:
    """Agent 提出的方案"""
    role: AgentRole
    content: str
    confidence: float = 0.0


class ConsensusEngine:
    """
    多 Agent 共识引擎。

    支持策略：
    - majority: 多数决
    - weighted: 加权投票（Architect/Reviewer 权重更高）
    - unanimous: 全票通过（安全关键场景）
    - best_confidence: 选择置信度最高的方案
    """

    ROLE_WEIGHTS = {
        AgentRole.ARCHITECT: 2.0,
        AgentRole.REVIEWER: 1.5,
        AgentRole.TESTER: 1.0,
        AgentRole.CODER: 1.0,
        AgentRole.RESEARCHER: 0.8,
        AgentRole.SYNTHESIZER: 1.0,
    }

    def __init__(self, strategy: str = "majority"):
        self.strategy = strategy

    def vote(self, proposals: list[AgentProposal]) -> dict:
        """
        对多个方案进行投票。

        Returns:
            {"winner": AgentProposal, "consensus_reached": bool, "details": dict}
        """
        if not proposals:
            return {"winner": None, "consensus_reached": False, "details": {}}

        if len(proposals) == 1:
            return {"winner": proposals[0], "consensus_reached": True, "details": {"unanimous": True}}

        if self.strategy == "best_confidence":
            winner = max(proposals, key=lambda p: p.confidence)
            return {
                "winner": winner,
                "consensus_reached": winner.confidence >= 0.7,
                "details": {"strategy": "best_confidence", "scores": [(p.role.name, p.confidence) for p in proposals]},
            }

        if self.strategy == "weighted":
            scores = {}
            for p in proposals:
                weight = self.ROLE_WEIGHTS.get(p.role, 1.0)
                scores[p.role] = scores.get(p.role, 0) + p.confidence * weight
            winner_role = max(scores, key=scores.get)
            winner = next(p for p in proposals if p.role == winner_role)
            return {
                "winner": winner,
                "consensus_reached": True,
                "details": {"strategy": "weighted", "scores": {k.name: v for k, v in scores.items()}},
            }

        if self.strategy == "unanimous":
            # 简化：检查是否所有方案内容一致
            contents = [p.content.strip() for p in proposals]
            unanimous = all(c == contents[0] for c in contents)
            return {
                "winner": proposals[0] if unanimous else None,
                "consensus_reached": unanimous,
                "details": {"strategy": "unanimous", "agreement": unanimous},
            }

        # majority: 选择置信度最高的（简化实现）
        winner = max(proposals, key=lambda p: p.confidence)
        return {
            "winner": winner,
            "consensus_reached": winner.confidence >= 0.5,
            "details": {"strategy": "majority", "votes": [(p.role.name, p.confidence) for p in proposals]},
        }
