"""Workflow version service layer.

Wraps the generic :class:`VersionRepository` for workflow definitions
behind a domain-specific facade. The MCP write surface and the HTTP
version-history controller both route through this service so the
persistence boundary stays honored (handlers never reach into
``app_state.persistence`` directly).
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_NOT_FOUND,
)
from synthorg.observability.events.workflow_version import (
    WORKFLOW_VERSION_INVALID_REQUEST,
)

if TYPE_CHECKING:
    from synthorg.engine.workflow.definition import WorkflowDefinition
    from synthorg.persistence.version_protocol import VersionRepository
    from synthorg.versioning.models import VersionSnapshot

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class WorkflowVersionService:
    """Read-side facade over workflow definition version snapshots."""

    __slots__ = ("_repo",)

    def __init__(
        self,
        *,
        version_repo: VersionRepository[WorkflowDefinition],
    ) -> None:
        self._repo = version_repo

    async def list_versions(
        self,
        definition_id: NotBlankStr,
        *,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[tuple[VersionSnapshot[WorkflowDefinition], ...], int]:
        """Return a paginated list of version snapshots and the total count.

        Snapshots are returned newest-first, matching the underlying
        repository's ordering. The page read and the unfiltered count
        read run concurrently via :class:`asyncio.TaskGroup` so the
        service round-trip matches the slower of the two repository
        calls rather than their sum (mirrors
        :class:`synthorg.budget.version_service.BudgetConfigVersionsService`).

        Raises:
            ValueError: When ``offset`` is negative or ``limit`` is
                less than 1.
        """
        if offset < 0:
            logger.warning(
                WORKFLOW_VERSION_INVALID_REQUEST,
                definition_id=str(definition_id),
                param="offset",
                value=offset,
            )
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            logger.warning(
                WORKFLOW_VERSION_INVALID_REQUEST,
                definition_id=str(definition_id),
                param="limit",
                value=limit,
            )
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        async with asyncio.TaskGroup() as tg:
            list_task = tg.create_task(
                self._repo.list_versions(
                    definition_id,
                    limit=limit,
                    offset=offset,
                ),
            )
            count_task = tg.create_task(
                self._repo.count_versions(definition_id),
            )
        return list_task.result(), count_task.result()

    async def get_version(
        self,
        definition_id: NotBlankStr,
        revision: int,
    ) -> VersionSnapshot[WorkflowDefinition] | None:
        """Return a specific version snapshot, or ``None`` if absent.

        Raises:
            ValueError: When ``revision`` is less than 1.
        """
        if revision < 1:
            logger.warning(
                WORKFLOW_VERSION_INVALID_REQUEST,
                definition_id=str(definition_id),
                param="revision",
                value=revision,
            )
            msg = f"revision must be >= 1, got {revision}"
            raise ValueError(msg)
        return await self._repo.get_version(definition_id, revision)

    async def get_version_pair_or_404(
        self,
        definition_id: NotBlankStr,
        from_revision: int,
        to_revision: int,
    ) -> tuple[
        VersionSnapshot[WorkflowDefinition],
        VersionSnapshot[WorkflowDefinition],
    ]:
        """Fetch two snapshots concurrently for diff/rollback orchestration.

        Both snapshots are fetched via :class:`asyncio.TaskGroup` so the
        round-trip matches the slower of the two repository calls. The
        helper centralises the missing-version logging + 404 raise that
        every diff endpoint would otherwise reimplement at the
        controller layer.

        Revision arguments are validated **before** the TaskGroup so a
        ``ValueError`` from ``get_version`` cannot be rewrapped in
        ``BaseExceptionGroup``; the controller's exception handler
        would otherwise see a group and route the request through the
        500 fallback instead of the 400 validation path.

        Returns:
            ``(old, new)`` snapshots for the requested ``from`` /
            ``to`` revisions.

        Raises:
            ValueError: When either revision is less than 1.
            NotFoundError: When either snapshot is absent.
        """
        for revision in (from_revision, to_revision):
            if revision < 1:
                logger.warning(
                    WORKFLOW_VERSION_INVALID_REQUEST,
                    definition_id=str(definition_id),
                    param="revision",
                    value=revision,
                )
                msg = f"revision must be >= 1, got {revision}"
                raise ValueError(msg)
        async with asyncio.TaskGroup() as tg:
            old_task = tg.create_task(self.get_version(definition_id, from_revision))
            new_task = tg.create_task(self.get_version(definition_id, to_revision))
        old = old_task.result()
        new = new_task.result()
        for snapshot, revision in ((old, from_revision), (new, to_revision)):
            if snapshot is None:
                logger.warning(
                    WORKFLOW_DEF_NOT_FOUND,
                    definition_id=str(definition_id),
                    version=revision,
                )
                msg = f"Version {revision} not found"
                raise NotFoundError(msg)
        assert old is not None  # noqa: S101 -- narrowed by loop above
        assert new is not None  # noqa: S101 -- narrowed by loop above
        return old, new


__all__ = ["WorkflowVersionService"]
