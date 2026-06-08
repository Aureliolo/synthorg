"""Unit tests for training mode source selectors."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from synthorg.hr.enums import AgentStatus
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.source_selectors.composite import (
    CompositeSelector,
)
from synthorg.hr.training.source_selectors.department_diversity import (
    DepartmentDiversitySampling,
)
from synthorg.hr.training.source_selectors.role_top_performers import (
    RoleTopPerformers,
)
from synthorg.hr.training.source_selectors.user_curated import (
    UserCuratedList,
)


def _make_identity(
    *,
    role: str = "engineer",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.SENIOR,
    status: AgentStatus = AgentStatus.ACTIVE,
) -> MagicMock:
    """Create a mock AgentIdentity."""
    identity = MagicMock()
    identity.id = uuid4()
    identity.name = f"agent-{identity.id}"
    identity.role = role
    identity.department = department
    identity.level = level
    identity.status = status
    return identity


def _make_snapshot(*, quality_score: float = 0.8) -> MagicMock:
    """Create a mock AgentPerformanceSnapshot."""
    snapshot = MagicMock()
    snapshot.overall_quality_score = quality_score
    return snapshot


# -- RoleTopPerformers ------------------------------------------------


@pytest.mark.unit
class TestRoleTopPerformers:
    """RoleTopPerformers selector tests."""

    def test_name(self) -> None:
        selector = RoleTopPerformers(
            registry=AsyncMock(spec=AgentRegistryService),
            tracker=AsyncMock(spec=PerformanceTracker),
        )
        assert selector.name == "role_top_performers"

    async def test_selects_top_n_by_quality(self) -> None:
        agents = [
            _make_identity(role="engineer"),
            _make_identity(role="engineer"),
            _make_identity(role="engineer"),
            _make_identity(role="engineer"),
        ]
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = tuple(agents)

        tracker = AsyncMock(spec=PerformanceTracker)
        snapshots = [
            _make_snapshot(quality_score=0.5),
            _make_snapshot(quality_score=0.9),
            _make_snapshot(quality_score=0.7),
            _make_snapshot(quality_score=0.3),
        ]
        tracker.get_snapshot.side_effect = snapshots

        selector = RoleTopPerformers(
            registry=registry,
            tracker=tracker,
            top_n=2,
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(result) == 2
        # Top 2 by quality: agent[1] (0.9) and agent[2] (0.7)
        assert str(agents[1].id) in result
        assert str(agents[2].id) in result

    async def test_filters_by_role(self) -> None:
        agents = [
            _make_identity(role="engineer"),
            _make_identity(role="designer"),
            _make_identity(role="engineer"),
        ]
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = tuple(agents)

        tracker = AsyncMock(spec=PerformanceTracker)
        tracker.get_snapshot.side_effect = [
            _make_snapshot(quality_score=0.8),
            _make_snapshot(quality_score=0.6),
        ]

        selector = RoleTopPerformers(
            registry=registry,
            tracker=tracker,
            top_n=5,
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(result) == 2

    async def test_returns_empty_when_no_matching_agents(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = ()

        selector = RoleTopPerformers(
            registry=registry,
            tracker=AsyncMock(spec=PerformanceTracker),
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ()

    async def test_handles_none_quality_score(self) -> None:
        agents = [_make_identity(role="engineer")]
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = tuple(agents)

        tracker = AsyncMock(spec=PerformanceTracker)
        snapshot = _make_snapshot()
        snapshot.overall_quality_score = None
        tracker.get_snapshot.return_value = snapshot

        selector = RoleTopPerformers(
            registry=registry,
            tracker=tracker,
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        # Agent with None score should still be included (scored as 0.0)
        assert len(result) == 1

    async def test_default_top_n_is_three(self) -> None:
        agents = [_make_identity(role="eng") for _ in range(5)]
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_active.return_value = tuple(agents)

        tracker = AsyncMock(spec=PerformanceTracker)
        tracker.get_snapshot.side_effect = [
            _make_snapshot(quality_score=i * 0.2) for i in range(5)
        ]

        selector = RoleTopPerformers(
            registry=registry,
            tracker=tracker,
        )
        result = await selector.select(
            new_agent_role="eng",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(result) == 3


# -- DepartmentDiversitySampling -------------------------------------


@pytest.mark.unit
class TestDepartmentDiversitySampling:
    """DepartmentDiversitySampling selector tests."""

    def test_name(self) -> None:
        selector = DepartmentDiversitySampling(
            registry=AsyncMock(spec=AgentRegistryService),
            tracker=AsyncMock(spec=PerformanceTracker),
        )
        assert selector.name == "department_diversity"

    async def test_selects_from_department(self) -> None:
        same_role = [
            _make_identity(role="engineer", department="eng"),
            _make_identity(role="engineer", department="eng"),
        ]
        diff_role = [
            _make_identity(role="designer", department="eng"),
            _make_identity(role="qa", department="eng"),
        ]
        all_agents = same_role + diff_role
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_by_department.return_value = tuple(all_agents)

        tracker = AsyncMock(spec=PerformanceTracker)
        tracker.get_snapshot.side_effect = [
            _make_snapshot(quality_score=0.9),
            _make_snapshot(quality_score=0.7),
            _make_snapshot(quality_score=0.8),
            _make_snapshot(quality_score=0.6),
        ]

        selector = DepartmentDiversitySampling(
            registry=registry,
            tracker=tracker,
            top_performer_count=1,
            complementary_count=1,
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            new_agent_department="eng",
        )
        assert len(result) == 2

    async def test_returns_empty_when_department_missing(self) -> None:
        """Selector skips when plan has no new_agent_department."""
        registry = AsyncMock(spec=AgentRegistryService)
        selector = DepartmentDiversitySampling(
            registry=registry,
            tracker=AsyncMock(spec=PerformanceTracker),
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ()
        registry.list_by_department.assert_not_called()

    async def test_returns_empty_when_no_department_agents(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.list_by_department.return_value = ()

        selector = DepartmentDiversitySampling(
            registry=registry,
            tracker=AsyncMock(spec=PerformanceTracker),
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
            new_agent_department="eng",
        )
        assert result == ()


# -- UserCuratedList --------------------------------------------------


@pytest.mark.unit
class TestUserCuratedList:
    """UserCuratedList selector tests."""

    def test_name(self) -> None:
        selector = UserCuratedList(
            registry=AsyncMock(spec=AgentRegistryService),
            agent_ids=("agent-1", "agent-2"),
        )
        assert selector.name == "user_curated"

    async def test_returns_provided_ids(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.side_effect = [MagicMock(), MagicMock()]

        selector = UserCuratedList(
            registry=registry,
            agent_ids=("agent-1", "agent-2"),
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ("agent-1", "agent-2")

    async def test_filters_nonexistent_agents(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.side_effect = [MagicMock(), None]

        selector = UserCuratedList(
            registry=registry,
            agent_ids=("agent-1", "agent-missing"),
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ("agent-1",)

    async def test_empty_list(self) -> None:
        selector = UserCuratedList(
            registry=AsyncMock(spec=AgentRegistryService),
            agent_ids=(),
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ()


# -- CompositeSelector ------------------------------------------------


@pytest.mark.unit
class TestCompositeSelector:
    """CompositeSelector tests."""

    def test_name(self) -> None:
        selector = CompositeSelector(selectors=(), weights=())
        assert selector.name == "composite"

    async def test_merges_and_deduplicates(self) -> None:
        sel1 = AsyncMock()
        sel1.select.return_value = ("agent-1", "agent-2")
        sel2 = AsyncMock()
        sel2.select.return_value = ("agent-2", "agent-3")

        selector = CompositeSelector(
            selectors=(sel1, sel2),
            weights=(0.6, 0.4),
        )
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert len(result) == 3
        assert "agent-1" in result
        assert "agent-2" in result
        assert "agent-3" in result

    async def test_empty_selectors(self) -> None:
        selector = CompositeSelector(selectors=(), weights=())
        result = await selector.select(
            new_agent_role="engineer",
            new_agent_level=SeniorityLevel.JUNIOR,
        )
        assert result == ()
