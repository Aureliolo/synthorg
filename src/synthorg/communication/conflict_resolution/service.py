"""Conflict resolution service orchestrator (see Communication design page).

Follows the ``DelegationService`` pattern: ``__slots__``, keyword-only
constructor, audit trail list, structured logging.
"""

from collections.abc import Mapping, Sequence  # noqa: TC003
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

from pydantic import AwareDatetime  # noqa: TC002

from synthorg.communication.conflict_resolution.config import (  # noqa: TC001
    ConflictResolutionConfig,
)
from synthorg.communication.conflict_resolution.models import (
    _MIN_POSITIONS,
    Conflict,
    ConflictPosition,
    ConflictResolution,
    DissentRecord,
)
from synthorg.communication.conflict_resolution.protocol import (  # noqa: TC001
    ConflictResolver,
)
from synthorg.communication.enums import (
    ConflictResolutionStrategy,  # noqa: TC001
    ConflictType,  # noqa: TC001
)
from synthorg.communication.errors import ConflictResolutionError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import (
    CONFLICT_DETECTED,
    CONFLICT_DISSENT_QUERIED,
    CONFLICT_DISSENT_RECORDED,
    CONFLICT_NO_RESOLVER,
    CONFLICT_RESOLUTION_FAILED,
    CONFLICT_RESOLUTION_STARTED,
    CONFLICT_RESOLVED,
    CONFLICT_VALIDATION_ERROR,
)

logger = get_logger(__name__)


class ConflictResolutionService:
    """Orchestrates conflict detection, resolution, and audit.

    Selects the configured strategy, delegates to the resolver,
    builds dissent records for all overruled positions, and
    maintains an audit trail.

    Args:
        config: Conflict resolution configuration.
        resolvers: Strategy → resolver mapping.
    """

    __slots__ = ("_audit_trail", "_config", "_resolvers")

    def __init__(
        self,
        *,
        config: ConflictResolutionConfig,
        resolvers: Mapping[ConflictResolutionStrategy, ConflictResolver],
    ) -> None:
        self._config = config
        self._resolvers: MappingProxyType[
            ConflictResolutionStrategy, ConflictResolver
        ] = MappingProxyType(dict(resolvers))
        self._audit_trail: list[DissentRecord] = []

    def create_conflict(
        self,
        *,
        conflict_type: ConflictType,
        subject: NotBlankStr,
        positions: Sequence[ConflictPosition],
        task_id: NotBlankStr | None = None,
    ) -> Conflict:
        """Create a conflict from agent positions.

        Validates minimum positions and unique agent IDs,
        and generates an ID.

        Args:
            conflict_type: Category of the conflict.
            subject: Brief description of the dispute.
            positions: Agent positions (minimum 2).
            task_id: Related task ID, if any.

        Returns:
            New Conflict instance.

        Raises:
            ConflictResolutionError: If fewer than 2 positions or
                duplicate agent IDs.
        """
        if len(positions) < _MIN_POSITIONS:
            msg = "A conflict requires at least 2 positions"
            logger.warning(
                CONFLICT_VALIDATION_ERROR,
                error=msg,
                position_count=len(positions),
            )
            raise ConflictResolutionError(msg)

        agent_ids = [p.agent_id for p in positions]
        if len(agent_ids) != len(set(agent_ids)):
            msg = "Duplicate agent_id in conflict positions"
            logger.warning(
                CONFLICT_VALIDATION_ERROR,
                error=msg,
                agent_ids=agent_ids,
            )
            raise ConflictResolutionError(msg)

        conflict = Conflict(
            id=f"conflict-{uuid4().hex[:12]}",
            type=conflict_type,
            task_id=task_id,
            subject=subject,
            positions=tuple(positions),
            detected_at=datetime.now(UTC),
        )

        logger.info(
            CONFLICT_DETECTED,
            conflict_id=conflict.id,
            conflict_type=conflict.type,
            subject=conflict.subject,
            is_cross_department=conflict.is_cross_department,
            agent_count=len(positions),
        )

        return conflict

    async def resolve(
        self,
        conflict: Conflict,
    ) -> tuple[ConflictResolution, tuple[DissentRecord, ...]]:
        """Resolve a conflict using the configured strategy.

        Args:
            conflict: The conflict to resolve.

        Returns:
            Tuple of ``(resolution, dissent_records)``.

        Raises:
            ConflictResolutionError: If the configured strategy has
                no registered resolver.
        """
        strategy = self._config.strategy
        resolver = self._resolvers.get(strategy)
        if resolver is None:
            msg = f"No resolver registered for strategy {strategy!r}"
            logger.warning(
                CONFLICT_NO_RESOLVER,
                strategy=strategy,
                error=msg,
            )
            raise ConflictResolutionError(
                msg,
                context={"strategy": strategy},
            )

        logger.info(
            CONFLICT_RESOLUTION_STARTED,
            conflict_id=conflict.id,
            strategy=strategy,
        )

        try:
            resolution = await resolver.resolve(conflict)
        except Exception:
            logger.exception(
                CONFLICT_RESOLUTION_FAILED,
                conflict_id=conflict.id,
                strategy=strategy,
            )
            raise

        dissent_records = resolver.build_dissent_records(conflict, resolution)
        self._audit_trail.extend(dissent_records)

        logger.info(
            CONFLICT_RESOLVED,
            conflict_id=conflict.id,
            outcome=resolution.outcome,
            winning_agent_id=resolution.winning_agent_id,
        )
        for record in dissent_records:
            logger.info(
                CONFLICT_DISSENT_RECORDED,
                dissent_id=record.id,
                conflict_id=conflict.id,
                dissenting_agent=record.dissenting_agent_id,
            )

        return resolution, dissent_records

    def get_dissent_records(self) -> tuple[DissentRecord, ...]:
        """Return all dissent records.

        Returns:
            Tuple of dissent records in chronological order.
        """
        return tuple(self._audit_trail)

    def query_dissent_records(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        conflict_type: ConflictType | None = None,
        strategy: ConflictResolutionStrategy | None = None,
        since: AwareDatetime | None = None,
    ) -> tuple[DissentRecord, ...]:
        """Query dissent records with optional filters.

        All filters are combined with AND logic.

        Args:
            agent_id: Filter by dissenting agent ID.
            conflict_type: Filter by conflict type.
            strategy: Filter by strategy used.
            since: Filter by records after this timestamp
                (must be timezone-aware).

        Returns:
            Matching dissent records.
        """
        logger.debug(
            CONFLICT_DISSENT_QUERIED,
            agent_id=agent_id,
            conflict_type=conflict_type,
            strategy=strategy,
            since=str(since) if since else None,
        )

        return tuple(
            r
            for r in self._audit_trail
            if (agent_id is None or r.dissenting_agent_id == agent_id)
            and (conflict_type is None or r.conflict.type == conflict_type)
            and (strategy is None or r.strategy_used == strategy)
            and (since is None or r.timestamp >= since)
        )
