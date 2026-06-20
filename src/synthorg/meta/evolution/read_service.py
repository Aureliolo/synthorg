# module-kind: service
"""Read service backing the ``/meta/evolution/*`` endpoints.

Wraps the durable :class:`EvolutionOutcomeRepository` so controllers
never touch persistence directly. Surfaces a paginated outcomes list, a
windowed org summary (rolled up via the shared
:func:`roll_up_outcomes`), and per-axis counts for the axes-stats panel.
"""

from datetime import datetime

from synthorg.core.pagination import collect_all
from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.meta.evolution.outcome_store import roll_up_outcomes
from synthorg.meta.signal_models import OrgEvolutionSummary
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_OUTCOMES_QUERIED,
    EVOLUTION_SUMMARY_DRAINED,
)
from synthorg.persistence.evolution_outcome_protocol import (
    EvolutionOutcomeFilterSpec,
    EvolutionOutcomeRepository,
)

logger = get_logger(__name__)


class EvolutionReadService:
    """Durable read views over the evolution-outcome log.

    Args:
        repo: Durable append-only outcome repository.
    """

    def __init__(self, *, repo: EvolutionOutcomeRepository) -> None:
        self._repo = repo

    async def list_outcomes(
        self,
        *,
        limit: int,
        offset: int = 0,
        agent_id: NotBlankStr | None = None,
        axis: NotBlankStr | None = None,
    ) -> tuple[EvolutionOutcomeRecord, ...]:
        """List outcomes newest-first, optionally filtered, paginated.

        Returns:
            The matching outcome records.
        """
        spec = EvolutionOutcomeFilterSpec(agent_id=agent_id, axis=axis)
        records = await self._repo.query(spec, limit=limit, offset=offset)
        logger.debug(
            EVOLUTION_OUTCOMES_QUERIED,
            count=len(records),
            limit=limit,
            offset=offset,
            filtered=agent_id is not None or axis is not None,
        )
        return records

    async def summary(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgEvolutionSummary:
        """Roll the outcomes recorded within ``[since, until)`` into a summary.

        Drains the window from the durable log (bounded by retention) so
        the totals and approval rate reflect persisted history, not just
        the in-memory ring buffer.

        Returns:
            The rolled-up org evolution summary.
        """
        spec = EvolutionOutcomeFilterSpec(since=since, until=until)
        records = await collect_all(
            lambda limit, offset: self._repo.query(spec, limit=limit, offset=offset)
        )
        logger.debug(
            EVOLUTION_SUMMARY_DRAINED,
            count=len(records),
            since=since.isoformat(),
            until=until.isoformat(),
        )
        return roll_up_outcomes(records)

    async def axis_stats(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[tuple[NotBlankStr, int], ...]:
        """Return per-axis outcome counts within ``[since, until)``.

        Returns:
            ``(axis, count)`` pairs, highest count first.
        """
        return await self._repo.axis_counts(since=since, until=until)


__all__ = ["EvolutionReadService"]
