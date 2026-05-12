"""Evaluation config version history controller -- list, get."""

import asyncio
from typing import Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_repo_seek_meta,
)
from synthorg.core.domain_errors import NotFoundError
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.observability import get_logger
from synthorg.observability.events.versioning import (
    VERSION_LISTED,
    VERSION_NOT_FOUND,
)
from synthorg.versioning import VersionSnapshot

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 20

SnapshotT = VersionSnapshot[EvaluationConfig]

#: Entity ID for the singleton evaluation configuration.
_ENTITY_ID = "default"


class EvaluationConfigVersionController(Controller):
    """Version history for evaluation configuration."""

    path = "/evaluation/config"
    tags = ("evaluation",)

    @get("/versions", guards=[require_read_access])
    async def list_versions(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> Response[PaginatedResponse[SnapshotT]]:
        """List version history for evaluation configuration."""
        secret = state.app_state.cursor_secret
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repo = state.app_state.persistence.evaluation_config_versions
        versions, total = await asyncio.gather(
            repo.list_versions(_ENTITY_ID, limit=limit, offset=offset),
            repo.count_versions(_ENTITY_ID),
        )
        logger.debug(
            VERSION_LISTED,
            entity_type="EvaluationConfig",
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
        version_num: Annotated[int, Parameter(ge=1)],
    ) -> Response[ApiResponse[SnapshotT]]:
        """Get a specific evaluation config version snapshot."""
        repo = state.app_state.persistence.evaluation_config_versions
        version = await repo.get_version(_ENTITY_ID, version_num)
        if version is None:
            logger.warning(
                VERSION_NOT_FOUND,
                entity_type="EvaluationConfig",
                entity_id=_ENTITY_ID,
                version=version_num,
            )
            msg = f"Version {version_num} not found"
            raise NotFoundError(msg)
        return Response(
            content=ApiResponse[SnapshotT](data=version),
        )
