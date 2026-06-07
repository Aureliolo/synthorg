"""Tests for the ``memory.consolidation_enabled`` kill-switch.

When ``ConsolidationConfig.enabled=False`` (mirrored from the
``memory.consolidation_enabled`` setting), both ``run_consolidation``
and ``run_maintenance`` must short-circuit immediately without
touching the backend, the strategy, or archival -- but still log
``consolidation.run.skipped`` with ``reason=disabled_by_setting``
so operators can observe the pause.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.config import ConsolidationConfig
from synthorg.memory.consolidation.models import ConsolidationResult
from synthorg.memory.consolidation.service import MemoryConsolidationService
from synthorg.memory.consolidation.strategy import ConsolidationStrategy
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.protocol import MemoryBackend
from synthorg.settings.resolver import ConfigResolver

_NOW = datetime.now(UTC)
_AGENT_ID = "kill-switch-agent"


def _make_entry(entry_id: str) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT_ID,
        category=MemoryCategory.EPISODIC,
        content=f"Content {entry_id}",
        metadata=MemoryMetadata(),
        created_at=_NOW - timedelta(hours=1),
    )


def _build_service(
    *, enabled: bool
) -> tuple[
    MemoryConsolidationService,
    AsyncMock,
    AsyncMock,
]:
    backend = AsyncMock(spec=MemoryBackend)
    backend.retrieve = AsyncMock(return_value=(_make_entry("m1"),))
    backend.delete = AsyncMock(return_value=True)
    backend.count = AsyncMock(return_value=0)
    strategy = AsyncMock(spec=ConsolidationStrategy)
    strategy.consolidate = AsyncMock()
    config = ConsolidationConfig(enabled=enabled)
    service = MemoryConsolidationService(
        backend=backend,
        config=config,
        strategy=strategy,
    )
    return service, backend, strategy


@pytest.mark.unit
class TestConsolidationKillSwitch:
    """``enabled=False`` dominates strategy and backend calls."""

    async def test_run_consolidation_skipped_when_disabled(self) -> None:
        service, backend, strategy = _build_service(enabled=False)

        with structlog.testing.capture_logs() as logs:
            result = await service.run_consolidation(_AGENT_ID)

        assert result.consolidated_count == 0
        assert result.removed_ids == ()
        # No backend interactions at all while disabled -- the kill-switch
        # dominates every branch of the consolidation cycle, not just
        # ``retrieve``.
        strategy.consolidate.assert_not_awaited()
        backend.retrieve.assert_not_awaited()
        backend.count.assert_not_awaited()
        backend.delete.assert_not_awaited()
        skipped = [
            log for log in logs if log.get("event") == "consolidation.run.skipped"
        ]
        assert any(log.get("reason") == "disabled_by_setting" for log in skipped), (
            skipped
        )

    async def test_run_consolidation_runs_when_enabled(self) -> None:
        service, backend, strategy = _build_service(enabled=True)
        expected = ConsolidationResult(
            removed_ids=(),
            summary_ids=(),
            mode_assignments=(),
        )
        strategy.consolidate.return_value = expected

        result = await service.run_consolidation(_AGENT_ID)

        strategy.consolidate.assert_awaited_once()
        backend.retrieve.assert_awaited_once()
        # Propagates the strategy result verbatim so the caller sees
        # the distillation/summary ids the strategy produced.
        assert result == expected

    async def test_run_maintenance_skipped_when_disabled(self) -> None:
        service, backend, strategy = _build_service(enabled=False)

        with structlog.testing.capture_logs() as logs:
            result = await service.run_maintenance(_AGENT_ID)

        assert result.consolidated_count == 0
        # No backend interactions at all while disabled -- the kill-switch
        # dominates every branch of the maintenance cycle.
        strategy.consolidate.assert_not_awaited()
        backend.retrieve.assert_not_awaited()
        backend.count.assert_not_awaited()
        backend.delete.assert_not_awaited()
        # The maintenance-scope skip log includes ``scope="maintenance"``
        # so operators can distinguish from consolidation-only skips.
        assert any(
            log.get("event") == "consolidation.run.skipped"
            and log.get("reason") == "disabled_by_setting"
            and log.get("scope") == "maintenance"
            for log in logs
        )

    async def test_run_maintenance_resolves_kill_switch_once_per_cycle(
        self,
    ) -> None:
        """The kill-switch is resolved exactly once per maintenance cycle.

        Resolving twice (in ``run_maintenance`` and again in
        ``run_consolidation``) opens a window where the flag flips
        mid-cycle and partial state is left behind: retention runs but
        consolidation skips, or vice versa.  ``run_maintenance`` must
        resolve once and pass the value into ``run_consolidation`` via
        the private ``_enabled`` parameter so the whole cycle observes
        the same snapshot.
        """
        backend = AsyncMock(spec=MemoryBackend)
        backend.retrieve = AsyncMock(return_value=())
        backend.delete = AsyncMock(return_value=True)
        backend.count = AsyncMock(return_value=0)

        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool = AsyncMock(return_value=True)

        service = MemoryConsolidationService(
            backend=backend,
            config=ConsolidationConfig(enabled=True),
            config_resolver=resolver,
        )

        await service.run_maintenance(_AGENT_ID)

        assert resolver.get_bool.await_count == 1, (
            f"Expected exactly one kill-switch resolution per maintenance "
            f"cycle, got {resolver.get_bool.await_count}"
        )
