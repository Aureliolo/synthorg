# module-kind: code
"""Durable-history support for :class:`PromotionService`.

Mixed into the service so the in-memory promotion history and per-agent
cooldown are write-through persisted and rehydrated at startup. Keeping
the cooldown durable is load-bearing: without it a crashloop could
re-enable a promotion by discarding the cooldown the previous run set.
"""

import asyncio
from datetime import timedelta

from pydantic import AwareDatetime

from synthorg.core.persistence_errors import PersistenceError
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.hr.promotion.models import PromotionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.promotion import (
    PROMOTION_HISTORY_HYDRATED,
    PROMOTION_PERSIST_FAILED,
)
from synthorg.persistence.promotion_history_protocol import (
    PromotionHistoryFilterSpec,
    PromotionHistoryRepository,
)

logger = get_logger(__name__)

_PERSIST_TIMEOUT_SECONDS: float = 5.0
_HYDRATE_PAGE_SIZE: int = 100


class PromotionPersistenceMixin:
    """Write-through persistence and startup rehydration for promotions."""

    # Owned and assigned by the concrete ``PromotionService``; declared
    # here so the type checker resolves the mixin's reads.
    _history_repo: PromotionHistoryRepository | None
    _config: PromotionConfig
    _promotion_history: dict[str, list[PromotionRecord]]
    _cooldown_until: dict[str, AwareDatetime]

    def attach_persistence(self, *, history_repo: PromotionHistoryRepository) -> None:
        """Attach the durable promotion-history repo after boot.

        The service is built in the construction phase before
        persistence exists; this is called from the on-startup wiring
        hook. Pair with :meth:`hydrate`.
        """
        self._history_repo = history_repo

    async def hydrate(self) -> None:
        """Load durable promotion history and recompute per-agent cooldown.

        Idempotent and a no-op when no repository is attached. The
        cooldown is derived from the newest record per agent so an
        in-cooldown agent stays gated across a restart.
        """
        if self._history_repo is None:
            return
        history: dict[str, list[PromotionRecord]] = {}
        offset = 0
        while True:
            records = await self._history_repo.query(
                PromotionHistoryFilterSpec(),
                limit=_HYDRATE_PAGE_SIZE,
                offset=offset,
            )
            for record in records:
                history.setdefault(str(record.agent_id), []).append(record)
            if len(records) < _HYDRATE_PAGE_SIZE:
                break
            offset += _HYDRATE_PAGE_SIZE
        cooldown: dict[str, AwareDatetime] = {}
        for agent_key, records_list in history.items():
            # query() returns newest-first; reverse to append (oldest-first)
            # order and derive cooldown from the most-recent record.
            newest = records_list[0]
            records_list.reverse()
            if self._config.cooldown_hours > 0:
                cooldown[agent_key] = newest.effective_at + timedelta(
                    hours=self._config.cooldown_hours
                )
        self._promotion_history = history
        self._cooldown_until = cooldown
        logger.info(
            PROMOTION_HISTORY_HYDRATED,
            agents=len(history),
            records=sum(len(v) for v in history.values()),
        )

    async def _persist_record(self, record: PromotionRecord) -> None:
        """Best-effort write-through of one promotion record."""
        if self._history_repo is None:
            return
        try:
            async with asyncio.timeout(_PERSIST_TIMEOUT_SECONDS):
                await self._history_repo.append(record)
        except (PersistenceError, TimeoutError) as exc:
            logger.warning(
                PROMOTION_PERSIST_FAILED,
                agent_id=str(record.agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
