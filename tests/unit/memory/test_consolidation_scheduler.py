"""Tests for the memory consolidation scheduler.

``MemoryConsolidationService`` had three strategies, a batch-size
setting and a kill switch, and no cron, route or scheduler anywhere: the
settings page offered operators control over a process that never ran.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.cycle_scheduler import (
    AgentIdSupplier,
    MemoryConsolidationScheduler,
    interval_seconds_for,
)
from synthorg.memory.enums import ConsolidationInterval
from synthorg.memory.errors import MemoryStoreError
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _supplier(agent_ids: tuple[str, ...]) -> AgentIdSupplier:
    """Build an agent-id supplier returning a fixed roster."""

    async def _supply() -> tuple[NotBlankStr, ...]:
        return tuple(NotBlankStr(a) for a in agent_ids)

    return _supply


class TestIntervalMapping:
    @pytest.mark.parametrize(
        ("interval", "expected"),
        [
            (ConsolidationInterval.HOURLY, 3600.0),
            (ConsolidationInterval.DAILY, 86400.0),
            (ConsolidationInterval.WEEKLY, 604800.0),
        ],
    )
    def test_known_intervals_map_to_seconds(
        self, interval: ConsolidationInterval, expected: float
    ) -> None:
        assert interval_seconds_for(interval) == expected

    def test_never_has_no_cadence(self) -> None:
        """``never`` must not be scheduled at all rather than run rarely."""
        assert interval_seconds_for(ConsolidationInterval.NEVER) is None


class TestSchedulerCycle:
    async def test_cycle_maintains_every_agent(self) -> None:
        from synthorg.memory.consolidation.service import (
            MemoryConsolidationService,
        )

        service = mock_of[MemoryConsolidationService]()
        scheduler = MemoryConsolidationScheduler(
            service,
            interval_seconds=3600.0,
            agent_ids=_supplier(("agent-1", "agent-2")),
        )

        await scheduler._run_cycle_once()

        assert service.run_maintenance.await_count == 2

    async def test_one_agent_failing_does_not_stop_the_rest(self) -> None:
        """Maintenance is per-agent, so one bad roster entry must not
        cost every other agent its retention pass."""
        from synthorg.memory.consolidation.service import (
            MemoryConsolidationService,
        )

        service = mock_of[MemoryConsolidationService]()
        service.run_maintenance.side_effect = [
            MemoryStoreError("boom"),
            None,
        ]
        scheduler = MemoryConsolidationScheduler(
            service,
            interval_seconds=3600.0,
            agent_ids=_supplier(("agent-1", "agent-2")),
        )

        await scheduler._run_cycle_once()

        assert service.run_maintenance.await_count == 2

    async def test_critical_error_nested_in_a_group_propagates(self) -> None:
        """A per-agent guard must never swallow an interpreter-critical.

        The bare-critical arm is caught before the broad handler, so the
        one way a ``MemoryError`` reaches ``reraise_critical`` is nested in
        an ``ExceptionGroup``. It must still take down the cycle rather
        than being logged as an ordinary agent failure.
        """
        from synthorg.memory.consolidation.service import (
            MemoryConsolidationService,
        )

        service = mock_of[MemoryConsolidationService]()
        service.run_maintenance.side_effect = ExceptionGroup("wrapped", [MemoryError()])
        scheduler = MemoryConsolidationScheduler(
            service,
            interval_seconds=3600.0,
            agent_ids=_supplier(("agent-1",)),
        )

        with pytest.raises(ExceptionGroup):
            await scheduler._run_cycle_once()

    async def test_no_supplier_is_a_no_op(self) -> None:
        from synthorg.memory.consolidation.service import (
            MemoryConsolidationService,
        )

        service = mock_of[MemoryConsolidationService]()
        scheduler = MemoryConsolidationScheduler(service, interval_seconds=3600.0)

        await scheduler._run_cycle_once()

        service.run_maintenance.assert_not_awaited()

    async def test_kill_switch_disables_the_cycle(self) -> None:
        from synthorg.memory.consolidation.service import (
            MemoryConsolidationService,
        )
        from synthorg.settings.resolver import ConfigResolver

        resolver = mock_of[ConfigResolver]()
        resolver.get_bool.return_value = False
        scheduler = MemoryConsolidationScheduler(
            mock_of[MemoryConsolidationService](),
            interval_seconds=3600.0,
            config_resolver=resolver,
        )

        assert await scheduler._resolve_cycle_enabled() is False

    async def test_missing_resolver_fails_safe_to_enabled(self) -> None:
        """A settings outage must not silently halt maintenance."""
        from synthorg.memory.consolidation.service import (
            MemoryConsolidationService,
        )

        scheduler = MemoryConsolidationScheduler(
            mock_of[MemoryConsolidationService](),
            interval_seconds=3600.0,
        )

        assert await scheduler._resolve_cycle_enabled() is True
