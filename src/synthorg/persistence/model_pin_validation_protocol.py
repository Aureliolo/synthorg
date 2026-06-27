# module-kind: declarative
"""Repository protocol for prompt-class pin-validation records.

A :class:`ModelPinValidationRow` row is the durable record of a prompt
class's last clean pin validation, keyed by ``prompt_class_id``. The
repository composes only the generic :class:`IdKeyedRepository` surface
(ADR-0001): ``save`` (upsert), ``get``, ``delete``, and ``list_items``
(which the audit dashboard uses to list pin freshness). No bespoke
methods.

Concrete implementations live in the backend packages
(``synthorg.persistence.sqlite`` / ``synthorg.persistence.postgres``).
All protocols are ``@runtime_checkable``; all methods are ``async``.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.llm.model_pin_validation import ModelPinValidationRow
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


@runtime_checkable
class ModelPinValidationRepository(
    IdKeyedRepository[ModelPinValidationRow, NotBlankStr],
    Protocol,
):
    """Id-keyed CRUD for prompt-class pin-validation records.

    Composes :class:`IdKeyedRepository` (ADR-0001) keyed by
    ``prompt_class_id``. ``save`` is an upsert so re-validating a prompt
    class replaces the prior row. No bespoke methods beyond the generic
    surface.

    Non-recoverable errors propagate. Constraint violations raise
    :class:`~synthorg.core.persistence_errors.ConstraintViolationError`;
    other database errors raise
    :class:`~synthorg.core.persistence_errors.QueryError`.
    """

    @override
    async def save(self, entity: ModelPinValidationRow, /) -> None:
        """Upsert a validation row keyed by ``prompt_class_id``.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> ModelPinValidationRow | None:
        """Retrieve a validation row by ``prompt_class_id``, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a validation row by ``prompt_class_id``. ``True`` iff a row existed.

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
    ) -> tuple[ModelPinValidationRow, ...]:
        """List rows ordered by ``prompt_class_id`` ascending (paginated).

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        ...


__all__ = ["ModelPinValidationRepository"]
