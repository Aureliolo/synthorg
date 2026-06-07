"""Unit tests for build_agent_contributions factory function."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.coordination.attribution import build_agent_contributions
from synthorg.engine.coordination.models import CoordinationWave
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.parallel_models import (
    AgentOutcome,
    ParallelExecutionResult,
)
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.routing.models import (
    RoutingCandidate,
    RoutingDecision,
    RoutingResult,
)
from synthorg.engine.run_result import AgentRunResult
from synthorg.hr.seniority import SeniorityLevel


def _make_identity(name: str = "test-agent", **kwargs: object) -> AgentIdentity:
    defaults: dict[str, object] = {
        "role": "engineer",
        "department": "engineering",
        "level": SeniorityLevel.MID,
        "hiring_date": date(2026, 1, 15),
        "personality": PersonalityConfig(traits=("analytical",)),
        "model": ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
    }
    defaults.update(kwargs)
    return AgentIdentity(name=name, **defaults)  # type: ignore[arg-type]


def _make_task(
    title: str = "test-task",
    task_id: str | None = None,
    **kwargs: object,
) -> Task:
    defaults: dict[str, object] = {
        "id": task_id or f"task-{title}",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "priority": Priority.MEDIUM,
        "project": "test-project",
        "created_by": "tester",
        "assigned_to": "test-agent",
        "status": TaskStatus.ASSIGNED,
        "estimated_complexity": Complexity.SIMPLE,
    }
    defaults.update(kwargs)
    return Task(title=title, **defaults)  # type: ignore[arg-type]


def _make_run_result(
    agent_id: str,
    task_id: str,
    *,
    reason: TerminationReason = TerminationReason.COMPLETED,
) -> AgentRunResult:
    identity = _make_identity(agent_id)
    task = _make_task(agent_id, task_id=task_id, assigned_to=agent_id)
    ctx = AgentContext.from_identity(identity, task=task)
    error_msg = "test error" if reason == TerminationReason.ERROR else None
    return AgentRunResult(
        execution_result=ExecutionResult(
            context=ctx,
            termination_reason=reason,
            error_message=error_msg,
        ),
        system_prompt=SystemPrompt(
            content="test",
            template_version="1.0",
            estimated_tokens=1,
            sections=("identity",),
            metadata={"agent_id": agent_id},
        ),
        duration_seconds=1.0,
        agent_id=agent_id,
        task_id=task_id,
        currency="USD",
    )


def _make_routing_result(
    agent_subtask_pairs: list[tuple[str, str]],
    *,
    parent_task_id: str = "parent-1",
) -> RoutingResult:
    decisions = tuple(
        RoutingDecision(
            subtask_id=NotBlankStr(subtask_id),
            selected_candidate=RoutingCandidate(
                agent_identity=_make_identity(agent_id),
                score=0.9,
                matched_skills=(),
                reason=NotBlankStr("test routing"),
            ),
            topology=CoordinationTopology.SAS,
        )
        for agent_id, subtask_id in agent_subtask_pairs
    )
    return RoutingResult(
        parent_task_id=NotBlankStr(parent_task_id),
        decisions=decisions,
    )


def _make_successful_outcome(
    agent_id: str,
    task_id: str,
) -> AgentOutcome:
    return AgentOutcome(
        task_id=NotBlankStr(task_id),
        agent_id=NotBlankStr(agent_id),
        result=_make_run_result(agent_id, task_id),
    )


def _make_failed_outcome(
    agent_id: str,
    task_id: str,
    *,
    error: str = "tool invocation failed: timeout",
) -> AgentOutcome:
    return AgentOutcome(
        task_id=NotBlankStr(task_id),
        agent_id=NotBlankStr(agent_id),
        error=error,
    )


def _make_terminated_outcome(
    agent_id: str,
    task_id: str,
    *,
    reason: TerminationReason = TerminationReason.STAGNATION,
) -> AgentOutcome:
    return AgentOutcome(
        task_id=NotBlankStr(task_id),
        agent_id=NotBlankStr(agent_id),
        result=_make_run_result(agent_id, task_id, reason=reason),
    )


def _make_waves(
    outcomes: list[AgentOutcome],
    *,
    group_id: str = "group-1",
) -> tuple[CoordinationWave, ...]:
    return (
        CoordinationWave(
            wave_index=0,
            subtask_ids=tuple(NotBlankStr(o.task_id) for o in outcomes),
            execution_result=ParallelExecutionResult(
                group_id=NotBlankStr(group_id),
                outcomes=tuple(outcomes),
                total_duration_seconds=2.0,
            ),
        ),
    )


class TestBuildAgentContributions:
    """Tests for the build_agent_contributions factory."""

    @pytest.mark.unit
    def test_all_success(self) -> None:
        """All agents succeed: score 1.0, no failure attribution."""
        routing = _make_routing_result(
            [
                ("agent-1", "sub-1"),
                ("agent-2", "sub-2"),
            ]
        )
        waves = _make_waves(
            [
                _make_successful_outcome("agent-1", "sub-1"),
                _make_successful_outcome("agent-2", "sub-2"),
            ]
        )

        contribs = build_agent_contributions(routing, waves)

        assert len(contribs) == 2
        for c in contribs:
            assert c.contribution_score == 1.0
            assert c.failure_attribution is None

    @pytest.mark.unit
    def test_all_failed_with_errors(self) -> None:
        """All agents fail with errors: score 0.0, direct attribution."""
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = _make_waves(
            [
                _make_failed_outcome("agent-1", "sub-1", error="tool execution failed"),
            ]
        )

        contribs = build_agent_contributions(routing, waves)

        assert len(contribs) == 1
        assert contribs[0].contribution_score == 0.0
        assert contribs[0].failure_attribution == "direct"
        assert contribs[0].evidence is not None

    @pytest.mark.unit
    def test_stagnation_termination(self) -> None:
        """Agent terminated by stagnation: score 0.0, direct."""
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = _make_waves(
            [
                _make_terminated_outcome(
                    "agent-1",
                    "sub-1",
                    reason=TerminationReason.STAGNATION,
                ),
            ]
        )

        contribs = build_agent_contributions(routing, waves)

        assert len(contribs) == 1
        assert contribs[0].contribution_score == 0.0
        assert contribs[0].failure_attribution == "direct"

    @pytest.mark.unit
    def test_budget_exhausted_termination(self) -> None:
        """Agent ran out of budget: coordination_overhead."""
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = _make_waves(
            [
                _make_terminated_outcome(
                    "agent-1",
                    "sub-1",
                    reason=TerminationReason.BUDGET_EXHAUSTED,
                ),
            ]
        )

        contribs = build_agent_contributions(routing, waves)

        assert len(contribs) == 1
        assert contribs[0].contribution_score == 0.0
        assert contribs[0].failure_attribution == "coordination_overhead"

    @pytest.mark.unit
    def test_mixed_outcomes(self) -> None:
        """Mix of success and failure: different scores and attributions."""
        routing = _make_routing_result(
            [
                ("agent-1", "sub-1"),
                ("agent-2", "sub-2"),
            ]
        )
        waves = _make_waves(
            [
                _make_successful_outcome("agent-1", "sub-1"),
                _make_failed_outcome("agent-2", "sub-2", error="budget exceeded"),
            ]
        )

        contribs = build_agent_contributions(routing, waves)

        by_agent = {c.agent_id: c for c in contribs}
        assert by_agent["agent-1"].contribution_score == 1.0
        assert by_agent["agent-2"].contribution_score == 0.0

    @pytest.mark.unit
    def test_empty_waves(self) -> None:
        """No waves: empty contributions."""
        routing = _make_routing_result([("agent-1", "sub-1")])
        contribs = build_agent_contributions(routing, ())
        assert contribs == ()

    @pytest.mark.unit
    def test_wave_without_execution_result(self) -> None:
        """Wave with no execution result: skipped."""
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = (
            CoordinationWave(
                wave_index=0,
                subtask_ids=(NotBlankStr("sub-1"),),
                execution_result=None,
            ),
        )
        contribs = build_agent_contributions(routing, waves)
        assert contribs == ()

    @pytest.mark.unit
    def test_evidence_truncated(self) -> None:
        """Long error messages are truncated to 500 chars."""
        long_error = "x" * 1000
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = _make_waves(
            [
                _make_failed_outcome("agent-1", "sub-1", error=long_error),
            ]
        )

        contribs = build_agent_contributions(routing, waves)

        assert contribs[0].evidence is not None
        assert len(contribs[0].evidence) <= 500

    @pytest.mark.unit
    def test_returns_tuple(self) -> None:
        """Return type is a tuple (immutable)."""
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = _make_waves([_make_successful_outcome("agent-1", "sub-1")])
        contribs = build_agent_contributions(routing, waves)
        assert isinstance(contribs, tuple)

    @pytest.mark.unit
    def test_quality_gate_error_classification(self) -> None:
        """Error containing 'quality' is classified as quality_gate."""
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = _make_waves(
            [
                _make_failed_outcome(
                    "agent-1",
                    "sub-1",
                    error="quality criteria not met",
                ),
            ]
        )

        contribs = build_agent_contributions(routing, waves)

        assert contribs[0].failure_attribution == "quality_gate"

    @pytest.mark.unit
    def test_multiple_waves(self) -> None:
        """Contributions collected from multiple waves."""
        routing = _make_routing_result(
            [
                ("agent-1", "sub-1"),
                ("agent-2", "sub-2"),
            ]
        )
        wave1 = CoordinationWave(
            wave_index=0,
            subtask_ids=(NotBlankStr("sub-1"),),
            execution_result=ParallelExecutionResult(
                group_id=NotBlankStr("g1"),
                outcomes=(_make_successful_outcome("agent-1", "sub-1"),),
                total_duration_seconds=1.0,
            ),
        )
        wave2 = CoordinationWave(
            wave_index=1,
            subtask_ids=(NotBlankStr("sub-2"),),
            execution_result=ParallelExecutionResult(
                group_id=NotBlankStr("g2"),
                outcomes=(_make_failed_outcome("agent-2", "sub-2"),),
                total_duration_seconds=1.0,
            ),
        )

        contribs = build_agent_contributions(routing, (wave1, wave2))

        assert len(contribs) == 2
        by_agent = {c.agent_id: c for c in contribs}
        assert by_agent["agent-1"].contribution_score == 1.0
        assert by_agent["agent-2"].contribution_score == 0.0

    @pytest.mark.unit
    def test_unmapped_agent_uses_task_id_as_subtask(self) -> None:
        """Agent not in routing decisions falls back to task_id."""
        # Route only agent-1, but include agent-2 in outcomes
        routing = _make_routing_result([("agent-1", "sub-1")])
        waves = _make_waves(
            [
                _make_successful_outcome("agent-1", "sub-1"),
                _make_successful_outcome("agent-2", "sub-2"),  # not in routing
            ]
        )
        contribs = build_agent_contributions(routing, waves)
        assert len(contribs) == 2
        by_agent = {c.agent_id: c for c in contribs}
        # Unmapped agent falls back to using task_id as subtask_id
        assert by_agent["agent-2"].subtask_id == "sub-2"
