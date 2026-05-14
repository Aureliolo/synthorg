"""Agent identity version service.

Wraps the :class:`VersionRepository` for :class:`AgentIdentity` with a
handler-friendly facade: ``list_versions`` returns ``(page, total)``
so MCP handlers can attach accurate pagination metadata without
reaching past the service boundary, and ``get_version`` returns
``None`` rather than raising for missing versions (handlers map the
``None`` onto ``not_found`` envelopes).

Owner-mismatch filtering (defence-in-depth against cross-entity rows)
stays at the HTTP controller layer because MCP handlers already
enforce ownership via authenticated-actor guardrails. Leaking that
filter into the service would over-broadcast the concern across a
second surface without improving security.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation
from synthorg.observability import get_logger
from synthorg.observability.events.agent_identity_version import (
    AGENT_IDENTITY_INVALID_REQUEST,
    AGENT_IDENTITY_VERSION_FETCHED,
    AGENT_IDENTITY_VERSION_LISTED,
    AGENT_IDENTITY_VERSION_NOT_FOUND,
    AGENT_IDENTITY_VERSION_OWNER_MISMATCH,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.persistence.version_protocol import VersionRepository
    from synthorg.versioning.models import VersionSnapshot

logger = get_logger(__name__)


class AgentVersionService:
    """Read-side facade over the agent identity version repository.

    Constructor:
        version_repo: The repository holding ``AgentIdentity`` version
            snapshots (typically ``persistence.identity_versions``).
    """

    __slots__ = ("_repo",)

    def __init__(
        self,
        *,
        version_repo: VersionRepository[AgentIdentity],
    ) -> None:
        """Initialise with the version repository dependency."""
        self._repo = version_repo

    async def list_versions(
        self,
        agent_id: NotBlankStr,
        *,
        offset: int,
        limit: int,
    ) -> tuple[tuple[VersionSnapshot[AgentIdentity], ...], int]:
        """Return a page of version snapshots + the total count.

        Snapshots are newest-first. The total count is the unfiltered
        tally the repository would return for an unpaginated query;
        it is always reported alongside the page so handlers can
        attach accurate ``PaginationMeta`` without a second round
        trip.

        Args:
            agent_id: The agent's primary key.
            offset: Page offset (>= 0).
            limit: Page size (> 0).

        Returns:
            Tuple of ``(page, total)``.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is not
                strictly positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                AGENT_IDENTITY_INVALID_REQUEST,
                param="offset",
                value=offset,
                agent_id=agent_id,
            )
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                AGENT_IDENTITY_INVALID_REQUEST,
                param="limit",
                value=limit,
                agent_id=agent_id,
            )
            raise ValueError(msg)
        async with asyncio.TaskGroup() as tg:
            list_task = tg.create_task(
                self._repo.list_versions(
                    agent_id,
                    limit=limit,
                    offset=offset,
                ),
            )
            count_task = tg.create_task(self._repo.count_versions(agent_id))
        versions = list_task.result()
        total = count_task.result()
        logger.debug(
            AGENT_IDENTITY_VERSION_LISTED,
            agent_id=agent_id,
            count=len(versions),
            total=total,
            offset=offset,
            limit=limit,
        )
        return versions, total

    async def get_version(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> VersionSnapshot[AgentIdentity] | None:
        """Fetch a specific version snapshot or ``None`` if absent.

        Args:
            agent_id: The agent's primary key.
            version: Version number (>= 1).

        Returns:
            The snapshot, or ``None`` if the ``(agent_id, version)``
            pair does not exist.

        Raises:
            ValueError: If ``version`` is less than 1.
        """
        if version < 1:
            msg = f"version must be >= 1, got {version}"
            logger.warning(
                AGENT_IDENTITY_INVALID_REQUEST,
                param="version",
                value=version,
                agent_id=agent_id,
            )
            raise ValueError(msg)
        snapshot = await self._repo.get_version(agent_id, version)
        if snapshot is not None:
            logger.debug(
                AGENT_IDENTITY_VERSION_FETCHED,
                agent_id=agent_id,
                version=version,
            )
        return snapshot

    async def get_for_rollback(
        self,
        agent_id: NotBlankStr,
        version: int,
    ) -> VersionSnapshot[AgentIdentity]:
        """Fetch a version snapshot with the rollback-time owner check applied.

        Combines :meth:`get_version` with the same defence-in-depth
        owner-mismatch validation the controller used to apply inline.
        The check guards against corrupted / cross-entity rows that
        could otherwise let a rollback mutate the wrong agent. Raises
        :class:`NotFoundError` when the version row is absent and
        :class:`ValidationError` when the snapshot's encoded owner
        does not match ``agent_id``.
        """
        target = await self.get_version(agent_id, version)
        if target is None:
            logger.warning(
                AGENT_IDENTITY_VERSION_NOT_FOUND,
                agent_id=agent_id,
                version=version,
            )
            msg = f"Target version {version} not found"
            raise NotFoundError(msg)
        if str(target.snapshot.id) != agent_id:
            logger.warning(
                AGENT_IDENTITY_VERSION_OWNER_MISMATCH,
                agent_id=agent_id,
                error="target snapshot id does not match path agent_id",
                snapshot_id=str(target.snapshot.id),
            )
            msg = "Target version belongs to a different agent"
            raise ValidationError(msg)
        return target

    async def get_version_pair_for_diff(
        self,
        agent_id: NotBlankStr,
        from_version: int,
        to_version: int,
    ) -> tuple[
        VersionSnapshot[AgentIdentity],
        VersionSnapshot[AgentIdentity],
    ]:
        """Concurrently fetch two snapshots with owner validation.

        Centralises the cross-snapshot loading for diff endpoints so
        the controller no longer reaches past the service boundary.

        Version arguments are validated **before** the TaskGroup so a
        ``ValueError`` from ``get_version`` cannot be rewrapped in
        ``BaseExceptionGroup``; the controller's exception handler
        would otherwise see a group and route the request through the
        500 fallback instead of the 400 validation path.
        """
        for version in (from_version, to_version):
            if version < 1:
                logger.warning(
                    AGENT_IDENTITY_INVALID_REQUEST,
                    param="version",
                    value=version,
                    agent_id=agent_id,
                )
                msg = f"version must be >= 1, got {version}"
                raise ValueError(msg)
        async with asyncio.TaskGroup() as tg:
            old_task = tg.create_task(self.get_version(agent_id, from_version))
            new_task = tg.create_task(self.get_version(agent_id, to_version))
        old = old_task.result()
        new = new_task.result()
        for snapshot, version in ((old, from_version), (new, to_version)):
            if snapshot is None:
                logger.warning(
                    AGENT_IDENTITY_VERSION_NOT_FOUND,
                    agent_id=agent_id,
                    version=version,
                )
                msg = f"Version {version} not found"
                raise NotFoundError(msg)
            if str(snapshot.snapshot.id) != agent_id:
                logger.warning(
                    AGENT_IDENTITY_VERSION_OWNER_MISMATCH,
                    agent_id=agent_id,
                    version=version,
                    snapshot_id=str(snapshot.snapshot.id),
                )
                msg = f"Version {version} belongs to a different agent"
                raise ValidationError(msg)
        assert old is not None  # noqa: S101 -- narrowed by loop above
        assert new is not None  # noqa: S101 -- narrowed by loop above
        return old, new


__all__ = ["AgentVersionService"]
