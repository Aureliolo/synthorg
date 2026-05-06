"""Repository protocol for subworkflow persistence.

A subworkflow is a ``WorkflowDefinition`` published to the registry under
a specific ``(subworkflow_id, semver)`` coordinate.  Unlike live workflow
definitions (which are mutable and use optimistic concurrency), subworkflow
versions are immutable -- updating a subworkflow always creates a new
semver row.  Parent workflows pin a specific version in their
``SUBWORKFLOW`` node configs; deleting a pinned version is rejected.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.definition import WorkflowDefinition  # noqa: TC001

# ``ParentReference`` and ``SubworkflowSummary`` appear in protocol
# method annotations (``find_parents``, ``list_summaries``,
# ``delete_if_unreferenced``, ``search``); under PEP 649 lazy
# annotation evaluation they must be resolvable from module globals
# when introspectors call ``inspect.get_type_hints()``.
from synthorg.engine.workflow.subworkflow_models import (  # noqa: TC001 -- runtime-resolvable annotation
    ParentReference,
    SubworkflowSummary,
)

__all__ = ["SubworkflowRepository"]


@runtime_checkable
class SubworkflowRepository(Protocol):
    """CRUD interface for subworkflow persistence.

    Subworkflows are stored keyed by ``(subworkflow_id, semver)``.
    """

    async def save(self, definition: WorkflowDefinition) -> None:
        """Persist a new subworkflow version.

        The definition's ``id`` is interpreted as the ``subworkflow_id``
        and its ``version`` (semver) as the version coordinate.  Writing
        the same ``(id, version)`` twice is rejected.

        Args:
            definition: The workflow definition to publish.

        Raises:
            PersistenceError: If the operation fails.
            DuplicateRecordError: If the ``(id, version)`` already exists.
        """
        ...

    async def get(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> WorkflowDefinition | None:
        """Fetch a specific subworkflow version.

        Args:
            subworkflow_id: The subworkflow identifier.
            version: The semver string.

        Returns:
            The definition, or ``None`` if not found.
        """
        ...

    async def list_versions(
        self,
        subworkflow_id: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        """List all semver strings for a subworkflow, newest first.

        Args:
            subworkflow_id: The subworkflow identifier.

        Returns:
            Tuple of semver strings sorted by ``packaging.version``
            comparison descending.  Empty when the subworkflow does
            not exist.
        """
        ...

    async def list_summaries(self) -> tuple[SubworkflowSummary, ...]:
        """Return a summary for every unique subworkflow in the registry.

        The summary reflects the latest version of each subworkflow.
        """
        ...

    async def search(
        self,
        query: NotBlankStr,
    ) -> tuple[SubworkflowSummary, ...]:
        """Search subworkflows by case-insensitive substring in name or description.

        Args:
            query: Search term.

        Returns:
            Matching summaries.
        """
        ...

    async def delete(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> bool:
        """Delete a specific subworkflow version.

        Deletion protection (rejecting when a parent pins the version)
        is enforced at the service layer, not here.

        Args:
            subworkflow_id: The subworkflow identifier.
            version: The semver string.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.
        """
        ...

    async def delete_if_unreferenced(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> tuple[bool, tuple[ParentReference, ...]]:
        """Atomically delete a subworkflow version only if no parents pin it.

        The check-and-delete runs inside a single transaction to
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
    ) -> tuple[ParentReference, ...]:
        """Find parent workflow definitions referencing a subworkflow.

        Args:
            subworkflow_id: The subworkflow identifier.
            version: Optional semver filter.  When ``None``, returns
                parents pinning any version of the subworkflow.

        Returns:
            Tuple of parent references (possibly empty).
        """
        ...
