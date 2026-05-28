"""Batch-call regression tests for _department_health helpers.

``_resolve_agent_ids`` and ``_resolve_snapshots`` previously fanned out N
``get_by_name`` / ``get_snapshot`` coroutines via a TaskGroup while each
call serialised on the registry's single lock. These tests pin the batch
contract: exactly one ``get_by_names`` / ``get_snapshots`` await per
helper call.
"""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.api.controllers._department_health import (
    _resolve_agent_ids,
    _resolve_snapshots,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import AgentStatus, SeniorityLevel
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from tests._shared import make_app_state
from tests.unit.hr.pruning.conftest import make_performance_snapshot


def _make_identity(*, agent_id: str, name: str) -> AgentIdentity:
    from datetime import date

    return AgentIdentity(
        id=UUID(agent_id),
        name=name,
        role="developer",
        department="eng",
        level=SeniorityLevel.MID,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


@pytest.mark.unit
class TestResolveAgentIdsBatch:
    """_resolve_agent_ids hits the registry's batch method exactly once."""

    async def test_uses_batch_call(self) -> None:
        alice = _make_identity(
            agent_id="00000000-0000-0000-0000-00000000000a",
            name="alice",
        )
        bob = _make_identity(
            agent_id="00000000-0000-0000-0000-00000000000b",
            name="bob",
        )
        mock_registry = AsyncMock(spec=AgentRegistryService)
        mock_registry.get_by_names.return_value = (alice, bob)

        app_state = make_app_state(agent_registry=mock_registry)

        result = await _resolve_agent_ids(
            app_state,
            ("alice", "bob"),
        )

        assert mock_registry.get_by_names.await_count == 1
        assert mock_registry.get_by_name.await_count == 0
        # Batch helpers are order-preserving; pin that contract with
        # an ordered tuple comparison rather than a set.
        assert result == (str(alice.id), str(bob.id))

    async def test_filters_missing_names(self) -> None:
        alice = _make_identity(
            agent_id="00000000-0000-0000-0000-00000000000a",
            name="alice",
        )
        mock_registry = AsyncMock(spec=AgentRegistryService)
        mock_registry.get_by_names.return_value = (alice, None)

        app_state = make_app_state(agent_registry=mock_registry)

        result = await _resolve_agent_ids(
            app_state,
            ("alice", "nobody"),
        )

        assert result == (str(alice.id),)
        assert mock_registry.get_by_names.await_count == 1

    async def test_empty_registry_returns_empty(self) -> None:
        app_state = make_app_state()
        result = await _resolve_agent_ids(
            app_state,
            ("alice", "bob"),
        )
        assert result == ()


@pytest.mark.unit
class TestResolveSnapshotsBatch:
    """_resolve_snapshots hits the tracker's batch method exactly once."""

    async def test_uses_batch_call(self) -> None:
        snap_a = make_performance_snapshot(agent_id="agent-a")
        snap_b = make_performance_snapshot(agent_id="agent-b")
        mock_tracker = AsyncMock(spec=PerformanceTracker)
        mock_tracker.get_snapshots.return_value = (snap_a, snap_b)

        app_state = make_app_state(performance_tracker=mock_tracker)

        result = await _resolve_snapshots(
            app_state,
            ("agent-a", "agent-b"),
        )

        assert mock_tracker.get_snapshots.await_count == 1
        assert mock_tracker.get_snapshot.await_count == 0
        # Batch helpers are order-preserving; pin that contract with
        # an ordered comparison rather than a set.
        assert tuple(str(s.agent_id) for s in result) == (
            "agent-a",
            "agent-b",
        )

    async def test_filters_none_snapshots(self) -> None:
        snap_a = make_performance_snapshot(agent_id="agent-a")
        mock_tracker = AsyncMock(spec=PerformanceTracker)
        mock_tracker.get_snapshots.return_value = (snap_a, None)

        app_state = make_app_state(performance_tracker=mock_tracker)

        result: tuple[AgentPerformanceSnapshot, ...] = await _resolve_snapshots(
            app_state,
            ("agent-a", "agent-missing"),
        )

        assert len(result) == 1
        assert str(result[0].agent_id) == "agent-a"

    async def test_empty_agent_ids(self) -> None:
        mock_tracker = AsyncMock(spec=PerformanceTracker)
        mock_tracker.get_snapshots.return_value = ()

        app_state = make_app_state(performance_tracker=mock_tracker)
        result = await _resolve_snapshots(
            app_state,
            (),
        )
        assert result == ()
