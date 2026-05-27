"""Tests for multi-agent swarm module."""
import pytest

from xagent.core.swarm.synthesizer import AgentSynthesizer, AgentRole, RoleConfig
from xagent.core.swarm.team import AgentTeam, AgentResult
from xagent.core.swarm.consensus import ConsensusEngine, AgentProposal
from xagent.core.task import TaskPlan, SubTask


class TestAgentSynthesizer:
    def test_coding_task(self):
        synth = AgentSynthesizer()
        plan = TaskPlan(goal="Implement a Flask API endpoint")
        plan.subtasks = [
            SubTask(id="1", description="Setup route", tool_hint="edit_file"),
            SubTask(id="2", description="Write tests", tool_hint="run_tests"),
        ]
        roles = synth.synthesize(plan)
        role_names = [r.role for r in roles]
        assert AgentRole.CODER in role_names
        assert AgentRole.REVIEWER in role_names

    def test_research_task(self):
        synth = AgentSynthesizer()
        plan = TaskPlan(goal="Research the best database for this project")
        roles = synth.synthesize(plan)
        role_names = [r.role for r in roles]
        assert AgentRole.RESEARCHER in role_names
        assert AgentRole.SYNTHESIZER in role_names

    def test_default_task(self):
        synth = AgentSynthesizer()
        plan = TaskPlan(goal="Something vague")
        roles = synth.synthesize(plan)
        assert len(roles) >= 1
        assert roles[0].system_prompt != ""

    def test_estimate_cost(self):
        synth = AgentSynthesizer()
        plan = TaskPlan(goal="Code task")
        plan.subtasks = [SubTask(id="1", description="A")]
        roles = synth.synthesize(plan)
        cost = synth.estimate_cost(roles, plan)
        assert cost["agent_count"] == len(roles)
        assert cost["estimated_cost_units"] > 0


class TestAgentTeam:
    def test_run_sequential(self):
        results_log = []
        def mock_execute(role, task):
            results_log.append(role)
            return f"Result from {role}"

        roles = [
            RoleConfig(AgentRole.CODER),
            RoleConfig(AgentRole.REVIEWER),
        ]
        team = AgentTeam(roles, execute_fn=mock_execute)
        plan = TaskPlan(goal="Test")
        results = team.run(plan)

        assert len(results) == 2
        assert results[0].role == AgentRole.CODER
        assert results[1].role == AgentRole.REVIEWER
        assert "Result from CODER" in results[0].content

    def test_synthesize_output(self):
        team = AgentTeam([])
        team.results = [
            AgentResult(AgentRole.CODER, "code"),
            AgentResult(AgentRole.SYNTHESIZER, "final answer"),
        ]
        assert team.synthesize_output() == "final answer"

    def test_synthesize_without_synthesizer(self):
        team = AgentTeam([])
        team.results = [
            AgentResult(AgentRole.CODER, "code"),
        ]
        assert team.synthesize_output() == "code"


class TestConsensusEngine:
    def test_majority_winner(self):
        engine = ConsensusEngine("majority")
        proposals = [
            AgentProposal(AgentRole.CODER, "A", confidence=0.9),
            AgentProposal(AgentRole.REVIEWER, "B", confidence=0.6),
        ]
        result = engine.vote(proposals)
        assert result["winner"].content == "A"
        assert result["consensus_reached"] is True

    def test_best_confidence(self):
        engine = ConsensusEngine("best_confidence")
        proposals = [
            AgentProposal(AgentRole.CODER, "A", confidence=0.5),
            AgentProposal(AgentRole.REVIEWER, "B", confidence=0.9),
        ]
        result = engine.vote(proposals)
        assert result["winner"].content == "B"

    def test_weighted_vote(self):
        engine = ConsensusEngine("weighted")
        proposals = [
            AgentProposal(AgentRole.CODER, "A", confidence=0.9),
            AgentProposal(AgentRole.ARCHITECT, "B", confidence=0.8),
        ]
        result = engine.vote(proposals)
        # Architect 权重更高 (2.0 vs 1.0)
        assert result["winner"].role == AgentRole.ARCHITECT

    def test_unanimous_pass(self):
        engine = ConsensusEngine("unanimous")
        proposals = [
            AgentProposal(AgentRole.CODER, "Same", confidence=0.9),
            AgentProposal(AgentRole.REVIEWER, "Same", confidence=0.9),
        ]
        result = engine.vote(proposals)
        assert result["consensus_reached"] is True
        assert result["winner"].content == "Same"

    def test_unanimous_fail(self):
        engine = ConsensusEngine("unanimous")
        proposals = [
            AgentProposal(AgentRole.CODER, "A", confidence=0.9),
            AgentProposal(AgentRole.REVIEWER, "B", confidence=0.9),
        ]
        result = engine.vote(proposals)
        assert result["consensus_reached"] is False
        assert result["winner"] is None

    def test_single_proposal(self):
        engine = ConsensusEngine("majority")
        result = engine.vote([AgentProposal(AgentRole.CODER, "A", 0.5)])
        assert result["consensus_reached"] is True

    def test_empty_proposals(self):
        engine = ConsensusEngine("majority")
        result = engine.vote([])
        assert result["winner"] is None
        assert result["consensus_reached"] is False
