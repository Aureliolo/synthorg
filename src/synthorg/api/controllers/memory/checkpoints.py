# module-kind: controller
"""Memory fine-tune checkpoint + run-history endpoints (CEO / SYSTEM only)."""

from litestar import Controller, delete, get, post
from litestar.datastructures import State

from synthorg.api.controllers.memory import _shared
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_roles
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_repo_seek_meta,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import (
    CheckpointOperationConflictError,
    NotFoundError,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
)
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_CHECKPOINT_DELETE_FAILED,
    MEMORY_CHECKPOINT_DEPLOY_FAILED,
    MEMORY_CHECKPOINT_NOT_FOUND,
    MEMORY_CHECKPOINT_ROLLBACK_FAILED,
)

logger = get_logger(__name__)


class MemoryCheckpointsController(Controller):
    """Fine-tune checkpoint management + run history."""

    path = "/admin/memory"
    tags = ("admin", "memory")
    guards = [require_roles(HumanRole.CEO, HumanRole.SYSTEM)]  # noqa: RUF012

    @get("/fine-tune/checkpoints")
    async def list_checkpoints(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[CheckpointRecord]:
        """List fine-tuning checkpoints.

        Returns:
            ``PaginatedResponse[CheckpointRecord]`` instance.
        """
        app_state: AppState = state.app_state
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        service = _shared.build_memory_service(app_state)
        cps, total = await service.list_checkpoints(limit=limit, offset=offset)
        meta = encode_repo_seek_meta(
            offset=offset,
            page_len=len(cps),
            total=total,
            limit=limit,
            secret=secret,
            reject_stale_cursor=False,
        )
        return PaginatedResponse(data=cps, pagination=meta)

    @post(
        "/fine-tune/checkpoints/{checkpoint_id:str}/deploy",
        guards=[
            per_op_rate_limit_from_policy(
                "memory.checkpoint_deploy",
                key="user",
            ),
        ],
        opt=per_op_concurrency_from_policy(
            "memory.checkpoint_deploy",
            key="user",
        ),
    )
    async def deploy_checkpoint(
        self,
        state: State,
        checkpoint_id: PathId,
    ) -> ApiResponse[CheckpointRecord]:
        """Deploy a specific checkpoint.

        Args:
            state: Application state.
            checkpoint_id: Checkpoint identifier (1-128 chars, enforced
                at the path-parameter boundary by ``PathId``).

        Exception mapping:

        - ``CheckpointNotFoundError`` -> HTTP 404
        - ``QueryError`` (persistence-level failure during activation
          or re-read) -> HTTP 409 with a safe message
        - Any other exception propagates so unexpected server bugs
          surface as HTTP 500 instead of being silenced as 409
          "conflict".

        Returns:
            ``ApiResponse[CheckpointRecord]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            CheckpointOperationConflictError: Raised on the corresponding failure path.
        """
        service = _shared.build_memory_service(state.app_state)
        try:
            updated = await service.deploy_checkpoint(checkpoint_id)
        except CheckpointNotFoundError as exc:
            logger.warning(
                MEMORY_CHECKPOINT_NOT_FOUND,
                checkpoint_id=checkpoint_id,
                operation="deploy",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Controller-authored 4xx message so the response body
            # never echoes backend exception class names / wording;
            # full diagnostic detail stays in the warning log above.
            msg = "Checkpoint not found"
            raise NotFoundError(msg) from exc
        except QueryError as exc:
            logger.warning(
                MEMORY_CHECKPOINT_DEPLOY_FAILED,
                checkpoint_id=checkpoint_id,
                operation="deploy",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to deploy checkpoint"
            raise CheckpointOperationConflictError(msg) from exc
        return ApiResponse(data=updated)

    @post(
        "/fine-tune/checkpoints/{checkpoint_id:str}/rollback",
        guards=[
            per_op_rate_limit_from_policy(
                "memory.checkpoint_rollback",
                key="user",
            ),
        ],
        opt=per_op_concurrency_from_policy(
            "memory.checkpoint_rollback",
            key="user",
        ),
    )
    async def rollback_checkpoint(
        self,
        state: State,
        checkpoint_id: PathId,
    ) -> ApiResponse[CheckpointRecord]:
        """Rollback: restore pre-deployment config from backup.

        Args:
            state: Application state.
            checkpoint_id: Checkpoint identifier (1-128 chars, enforced
                at the path-parameter boundary by ``PathId``).

        Exception mapping:

        - ``CheckpointNotFoundError`` -> HTTP 404 via ``NotFoundError``
        - ``CheckpointRollbackUnavailableError`` (HTTP 422, code
          ``CHECKPOINT_ROLLBACK_UNAVAILABLE``) and
          ``CheckpointRollbackCorruptError`` (HTTP 422, code
          ``CHECKPOINT_ROLLBACK_CORRUPT``) carry distinct codes so the
          dashboard can message operator error vs corrupt backup apart
        - Any other exception propagates as HTTP 500

        Returns:
            ``ApiResponse[CheckpointRecord]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            CheckpointRollbackUnavailableError: Rollback target unusable.
            CheckpointRollbackCorruptError: Raised on the corresponding failure path.
        """
        service = _shared.build_memory_service(state.app_state)
        try:
            updated = await service.rollback_checkpoint(checkpoint_id)
        except CheckpointNotFoundError as exc:
            logger.warning(
                MEMORY_CHECKPOINT_NOT_FOUND,
                checkpoint_id=checkpoint_id,
                operation="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Checkpoint not found"
            raise NotFoundError(msg) from exc
        except CheckpointRollbackUnavailableError as exc:
            # Operator-error / corrupt backup conditions; 422 better
            # reflects "rollback target invalid" than a generic 400.
            logger.warning(
                MEMORY_CHECKPOINT_ROLLBACK_FAILED,
                checkpoint_id=checkpoint_id,
                operation="rollback",
                reason="rollback_unavailable",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Checkpoint rollback is unavailable"
            raise CheckpointRollbackUnavailableError(msg) from exc
        except CheckpointRollbackCorruptError as exc:
            logger.warning(
                MEMORY_CHECKPOINT_ROLLBACK_FAILED,
                checkpoint_id=checkpoint_id,
                operation="rollback",
                reason="rollback_corrupt",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Checkpoint rollback data is corrupt"
            raise CheckpointRollbackCorruptError(msg) from exc
        return ApiResponse(data=updated)

    @delete(
        "/fine-tune/checkpoints/{checkpoint_id:str}",
        status_code=200,
        guards=[
            per_op_rate_limit_from_policy(
                "memory.checkpoint_delete",
                key="user",
            ),
        ],
    )
    async def delete_checkpoint(
        self,
        state: State,
        checkpoint_id: PathId,
    ) -> ApiResponse[None]:
        """Delete a checkpoint (rejects active checkpoint).

        Args:
            state: Application state.
            checkpoint_id: Checkpoint identifier (1-128 chars, enforced
                at the path-parameter boundary by ``PathId``).

        Exception mapping mirrors deploy/rollback so all checkpoint
        endpoints share the same contract:

        - ``CheckpointNotFoundError`` -> HTTP 404
        - ``QueryError`` (e.g. attempt to delete the active checkpoint)
          -> HTTP 409
        - anything else propagates as HTTP 500

        Returns:
            ``ApiResponse[None]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            CheckpointOperationConflictError: Raised on the corresponding failure path.
        """
        service = _shared.build_memory_service(state.app_state)
        try:
            await service.delete_checkpoint(checkpoint_id)
        except CheckpointNotFoundError as exc:
            logger.warning(
                MEMORY_CHECKPOINT_NOT_FOUND,
                checkpoint_id=checkpoint_id,
                operation="delete",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Checkpoint not found"
            raise NotFoundError(msg) from exc
        except QueryError as exc:
            logger.warning(
                MEMORY_CHECKPOINT_DELETE_FAILED,
                checkpoint_id=checkpoint_id,
                operation="delete",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Use a controller-authored message so backend exception
            # text doesn't leak into the 409 response.  Detail stays in
            # the warning log above for operator triage.
            msg = "Failed to delete checkpoint"
            raise CheckpointOperationConflictError(msg) from exc
        return ApiResponse(data=None)

    @get("/fine-tune/runs")
    async def list_runs(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[FineTuneRun]:
        """List historical pipeline runs with pagination metadata.

        Returns:
            ``PaginatedResponse[FineTuneRun]`` instance.
        """
        app_state: AppState = state.app_state
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        service = _shared.build_memory_service(app_state)
        runs, total = await service.list_runs(limit=limit, offset=offset)
        return PaginatedResponse(
            data=runs,
            pagination=encode_repo_seek_meta(
                offset=offset,
                page_len=len(runs),
                total=total,
                limit=limit,
                secret=secret,
                reject_stale_cursor=False,
            ),
        )
