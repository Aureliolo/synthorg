"""Unit tests for ``CostTrackerHistoryLookup``.

Verifies the forecaster's history lookup groups windowed productive spend by the
recording agent's live role and the model's tier, excludes non-productive call
categories, and returns an empty sequence for an unobserved (tier, role) key.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.forecast_history import CostTrackerHistoryLookup
from synthorg.budget.tracker import CostTracker
from synthorg.hr.registry import AgentRegistryService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _agent(agent_id: str, role: str) -> Any:
    return SimpleNamespace(id=agent_id, role=role)


def _record(
    *,
    agent_id: str,
    model: str,
    cost: float,
    category: LLMCallCategory | None = LLMCallCategory.PRODUCTIVE,
) -> Any:
    return SimpleNamespace(
        agent_id=agent_id, model=model, cost=cost, call_category=category
    )


def _lookup(*, agents: tuple[Any, ...], records: tuple[Any, ...]) -> Any:
    registry = mock_of[AgentRegistryService](
        list_active=AsyncMock(return_value=agents),
    )
    tracker = mock_of[CostTracker](
        get_records=AsyncMock(return_value=records),
    )
    return CostTrackerHistoryLookup(
        registry=registry,
        cost_tracker=tracker,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


async def test_returns_productive_costs_for_role_and_tier() -> None:
    """Productive spend by a role on a tier is returned as per-turn samples."""
    lookup = _lookup(
        agents=(_agent("a1", "Backend Developer"),),
        records=(
            _record(agent_id="a1", model="example-large-001", cost=0.4),
            _record(agent_id="a1", model="example-large-001", cost=0.6),
        ),
    )

    observations = await lookup("large", "backend developer")

    assert sorted(observations) == [0.4, 0.6]


async def test_excludes_non_productive_categories() -> None:
    """Coordination / system / embedding calls are not per-turn observations."""
    lookup = _lookup(
        agents=(_agent("a1", "Backend Developer"),),
        records=(
            _record(agent_id="a1", model="example-large-001", cost=0.5),
            _record(
                agent_id="a1",
                model="example-large-001",
                cost=9.9,
                category=LLMCallCategory.COORDINATION,
            ),
            _record(
                agent_id="a1",
                model="example-large-001",
                cost=9.9,
                category=LLMCallCategory.SYSTEM,
            ),
        ),
    )

    assert tuple(await lookup("large", "backend developer")) == (0.5,)


async def test_buckets_by_tier() -> None:
    """A different tier's spend is not returned for the queried tier."""
    lookup = _lookup(
        agents=(_agent("a1", "Backend Developer"),),
        records=(
            _record(agent_id="a1", model="example-large-001", cost=0.5),
            _record(agent_id="a1", model="example-small-001", cost=0.1),
        ),
    )

    assert tuple(await lookup("large", "backend developer")) == (0.5,)
    assert tuple(await lookup("small", "backend developer")) == (0.1,)


async def test_excludes_unknown_agents_and_other_roles() -> None:
    """Records from agents not currently in the role are excluded."""
    lookup = _lookup(
        agents=(_agent("a1", "Backend Developer"),),
        records=(
            _record(agent_id="ghost", model="example-large-001", cost=5.0),
            _record(agent_id="a1", model="example-large-001", cost=0.5),
        ),
    )

    assert tuple(await lookup("large", "backend developer")) == (0.5,)


async def test_empty_for_unobserved_key() -> None:
    """A (tier, role) with no observed productive spend returns empty."""
    lookup = _lookup(
        agents=(_agent("a1", "Backend Developer"),),
        records=(_record(agent_id="a1", model="example-large-001", cost=0.5),),
    )

    assert await lookup("medium", "designer") == ()
