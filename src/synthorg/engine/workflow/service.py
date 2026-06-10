"""Workflow definition service layer.

Wraps :class:`WorkflowDefinitionRepository` +
:class:`VersionRepository[WorkflowDefinition]` so the ``/workflows``
controller does not touch ``app_state.persistence.*`` directly. Handles
the cascade from definition deletion to its version snapshots in one
place so the audit trail stays consistent.
"""

from typing import TYPE_CHECKING, ClassVar

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    VersionConflictError,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.validation_types import (
    WorkflowValidationResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_CREATE_CONFLICT,
    WORKFLOW_DEF_CREATED,
    WORKFLOW_DEF_DELETED,
    WORKFLOW_DEF_NOT_FOUND,
    WORKFLOW_DEF_UPDATED,
    WORKFLOW_DEF_VERSION_CONFLICT,
)
from synthorg.observability.events.workflow_version import (
    WORKFLOW_VERSION_SNAPSHOT_FAILED,
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT
from synthorg.persistence.version_protocol import VersionRepository
from synthorg.versioning.service import VersioningService

if TYPE_CHECKING:
    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionRepository,
    )

logger = get_logger(__name__)


class WorkflowDefinitionExistsError(ConflictError):
    """Raised when ``create_definition`` targets an id that already exists."""

    default_message: ClassVar[str] = "Workflow definition already exists"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    status_code: ClassVar[int] = 409


class WorkflowDefinitionNotFoundError(NotFoundError):
    """Raised when ``fetch_for_update`` / update targets a missing id."""

    default_message: ClassVar[str] = "Workflow definition not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_DEFINITION_NOT_FOUND
    status_code: ClassVar[int] = 404


class WorkflowDefinitionRevisionMismatchError(VersionConflictError):
    """Raised when an optimistic-concurrency revision check fails.

    Inherits the HTTP 409 / ``VERSION_CONFLICT`` ClassVar mapping from
    :class:`VersionConflictError`; the centralised RFC 9457 dispatch
    therefore produces a 409 response without any controller-side
    translation.

    ``actual`` is ``None`` when the persistence layer surfaces a
    conflict without a usable stored-revision read (e.g. the follow-up
    lookup raced with a delete). Callers should treat ``None`` as
    "unknown stored revision" rather than a sentinel integer.
    """

    def __init__(
        self,
        message: str,
        *,
        definition_id: str,
        expected: int,
        actual: int | None,
    ) -> None:
        """Build a revision-mismatch error with structured context.

        Args:
            message: Human-readable description (passed to
                ``DomainError.__init__``).
            definition_id: Workflow definition identifier that hit the
                conflict.
            expected: Stored revision the caller was asserting against
                (i.e. ``incoming_revision - 1`` for ``update_if_exists``).
            actual: Revision actually persisted, or ``None`` if the
                follow-up lookup could not determine it.
        """
        super().__init__(message)
        self.definition_id = definition_id
        self.expected = expected
        self.actual = actual


