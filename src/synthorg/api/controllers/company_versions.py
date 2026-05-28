"""Company version history controller -- list, get."""

import asyncio
from typing import Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import PathParameter

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_repo_seek_meta,
)
from synthorg.core.company import Company
from synthorg.core.domain_errors import NotFoundError
from synthorg.observability import get_logger
from synthorg.observability.events.versioning import (
    VERSION_LISTED,
    VERSION_NOT_FOUND,
)
from synthorg.persistence.state import persistence_of
from synthorg.versioning import VersionSnapshot

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 20

SnapshotT = VersionSnapshot[Company]

#: Entity ID for the company structure (single-tenant).
_ENTITY_ID = "default"


class CompanyVersionController(Controller):
    """Version history for company structure."""

    path = "/company"
    tags = ("company",)

    @get("/versions", guards=[require_read_access])
    async def list_versions(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> Response[PaginatedResponse[SnapshotT]]:
        """List version history for company structure.

        Returns:
            ``Response[PaginatedResponse[SnapshotT]]`` instance.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repo = persistence_of(state.app_state).company_versions
        versions, total = await asyncio.gather(
            repo.list_versions(_ENTITY_ID, limit=limit, offset=offset),
            repo.count_versions(_ENTITY_ID),
        )
        logger.debug(
            VERSION_LISTED,
            entity_type="Company",
            entity_id=_ENTITY_ID,
            count=len(versions),
        )
        meta = encode_repo_seek_meta(
            offset=offset,
            page_len=len(versions),
            total=total,
            limit=limit,
            secret=secret,
        )
        return Response(
            content=PaginatedResponse[SnapshotT](
                data=versions,
                pagination=meta,
            ),
        )

    @get(
        "/versions/{version_num:int}",
        guards=[require_read_access],
    )
    async def get_version(
        self,
        state: State,
        version_num: Annotated[
            int,
            PathParameter(
                ge=1,
                description="Company version (one-based; 1 = first revision).",
            ),
        ],
    ) -> Response[ApiResponse[SnapshotT]]:
        """Get a specific company version snapshot.

        Returns:
            ``Response[ApiResponse[SnapshotT]]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        repo = persistence_of(state.app_state).company_versions
        version = await repo.get_version(_ENTITY_ID, version_num)
        if version is None:
            logger.warning(
                VERSION_NOT_FOUND,
                entity_type="Company",
                entity_id=_ENTITY_ID,
                version=version_num,
            )
            msg = f"Version {version_num} not found"
            raise NotFoundError(msg)
        return Response(
            content=ApiResponse[SnapshotT](data=version),
        )
