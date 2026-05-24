"""Scaling history signal aggregator.

Wraps :class:`synthorg.hr.scaling.service.ScalingService` to produce an
:class:`OrgScalingSummary` with recent decisions, their outcomes, and
derived success-rate / most-common-signal metrics.
"""

import asyncio
from collections import Counter
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingOutcome
from synthorg.meta.signal_models import (
    OrgScalingSummary,
    ScalingDecisionSummary,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.hr.scaling.models import ScalingDecision
    from synthorg.hr.scaling.service import ScalingService

logger = get_logger(__name__)

_EMPTY = OrgScalingSummary()

_PENDING_OUTCOME = NotBlankStr("pending")


class ScalingSignalAggregator:
    """Aggregates recent scaling decisions into an org-wide summary.

    Queries :class:`ScalingService` for the bounded in-memory history of
    decisions and action records, joins them by ``decision_id`` to
    project each decision's outcome, filters to the requested time
    window, and reduces to an :class:`OrgScalingSummary`.

    Args:
        service: The active scaling service instance whose recent
            decisions and actions are aggregated.
    """

    def __init__(self, *, service: ScalingService) -> None:
        self._service = service

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name."""
        return NotBlankStr("scaling")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgScalingSummary:
        """Aggregate scaling signals for the ``[since, until)`` window.

        Args:
            since: Inclusive lower bound on decision ``created_at``.
            until: Exclusive upper bound on decision ``created_at``.

        Returns:
            Org-wide scaling summary.  Returns an empty summary when
            no decisions fall in the window, or when the underlying
            :class:`ScalingService` raises a non-fatal exception while
            fetching recent history (the error is logged via
            ``META_SIGNAL_AGGREGATION_FAILED``).  Bugs in local
            aggregation (filtering, joining, reducing) propagate
            unchanged so they are never masked as empty summaries.

        Raises:
            MemoryError: Re-raised without logging -- fatal.
            RecursionError: Re-raised without logging -- fatal.
            asyncio.CancelledError: Re-raised without logging so task
                cancellation propagates as normal control flow.
        """
        try:
            decisions = self._service.get_recent_decisions()
            actions = self._service.get_recent_actions()
        except MemoryError, RecursionError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="scaling"
            )
            return _EMPTY

        # Local aggregation (filter / join / reduce) must NOT be
        # swallowed as an empty summary -- service-layer errors were
        # already handled above.  Wrap the reducer in a narrow
        # log-and-rethrow so operators see aggregation bugs in the
        # observability pipeline while callers still get a real
        # exception instead of silently degraded output.
        try:
            filtered = tuple(d for d in decisions if since <= d.created_at < until)
            if not filtered:
                logger.info(
                    META_SIGNAL_AGGREGATION_COMPLETED,
                    domain="scaling",
                    total_decisions=0,
                )
                return _EMPTY

            outcome_by_decision = {a.decision_id: a.outcome for a in actions}
            summaries = tuple(
                _build_summary(d, outcome_by_decision.get(d.id)) for d in filtered
            )

            total_decisions = len(filtered)
            executed_count = sum(
                1 for s in summaries if s.outcome == ScalingOutcome.EXECUTED.value
            )
            success_rate = executed_count / total_decisions

            counter = Counter(s.source_strategy for s in summaries)
            # Counter.most_common returns items sorted by count desc,
            # insertion order preserved for ties -- deterministic.
            most_common_signal = counter.most_common(1)[0][0]
        except MemoryError, RecursionError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_exception_redacted(
                logger,
                META_SIGNAL_AGGREGATION_FAILED,
                exc,
                domain="scaling",
                stage="reduce",
                decision_count=len(decisions),
                action_count=len(actions),
            )
            raise

        summary = OrgScalingSummary(
            recent_decisions=summaries,
            total_decisions=total_decisions,
            success_rate=success_rate,
            most_common_signal=most_common_signal,
        )
        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="scaling",
            total_decisions=total_decisions,
            success_rate=round(success_rate, 4),
            most_common_signal=most_common_signal,
        )
        return summary


def _build_summary(
    decision: ScalingDecision,
    outcome: ScalingOutcome | None,
) -> ScalingDecisionSummary:
    """Project a decision + optional action outcome into a summary row."""
    outcome_str = (
        NotBlankStr(outcome.value) if outcome is not None else _PENDING_OUTCOME
    )
    return ScalingDecisionSummary(
        decision_id=decision.id,
        action_type=NotBlankStr(decision.action_type.value),
        outcome=outcome_str,
        source_strategy=NotBlankStr(decision.source_strategy.value),
        rationale=decision.rationale,
        created_at=decision.created_at,
    )


__all__ = ["ScalingSignalAggregator"]