class WorkflowService:
    """Service for workflow definition CRUD + version cascade.

    When ``versioning_service`` is provided, :meth:`create_definition`
    and :meth:`update_definition` persist the definition AND best-effort
    snapshot the new revision in a single service call so controllers
    no longer need to reach into ``VersioningService`` directly. A
    snapshot failure does not fail the whole operation (orphaned
    versions are tolerable and periodically swept); ordinary failures
    are logged at WARNING. ``MemoryError`` and ``RecursionError`` are
    NOT swallowed -- those fatal system errors propagate so the
    workload can shed load even from the best-effort path. Callers
    should still expect those two exception types to surface from any
    method on this service.
    """

    __slots__ = ("_definitions", "_versioning", "_versions")

    def __init__(
        self,
        *,
        definition_repo: WorkflowDefinitionRepository,
        version_repo: VersionRepository[WorkflowDefinition],
        versioning_service: VersioningService[WorkflowDefinition] | None = None,
    ) -> None:
        """Wire repository and versioning dependencies into the service.

        Args:
            definition_repo: Workflow-definition repository. Supplies
                the atomic ``create_if_absent`` / ``update_if_exists``
                contract the service depends on.
            version_repo: Repository for version-snapshot storage used
                by :meth:`delete_definition`'s cascade delete.
            versioning_service: Optional :class:`VersioningService` for
                workflow definitions. When set, create/update operations
                record a best-effort version snapshot of the new
                revision in the same service call; when ``None`` (or
                when callers omit ``saved_by``), snapshotting is
                skipped. ``MemoryError`` / ``RecursionError`` from the
                snapshot still propagate; ordinary failures are logged
                at WARNING.
        """
        self._definitions = definition_repo
        self._versions = version_repo
        self._versioning = versioning_service

    async def list_definitions(
        self,
        *,
        workflow_type: WorkflowType | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[WorkflowDefinition, ...]:
        """List definitions filtered by optional workflow type.

        Bounded by *limit* (default :data:`DEFAULT_LIST_LIMIT`) so an
        unauth'd caller cannot materialise the full table.

        Returns:
            The tuple of matching :class:`WorkflowDefinition` rows.
        """
        from synthorg.persistence.workflow_definition_protocol import (  # noqa: PLC0415
            WorkflowDefinitionFilterSpec,
        )

        return await self._definitions.query(
            WorkflowDefinitionFilterSpec(workflow_type=workflow_type),
            limit=limit,
        )

    async def get_definition(
        self,
        definition_id: NotBlankStr,
    ) -> WorkflowDefinition | None:
        """Fetch a single definition by id.

        Returns:
            The :class:`WorkflowDefinition`, or ``None`` if no row
            matches ``definition_id``.
        """
        return await self._definitions.get(definition_id)

    async def fetch_for_update(
        self,
        definition_id: NotBlankStr,
        expected_revision: int | None,
    ) -> WorkflowDefinition:
        """Return the definition for an optimistic-concurrency update.

        Enforces the same preconditions the controller previously ran
        inline against the repository so all persistence access flows
        through the service layer:

        * the definition exists;
        * when *expected_revision* is supplied, the stored revision
          matches it.

        Returns:
            The matching :class:`WorkflowDefinition` confirmed to
            satisfy ``expected_revision`` (when supplied).

        Raises:
            WorkflowDefinitionNotFoundError: The id does not exist.
            WorkflowDefinitionRevisionMismatchError: The stored revision
                differs from *expected_revision*.
        """
        existing = await self._definitions.get(definition_id)
        if existing is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=str(definition_id),
                operation="fetch_for_update",
            )
            msg = f"Workflow definition {definition_id!r} not found"
            raise WorkflowDefinitionNotFoundError(msg)
        if expected_revision is not None and expected_revision != existing.revision:
            logger.warning(
                WORKFLOW_DEF_VERSION_CONFLICT,
                definition_id=str(definition_id),
                expected_revision=expected_revision,
                stored_revision=existing.revision,
            )
            msg = (
                f"Workflow definition {definition_id!r} revision conflict: "
                f"expected {expected_revision}, stored {existing.revision}"
            )
            raise WorkflowDefinitionRevisionMismatchError(
                msg,
                definition_id=str(definition_id),
                expected=expected_revision,
                actual=existing.revision,
            )
        return existing

    async def create_definition(
        self,
        definition: WorkflowDefinition,
        *,
        saved_by: str | None = None,
    ) -> WorkflowDefinition:
        """Persist a new definition with audit log.

        Uses the repository's atomic ``create_if_absent`` so two
        concurrent callers with the same ``definition.id`` cannot both
        observe "not found" and then both upsert via ``save`` -- that
        check-then-save pattern has a TOCTOU window the backend's
        ``INSERT ... ON CONFLICT DO NOTHING`` closes at the SQL level.

        When ``saved_by`` is provided AND the service was constructed
        with a ``VersioningService``, a best-effort version snapshot is
        recorded for the new revision. Ordinary snapshot failures are
        logged at WARNING and swallowed; the definition write is
        authoritative. ``MemoryError`` / ``RecursionError`` are never
        suppressed -- those fatal system errors propagate up even from
        the best-effort snapshot path, so callers should still handle
        them.

        Returns:
            The :class:`WorkflowDefinition` after the successful insert
            (the input ``definition`` echoed back for convenience).

        Raises:
            WorkflowDefinitionExistsError: ``definition.id`` already
                exists; the caller should use ``update_definition``.
            MemoryError: Propagated from the best-effort snapshot if
                raised there; never swallowed.
            RecursionError: Propagated from the best-effort snapshot
                if raised there; never swallowed.
        """
        inserted = await self._definitions.create_if_absent(definition)
        if not inserted:
            logger.warning(
                WORKFLOW_DEF_CREATE_CONFLICT,
                definition_id=str(definition.id),
                reason="duplicate_id",
            )
            msg = (
                f"Workflow definition {definition.id!r} already exists; "
                "use update_definition to modify it"
            )
            raise WorkflowDefinitionExistsError(msg)
        # Emit the state-transition INFO log BEFORE the best-effort
        # snapshot so the committed create is always in the audit trail,
        # even if the snapshot follow-up fails loud or silent.
        logger.info(WORKFLOW_DEF_CREATED, definition_id=str(definition.id))
        await self._best_effort_snapshot(definition, saved_by)
        return definition

    async def update_definition(
        self,
        definition: WorkflowDefinition,
        *,
        saved_by: str | None = None,
    ) -> WorkflowDefinition:
        """Update an existing definition with audit log.

        Uses the repository's ``update_if_exists`` so a row deleted
        after the controller's existence check cannot be silently
        resurrected by an upsert while still emitting
        ``WORKFLOW_DEF_UPDATED``. A missing row now surfaces as
        ``WorkflowDefinitionNotFoundError`` (HTTP 404) -- create/update
        audit semantics stay distinct even under delete races.

        When ``saved_by`` is provided AND the service was constructed
        with a ``VersioningService``, a best-effort version snapshot
        is recorded. Same best-effort semantics as
        :meth:`create_definition`: ordinary snapshot failures are
        logged at WARNING and swallowed, but ``MemoryError`` and
        ``RecursionError`` propagate so the workload can shed load.

        Raises:
            WorkflowDefinitionNotFoundError: No row exists for
                ``definition.id`` -- caller should use
                ``create_definition``.
            WorkflowDefinitionRevisionMismatchError: A row exists but
                its stored ``revision`` does not match
                ``definition.revision - 1`` (optimistic-concurrency
                failure). Translated from the persistence layer's
                ``PersistenceVersionConflictError`` so callers of this
                service never depend on a persistence-level exception type.
            MemoryError: Propagated from either the stored-revision
                lookup probe or the best-effort snapshot; never
                swallowed.
            RecursionError: Propagated from either the stored-revision
                lookup probe or the best-effort snapshot; never
                swallowed.

        Returns:
            The :class:`WorkflowDefinition` after the successful update
            (the input ``definition`` echoed back for convenience).
        """
        try:
            updated = await self._definitions.update_if_exists(definition)
        except PersistenceVersionConflictError as exc:
            # ``update_if_exists`` applies the UPDATE only when the
            # stored row's ``revision`` equals ``definition.revision - 1``,
            # so the "expected stored revision" the caller is asserting
            # against is N-1, not the incoming N. Reporting the incoming
            # value would be off-by-one and mislead clients doing
            # optimistic-concurrency retries.
            expected_stored_revision = definition.revision - 1
            # Look up the stored revision so the domain exception reports
            # the real ``actual`` value rather than a made-up sentinel.
            # If the follow-up read itself fails, fall back to ``None``
            # for ``actual`` and let the domain exception carry just the
            # expected revision; swallowing that lookup is fine because
            # we still propagate the original conflict as ``__cause__``.
            stored_revision: int | None = None
            try:
                existing = await self._definitions.get(str(definition.id))
            except Exception as lookup_exc:  # noqa: BLE001 -- criticals re-raised
                # ``reraise_critical`` propagates fatal system errors
                # even from this best-effort probe; otherwise the
                # outer ``raise`` below would swallow them.
                reraise_critical(lookup_exc)
                logger.debug(
                    WORKFLOW_DEF_VERSION_CONFLICT,
                    definition_id=str(definition.id),
                    stage="stored_revision_lookup_failed",
                    error_type=type(lookup_exc).__name__,
                )
            else:
                if existing is not None:
                    stored_revision = existing.revision
            logger.warning(
                WORKFLOW_DEF_VERSION_CONFLICT,
                definition_id=str(definition.id),
                operation="update_definition",
                expected_revision=expected_stored_revision,
                stored_revision=stored_revision,
            )
            msg = (
                f"Workflow definition {definition.id!r} revision conflict:"
                f" expected {expected_stored_revision},"
                f" stored {stored_revision}"
            )
            raise WorkflowDefinitionRevisionMismatchError(
                msg,
                definition_id=str(definition.id),
                expected=expected_stored_revision,
                actual=stored_revision,
            ) from exc
        if not updated:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=str(definition.id),
                operation="update_definition",
            )
            msg = (
                f"Workflow definition {definition.id!r} not found; "
                "use create_definition to insert it"
            )
            raise WorkflowDefinitionNotFoundError(msg)
        # Emit the state-transition INFO log BEFORE the best-effort
        # snapshot so the committed update is always in the audit trail,
        # even if the snapshot follow-up fails loud or silent.
        logger.info(WORKFLOW_DEF_UPDATED, definition_id=str(definition.id))
        await self._best_effort_snapshot(definition, saved_by)
        return definition

    async def _best_effort_snapshot(
        self,
        definition: WorkflowDefinition,
        saved_by: str | None,
    ) -> None:
        """Record a version snapshot if the service has versioning wired in.

        No-op when either the versioning service is not attached or the
        caller did not provide ``saved_by`` (e.g. system-driven writes
        that do not attribute authorship). Snapshot failures are logged
        at WARNING and swallowed; orphaned snapshots are tolerable and
        periodically swept; losing a definition write because the
        snapshot table is momentarily unavailable is not.

        Raises:
            MemoryError: Re-raised from :func:`reraise_critical` so a
                fatal interpreter signal cannot be silently absorbed
                from the best-effort path.
            RecursionError: Same path as ``MemoryError``.
        """
        if self._versioning is None or saved_by is None:
            return
        try:
            await self._versioning.snapshot_if_changed(
                entity_id=str(definition.id),
                snapshot=definition,
                saved_by=saved_by,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # ``reraise_critical`` propagates fatal system errors
            # so the workload can shed load; best-effort logging is
            # the wrong response here.
            reraise_critical(exc)
            logger.warning(
                WORKFLOW_VERSION_SNAPSHOT_FAILED,
                definition_id=str(definition.id),
                revision=definition.revision,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def validate_definition(
        self,
        definition: WorkflowDefinition,
    ) -> WorkflowValidationResult:
        """Run graph-level validation against a candidate definition.

        Wraps the pure :func:`validate_workflow` topology checks in a
        thin async surface so the MCP write facade can ``await`` it
        through the same service it uses for create/update. Validation
        failures are *valid* responses; they return a result with
        ``valid=False`` rather than raising, since callers may use
        this method to drive a UI that surfaces structural errors back
        to the operator before submitting a real create/update.

        Returns:
            A :class:`WorkflowValidationResult` carrying ``valid`` plus
            structured ``errors`` for the candidate.
        """
        # Local import: ``validate_workflow`` lives alongside the
        # specialised graph checks and pulling it eagerly would create
        # an import cycle through ``WorkflowDefinition``.
        # ``validate_workflow`` itself already emits
        # ``WORKFLOW_DEF_VALIDATED`` / ``WORKFLOW_DEF_VALIDATION_FAILED``
        # at INFO / WARNING -- so this facade does not re-emit them
        # here. A second emission would double-count validation traffic
        # and force consumers to dedup on event payload shape.
        from synthorg.engine.workflow.validation import (  # noqa: PLC0415
            validate_workflow,
        )

        return validate_workflow(definition)

    async def delete_definition(
        self,
        definition_id: NotBlankStr,
    ) -> bool:
        """Delete a definition and its version snapshots.

        The version-snapshot cleanup is best-effort: a failure there is
        logged with :data:`WORKFLOW_VERSION_SNAPSHOT_FAILED` but does
        not block the overall delete (orphaned snapshots are tolerable
        and periodically swept).

        Returns:
            ``True`` when the definition row was removed, ``False``
            when no row matched.
        """
        deleted = await self._definitions.delete(definition_id)
        if not deleted:
            return False

        # Emit the state-transition INFO log BEFORE the best-effort
        # version cascade so the committed delete is always in the audit
        # trail, regardless of whether the snapshot cleanup later fails.
        logger.info(WORKFLOW_DEF_DELETED, definition_id=definition_id)

        try:
            await self._versions.delete_versions_for_entity(definition_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                WORKFLOW_VERSION_SNAPSHOT_FAILED,
                definition_id=definition_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                stage="cascade_delete",
            )

        return True
