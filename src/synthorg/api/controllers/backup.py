"""Backup controller -- admin endpoints for backup/restore operations.

All endpoints require CEO or the internal SYSTEM role
(used by the CLI for ``synthorg backup`` / ``synthorg wipe``).
"""

from typing import TYPE_CHECKING, Annotated, Final

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from litestar import Controller, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.exceptions import InternalServerException
from litestar.params import HeaderParameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_roles
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
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import (
    ConflictError,
    ValidationError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.backup import (
    BACKUP_FAILED,
    BACKUP_NOT_FOUND,
    BACKUP_RESTORE_FAILED,
)
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


async def _do_backup_as_dict(
    backup_callable: Callable[[], Awaitable[BackupManifest]],
) -> dict[str, object]:
    """Bridge a ``BackupManifest``-returning callable to JSON dict.

    The idempotency service caches a JSON-serialized response. The
    backup service returns a Pydantic model, so we serialize via
    ``model_dump`` for caching and re-validate on cache hit.

    The dumped payload is round-trip validated via
    ``BackupManifest.model_validate`` BEFORE we hand it to the
    idempotency service for caching. A corrupt manifest (impossible
    by construction today but cheap to guard) is rejected before it
    pollutes the cache; the controller's cache-hit branch already
    handles the symmetric "row already in cache fails to validate"
    case, so this closes the loop on both directions.
    """
    manifest = await backup_callable()
    dumped = manifest.model_dump(mode="json")
    BackupManifest.model_validate(dumped)
    return dumped


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
            NotBlankStr,
            HeaderParameter(
                name="Idempotency-Key",
                description=(
                    "RFC-style retry-safe key. Required: identical keys "
                    "within 24h return the cached manifest instead of "
                    "starting a second backup. Without a key a 5xx-driven "
                    "client retry could launch concurrent backups, "
                    "violating the at-most-one-running invariant."
                ),
                required=True,
                min_length=1,
                # Bound the key length so a malicious client cannot
                # exhaust the durable idempotency store with arbitrarily
                # large keys; 255 chars is plenty for UUIDs / SHAs and
                # matches common header-value column widths.
                max_length=255,
            ),
        ],
    ) -> ApiResponse[BackupManifest]:
        """Trigger a manual backup.

        Args:
            state: Application state.
            idempotency_key: Required caller-supplied retry token.

        Returns:
            Manifest of the created backup.
        """
        app_state: AppState = state.app_state

        async def _do_backup() -> BackupManifest:
            # ``BackupError`` and its subclasses propagate directly to
            # ``handle_backup_error`` which maps
            # ``BackupInProgressError`` to 409,
            # ``BackupNotFoundError`` to 404, and any other
            # ``BackupError`` type to a scrubbed 5xx with the
            # RFC 9457 envelope. Translating here would route
            # through the generic ``ConflictError`` /
            # ``InternalServerException`` paths and drop the
            # domain-specific ``BackupError`` type the handler
            # discriminates on.
            return await app_state.backup_service.create_backup(
                BackupTrigger.MANUAL,
            )

        # ``NotBlankStr`` is an Annotated type alias, not a callable
        # constructor; calling it at runtime returns the underlying
        # ``str`` without running the AfterValidator (which only fires
        # through Pydantic). The literal "backup" and the
        # already-validated header value satisfy the parameter contract
        # directly, so pass them as plain strings instead of fake-
        # wrapping them in a no-op call.
        outcome = await app_state.idempotency_service.run_idempotent(
            scope="backup",
            key=idempotency_key,
            callback=lambda: _do_backup_as_dict(_do_backup),
        )
        if outcome.timed_out:
            # Discriminated 409 path: distinct from a callback that
            # legitimately returned ``None``.
            logger.warning(
                IDEMPOTENCY_CLAIM_IN_FLIGHT,
                scope="backup",
                idempotency_key=idempotency_key,
                endpoint="backup.create",
            )
            msg = "Concurrent in-flight backup with this idempotency key"
            raise ConflictError(msg)
        try:
            manifest = BackupManifest.model_validate(outcome.result)
        except (ValueError, TypeError) as exc:
            # A corrupt or stale cached payload (e.g. schema added a
            # field after the entry was stored) would otherwise leak
            # the raw pydantic ValidationError. Surface a 5xx instead
            # so the operator gets a stable error and the failure is
            # visible in logs.
            log_exception_redacted(
                logger,
                BACKUP_FAILED,
                exc,
                scope="backup",
                idempotency_key=idempotency_key,
                endpoint="backup.create",
                stage="cached_manifest_validate",
            )
            msg = "Cached backup manifest failed validation; rerun the backup"
            raise InternalServerException(msg) from exc
        return ApiResponse(data=manifest)

    @get()
    async def list_backups(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
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
        # ``BackupError`` propagates to ``handle_backup_error`` so the
        # response carries the structured RFC 9457 envelope; an
        # ``InternalServerException`` re-raise here would drop the
        # ``BackupError`` type and force an unstructured 500.
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
        # ``BackupNotFoundError`` propagates to
        # ``handle_backup_error`` which maps it to 404 with
        # ``error_code=RECORD_NOT_FOUND``. A controller-level
        # translation to the generic ``NotFoundError`` collapses
        # the type into ``RESOURCE_NOT_FOUND`` and clients lose
        # the ability to discriminate which resource was missing.
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
        # ``BackupError`` and its subclasses propagate to
        # ``handle_backup_error`` so the response carries the
        # structured envelope (404 + ``RECORD_NOT_FOUND`` for
        # ``BackupNotFoundError``, 409 + ``RESOURCE_CONFLICT`` for
        # ``BackupInProgressError``).
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
        except BackupNotFoundError:
            logger.warning(
                BACKUP_NOT_FOUND,
                backup_id=data.backup_id,
            )
            # Let ``BackupNotFoundError`` propagate so
            # ``handle_backup_error`` maps it to 404 with the
            # domain-specific ``RECORD_NOT_FOUND`` envelope; the prior
            # translation to generic ``NotFoundError`` dropped the
            # discriminating error code. ``ManifestError`` and
            # ``BackupInProgressError`` below stay translated by
            # design (they carry potentially internal detail in their
            # messages and the controller authors a sanitized 4xx).
            raise
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
            log_exception_redacted(
                logger, BACKUP_RESTORE_FAILED, exc, backup_id=data.backup_id
            )
            msg = "Restore operation failed"
            raise InternalServerException(msg) from exc
        return ApiResponse(data=response)
