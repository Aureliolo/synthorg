"""Role version history controller -- list, get."""

import asyncio
from typing import Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State
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
from synthorg.api.path_params import PathName
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.role import Role
from synthorg.observability import get_logger
from synthorg.observability.events.versioning import (
    VERSION_LISTED,
    VERSION_NOT_FOUND,
)
from synthorg.persistence.state import persistence_of
from synthorg.versioning import VersionSnapshot

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 20

SnapshotT = VersionSnapshot[Role]


class RoleVersionController(Controller):
    """Version history for role definitions (per-role granularity)."""

    path = "/roles"
    tags = ("roles",)

    @get("/{role_name:str}/versions", guards=[require_read_access])
    async def list_versions(
        self,
        state: State,
        role_name: PathName,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> Response[PaginatedResponse[SnapshotT]]:
        """List version history for a specific role definition.

        Returns:
            ``Response[PaginatedResponse[SnapshotT]]`` instance.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repo = persistence_of(state.app_state).role_versions
        versions, total = await asyncio.gather(
            repo.list_versions(role_name, limit=limit, offset=offset),
            repo.count_versions(role_name),
        )
        logger.debug(
            VERSION_LISTED,
            entity_type="Role",
            entity_id=role_name,
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
        "/{role_name:str}/versions/{version_num:int}",
        guards=[require_read_access],
    )
    async def get_version(
        self,
        state: State,
        role_name: PathName,
        version_num: Annotated[
            int,
            PathParameter(
                ge=1,
                description="Role version (one-based; 1 = first revision).",
            ),
        ],
    ) -> Response[ApiResponse[SnapshotT]]:
        """Get a specific role version snapshot.

        Returns:
            ``Response[ApiResponse[SnapshotT]]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        repo = persistence_of(state.app_state).role_versions
        version = await repo.get_version(role_name, version_num)
        if version is None:
            logger.warning(
                VERSION_NOT_FOUND,
                entity_type="Role",
                entity_id=role_name,
                version=version_num,
            )
            msg = f"Version {version_num} not found for role {role_name!r}"
            raise NotFoundError(msg)
        return Response(
            content=ApiResponse[SnapshotT](data=version),
        )
