"""WorkflowRollbackService -- audit-aware facade for rollback persistence.

The :class:`WorkflowVersionController.rollback_workflow` handler
previously called ``repo.save(rolled_back)`` directly on the
workflow_definitions repository, then constructed a fresh
:class:`VersioningService` to record the post-rollback snapshot.
Audit ``68-state-mutation-leaks`` flagged the direct repo write as a
service-boundary bypass that must route through one cohesive service
so audit logging cannot regress when a new write path is added to the
rollback contract.

This service centralises:

1. The durable definition save (raises :class:`VersionConflictError`
   on optimistic-concurrency mismatch -- the controller still owns
   the 409 translation).
2. The best-effort post-rollback snapshot via
   :class:`VersioningService.snapshot_if_changed`.  A snapshot failure
   is logged at WARNING and swallowed -- the rollback itself has
   already been persisted, so dropping the audit row keeps service
   availability while operators receive the WARN signal.
3. The audit-grade :data:`WORKFLOW_DEF_ROLLED_BACK` event emitted on
   success.

The service is constructed per request from the controller (it is a
thin two-method object whose lifecycle is bound to the request, not
the process); no AppState wiring is required.
"""

from typing import TYPE_CHECKING

from synthorg.core.persistence_errors import PersistenceError
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,  # noqa: TC001 -- runtime annotation
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_ROLLED_BACK,
)
from synthorg.observability.events.workflow_version import (
    WORKFLOW_VERSION_SNAPSHOT_FAILED,
)
from synthorg.versioning import VersioningService

if TYPE_CHECKING:
    from synthorg.persistence.version_repo import VersionRepository
    from synthorg.persistence.workflow_definition_repo import (
        WorkflowDefinitionRepository,
    )

logger = get_logger(__name__)


class WorkflowRollbackService:
    """Audit-aware facade over the rollback save + snapshot pair.

    Args:
        definition_repo: Repository for the live ``workflow_definitions``
            table; ``save(...)`` is the optimistic-concurrency point.
        version_repo: Repository for ``workflow_definition_versions``;
            wrapped in a fresh :class:`VersioningService` per call so
            no service state survives across requests.
    """

    __slots__ = ("_definition_repo", "_version_repo")

    def __init__(
        self,
        *,
        definition_repo: WorkflowDefinitionRepository,
        version_repo: VersionRepository[WorkflowDefinition],
    ) -> None:
        self._definition_repo = definition_repo
        self._version_repo = version_repo

    async def rollback(
        self,
        rolled_back: WorkflowDefinition,
        *,
        target_version: int,
        saved_by: NotBlankStr,
    ) -> WorkflowDefinition:
        """Persist ``rolled_back`` and snapshot the new revision.

        Raises:
            VersionConflictError: When the optimistic-concurrency
                guard on ``definition_repo.save`` rejects the write
                (the controller catches this and returns 409).

        Returns ``rolled_back`` unchanged so the caller can serialise
        it onto the response without re-fetching.
        """
        await self._definition_repo.save(rolled_back)
        snapshot_service = VersioningService(self._version_repo)
        try:
            await snapshot_service.snapshot_if_changed(
                entity_id=rolled_back.id,
                snapshot=rolled_back,
                saved_by=saved_by,
            )
        except PersistenceError as exc:
            # Snapshot is best-effort: the rollback itself has already
            # been committed, so a snapshot write failure must not
            # surface as a 5xx.  Use safe_error_description so wrapped
            # causes cannot leak backend internals into the WARN line.
            logger.warning(
                WORKFLOW_VERSION_SNAPSHOT_FAILED,
                definition_id=rolled_back.id,
                revision=rolled_back.revision,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        logger.info(
            WORKFLOW_DEF_ROLLED_BACK,
            definition_id=rolled_back.id,
            target_version=target_version,
            new_revision=rolled_back.revision,
        )
        return rolled_back


__all__ = ["WorkflowRollbackService"]
