"""Subworkflow registry service.

A thin coordination layer on top of :class:`SubworkflowRepository`
that:

- publishes new subworkflow versions (validating ``is_subworkflow`` and
  semver uniqueness),
- resolves pinned ``(subworkflow_id, version)`` references,
- enforces deletion protection when a version is still referenced by a
  live parent workflow,
- emits observability events for every lifecycle action.

The runtime nesting-depth limit for subworkflow calls is configured
through ``engine.max_subworkflow_depth`` (registered in
``settings/definitions/engine.py`` and surfaced on
``EngineBridgeConfig.max_subworkflow_depth``); the
``WorkflowExecutionService`` constructor takes the resolved value as a
required parameter so the limit follows the standard
DB > env > YAML > code-default settings resolution path.
"""

import json
from typing import Final

from packaging.version import InvalidVersion, Version

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.errors import (
    SubworkflowIOError,
    SubworkflowNotFoundError,
)

# Imports kept at runtime (rather than under TYPE_CHECKING) so PEP 649
# lazy annotation evaluation can resolve names like SubworkflowSummary
# in ``encode_subworkflow_keyset()`` and ParentReference in
# ``SubworkflowRegistry.find_parents()`` when introspectors call
# ``typing.get_type_hints()`` against module globals.
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,  # noqa: TC001 -- runtime-resolvable annotation
)
from synthorg.engine.workflow.subworkflow_models import (  # noqa: TC001 -- runtime-resolvable annotation
    ParentReference,
    SubworkflowSummary,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import (
    SUBWORKFLOW_DELETED,
    SUBWORKFLOW_REGISTERED,
    SUBWORKFLOW_RESOLVED,
)
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT
from synthorg.persistence.subworkflow_protocol import (
    SubworkflowRepository,  # noqa: TC001 -- runtime-resolvable annotation
)

logger = get_logger(__name__)

_SUBWORKFLOW_KEYSET_ARITY: Final[int] = 3


def encode_subworkflow_keyset(summary: SubworkflowSummary) -> str:
    """Encode a summary's composite sort key as a JSON-safe string.

    The composite key is ``(name, latest_version, subworkflow_id)``.
    A naive ``f"{name}|{version}|{id}"`` join can collide whenever any
    component contains the delimiter -- ``NotBlankStr`` does not
    forbid pipes, colons, or other separator characters.  JSON-encoding
    the tuple gives an unambiguous round-trip regardless of content.
    """
    return json.dumps(
        [summary.name, summary.latest_version, summary.subworkflow_id],
        separators=(",", ":"),
    )


def _decode_subworkflow_keyset(after_key: str) -> tuple[str, str, str]:
    """Reverse of :func:`encode_subworkflow_keyset`.

    Tolerates malformed inputs by raising ``ValueError`` -- the
    controller catches this through the cursor decode layer.
    """
    parsed = json.loads(after_key)
    if (
        not isinstance(parsed, list)
        or len(parsed) != _SUBWORKFLOW_KEYSET_ARITY
        or not all(isinstance(part, str) for part in parsed)
    ):
        msg = "subworkflow keyset cursor must encode [name, version, id]"
        raise ValueError(msg)
    return parsed[0], parsed[1], parsed[2]


class SubworkflowRegistry:
    """High-level service for publishing and resolving subworkflows.

    Args:
        repository: The underlying :class:`SubworkflowRepository`.
    """

    def __init__(self, repository: SubworkflowRepository) -> None:
        self._repo = repository

    async def register(self, definition: WorkflowDefinition) -> None:
        """Publish a new subworkflow version to the registry.

        Args:
            definition: The workflow definition to publish.  Must have
                ``is_subworkflow = True``.

        Raises:
            SubworkflowIOError: If ``definition`` is not marked as a
                subworkflow or carries an invalid semver.
            DuplicateRecordError: If ``(id, version)`` already exists.
        """
        if not definition.is_subworkflow:
            msg = (
                f"Cannot register workflow definition {definition.id!r} "
                "as a subworkflow: is_subworkflow flag is False"
            )
            raise SubworkflowIOError(msg)
        try:
            Version(definition.version)
        except InvalidVersion as exc:
            msg = (
                f"Subworkflow {definition.id!r} has invalid semver "
                f"{definition.version!r}: {safe_error_description(exc)}"
            )
            raise SubworkflowIOError(msg) from exc

        await self._repo.save(definition)
        logger.info(
            SUBWORKFLOW_REGISTERED,
            subworkflow_id=definition.id,
            version=definition.version,
        )

    async def get(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> WorkflowDefinition:
        """Resolve a pinned ``(id, version)`` reference.

        Args:
            subworkflow_id: Subworkflow identifier.
            version: Semver version string.

        Returns:
            The resolved ``WorkflowDefinition``.

        Raises:
            SubworkflowNotFoundError: If the version is not in the
                registry.
        """
        definition = await self._repo.get(subworkflow_id, version)
        if definition is None:
            msg = (
                f"Subworkflow {subworkflow_id!r} version {version!r} "
                "not found in registry"
            )
            logger.warning(
                SUBWORKFLOW_RESOLVED,
                subworkflow_id=subworkflow_id,
                version=version,
                found=False,
            )
            raise SubworkflowNotFoundError(
                msg,
                subworkflow_id=subworkflow_id,
                version=version,
            )
        logger.debug(
            SUBWORKFLOW_RESOLVED,
            subworkflow_id=subworkflow_id,
            version=version,
            found=True,
        )
        return definition

    async def list_versions(
        self,
        subworkflow_id: NotBlankStr,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[str, ...]:
        """List semver strings for a subworkflow, newest first (bounded by *limit*)."""
        return await self._repo.list_versions(subworkflow_id, limit=limit)

    async def latest_version(
        self,
        subworkflow_id: NotBlankStr,
    ) -> str | None:
        """Return the highest semver for a subworkflow, or ``None``.

        Fetches a single-page slice; the underlying repo bounds the
        scan and the registry takes the first (newest) entry.
        """
        versions = await self._repo.list_versions(subworkflow_id)
        return versions[0] if versions else None

    async def list_all(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[SubworkflowSummary, ...]:
        """Return summaries for unique subworkflows (bounded by *limit*)."""
        return await self._repo.list_summaries(limit=limit)

    async def list_page(
        self,
        *,
        after_key: str | None,
        limit: int,
    ) -> tuple[tuple[SubworkflowSummary, ...], bool]:
        """Return a single keyset page of summaries plus an ``has_more`` flag.

        Sorted by ``(name, latest_version, subworkflow_id)`` -- the
        ``subworkflow_id`` tail is the unique tie-breaker so cursor
        pages stay total when two subworkflows share a name +
        latest_version.  The cursor encodes the composite sort key as
        a JSON-encoded ``[name, version, id]`` triple
        (see :func:`encode_subworkflow_keyset`); a naive
        ``f"{name}|{version}|{id}"`` join could collide whenever any
        component contains the delimiter, since ``NotBlankStr`` does
        not forbid pipes / colons / etc.  The next page is sliced
        where the composite sort key tuple is strictly greater than
        the decoded ``after_key`` tuple.

        Slices in the registry rather than the SQL layer because
        ``SubworkflowSummary.version_count`` requires aggregating
        every version row per subworkflow.  A true SQL push-down
        would need a window-function query plus a secondary fetch of
        the page's versions, which is a substantial per-backend
        rewrite for a list whose typical row count is small.  Revisit
        if subworkflow rosters grow large enough that the full-fetch
        dominates request latency.

        Args:
            after_key: ``None`` for the first page; the previous
                page's last composite sort key for follow-up pages.
            limit: Page size requested.

        Returns:
            ``(page, has_more)`` where ``page`` is at most ``limit``
            summaries in canonical sort order and ``has_more`` is
            ``True`` when an additional summary was observed past the
            requested page.
        """
        # Pull the full population so cursor pagination and has_more
        # reflect the entire roster, not just the repo's default page
        # of summaries (which would silently truncate large catalogs).
        all_summaries = await self._repo.list_summaries(limit=MAX_LIST_LIMIT)
        sorted_summaries = sorted(
            all_summaries,
            key=lambda s: (s.name, s.latest_version, s.subworkflow_id),
        )
        if after_key is not None:
            after_tuple = _decode_subworkflow_keyset(after_key)
            sorted_summaries = [
                s
                for s in sorted_summaries
                if (s.name, s.latest_version, s.subworkflow_id) > after_tuple
            ]
        # Over-fetch by one to detect has_more without a separate count.
        page = sorted_summaries[: limit + 1]
        has_more = len(page) > limit
        return tuple(page[:limit]), has_more

    async def search(
        self,
        query: NotBlankStr,
    ) -> tuple[SubworkflowSummary, ...]:
        """Search subworkflows by name or description substring."""
        return await self._repo.search(query)

    async def delete(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> None:
        """Delete a subworkflow version with parent-reference protection.

        Uses an atomic check-and-delete to eliminate the TOCTOU race
        between the parent scan and the actual deletion.

        Raises:
            SubworkflowIOError: If any live parent still pins this
                ``(id, version)`` coordinate.
            SubworkflowNotFoundError: If the coordinate does not exist.
        """
        deleted, parents = await self._repo.delete_if_unreferenced(
            subworkflow_id,
            version,
        )
        if parents:
            names = ", ".join(f"{p.parent_name!r}" for p in parents)
            msg = (
                f"Cannot delete subworkflow {subworkflow_id!r} version "
                f"{version!r}: still referenced by {len(parents)} parent "
                f"workflow(s): {names}"
            )
            logger.warning(
                SUBWORKFLOW_DELETED,
                subworkflow_id=subworkflow_id,
                version=version,
                deleted=False,
                blocked_by_parents=len(parents),
            )
            raise SubworkflowIOError(msg)

        if not deleted:
            msg = (
                f"Subworkflow {subworkflow_id!r} version {version!r} "
                "not found in registry"
            )
            logger.warning(
                SUBWORKFLOW_DELETED,
                subworkflow_id=subworkflow_id,
                version=version,
                deleted=False,
                reason="not_found",
            )
            raise SubworkflowNotFoundError(
                msg,
                subworkflow_id=subworkflow_id,
                version=version,
            )
        logger.info(
            SUBWORKFLOW_DELETED,
            subworkflow_id=subworkflow_id,
            version=version,
        )

    async def find_parents(
        self,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr | None = None,
    ) -> tuple[ParentReference, ...]:
        """Return parent workflow definitions referencing a subworkflow."""
        return await self._repo.find_parents(subworkflow_id, version)
