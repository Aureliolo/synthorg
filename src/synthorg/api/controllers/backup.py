"""Backup controller -- admin endpoints for backup/restore operations.

All endpoints require CEO or the internal SYSTEM role
(used by the CLI for ``synthorg backup`` / ``synthorg wipe``).
"""

from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from litestar import Controller, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.exceptions import (
    ClientException,
    InternalServerException,
    NotFoundException,
)
from litestar.params import Parameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import HumanRole, require_roles
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.backup.errors import (
    BackupError,
    BackupInProgressError,
    BackupNotFoundError,
    ManifestError,
    RestoreError,
)
from synthorg.backup.models import (
    BackupInfo,
    BackupManifest,
    BackupTrigger,
    RestoreRequest,
    RestoreResponse,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.backup import (
    BACKUP_FAILED,
    BACKUP_NOT_FOUND,
    BACKUP_RESTORE_FAILED,
)

logger = get_logger(__name__)


async def _do_backup_as_dict(
    backup_callable: Callable[[], Awaitable[BackupManifest]],
) -> dict[str, object]:
    """Bridge a ``BackupManifest``-returning callable to JSON dict.

    The idempotency service caches a JSON-serialized response. The
    backup service returns a Pydantic model, so we serialize via
    ``model_dump`` for caching and re-validate on cache hit.
    """
    manifest = await backup_callable()
    return manifest.model_dump(mode="json")


class BackupController(Controller):
    """Admin endpoints for backup and restore operations.

    All endpoints require CEO or the internal SYSTEM role
    (CLI-to-backend identity).
    """

    path = "/admin/backups"
    tags = ("admin", "backup")
    guards = [require_roles(HumanRole.CEO, HumanRole.SYSTEM)]  # noqa: RUF012

    @post()
    async def create_backup(
        self,
        state: State,
        idempotency_key: Annotated[
            str | None,
            Parameter(
                header="Idempotency-Key",
                description=(
                    "RFC-style retry-safe key. Same key within 24h "
                    "returns the cached manifest instead of starting "
                    "a second backup."
                ),
                required=False,
            ),
        ] = None,
    ) -> ApiResponse[BackupManifest]:
        """Trigger a manual backup.

        Args:
            state: Application state.
            idempotency_key: Optional caller-supplied retry token.

        Returns:
            Manifest of the created backup.
        """
        app_state: AppState = state.app_state

        async def _do_backup() -> BackupManifest:
            try:
                return await app_state.backup_service.create_backup(
                    BackupTrigger.MANUAL,
                )
            except BackupInProgressError as exc:
                logger.warning(
                    BACKUP_FAILED,
                    error=str(exc),
                )
                raise ClientException(
                    str(exc),
                    status_code=409,
                ) from exc
            except BackupError as exc:
                logger.error(
                    BACKUP_FAILED,
                    error=str(exc),
                    exc_info=True,
                )
                msg = "Backup operation failed"
                raise InternalServerException(msg) from exc

        if idempotency_key:
            cached, _fresh = await app_state.idempotency_service.run_idempotent(
                scope=NotBlankStr("backup"),
                key=NotBlankStr(idempotency_key),
                callback=lambda: _do_backup_as_dict(_do_backup),
            )
            if cached is None:
                msg = "Concurrent in-flight backup with this idempotency key"
                raise ClientException(msg, status_code=409)
            return ApiResponse(data=BackupManifest.model_validate(cached))

        manifest = await _do_backup()
        return ApiResponse(data=manifest)

    @get()
    async def list_backups(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[BackupInfo]:
        """List available backups (paginated, newest first).

        Pushes ``limit + 1 / offset`` into ``BackupService.list_backups``
        so manifest parsing stays O(limit) instead of scaling with the
        total on-disk backup count.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page;
                ``None`` starts at the newest backup.
            limit: Page size.

        Returns:
            Paginated backup info summaries.
        """
        app_state: AppState = state.app_state
        offset = (
            0
            if cursor is None
            else decode_cursor(cursor, secret=app_state.cursor_secret)
        )
        try:
            # Fetch ``limit + 1`` so we can detect that another page
            # follows without a second full-directory scan.
            backups = await app_state.backup_service.list_backups(
                limit=limit + 1,
                offset=offset,
            )
        except BackupError as exc:
            logger.error(
                BACKUP_FAILED,
                error=str(exc),
                exc_info=True,
            )
            msg = "Failed to list backups"
            raise InternalServerException(msg) from exc
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(backups),
            limit=limit,
            secret=app_state.cursor_secret,
        )
        window = backups[:limit]
        return PaginatedResponse[BackupInfo](data=window, pagination=meta)

    @get("/{backup_id:str}")
    async def get_backup(
        self,
        state: State,
        backup_id: PathId,
    ) -> ApiResponse[BackupManifest]:
        """Get details of a specific backup.

        Args:
            state: Application state.
            backup_id: Backup identifier.

        Returns:
            Full backup manifest.
        """
        app_state: AppState = state.app_state
        try:
            manifest = await app_state.backup_service.get_backup(backup_id)
        except BackupNotFoundError as exc:
            logger.warning(
                BACKUP_NOT_FOUND,
                backup_id=backup_id,
            )
            raise NotFoundException(str(exc)) from exc
        return ApiResponse(data=manifest)

    @delete("/{backup_id:str}", status_code=HTTP_204_NO_CONTENT)
    async def delete_backup(
        self,
        state: State,
        backup_id: PathId,
    ) -> None:
        """Delete a backup.

        Args:
            state: Application state.
            backup_id: Backup identifier.
        """
        app_state: AppState = state.app_state
        try:
            await app_state.backup_service.delete_backup(backup_id)
        except BackupNotFoundError as exc:
            logger.warning(
                BACKUP_NOT_FOUND,
                backup_id=backup_id,
            )
            raise NotFoundException(str(exc)) from exc

    @post(
        "/restore",
        guards=[
            per_op_rate_limit_from_policy("admin.backup_restore", key="user"),
        ],
    )
    async def restore_backup(
        self,
        state: State,
        data: RestoreRequest,
    ) -> ApiResponse[RestoreResponse]:
        """Restore from a backup.

        Requires ``confirm=true`` in the request body as a safety gate.

        Args:
            state: Application state.
            data: Restore request with backup_id and confirmation.

        Returns:
            Restore response with safety backup ID.

        Raises:
            ClientException: If confirm is false (400), backup in
                progress (409), or manifest invalid (422).
            NotFoundException: If the backup does not exist.
            InternalServerException: If the restore fails.
        """
        if not data.confirm:
            msg = "Restore requires confirm=true"
            raise ClientException(msg, status_code=400)

        app_state: AppState = state.app_state
        try:
            response = await app_state.backup_service.restore_from_backup(
                data.backup_id,
                components=data.components,
            )
        except BackupNotFoundError as exc:
            logger.warning(
                BACKUP_NOT_FOUND,
                backup_id=data.backup_id,
            )
            raise NotFoundException(str(exc)) from exc
        except ManifestError as exc:
            logger.warning(
                BACKUP_RESTORE_FAILED,
                backup_id=data.backup_id,
                error=str(exc),
            )
            raise ClientException(str(exc), status_code=422) from exc
        except BackupInProgressError as exc:
            logger.warning(
                BACKUP_FAILED,
                backup_id=data.backup_id,
                error=str(exc),
            )
            raise ClientException(str(exc), status_code=409) from exc
        except RestoreError as exc:
            logger.error(
                BACKUP_RESTORE_FAILED,
                backup_id=data.backup_id,
                error=str(exc),
                exc_info=True,
            )
            msg = "Restore operation failed"
            raise InternalServerException(msg) from exc
        return ApiResponse(data=response)
