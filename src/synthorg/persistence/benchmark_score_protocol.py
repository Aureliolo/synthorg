# module-kind: declarative
"""Repository protocol for measured per-model benchmark scores.

A :class:`BenchmarkScoreRecord` row is the durable record of a per-model
quality score measured offline from a recorded eval run. The repository
is keyed by ``model_id`` and composes only the generic
:class:`IdKeyedRepository` surface (ADR-0001): ``save`` (upsert),
``get``, ``delete``, and ``list_items`` (which the
:class:`~synthorg.budget.benchmark_measured.MeasuredBenchmarkScoreProvider`
uses to serve ``list_scores``). No bespoke methods.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class BenchmarkScoreRepository(
    IdKeyedRepository[BenchmarkScoreRecord, NotBlankStr],
    Protocol,
):
    """Id-keyed CRUD for measured per-model benchmark scores.

    Composes :class:`IdKeyedRepository` (ADR-0001) keyed by
    ``model_id``. ``save`` is an upsert so re-recording a model's score
    replaces the prior row. No bespoke methods beyond the generic
    surface.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`~synthorg.core.persistence_errors.ConstraintViolationError`;
    other database errors raise
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: BenchmarkScoreRecord, /) -> None:
        """Upsert a benchmark-score row keyed by ``model_id``.

        Raises:
            ConstraintViolationError: On constraint violations (e.g. the
                confidence-band CHECK).
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> BenchmarkScoreRecord | None:
        """Retrieve a score by ``model_id``, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a score by ``model_id``. ``True`` iff a row existed.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BenchmarkScoreRecord, ...]:
        """List scores, ordered by ``model_id`` ascending (paginated).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...


__all__ = ["BenchmarkScoreRepository"]
