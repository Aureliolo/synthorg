"""Memory-backed outcome store for proposal decision tracking.

Persists ``ProposalOutcome`` records as episodic memories in the
``chief_of_staff`` namespace and aggregates them into
``OutcomeStats`` for the confidence learning pipeline.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryMetadata, MemoryQuery, MemoryStoreRequest
from synthorg.meta.chief_of_staff.models import OutcomeStats, ProposalOutcome
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_OUTCOME_RECORD_FAILED,
    COS_OUTCOME_RECORDED,
)

if TYPE_CHECKING:
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.meta.models import ProposalAltitude

logger = get_logger(__name__)
_DEFAULT_MIN_OUTCOMES: Final[int] = 3
_DEFAULT_LIMIT: Final[int] = 10

_NAMESPACE = NotBlankStr("chief_of_staff")


class MemoryBackendOutcomeStore:
    """Outcome store backed by the agent memory subsystem.

    Stores each ``ProposalOutcome`` as a JSON-serialized episodic
    memory entry with filterable tags for rule name, altitude,
    and decision.

    Args:
        backend: Memory backend for persistence.
        agent_id: CoS agent identifier (all outcomes stored under
            this single agent).
        min_outcomes: Minimum decision count before ``get_stats``
            returns results (prevents premature adjustment).
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        agent_id: NotBlankStr,
        min_outcomes: int = _DEFAULT_MIN_OUTCOMES,
    ) -> None:
        self._backend = backend
        self._agent_id = agent_id
        self._min_outcomes = min_outcomes

    async def record_outcome(
        self,
        outcome: ProposalOutcome,
    ) -> NotBlankStr:
        """Record a proposal decision as episodic memory.

        Args:
            outcome: The proposal outcome to record.

        Returns:
            Memory ID assigned by the backend.
        """
        tags = self._build_tags(outcome)
        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            namespace=_NAMESPACE,
            content=NotBlankStr(outcome.model_dump_json()),
            metadata=MemoryMetadata(
                source=NotBlankStr(str(outcome.proposal_id)),
                confidence=outcome.confidence_at_decision,
                tags=tags,
            ),
        )
        try:
            memory_id = await self._backend.store(self._agent_id, request)
        except Exception as exc:
            logger.error(
                COS_OUTCOME_RECORD_FAILED,
                proposal_id=str(outcome.proposal_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            COS_OUTCOME_RECORDED,
            proposal_id=str(outcome.proposal_id),
            decision=outcome.decision,
            rule=outcome.source_rule,
            altitude=outcome.altitude.value,
            memory_id=memory_id,
        )
        return memory_id

    async def get_stats(
        self,
        rule_name: NotBlankStr,
        altitude: ProposalAltitude,
    ) -> OutcomeStats | None:
        """Get aggregated approval stats for a rule/altitude pair.

        Returns ``None`` if fewer than ``min_outcomes`` decisions
        have been recorded.

        Args:
            rule_name: Name of the triggering rule.
            altitude: Proposal altitude to filter by.

        Returns:
            Aggregated statistics or None if insufficient data.
        """
        entries = await self._backend.retrieve(
            self._agent_id,
            MemoryQuery(
                namespaces=frozenset({_NAMESPACE}),
                categories=frozenset({MemoryCategory.EPISODIC}),
                tags=(
                    NotBlankStr(f"rule:{rule_name}"),
                    NotBlankStr(f"altitude:{altitude.value}"),
                ),
                # Cap at 1000 most recent entries to bound memory.
                # Sufficient for confidence learning; older outcomes
                # contribute negligible weight to EMA/Bayesian models.
                limit=1000,
            ),
        )
        approved = 0
        rejected = 0
        latest_at = datetime.min.replace(tzinfo=UTC)
        for entry in entries:
            try:
                outcome = ProposalOutcome.model_validate_json(entry.content)
            except Exception:
                logger.warning(
                    COS_OUTCOME_RECORD_FAILED,
                    memory_id=entry.id,
                    reason="deserialization_failed",
                )
                continue
            if outcome.decision == "approved":
                approved += 1
            else:
                rejected += 1
            latest_at = max(latest_at, outcome.decided_at)
        total = approved + rejected
        if total < self._min_outcomes:
            return None
        return OutcomeStats(
            rule_name=rule_name,
            altitude=altitude,
            total_proposals=total,
            approved_count=approved,
            rejected_count=rejected,
            last_updated=latest_at,
        )

    async def recent_outcomes(
        self,
        *,
        rule_name: NotBlankStr | None = None,
        altitude: ProposalAltitude | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[ProposalOutcome, ...]:
        """Retrieve recent outcomes with optional filtering.

        Args:
            rule_name: Filter by rule name.
            altitude: Filter by proposal altitude.
            limit: Maximum entries to return.

        Returns:
            Recent outcomes ordered by creation time (newest first).
        """
        tags: list[NotBlankStr] = []
        if rule_name is not None:
            tags.append(NotBlankStr(f"rule:{rule_name}"))
        if altitude is not None:
            tags.append(NotBlankStr(f"altitude:{altitude.value}"))
        entries = await self._backend.retrieve(
            self._agent_id,
            MemoryQuery(
                namespaces=frozenset({_NAMESPACE}),
                categories=frozenset({MemoryCategory.EPISODIC}),
                tags=tuple(tags),
                limit=limit,
            ),
        )
        results: list[ProposalOutcome] = []
        for entry in entries:
            try:
                results.append(
                    ProposalOutcome.model_validate_json(entry.content),
                )
            except Exception:
                logger.warning(
                    COS_OUTCOME_RECORD_FAILED,
                    memory_id=entry.id,
                    reason="deserialization_failed",
                )
        return tuple(results)

    @staticmethod
    def _build_tags(outcome: ProposalOutcome) -> tuple[NotBlankStr, ...]:
        """Build filterable metadata tags from an outcome."""
        tags: list[NotBlankStr] = [
            NotBlankStr(f"altitude:{outcome.altitude.value}"),
            NotBlankStr(f"decision:{outcome.decision}"),
        ]
        if outcome.source_rule is not None:
            tags.append(NotBlankStr(f"rule:{outcome.source_rule}"))
        return tuple(tags)
