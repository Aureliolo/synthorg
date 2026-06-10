"""Subworkflow service layer.

Wraps :class:`SubworkflowRegistry` with the business logic the MCP
write facade needs: paginated listing, version-pinning helpers,
parent-cascade protection on delete, and audit-event emission for
each lifecycle step. Controllers and MCP handlers route through this
service so persistence access stays behind a single facade.

The thin :class:`SubworkflowRegistry` underneath is preserved for the
pure resolution paths (engine activation, blueprint loaders) that
already depend on its narrow surface; this service is the broader
"control plane" entry point.
"""

from typing import ClassVar

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SubworkflowIOError,
    SubworkflowNotFoundError,
)

# Imports kept at runtime (rather than under TYPE_CHECKING) so PEP 649
# lazy annotation evaluation can resolve names like ParentReference in
# ``SubworkflowHasParentsError.__init__`` when callers introspect via
# ``typing.get_type_hints`` or ``inspect.get_annotations``.
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
)
from synthorg.engine.workflow.subworkflow_models import (
    ParentReference,
    SubworkflowSummary,
)
from synthorg.engine.workflow.subworkflow_registry import (
    SubworkflowRegistry,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import (
    SUBWORKFLOW_DELETE_BLOCKED,
    SUBWORKFLOW_DELETE_FAILED,
    SUBWORKFLOW_DELETED,
    SUBWORKFLOW_INVALID_REQUEST,
    SUBWORKFLOW_NOT_FOUND,
    SUBWORKFLOW_PUBLISH_FAILED,
    SUBWORKFLOW_REGISTERED,
)
from synthorg.persistence._shared import collect_all

logger = get_logger(__name__)


class SubworkflowHasParentsError(SubworkflowIOError):
    """Raised when ``delete`` is blocked by live parent references.

    Carries the parent list so callers can surface the conflict to the
    operator without re-querying. Overrides the parent's 422/VALIDATION
    mapping with 409/CONFLICT because a still-referenced subworkflow
    is a resource-state conflict, not a validation failure.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = (
        "Subworkflow is still referenced by parent workflows"
    )

    def __init__(
        self,
        message: str,
        *,
        subworkflow_id: str,
        version: str,
        parents: tuple[ParentReference, ...],
    ) -> None:
        if not parents:
            msg = (
                "SubworkflowHasParentsError requires at least one parent "
                "reference; raise SubworkflowNotFoundError instead when "
                "the subworkflow has no live parents."
            )
            raise ValueError(msg)
        super().__init__(message)
        self.subworkflow_id: str = subworkflow_id
        self.version: str = version
        self.parents: tuple[ParentReference, ...] = parents
        # Primitive projection so ``_safe_log_attrs`` in the
        # centralised exception handler can include the blocking
        # parent IDs in the structured log envelope. The full
        # ``parents`` tuple is kept for callers (API response
        # serialisation, MCP) but is skipped by the log clamp because
        # ``ParentReference`` is a Pydantic model, not a primitive.
        self.parent_ids: tuple[str, ...] = tuple(p.parent_id for p in parents)


class SubworkflowService:
    """Control-plane service for subworkflows.

    Adds a paginated list with optional substring search, version-pinning
    resolution (``version=None`` resolves to latest), parent-cascade
    enforcement before delete, and audit-log emission on publish + delete.
    Existing engine paths (activation, blueprint loading) keep using
    :class:`SubworkflowRegistry` directly for low-level resolution.
    """

    __slots__ = ("_registry",)

    def __init__(
        self,
        *,
        registry: SubworkflowRegistry,
    ) -> None:
        self._registry = registry

    async def list_summaries(
        self,
        *,
        offset: int,
        limit: int,
        query: str | None = None,
    ) -> tuple[tuple[SubworkflowSummary, ...], int]:
        """Return a paginated list of subworkflow summaries.

        When ``query`` is provided, the registry's substring search
        runs first; otherwise we fall back to the unfiltered summary
        list. Results are sorted by ``(name, latest_version,
        subworkflow_id)`` and offset/limit are applied here so callers
        get a stable total count.

        Note:
            Sort + slice run in process rather than at the SQL layer
            because :class:`SubworkflowSummary.version_count` requires
            aggregating every version row per subworkflow; a true
            SQL push-down would need a per-backend window-function
            rewrite (see :meth:`SubworkflowRegistry.list_page`). This
            is acceptable while subworkflow rosters stay small; revisit
            if request latency starts being dominated by the full
            fetch.

        Returns:
            ``(page, total)`` where ``page`` is the paginated tuple of
            :class:`SubworkflowSummary` rows and ``total`` is the full
            match count before pagination.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is
                less than 1.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                SUBWORKFLOW_INVALID_REQUEST,
                reason="invalid_offset",
                offset=offset,
            )
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                SUBWORKFLOW_INVALID_REQUEST,
                reason="invalid_limit",
                limit=limit,
            )
            raise ValueError(msg)

        if query is not None and query.strip():
            search_term = NotBlankStr(query.strip())
            # This endpoint sorts + paginates the full match set in
            # memory, so drain every bounded repo page rather than
            # silently showing only the first.
            summaries = await collect_all(
                lambda limit, offset: self._registry.search(
                    search_term,
                    limit=limit,
                    offset=offset,
                ),
            )
        else:
            summaries = await self._registry.list_all()
        sorted_summaries = sorted(
            summaries,
            key=lambda s: (s.name, s.latest_version, s.subworkflow_id),
        )
        total = len(sorted_summaries)
        page = tuple(sorted_summaries[offset : offset + limit])
        return page, total

    async def get(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr | None = None,
    ) -> WorkflowDefinition:
        """Resolve a subworkflow definition.

        When ``version`` is omitted, the latest semver is selected. A
        missing subworkflow raises :class:`SubworkflowNotFoundError`.

        Returns:
            The resolved :class:`WorkflowDefinition` for the requested
            coordinate (latest version when ``version`` is ``None``).

        Raises:
            SubworkflowNotFoundError: If no version exists when
                ``version`` is ``None``, or if the registry has no
                row for the explicit ``(subworkflow_id, version)``
                coordinate.
        """
        if version is None:
            latest = await self._registry.latest_version(subworkflow_id)
            if latest is None:
                msg = f"Subworkflow {subworkflow_id!r} has no versions in the registry"
                logger.warning(
                    SUBWORKFLOW_NOT_FOUND,
                    subworkflow_id=str(subworkflow_id),
                    version="<latest>",
                    reason="no_versions",
                )
                raise SubworkflowNotFoundError(
                    msg,
                    subworkflow_id=str(subworkflow_id),
                    version="<latest>",
                )
            return await self._registry.get(subworkflow_id, NotBlankStr(latest))
        return await self._registry.get(subworkflow_id, version)

    async def create(
        self,
        definition: WorkflowDefinition,
        *,
        saved_by: str,
    ) -> WorkflowDefinition:
        """Publish a new subworkflow version.

        Validates that ``is_subworkflow=True``, delegates to the
        registry for the atomic write, and emits the publish audit
        event including the actor.

        Returns:
            The published :class:`WorkflowDefinition` (input echoed
            back for convenience).

        Raises:
            ValueError: If ``definition.is_subworkflow`` is ``False``;
                ``ValueError`` lets the MCP handler map to
                ``invalid_argument`` so storage failures keep the
                dedicated :class:`SubworkflowIOError` path.
        """
        if not definition.is_subworkflow:
            msg = (
                f"Cannot create subworkflow from definition {definition.id!r}: "
                "is_subworkflow flag is False"
            )
            logger.warning(
                SUBWORKFLOW_INVALID_REQUEST,
                subworkflow_id=str(definition.id),
                version=str(definition.version),
                reason="not_subworkflow",
                saved_by=saved_by,
            )
            # ``SubworkflowIOError`` is the registry / storage error
            # type; conflating it with caller-supplied bad input
            # (``is_subworkflow=False``) would force handlers to
            # second-guess whether a single exception class means
            # "your input is wrong" or "the backend is down".
            # Surface the validation failure as ``ValueError`` so
            # the MCP handler maps it to ``invalid_argument`` and
            # storage failures keep the dedicated IO type.
            raise ValueError(msg)
        try:
            await self._registry.register(definition)
        except Exception as exc:
            reraise_critical(exc)
            # Log a service-level failure event before the exception
            # escapes so the control-plane audit trail records the
            # publish attempt with the same identifying fields the
            # success path uses.
            logger.warning(
                SUBWORKFLOW_PUBLISH_FAILED,
                subworkflow_id=str(definition.id),
                version=str(definition.version),
                saved_by=saved_by,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SUBWORKFLOW_REGISTERED,
            subworkflow_id=str(definition.id),
            version=definition.version,
            saved_by=saved_by,
            stage="service.create",
        )
        return definition

    async def delete(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
        *,
        reason: str,
        actor_id: str,
    ) -> None:
        """Delete a subworkflow version after parent-cascade check.

        The check-then-delete sequence is intentionally non-atomic at
        the service layer: ``find_parents`` here is for early,
        operator-friendly error reporting only. The actual safety net
        is :meth:`SubworkflowRegistry.delete`, which delegates to the
        repository's atomic ``delete_if_unreferenced``. A parent that
        appears in the narrow TOCTOU window between this check and the
        registry call still gets caught at the SQL level and surfaces
        as a :class:`SubworkflowHasParentsError` (or its base class
        :class:`SubworkflowIOError` when the cascade error is raised
        directly by the repository).

        Raises:
            SubworkflowHasParentsError: If any live parent workflow
                still pins ``(subworkflow_id, version)``. The exception
                carries the parent list so callers can surface the
                conflict without a second query.
            SubworkflowNotFoundError: If the coordinate does not exist.
            SubworkflowIOError: If the registry's atomic delete raises
                a storage error after the TOCTOU recheck cannot prove
                a parent-cascade conflict.
        """
        # Referential-integrity gate: the conflict error reports the
        # exact parent count + names, so the complete set is required
        # (a truncated page would under-report and could let a
        # still-referenced version be deleted).
        parents = await collect_all(
            lambda limit, offset: self._registry.find_parents(
                subworkflow_id,
                version,
                limit=limit,
                offset=offset,
            ),
        )
        if parents:
            names = ", ".join(f"{p.parent_name!r}" for p in parents)
            msg = (
                f"Cannot delete subworkflow {subworkflow_id!r} version "
                f"{version!r}: still referenced by {len(parents)} parent "
                f"workflow(s): {names}"
            )
            logger.warning(
                SUBWORKFLOW_DELETE_BLOCKED,
                subworkflow_id=subworkflow_id,
                version=version,
                blocked_by_parents=len(parents),
                actor_id=actor_id,
                reason=reason,
            )
            raise SubworkflowHasParentsError(
                msg,
                subworkflow_id=str(subworkflow_id),
                version=str(version),
                parents=parents,
            )
        try:
            await self._registry.delete(subworkflow_id, version)
        except SubworkflowIOError as exc:
            # The pre-check above closes the common case, but the real
            # safety net is the repository's atomic
            # ``delete_if_unreferenced``. A parent that appears in the
            # narrow TOCTOU window between the check and this call is
            # surfaced here as a plain ``SubworkflowIOError`` -- which
            # the MCP handler would map to ``unavailable`` instead of
            # the more accurate ``conflict``. Re-fetch parents and
            # re-wrap so callers see the same envelope shape (and
            # parent list) as the pre-check path.
            #
            # The re-fetch itself can raise; if it does, fall back to
            # re-raising the *original* ``SubworkflowIOError`` so we
            # never mask a real storage failure behind a secondary
            # observability lookup error.
            try:
                late_parents = await collect_all(
                    lambda limit, offset: self._registry.find_parents(
                        subworkflow_id,
                        version,
                        limit=limit,
                        offset=offset,
                    ),
                )
            except Exception as recheck_exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(recheck_exc)
                # Use the neutral ``SUBWORKFLOW_DELETE_FAILED`` event
                # rather than ``SUBWORKFLOW_DELETE_BLOCKED``: at this
                # point we have NOT proven a parent-cascade conflict,
                # only that the recheck itself failed. Logging the
                # blocked-delete event here would otherwise inflate
                # blocked-delete metrics with what are really storage
                # / lookup failures.
                logger.warning(
                    SUBWORKFLOW_DELETE_FAILED,
                    subworkflow_id=subworkflow_id,
                    version=version,
                    actor_id=actor_id,
                    reason=reason,
                    stage="service.delete.toctou_recheck_failed",
                    error_type=type(recheck_exc).__name__,
                    error=safe_error_description(recheck_exc),
                )
                # Original delete failure is the load-bearing exception;
                # re-raise it (not the recheck error) so the handler
                # sees the real cause.
                raise exc from None
            if late_parents:
                logger.warning(
                    SUBWORKFLOW_DELETE_BLOCKED,
                    subworkflow_id=subworkflow_id,
                    version=version,
                    blocked_by_parents=len(late_parents),
                    actor_id=actor_id,
                    reason=reason,
                    stage="service.delete.toctou",
                )
                msg = (
                    f"Cannot delete subworkflow {subworkflow_id!r} version "
                    f"{version!r}: still referenced by "
                    f"{len(late_parents)} parent workflow(s)"
                )
                raise SubworkflowHasParentsError(
                    msg,
                    subworkflow_id=str(subworkflow_id),
                    version=str(version),
                    parents=late_parents,
                ) from exc
            raise
        logger.info(
            SUBWORKFLOW_DELETED,
            subworkflow_id=subworkflow_id,
            version=version,
            actor_id=actor_id,
            reason=reason,
            stage="service.delete",
        )


__all__ = [
    "SubworkflowHasParentsError",
    "SubworkflowService",
]
