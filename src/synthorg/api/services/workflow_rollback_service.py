"""WorkflowRollbackService -- audit-aware facade for rollback persistence.

Routes the rollback save and the post-rollback snapshot through one
cohesive service so callers get:

1. The durable definition save, which may raise
   :class:`PersistenceVersionConflictError` on optimistic-concurrency
   mismatch.
2. A best-effort post-rollback snapshot via
   :class:`VersioningService.snapshot_if_changed`. Snapshot failures
   are logged at WARNING and swallowed so a snapshot write failure
   cannot fail the whole rollback after the durable save committed.
3. The audit-grade :data:`WORKFLOW_DEF_ROLLED_BACK` event emitted on
   success.

The service is a thin two-method object constructed per request; no
AppState wiring is required.
"""

from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import NotFoundError, VersionConflictError
from synthorg.core.persistence_errors import PersistenceError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_NOT_FOUND,
    WORKFLOW_DEF_ROLLED_BACK,
    WORKFLOW_DEF_VERSION_CONFLICT,
)
from synthorg.observability.events.workflow_version import (
    WORKFLOW_VERSION_SNAPSHOT_FAILED,
)
from synthorg.versioning import VersioningService

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.persistence.version_protocol import VersionRepository
    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionRepository,
    )
    from synthorg.versioning.models import RollbackWorkflowRequest, VersionSnapshot

logger = get_logger(__name__)


class WorkflowRollbackService:
    """Audit-aware facade over the rollback save + snapshot pair.

    Args:
        definition_repo: Repository for the live ``workflow_definitions``
            table; ``save(...)`` is the optimistic-concurrency point.
        version_repo: Repository for ``workflow_definition_versions``;
            wrapped in a fresh :class:`VersioningService` per call so
            no service state survives across requests.
        clock: Time source used when stamping ``updated_at`` during
            :meth:`prepare_rollback`. Tests inject a :class:`FakeClock`
            for deterministic ``updated_at`` values.
    """

    __slots__ = ("_clock", "_definition_repo", "_version_repo")

    def __init__(
        self,
        *,
        definition_repo: WorkflowDefinitionRepository,
        version_repo: VersionRepository[WorkflowDefinition],
        clock: Clock | None = None,
    ) -> None:
        self._definition_repo = definition_repo
        self._version_repo = version_repo
        self._clock = clock if clock is not None else SystemClock()

    async def _fetch_existing(
        self,
        workflow_id: NotBlankStr,
        expected_revision: int,
    ) -> WorkflowDefinition:
        """Return the live definition or raise ``NotFoundError`` / version conflict.

        Returns:
            ``WorkflowDefinition`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            VersionConflictError: Raised on the corresponding failure path.
        """
        existing = await self._definition_repo.get(workflow_id)
        if existing is None:
            logger.warning(WORKFLOW_DEF_NOT_FOUND, definition_id=workflow_id)
            msg = "Workflow definition not found"
            raise NotFoundError(msg)
        if expected_revision != existing.revision:
            logger.warning(
                WORKFLOW_DEF_VERSION_CONFLICT,
                definition_id=workflow_id,
                expected=expected_revision,
                actual=existing.revision,
            )
            msg = "Version conflict: the workflow was modified. Reload and retry."
            raise VersionConflictError(msg)
        return existing

    async def _fetch_target(
        self,
        workflow_id: NotBlankStr,
        target_version: int,
    ) -> VersionSnapshot[WorkflowDefinition]:
        """Return the target snapshot or raise ``NotFoundError``.

        Returns:
            ``VersionSnapshot[WorkflowDefinition]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        target = await self._version_repo.get_version(workflow_id, target_version)
        if target is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
                version=target_version,
            )
            msg = f"Target version {target_version} not found"
            raise NotFoundError(msg)
        return target

    @staticmethod
    def _build_rolled_back(
        existing: WorkflowDefinition,
        target: VersionSnapshot[WorkflowDefinition],
        now: datetime,
    ) -> WorkflowDefinition:
        """Restore ``target``'s content onto a new revision of ``existing``.

        Returns:
            ``WorkflowDefinition`` instance.
        """
        return target.snapshot.model_copy(
            update={
                "id": existing.id,
                "version": existing.version,
                "created_by": existing.created_by,
                "created_at": existing.created_at,
                "updated_at": now,
                "revision": existing.revision + 1,
            },
            deep=True,
        )

    async def prepare_rollback(
        self,
        workflow_id: NotBlankStr,
        request: RollbackWorkflowRequest,
        *,
        saved_by: NotBlankStr,
    ) -> WorkflowDefinition:
        """Validate, build, and persist a rollback in one service call.

        Combines the existence + revision check, target-version lookup,
        rolled-back definition construction, and the durable
        :meth:`rollback` write so the HTTP controller has a single
        entry point and never reaches into ``app_state.persistence``
        directly.

        Returns:
            ``WorkflowDefinition`` instance.
        """
        existing = await self._fetch_existing(workflow_id, request.expected_revision)
        target = await self._fetch_target(workflow_id, request.target_version)
        rolled_back = self._build_rolled_back(existing, target, self._clock.now())
        return await self.rollback(
            rolled_back,
            target_version=request.target_version,
            saved_by=saved_by,
        )

    async def rollback(
        self,
        rolled_back: WorkflowDefinition,
        *,
        target_version: int,
        saved_by: NotBlankStr,
    ) -> WorkflowDefinition:
        """Persist ``rolled_back`` and snapshot the new revision.

        Raises:
            PersistenceVersionConflictError: When the optimistic-concurrency
                guard on ``definition_repo.save`` rejects the write.

        Returns ``rolled_back`` unchanged so the caller can serialise
        it onto the response without re-fetching.

        Returns:
            ``WorkflowDefinition`` instance.
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
