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

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation
from synthorg.observability import get_logger
from synthorg.observability.events.agent_identity_version import (
    AGENT_IDENTITY_INVALID_REQUEST,
    AGENT_IDENTITY_VERSION_FETCHED,
    AGENT_IDENTITY_VERSION_LISTED,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.persistence.version_repo import VersionRepository
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


__all__ = ["AgentVersionService"]
