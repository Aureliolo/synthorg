"""Repository protocol for subworkflow persistence.

A subworkflow is a ``WorkflowDefinition`` published to the registry under
a specific ``(subworkflow_id, semver)`` coordinate.  Unlike live workflow
definitions (which are mutable and use optimistic concurrency), subworkflow
versions are immutable -- updating a subworkflow always creates a new
semver row.  Parent workflows pin a specific version in their
``SUBWORKFLOW`` node configs; deleting a pinned version is rejected.
"""

from typing import Protocol, override, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition

# ``ParentReference`` and ``SubworkflowSummary`` appear in protocol
# method annotations (``find_parents``, ``list_summaries``,
# ``delete_if_unreferenced``, ``search``); under PEP 649 lazy
# annotation evaluation they must be resolvable from module globals
# when introspectors call ``typing.get_type_hints()``.
from synthorg.engine.workflow.subworkflow_models import (
    ParentReference,
    SubworkflowSummary,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository

__all__ = ["SubworkflowRepository"]

SubworkflowKey = tuple[NotBlankStr, NotBlankStr]
"""Composite primary key: ``(subworkflow_id, semver)``."""


@runtime_checkable
class SubworkflowRepository(
    IdKeyedRepository[WorkflowDefinition, SubworkflowKey],
    Protocol,
):
    """CRUD interface for subworkflow persistence.

    Composes :class:`IdKeyedRepository` (ADR-0001) with composite key
    ``(subworkflow_id, semver)`` per D8. Bespoke per D7:
    :meth:`list_versions` returns semver strings for a subworkflow;
    :meth:`list_summaries` aggregates latest-version summaries;
    :meth:`search` does full-text-like substring matching; and
    :meth:`delete_if_unreferenced` is an atomic check-and-delete
    to eliminate TOCTOU races; :meth:`find_parents` scans upstream
    references with domain logic (scanning nested subworkflows).
    """

    @override
    async def save(self, entity: WorkflowDefinition, /) -> None:
        """Persist a new subworkflow version (insert-only).

        The entity's ``id`` is interpreted as the ``subworkflow_id``
        and its ``version`` (semver) as the version coordinate.  Writing
        the same ``(id, version)`` twice is rejected.

        Args:
            entity: The workflow definition to publish.

        Raises:
            PersistenceError: If the operation fails.
            DuplicateRecordError: If the ``(id, version)`` already exists.
        """
        ...

    @override
    async def get(self, entity_id: SubworkflowKey, /) -> WorkflowDefinition | None:
        """Fetch a specific subworkflow version.

        Args:
            entity_id: ``(subworkflow_id, semver)`` tuple.

        Returns:
            The definition, or ``None`` if not found.
        """
        ...

    @override
    async def delete(self, entity_id: SubworkflowKey, /) -> bool:
        """Delete a specific subworkflow version.

        Deletion protection (rejecting when a parent pins the version)
        is enforced at the service layer, not here.

        Args:
            entity_id: ``(subworkflow_id, semver)`` tuple.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[WorkflowDefinition, ...]:
        """List entities in ``(subworkflow_id, semver)`` order (paginated).

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Paginated definitions ordered by composite key ascending.

        Raises:
            PersistenceError: If the query fails.
        """
        ...

    async def list_versions(
        self,
        subworkflow_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[NotBlankStr, ...]:
        """List semver strings for a subworkflow, newest first.

        Bespoke per ADR-0001 D7.

        Args:
            subworkflow_id: The subworkflow identifier.
            limit: Maximum versions to return.

        Returns:
            Tuple of semver strings sorted by ``packaging.version``
            comparison descending, capped at *limit* rows. Empty when
            the subworkflow does not exist.
        """
        ...

    async def list_summaries(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[SubworkflowSummary, ...]:
        """Return summaries for unique subworkflows in the registry.

        Bespoke per ADR-0001 D7. The summary reflects the latest version
        of each subworkflow.

        Args:
            limit: Maximum summaries to return.

        Returns:
            Summaries for unique subworkflows, sorted by subworkflow_id.
        """
        ...

    async def search(
        self,
        query: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SubworkflowSummary, ...]:
        """Search subworkflows by case-insensitive substring (paginated).

        Bespoke per ADR-0001 D7. Matches against name or description
        fields.

        Args:
            query: Search term.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of matching summaries in ``subworkflow_id`` order.
            Callers needing every match drain via
            :func:`synthorg.persistence._shared.collect_all`.
        """
        ...

    async def delete_if_unreferenced(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> tuple[bool, tuple[ParentReference, ...]]:
        """Atomically delete a subworkflow version if no parents pin it.

        Bespoke per ADR-0001 D7. The check-and-delete runs inside a
        single transaction to
        eliminate the TOCTOU race between ``find_parents`` and
        ``delete``.

        Args:
            subworkflow_id: The subworkflow identifier.
            version: The semver string.

        Returns:
            ``(True, ())`` when the version was deleted.
            ``(False, parents)`` when parents still reference it.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def find_parents(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr | None = None,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ParentReference, ...]:
        """Find parent workflow definitions referencing a subworkflow.

        Bespoke per ADR-0001 D7.

        Args:
            subworkflow_id: The subworkflow identifier.
            version: Optional semver filter.  When ``None``, returns
                parents pinning any version of the subworkflow.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            A page of parent references in
            ``(parent_type, parent_id, node_id, pinned_version)``
            order. Referential-integrity callers MUST drain every page
            via :func:`synthorg.persistence._shared.collect_all`; a
            truncated parent set would let a still-referenced version
            be deleted.
        """
        ...
