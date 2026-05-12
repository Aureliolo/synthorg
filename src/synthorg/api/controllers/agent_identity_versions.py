"""Agent identity version history API -- list, get, diff, rollback."""

import asyncio
from typing import Annotated, Final

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.exceptions import InternalServerException
from litestar.params import Parameter

from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import (
    ApiResponse,
    PaginatedResponse,
    RollbackAgentIdentityRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_repo_seek_meta,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.core.agent import AgentIdentity
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.engine.identity.diff import AgentIdentityDiff, compute_diff
from synthorg.hr.errors import AgentNotFoundError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.agent_identity_version import (
    AGENT_IDENTITY_DIFF_COMPUTED,
    AGENT_IDENTITY_INVALID_REQUEST,
    AGENT_IDENTITY_ROLLBACK_FAILED,
    AGENT_IDENTITY_ROLLED_BACK,
    AGENT_IDENTITY_VERSION_FETCHED,
    AGENT_IDENTITY_VERSION_LISTED,
    AGENT_IDENTITY_VERSION_NOT_FOUND,
    AGENT_IDENTITY_VERSION_OWNER_MISMATCH,
)
from synthorg.persistence.version_protocol import VersionRepository  # noqa: TC001
from synthorg.versioning import VersionSnapshot

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 20

SnapshotT = VersionSnapshot[AgentIdentity]


def _snapshot_owner_matches(snapshot: SnapshotT, agent_id: str) -> bool:
    """Return True when ``snapshot.snapshot.id`` equals the path ``agent_id``.

    Defence in depth: the stored snapshot payload encodes its owner's
    identity id.  Cross-wired/corrupted rows could otherwise leak one
    agent's history under another agent's URL.  Read and mutation
    endpoints both verify ownership before returning or acting on the
    payload.
    """
    return str(snapshot.snapshot.id) == agent_id


async def _fetch_version_pair(
    version_repo: VersionRepository[AgentIdentity],
    agent_id: str,
    from_version: int,
    to_version: int,
) -> tuple[SnapshotT, SnapshotT]:
    """Fetch two snapshots concurrently.

    Raises ``NotFoundError`` (404) if either version is missing and
    ``ValidationError`` (422) if a snapshot's encoded owner does not
    match the path agent id.
    """
    old, new = await asyncio.gather(
        version_repo.get_version(agent_id, from_version),
        version_repo.get_version(agent_id, to_version),
    )
    for snapshot, version in ((old, from_version), (new, to_version)):
        if snapshot is None:
            logger.warning(
                AGENT_IDENTITY_VERSION_NOT_FOUND,
                agent_id=agent_id,
                version=version,
            )
            msg = f"Version {version} not found"
            raise NotFoundError(msg)
        if not _snapshot_owner_matches(snapshot, agent_id):
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


class AgentIdentityVersionController(Controller):
    """Version history, diff, and rollback for agent identities."""

    path = "/agents"
    tags = ("agents",)

    @get("/{agent_id:str}/versions", guards=[require_read_access])
    async def list_versions(
        self,
        state: State,
        agent_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[SnapshotT]:
        """List version history for an agent identity."""
        secret = state.app_state.cursor_secret
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        version_repo = state.app_state.persistence.identity_versions
        versions, total = await asyncio.gather(
            version_repo.list_versions(agent_id, limit=limit, offset=offset),
            version_repo.count_versions(agent_id),
        )
        # Filter out any rows whose encoded owner does not match the path
        # agent_id.  This cannot happen for well-formed repositories but
        # guards against forged/cross-wired snapshots leaking across
        # agents' URLs.
        safe_versions = tuple(
            v for v in versions if _snapshot_owner_matches(v, agent_id)
        )
        dropped = len(versions) - len(safe_versions)
        if dropped:
            logger.warning(
                AGENT_IDENTITY_VERSION_OWNER_MISMATCH,
                agent_id=agent_id,
                dropped=dropped,
            )
        logger.debug(
            AGENT_IDENTITY_VERSION_LISTED,
            agent_id=agent_id,
            count=len(safe_versions),
        )
        # Forged-row filtering: rows whose encoded owner does not match
        # the path agent_id are dropped from ``safe_versions`` (above)
        # but still counted in the cursor advance.  ``has_more`` is
        # driven by the repo's full ``total`` and the cursor steps by
        # ``len(versions)`` (consumed repo rows, NOT filtered slice
        # length) so a page where the filter discards rows does not
        # strand the client before reaching later legitimate rows.
        meta = encode_repo_seek_meta(
            offset=offset,
            page_len=len(versions),
            total=total,
            limit=limit,
            secret=secret,
        )
        return PaginatedResponse[SnapshotT](
            data=safe_versions,
            pagination=meta,
        )

    @get(
        "/{agent_id:str}/versions/diff",
        guards=[require_read_access],
    )
    async def get_diff(
        self,
        state: State,
        agent_id: PathId,
        from_version: Annotated[
            int,
            Parameter(
                required=True,
                ge=1,
                description="Source version",
            ),
        ],
        to_version: Annotated[
            int,
            Parameter(
                required=True,
                ge=1,
                description="Target version",
            ),
        ],
    ) -> ApiResponse[AgentIdentityDiff]:
        """Compute diff between two agent identity versions."""
        if from_version >= to_version:
            error = (
                "from_version and to_version must differ"
                if from_version == to_version
                else "from_version must be less than to_version"
            )
            logger.warning(
                AGENT_IDENTITY_INVALID_REQUEST,
                agent_id=agent_id,
                error=error,
            )
            raise ValidationError(error)

        version_repo = state.app_state.persistence.identity_versions
        old, new = await _fetch_version_pair(
            version_repo,
            agent_id,
            from_version,
            to_version,
        )

        diff = compute_diff(
            agent_id=agent_id,
            old_snapshot=old.snapshot,
            new_snapshot=new.snapshot,
            from_version=from_version,
            to_version=to_version,
        )
        logger.debug(
            AGENT_IDENTITY_DIFF_COMPUTED,
            agent_id=agent_id,
            from_version=from_version,
            to_version=to_version,
        )
        return ApiResponse[AgentIdentityDiff](data=diff)

    @get(
        "/{agent_id:str}/versions/{version_num:int}",
        guards=[require_read_access],
    )
    async def get_version(
        self,
        state: State,
        agent_id: PathId,
        version_num: Annotated[int, Parameter(ge=1)],
    ) -> ApiResponse[SnapshotT]:
        """Get a specific agent identity version snapshot."""
        version_repo = state.app_state.persistence.identity_versions
        version = await version_repo.get_version(agent_id, version_num)
        if version is None:
            logger.warning(
                AGENT_IDENTITY_VERSION_NOT_FOUND,
                agent_id=agent_id,
                version=version_num,
            )
            msg = f"Version {version_num} not found"
            raise NotFoundError(msg)
        if not _snapshot_owner_matches(version, agent_id):
            logger.warning(
                AGENT_IDENTITY_VERSION_OWNER_MISMATCH,
                agent_id=agent_id,
                version=version_num,
                snapshot_id=str(version.snapshot.id),
            )
            msg = f"Version {version_num} belongs to a different agent"
            raise ValidationError(msg)
        logger.debug(
            AGENT_IDENTITY_VERSION_FETCHED,
            agent_id=agent_id,
            version=version_num,
        )
        return ApiResponse[SnapshotT](data=version)

    @post(
        "/{agent_id:str}/versions/rollback",
        guards=[require_write_access],
        status_code=200,
    )
    async def rollback_identity(
        self,
        state: State,
        agent_id: PathId,
        data: RollbackAgentIdentityRequest,
    ) -> ApiResponse[AgentIdentity]:
        """Roll the agent's identity back to ``target_version``.

        Produces a new version snapshot (N+1) whose content hash equals the
        restored snapshot's content hash, preserving the full audit trail.
        """
        version_repo = state.app_state.persistence.identity_versions
        target = await version_repo.get_version(agent_id, data.target_version)
        if target is None:
            logger.warning(
                AGENT_IDENTITY_VERSION_NOT_FOUND,
                agent_id=agent_id,
                version=data.target_version,
            )
            msg = f"Target version {data.target_version} not found"
            raise NotFoundError(msg)

        # Defence in depth: the snapshot's entity id must match the URL path.
        # A mismatch can only occur on corrupted/cross-entity rows -- refuse
        # to mutate the wrong agent.
        if not _snapshot_owner_matches(target, agent_id):
            logger.warning(
                AGENT_IDENTITY_VERSION_OWNER_MISMATCH,
                agent_id=agent_id,
                error="target snapshot id does not match path agent_id",
                snapshot_id=str(target.snapshot.id),
            )
            msg = "Target version belongs to a different agent"
            raise ValidationError(msg)

        actor = get_authenticated_user_id()
        rationale = f"rollback to v{data.target_version} by {actor}"
        if data.reason is not None:
            rationale = f"{rationale}: {data.reason}"
        try:
            rolled_back = await state.app_state.agent_registry.evolve_identity(
                agent_id,
                target.snapshot,
                evolution_rationale=rationale,
            )
        except AgentNotFoundError as exc:
            logger.warning(
                AGENT_IDENTITY_ROLLBACK_FAILED,
                agent_id=agent_id,
                error="agent not found",
            )
            msg = "Agent not found"
            raise NotFoundError(msg) from exc
        except ValueError as exc:
            # evolve_identity raises ValueError when immutable fields
            # (id/name/department) differ between the current registry entry
            # and the restored snapshot.  Surface as 422 (validation), not
            # 500.  Use ``safe_error_description`` and avoid interpolating
            # ``exc`` into the user-facing message so identity values from
            # the mismatched snapshot cannot leak through logs or the API
            # response.
            logger.warning(
                AGENT_IDENTITY_ROLLBACK_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Cannot rollback: immutable field mismatch"
            raise ValidationError(msg) from exc
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                AGENT_IDENTITY_ROLLBACK_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Rollback failed due to an unexpected server error"
            raise InternalServerException(msg) from exc

        logger.info(
            AGENT_IDENTITY_ROLLED_BACK,
            agent_id=agent_id,
            target_version=data.target_version,
        )
        return ApiResponse[AgentIdentity](data=rolled_back)
