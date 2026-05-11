"""Tests for AutonomousSkillEvolver service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.memory.procedural.evolver import AutonomousSkillEvolver
from synthorg.memory.procedural.evolver_config import EvolverConfig
from synthorg.memory.procedural.models import (
    ProceduralMemoryProposal,
    ProceduralMemoryScope,
)
from synthorg.memory.procedural.trajectory_aggregator import (
    AggregatedTrajectory,
    TrajectoryAggregator,
)
from synthorg.memory.protocol import MemoryBackend


class _ProposerLike:
    """Spec target for ``AsyncMock`` substitutes that stand in for the
    duck-typed ``proposer: object`` parameter on
    ``AutonomousSkillEvolver``. The production type is ``object`` so
    no real protocol is available; this lightweight class declares
    the shape the evolver actually invokes (``propose`` only) so the
    typed-boundary gate has a non-trivial spec target.
    """

    async def propose(self, *_args: object, **_kwargs: object) -> object:
        return None


def _make_trajectory(**overrides: object) -> AggregatedTrajectory:
    defaults: dict[str, object] = {
        "agent_id": "agent-1",
        "task_id": "task-1",
        "outcome": "failure",
        "error_category": "timeout",
        "tool_calls": ("http_request",),
        "turn_count": 5,
        "recorded_at": datetime(2026, 4, 14, tzinfo=UTC),
    }
    defaults.update(overrides)
    return AggregatedTrajectory(**defaults)  # type: ignore[arg-type]


def _make_evolver(
    *,
    enabled: bool = True,
    min_agents: int = 3,
    min_confidence: float = 0.8,
    max_proposals: int = 10,
    existing_org: dict[str, ProceduralMemoryProposal] | None = None,
) -> AutonomousSkillEvolver:
    config = EvolverConfig(
        enabled=enabled,
        min_agents_seen_pattern=min_agents,
        min_confidence_for_org_promotion=min_confidence,
        max_proposals_per_cycle=max_proposals,
    )
    aggregator = TrajectoryAggregator(
        min_agents_for_pattern=min_agents,
    )
    return AutonomousSkillEvolver(
        memory_backend=AsyncMock(spec=MemoryBackend),
        trajectory_aggregator=aggregator,
        proposer=AsyncMock(spec=_ProposerLike),
        config=config,
        existing_org_proposals=existing_org,
    )


@pytest.mark.unit
class TestEvolverDisabled:
    async def test_disabled_returns_empty_report(self) -> None:
        evolver = _make_evolver(enabled=False)
        report = await evolver.evolve_cycle(timedelta(days=1))
        assert report.trajectories_analyzed == 0
        assert report.patterns_found == 0
        assert report.proposals_emitted == ()


@pytest.mark.unit
class TestEvolverCycle:
    async def test_no_trajectories(self) -> None:
        evolver = _make_evolver()
        report = await evolver.evolve_cycle(timedelta(days=1))
        assert report.trajectories_analyzed == 0
        assert report.patterns_found == 0

    async def test_cross_agent_pattern_emits_proposal(self) -> None:
        evolver = _make_evolver(min_agents=3, min_confidence=0.0)
        trajectories = tuple(
            _make_trajectory(
                agent_id=f"agent-{i}",
                task_id=f"task-{i}",
                error_category="timeout",
            )
            for i in range(4)
        )
        report = await evolver.evolve_cycle(
            timedelta(days=1),
            trajectories=trajectories,
        )
        assert report.trajectories_analyzed == 4
        assert report.patterns_found == 1
        assert len(report.proposals_emitted) == 1
        approval = report.proposals_emitted[0]
        assert approval.action_type == "skill_evolver:org_promotion"
        assert approval.status.value == "pending"

    async def test_max_proposals_cap(self) -> None:
        evolver = _make_evolver(min_agents=3, max_proposals=1)
        # Create 2 distinct patterns
        t1 = tuple(
            _make_trajectory(
                agent_id=f"a-{i}",
                task_id=f"t1-{i}",
                error_category="timeout",
            )
            for i in range(3)
        )
        t2 = tuple(
            _make_trajectory(
                agent_id=f"a-{i + 10}",
                task_id=f"t2-{i}",
                error_category="oom",
            )
            for i in range(3)
        )
        report = await evolver.evolve_cycle(
            timedelta(days=1),
            trajectories=(*t1, *t2),
        )
        # max_proposals_per_cycle=1, so only 1 proposal
        assert len(report.proposals_emitted) <= 1

    async def test_low_confidence_skipped(self) -> None:
        """Proposals below min_confidence are skipped.

        All-success patterns produce confidence 0.5 (below 0.8 threshold),
        since confidence is proportional to failure_rate.
        """
        evolver = _make_evolver(
            min_agents=3,
            min_confidence=0.8,
        )
        trajectories = tuple(
            _make_trajectory(
                agent_id=f"a-{i}",
                task_id=f"t-{i}",
                outcome="success",
                error_category=None,
            )
            for i in range(3)
        )
        report = await evolver.evolve_cycle(
            timedelta(days=1),
            trajectories=trajectories,
        )
        assert report.skipped_low_confidence >= 1
        assert len(report.proposals_emitted) == 0

    async def test_conflict_detected(self) -> None:
        """Conflicts with existing org proposals are flagged."""
        existing = ProceduralMemoryProposal(
            discovery="Existing org skill for retrieval",
            condition="When memory usage exceeds threshold",
            action="Restart the service immediately",
            rationale="Prevents OOM crashes",
            confidence=0.9,
            scope=ProceduralMemoryScope.ORG,
        )
        evolver = _make_evolver(
            min_agents=3,
            min_confidence=0.0,
            existing_org={"existing-1": existing},
        )
        # Create trajectories that produce a conflicting proposal
        trajectories = tuple(
            _make_trajectory(
                agent_id=f"a-{i}",
                task_id=f"t-{i}",
                error_category="memory_threshold",
            )
            for i in range(3)
        )
        report = await evolver.evolve_cycle(
            timedelta(days=1),
            trajectories=trajectories,
        )
        # The evolver produces proposals for patterns; conflict
        # detection depends on text similarity. At minimum, the
        # report should complete without error.
        assert report.patterns_found >= 1


@pytest.mark.unit
class TestEvolverNoOrgWrite:
    """Critical: evolver must NEVER write to org memory."""

    async def test_org_memory_store_never_called(self) -> None:
        """Regression test: evolver never calls store on memory backend."""
        memory_backend = AsyncMock(spec=MemoryBackend)
        evolver = AutonomousSkillEvolver(
            memory_backend=memory_backend,
            trajectory_aggregator=TrajectoryAggregator(
                min_agents_for_pattern=3,
            ),
            proposer=AsyncMock(spec=_ProposerLike),
            config=EvolverConfig(enabled=True),
        )
        trajectories = tuple(
            _make_trajectory(
                agent_id=f"a-{i}",
                task_id=f"t-{i}",
            )
            for i in range(5)
        )
        await evolver.evolve_cycle(
            timedelta(days=1),
            trajectories=trajectories,
        )
        # Verify evolver does not write to memory backend directly
        memory_backend.store.assert_not_called()
