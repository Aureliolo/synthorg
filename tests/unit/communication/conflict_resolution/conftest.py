"""Fixtures and factories for conflict resolution tests."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
    DebateConfig,
    HybridConfig,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
    ConflictResolution,
    ConflictResolutionOutcome,
)
from synthorg.communication.delegation.hierarchy import HierarchyResolver
from synthorg.communication.enums import (
    ConflictType,
)
from synthorg.core.company import Company, CompanyConfig, Department, Team
from synthorg.core.enums import SeniorityLevel

_NOW = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)


def make_position(  # noqa: PLR0913
    *,
    agent_id: str = "agent-a",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.SENIOR,
    position: str = "Use microservices",
    reasoning: str = "Better scalability",
    timestamp: datetime | None = None,
) -> ConflictPosition:
    """Create a conflict position with defaults."""
    return ConflictPosition(
        agent_id=agent_id,
        agent_department=department,
        agent_level=level,
        position=position,
        reasoning=reasoning,
        timestamp=timestamp or _NOW,
    )


def make_conflict(  # noqa: PLR0913
    *,
    conflict_id: str = "conflict-test12345",
    conflict_type: ConflictType = ConflictType.ARCHITECTURE,
    subject: str = "Microservices vs monolith",
    positions: tuple[ConflictPosition, ...] | None = None,
    task_id: str | None = None,
    detected_at: datetime | None = None,
) -> Conflict:
    """Create a conflict with defaults."""
    if positions is None:
        positions = (
            make_position(agent_id="agent-a", level=SeniorityLevel.SENIOR),
            make_position(
                agent_id="agent-b",
                level=SeniorityLevel.MID,
                position="Use monolith",
                reasoning="Simpler to start",
            ),
        )
    return Conflict(
        id=conflict_id,
        type=conflict_type,
        task_id=task_id,
        subject=subject,
        positions=positions,
        detected_at=detected_at or _NOW,
    )


_DEFAULT_OUTCOME = ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY


def make_resolution(  # noqa: PLR0913
    *,
    conflict_id: str = "conflict-test12345",
    outcome: ConflictResolutionOutcome = _DEFAULT_OUTCOME,
    winning_agent_id: str | None = "agent-a",
    winning_position: str | None = "Use microservices",
    decided_by: str = "agent-a",
    reasoning: str = "Higher seniority",
    resolved_at: datetime | None = None,
) -> ConflictResolution:
    """Create a resolution with defaults."""
    return ConflictResolution(
        conflict_id=conflict_id,
        outcome=outcome,
        winning_agent_id=winning_agent_id,
        winning_position=winning_position,
        decided_by=decided_by,
        reasoning=reasoning,
        resolved_at=resolved_at or _NOW,
    )


def make_company() -> Company:
    """Create a test company with eng + qa departments."""
    return Company(
        name="Test Corp",
        departments=(
            Department(
                name="Engineering",
                head="cto",
                budget_percent=60.0,
                teams=(
                    Team(
                        name="backend",
                        lead="backend_lead",
                        members=("sr_dev", "jr_dev"),
                    ),
                    Team(
                        name="frontend",
                        lead="frontend_lead",
                        members=("ui_dev",),
                    ),
                ),
            ),
            Department(
                name="QA",
                head="qa_head",
                budget_percent=20.0,
                teams=(
                    Team(
                        name="testing",
                        lead="qa_lead",
                        members=("qa_eng",),
                    ),
                ),
            ),
        ),
        config=CompanyConfig(budget_monthly=100.0),
    )


@pytest.fixture
def hierarchy() -> HierarchyResolver:
    """Hierarchy resolver for the test company."""
    return HierarchyResolver(make_company())


@pytest.fixture
def sample_conflict() -> Conflict:
    """A standard architecture conflict between two agents."""
    return make_conflict()


@pytest.fixture
def sample_resolution() -> ConflictResolution:
    """A standard authority-based resolution."""
    return make_resolution()


@pytest.fixture
def default_config() -> ConflictResolutionConfig:
    """Default conflict resolution config."""
    return ConflictResolutionConfig()


@pytest.fixture
def debate_config() -> DebateConfig:
    """Default debate config."""
    return DebateConfig()


@pytest.fixture
def hybrid_config() -> HybridConfig:
    """Default hybrid config."""
    return HybridConfig()
