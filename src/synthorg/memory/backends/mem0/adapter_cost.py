"""Embedding-cost tracking mixin for ``Mem0MemoryBackend``.

Isolates the best-effort cost recording path so the adapter module
can focus on connection lifecycle and CRUD.  Relies on these
attributes declared on the concrete class:

* ``_mem0_config``    -- :class:`synthorg.memory.backends.mem0.config.Mem0BackendConfig`
* ``_cost_tracker``   -- optional :class:`synthorg.budget.tracker.CostTracker`
"""

import asyncio
import builtins
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.backends.mem0.config import Mem0BackendConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_EMBEDDING_COST_FAILED,
    BUDGET_EMBEDDING_COST_RECORDED,
    BUDGET_EMBEDDING_MODEL_UNPRICED,
)

if TYPE_CHECKING:
    from synthorg.budget.tracker import CostTracker

logger = get_logger(__name__)


class Mem0AdapterCostMixin:
    """Embedding-cost tracking for ``Mem0MemoryBackend``."""

    __slots__ = ()

    _mem0_config: Mem0BackendConfig
    _cost_tracker: CostTracker | None

    async def _record_embedding_cost(
        self,
        *,
        agent_id: str,
        task_id: str,
        content_length: int,
        operation: str,
    ) -> None:
        """Record an embedding cost estimate if tracking is enabled.

        Best-effort: non-system failures are logged but never
        propagate.  The ``task_id`` is a synthetic sentinel
        (``"memory-store"`` / ``"memory-retrieve"``) because the
        backend protocol does not carry task context.
        """
        cost_cfg = self._mem0_config.embedding_cost
        if not cost_cfg.enabled or self._cost_tracker is None:
            return
        cpt = cost_cfg.default_chars_per_token
        input_tokens = max(1, (content_length + cpt - 1) // cpt)
        model = str(self._mem0_config.embedder.model)
        cost_per_1k = cost_cfg.model_pricing.get(model, 0.0)
        if cost_per_1k == 0.0 and model not in cost_cfg.model_pricing:
            logger.debug(
                BUDGET_EMBEDDING_MODEL_UNPRICED,
                agent_id=agent_id,
                operation=operation,
                model=model,
            )
        cost = input_tokens * cost_per_1k / 1000.0
        budget_cfg = self._cost_tracker.budget_config
        currency = budget_cfg.currency if budget_cfg is not None else DEFAULT_CURRENCY
        record = CostRecord(
            agent_id=agent_id,
            task_id=task_id,
            provider=str(self._mem0_config.embedder.provider),
            model=model,
            input_tokens=input_tokens,
            output_tokens=0,
            cost=round(cost, 8),
            currency=currency,
            timestamp=datetime.now(UTC),
            call_category=LLMCallCategory.EMBEDDING,
        )
        await self._record_cost(record, agent_id, operation, model)

    async def _record_cost(
        self,
        record: CostRecord,
        agent_id: str,
        operation: str,
        model: str,
    ) -> None:
        """Persist a CostRecord via the tracker (best-effort).

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
        try:
            await self._cost_tracker.record(record)  # type: ignore[union-attr]
            logger.debug(
                BUDGET_EMBEDDING_COST_RECORDED,
                agent_id=agent_id,
                operation=operation,
                input_tokens=record.input_tokens,
                cost=record.cost,
                model=model,
            )
        except builtins.MemoryError, RecursionError:
            logger.error(
                BUDGET_EMBEDDING_COST_FAILED,
                agent_id=agent_id,
                operation=operation,
                error_type="system",
                model=model,
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                BUDGET_EMBEDDING_COST_FAILED,
                agent_id=agent_id,
                operation=operation,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
                reason="cost_tracking_failed",
            )
