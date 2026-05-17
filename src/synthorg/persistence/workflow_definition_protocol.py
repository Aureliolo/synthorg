"""Repository protocol for workflow definition persistence."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import WorkflowType  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT  # noqa: F401


class WorkflowDefinitionFilterSpec(BaseModel):
    """Filter spec for ``WorkflowDefinitionRepository.query`` (ADR-0001)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    workflow_type: WorkflowType | None = Field(default=None)


@runtime_checkable
class WorkflowDefinitionRepository(
    IdKeyedRepository[WorkflowDefinition, NotBlankStr],
    FilteredQueryRepository[WorkflowDefinition, WorkflowDefinitionFilterSpec],
    Protocol,
):
    """CRUD interface for workflow definition persistence.

    Workflow definitions are design-time blueprints for visual
    workflow graphs, stored with their full node/edge data.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001). Bespoke per D7: ``create_if_absent`` and
    ``update_if_exists`` are atomic CAS variants that the upsert-based
    :meth:`save` cannot express; they preserve distinct create-vs-update
    audit semantics that the service layer depends on.
    """

    async def save(self, entity: WorkflowDefinition) -> None:
        """Persist a workflow definition (insert or update).

        Args:
            entity: The workflow definition to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def create_if_absent(self, definition: WorkflowDefinition) -> bool:
        """Atomically insert a definition iff no row with the same id exists.

        Implementations MUST rely on backend-native conflict semantics
        (``INSERT ... ON CONFLICT DO NOTHING`` or equivalent) so two
        concurrent callers cannot both see "not found" and then both
        insert. The check-then-save pattern at the service layer is
        vulnerable to TOCTOU; this atomic path closes that window.

        Args:
            definition: The workflow definition to insert.

        Returns:
            ``True`` when the row was inserted, ``False`` when an
            existing row with ``definition.id`` already existed and
            the insert was skipped.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def update_if_exists(self, definition: WorkflowDefinition) -> bool:
        """Update an existing definition iff a row with the same id exists.

        Pair with :meth:`create_if_absent` to keep create and update
        audit semantics distinct: :meth:`save` is upsert and can
        silently resurrect a row that was deleted after the caller's
        existence check, which would then be logged as an update. This
        method issues a conditional UPDATE and returns ``False`` when
        no row was found so the service layer can raise
        ``WorkflowDefinitionNotFoundError`` instead of emitting a
        misleading ``WORKFLOW_DEF_UPDATED`` event.

        Args:
            definition: The workflow definition to update.

        Returns:
            ``True`` when a row was updated, ``False`` when no row
            matched the id.

        Raises:
            PersistenceError: If the operation fails.
            PersistenceVersionConflictError: If optimistic-concurrency
                fields do not match (backends that enforce ``revision``-based
                concurrency should raise).
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> WorkflowDefinition | None:
        """Retrieve a workflow definition by its ID.

        Args:
            entity_id: The definition identifier.

        Returns:
            The definition, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        """List workflow definitions in id order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Definitions in ascending id order.
        """
        ...

    async def query(
        self,
        filter_spec: WorkflowDefinitionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        """List workflow definitions matching the filter spec.

        Args:
            filter_spec: Carries optional ``workflow_type`` filter.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching definitions in ascending id order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def count(self, filter_spec: WorkflowDefinitionFilterSpec) -> int:
        """Count workflow definitions matching the filter spec."""
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a workflow definition by ID.

        Args:
            entity_id: The definition identifier.

        Returns:
            ``True`` if the definition was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
