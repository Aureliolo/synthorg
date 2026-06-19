"""Unit tests for ``AgentRegistryAssignmentLookup``.

Verifies the Pareto frontier's role-assignment lookup sources the
per-role current model from the live registry and the observed cost from
the cost tracker, and omits roles with no observed spend.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.pareto_assignments import AgentRegistryAssignmentLookup
from synthorg.budget.tracker import CostTracker
from synthorg.hr.registry import AgentRegistryService
from tests._shared import mock_of
from tests.unit.budget.conftest import make_cost_record

pytestmark = pytest.mark.unit


def _agent(role: str, model_id: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, model=SimpleNamespace(model_id=model_id))


def _record(model: str, cost: float) -> CostRecord:
    return make_cost_record(model=model, cost=cost, currency="USD")


def _lookup(
    *,
    agents: tuple[SimpleNamespace, ...],
    records: tuple[CostRecord, ...],
) -> AgentRegistryAssignmentLookup:
    registry = mock_of[AgentRegistryService](
        list_active=AsyncMock(return_value=agents),
    )
    tracker = mock_of[CostTracker](
        get_records=AsyncMock(return_value=records),
    )
    return AgentRegistryAssignmentLookup(
        registry=registry,
        cost_tracker=tracker,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


async def test_builds_assignment_for_active_role_with_spend() -> None:
    """A role on a model with observed spend yields one assignment."""
    lookup = _lookup(
        agents=(_agent("Backend Developer", "example-large-001"),),
        records=(_record("example-large-001", 0.4), _record("example-large-001", 0.6)),
    )

    assignments = await lookup()

    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.role_id == "Backend Developer"
    assert assignment.current_model == "example-large-001"
    assert assignment.current_cost_per_task == pytest.approx(0.5)


async def test_omits_role_without_observed_spend() -> None:
    """A role whose model has no spend is omitted (no cost baseline)."""
    lookup = _lookup(
        agents=(_agent("Backend Developer", "example-large-001"),),
        records=(_record("example-medium-001", 0.2),),
    )

    assert await lookup() == ()


async def test_empty_when_no_active_agents() -> None:
    """No active agents -> no assignments."""
    lookup = _lookup(agents=(), records=(_record("example-large-001", 0.2),))

    assert await lookup() == ()


async def test_picks_representative_model_per_role() -> None:
    """The model most active agents in a role run on is the current model."""
    lookup = _lookup(
        agents=(
            _agent("Backend Developer", "example-large-001"),
            _agent("Backend Developer", "example-large-001"),
            _agent("Backend Developer", "example-medium-001"),
        ),
        records=(
            _record("example-large-001", 0.5),
            _record("example-medium-001", 0.2),
        ),
    )

    assignments = await lookup()

    assert len(assignments) == 1
    assert assignments[0].current_model == "example-large-001"
