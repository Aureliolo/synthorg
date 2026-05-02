"""Backup controller -- admin endpoints for backup/restore operations.

All endpoints require CEO or the internal SYSTEM role
(used by the CLI for ``synthorg backup`` / ``synthorg wipe``).
"""

from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from litestar import Controller, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.exceptions import InternalServerException
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
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_NOT_FOUND,
    BACKUP_RESTORE_FAILED,
)
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT

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

    @post(
        guards=[
            per_op_rate_limit_from_policy("admin.backup_create", key="user"),
        ],
    )
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
                min_length=1,
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
            # Audit 147-error-mapping-inconsistency: BackupError +
            # subclasses propagate directly to ``handle_backup_error``
            # which already maps BackupInProgressError to 409,
            # BackupNotFoundError to 404, and other BackupError types
            # to a scrubbed 5xx with the right RFC 9457 envelope.
            # The previous controller-level translation lost the
            # domain-specific BackupError hierarchy by routing through
            # the generic ConflictError / InternalServerException
            # paths.
            return await app_state.backup_service.create_backup(
                BackupTrigger.MANUAL,
            )

        if idempotency_key:
            outcome = await app_state.idempotency_service.run_idempotent(
                scope=NotBlankStr("backup"),
                key=NotBlankStr(idempotency_key),
                callback=lambda: _do_backup_as_dict(_do_backup),
            )
            if outcome.timed_out:
                # Discriminated 409 path: distinct from a callback
                # that legitimately returned ``None``.
                logger.warning(
                    IDEMPOTENCY_CLAIM_IN_FLIGHT,
                    scope="backup",
                    idempotency_key=idempotency_key,
                    endpoint="backup.create",
                )
                msg = "Concurrent in-flight backup with this idempotency key"
                raise ConflictError(msg)
            return ApiResponse(data=BackupManifest.model_validate(outcome.result))

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
        # Audit 147-error-mapping-inconsistency: let BackupError
        # propagate to handle_backup_error so the response carries
        # the structured RFC 9457 envelope; the previous
        # InternalServerException re-raise dropped the BackupError
        # type and forced an unstructured 500 response.
        # Fetch ``limit + 1`` so we can detect that another page
        # follows without a second full-directory scan.
        backups = await app_state.backup_service.list_backups(
            limit=limit + 1,
            offset=offset,
        )
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
        # Audit 147-error-mapping-inconsistency: BackupNotFoundError
        # propagates to handle_backup_error which maps it to 404
        # with error_code=RECORD_NOT_FOUND -- preserves the
        # domain-specific BackupNotFoundError type instead of
        # collapsing it into the generic NotFoundError /
        # RESOURCE_NOT_FOUND that controller-level translation
        # produced.
        app_state: AppState = state.app_state
        manifest = await app_state.backup_service.get_backup(backup_id)
        return ApiResponse(data=manifest)

    @delete(
        "/{backup_id:str}",
        status_code=HTTP_204_NO_CONTENT,
        guards=[
            per_op_rate_limit_from_policy("admin.backup_delete", key="user"),
        ],
    )
    async def delete_backup(
        self,
        state: State,
        backup_id: PathId,
    ) -> None:
        """Delete a backup.

        Args:
            state: Application state.
            backup_id: Backup identifier.

        Raises:
            NotFoundError: If backup does not exist (404).
            ConflictError: If a backup operation is already in progress
                (409); mirrors ``create_backup`` / ``restore_backup``
                so all three backup-mutation endpoints share the same
                domain-error mapping.
        """
        # Audit 147-error-mapping-inconsistency: BackupError +
        # subclasses propagate to handle_backup_error so the
        # response carries the structured envelope (404 +
        # RECORD_NOT_FOUND for BackupNotFoundError, 409 +
        # RESOURCE_CONFLICT for BackupInProgressError).
        app_state: AppState = state.app_state
        await app_state.backup_service.delete_backup(backup_id)

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
            ValidationError: If confirm is false or manifest invalid (422).
            ConflictError: If a backup is in progress (409).
            NotFoundError: If the backup does not exist (404).
            InternalServerException: If the restore fails.
        """
        if not data.confirm:
            msg = "Restore requires confirm=true"
            # Missing required precondition is a validation failure, not
            # a generic 400.  ValidationError maps to 422 via
            # EXCEPTION_HANDLERS, matching the rest of the codebase's
            # input-shape errors.  Emit a warning before raising so the
            # rejection is observable in the audit stream the same way
            # every other restore-failure branch is.
            logger.warning(
                BACKUP_RESTORE_FAILED,
                backup_id=data.backup_id,
                reason="confirm_false",
            )
            raise ValidationError(msg)

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
            msg = "Backup not found"
            raise NotFoundError(msg) from exc
        except ManifestError as exc:
            logger.warning(
                BACKUP_RESTORE_FAILED,
                backup_id=data.backup_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Controller-authored 4xx message so the response body
            # never echoes raw manifest-parse internals; full diagnostic
            # detail stays in the warning log.
            msg = "Invalid backup manifest"
            raise ValidationError(msg) from exc
        except BackupInProgressError as exc:
            # Use BACKUP_RESTORE_FAILED (not BACKUP_FAILED) so restore
            # failures are tracked separately from create-backup
            # failures in the audit stream and dashboards.
            logger.warning(
                BACKUP_RESTORE_FAILED,
                backup_id=data.backup_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg_in_progress = "A backup operation is already in progress"
            raise ConflictError(msg_in_progress) from exc
        except RestoreError as exc:
            logger.error(
                BACKUP_RESTORE_FAILED,
                backup_id=data.backup_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                exc_info=True,
            )
            msg = "Restore operation failed"
            raise InternalServerException(msg) from exc
        return ApiResponse(data=response)
