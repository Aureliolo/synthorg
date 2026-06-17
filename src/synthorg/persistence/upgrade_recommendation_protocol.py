"""Repository protocol for persisted upgrade recommendations.

A :class:`StoredUpgradeRecommendation` records a newer-in-family model
the refresh service surfaced, with a review lifecycle
(``PENDING -> APPROVED``/``REJECTED``/``AUTO_APPLIED``).  The repository
composes :class:`StatefulRepository` (atomic status CAS, carrying the
``decided_at`` / ``decided_by`` status-correlated columns) and
:class:`FilteredQueryRepository` (lookup by status).

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from datetime import datetime
from typing import Protocol, TypedDict, override, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    StatefulRepository,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.upgrade_models import StoredUpgradeRecommendation


class UpgradeRecommendationTransitionUpdates(TypedDict, total=False):
    """Status-correlated columns accepted by ``transition_if``."""

    decided_at: datetime
    decided_by: str


class UpgradeRecommendationFilterSpec(BaseModel):
    """Filter spec for ``UpgradeRecommendationRepository.query``.

    All fields optional; an empty spec matches every recommendation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: RecommendationStatus | None = Field(default=None)


@runtime_checkable
class UpgradeRecommendationRepository(
    StatefulRepository[StoredUpgradeRecommendation, UUID, RecommendationStatus],
    FilteredQueryRepository[
        StoredUpgradeRecommendation, UpgradeRecommendationFilterSpec
    ],
    Protocol,
):
    """CRUD + state-transition + filtered query for upgrade recommendations.

    Composes :class:`StatefulRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). No bespoke methods.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`ConstraintViolationError`; other DB errors raise
    :class:`QueryError`.
    """

    @override
    async def save(self, entity: StoredUpgradeRecommendation, /) -> None:
        """Upsert a recommendation.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: UUID, /) -> StoredUpgradeRecommendation | None:
        """Retrieve a recommendation by id, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: UUID, /) -> bool:
        """Delete a recommendation by id. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[StoredUpgradeRecommendation, ...]:
        """List recommendations, newest-first (``created_at DESC, id DESC``).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def transition_if(
        self,
        /,
        entity_id: UUID,
        from_state: RecommendationStatus,
        to_state: RecommendationStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the recommendation status.

        ``**updates`` accepts the keys declared by
        :class:`UpgradeRecommendationTransitionUpdates` (``decided_at`` /
        ``decided_by``); any other key is rejected.

        Returns:
            ``True`` iff the row was in ``from_state`` and is now in
            ``to_state``; ``False`` on state mismatch or missing row.

        Raises:
            QueryError: On database errors, or if ``updates`` carries an
                unknown key.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: UpgradeRecommendationFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[StoredUpgradeRecommendation, ...]:
        """Return recommendations matching the spec, newest-first.

        Order is ``(created_at DESC, id DESC)``.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...

    @override
    async def count(self, filter_spec: UpgradeRecommendationFilterSpec) -> int:
        """Count recommendations matching the filter spec.

        Raises:
            QueryError: If the database query fails.
        """
        ...
