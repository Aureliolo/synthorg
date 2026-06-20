"""Durable evolution-outcome store: ring-buffer hot reads + durable log.

Composes an in-memory :class:`InMemoryEvolutionOutcomeStore` (the hot
read path the signals aggregator queries) with a durable
:class:`EvolutionOutcomeRepository` (the restart-surviving append-only
log the ``/meta/evolution/*`` endpoints page). ``record`` writes through
to both with a single shared ``recorded_at``; ``rehydrate`` reloads the
ring buffer from the durable log at boot.

Implements the
:class:`~synthorg.meta.evolution.outcome_store_protocol.EvolutionOutcomeStore`
protocol so it drops into ``build_signals_service(evolution_store=...)``,
and satisfies the engine's narrow ``EvolutionOutcomeSink`` so the engine
evolution loop records terminal outcomes here.
"""

from datetime import datetime
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.meta.evolution.outcome_store import InMemoryEvolutionOutcomeStore
from synthorg.meta.signal_models import OrgEvolutionSummary
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import (
    EVOLUTION_OUTCOME_RECORD_FAILED,
)
from synthorg.persistence.evolution_outcome_protocol import (
    EvolutionOutcomeFilterSpec,
    EvolutionOutcomeRepository,
)

logger = get_logger(__name__)

_DEFAULT_MAX_RESULTS: Final[int] = 5_000
_DEFAULT_MAX_RECENT: Final[int] = 10


class DurableEvolutionOutcomeStore:
    """Write-through cache over a durable evolution-outcome repository.

    Args:
        repo: Durable append-only outcome repository.
        clock: Clock seam for the ``recorded_at`` stamp; tests inject a
            ``FakeClock``.
        max_results: Ring-buffer capacity for the hot read path.
    """

    def __init__(
        self,
        *,
        repo: EvolutionOutcomeRepository,
        clock: Clock | None = None,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> None:
        self._repo = repo
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._buffer = InMemoryEvolutionOutcomeStore(
            max_results=max_results, clock=self._clock
        )
        self._max_results = max_results

    async def record(
        self,
        *,
        agent_id: NotBlankStr,
        axis: NotBlankStr,
        applied: bool,
        proposed_at: datetime,
    ) -> None:
        """Record a terminal outcome to the durable log and the ring buffer.

        Best-effort: a durable-append failure logs and still keeps the
        record in the hot buffer so the signals aggregator stays current.
        Criticals (``MemoryError`` / ``RecursionError``) re-raise.
        """
        try:
            record = EvolutionOutcomeRecord(
                agent_id=agent_id,
                axis=axis,
                applied=applied,
                proposed_at=proposed_at,
                recorded_at=self._clock.now(),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EVOLUTION_OUTCOME_RECORD_FAILED,
                agent_id=agent_id,
                axis=axis,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        # Hot buffer first so the signals aggregator stays current even when
        # the durable append fails; the failure log carries the record's
        # timestamps (the record has no domain id) so the dropped durable
        # write can be reconciled against the buffer after restart.
        await self._buffer.ingest(record)
        try:
            await self._repo.append(record)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EVOLUTION_OUTCOME_RECORD_FAILED,
                agent_id=agent_id,
                axis=axis,
                phase="durable_append",
                proposed_at=record.proposed_at.isoformat(),
                recorded_at=record.recorded_at.isoformat(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def query(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[EvolutionOutcomeRecord, ...]:
        """Return outcomes recorded within ``[since, until)`` (hot read).

        Returns:
            Tuple of records, newest-first.
        """
        return await self._buffer.query(since=since, until=until)

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
        max_recent: int = _DEFAULT_MAX_RECENT,
    ) -> OrgEvolutionSummary:
        """Roll outcomes within the window into an org summary (hot read).

        Returns:
            The window summary.
        """
        return await self._buffer.summarize(
            since=since, until=until, max_recent=max_recent
        )

    async def count(self) -> int:
        """Return the current ring-buffer size.

        Returns:
            Number of buffered records.
        """
        return await self._buffer.count()

    async def clear(self) -> None:
        """Drop the in-memory ring buffer (the durable log is untouched)."""
        await self._buffer.clear()

    async def rehydrate(self) -> None:
        """Reload the ring buffer from the durable log at boot.

        Loads the most recent ``max_results`` outcomes (newest-first from
        the repo) and ingests them oldest-first so the buffer's append
        order matches a live run.
        """
        # Clear BEFORE querying the durable log: clearing after the query
        # would silently wipe any outcome recorded concurrently between the
        # query returning and the clear, dropping it from every hot read.
        await self._buffer.clear()
        records = await self._repo.query(
            EvolutionOutcomeFilterSpec(),
            limit=self._max_results,
            offset=0,
        )
        for record in reversed(records):
            await self._buffer.ingest(record)


__all__ = ["DurableEvolutionOutcomeStore"]
