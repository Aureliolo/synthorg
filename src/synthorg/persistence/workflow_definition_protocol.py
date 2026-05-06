"""Repository protocol for workflow definition persistence."""

from typing import Protocol, runtime_checkable

from synthorg.core.enums import WorkflowType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.definition import WorkflowDefinition  # noqa: TC001


@runtime_checkable
class WorkflowDefinitionRepository(Protocol):
    """CRUD interface for workflow definition persistence.

    Workflow definitions are design-time blueprints for visual
    workflow graphs, stored with their full node/edge data.
    """

    async def save(self, definition: WorkflowDefinition) -> None:
        """Persist a workflow definition (insert or update).

        Args:
            definition: The workflow definition to persist.

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

    async def get(self, definition_id: NotBlankStr) -> WorkflowDefinition | None:
        """Retrieve a workflow definition by its ID.

        Args:
            definition_id: The definition identifier.

        Returns:
            The definition, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_definitions(
        self,
        *,
        workflow_type: WorkflowType | None = None,
    ) -> tuple[WorkflowDefinition, ...]:
        """List workflow definitions with optional filters.

        Args:
            workflow_type: Filter by workflow type.

        Returns:
            Matching definitions as a tuple.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, definition_id: NotBlankStr) -> bool:
        """Delete a workflow definition by ID.

        Args:
            definition_id: The definition identifier.

        Returns:
            ``True`` if the definition was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
