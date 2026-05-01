"""Memory admin controller -- fine-tuning and embedder endpoints.

All endpoints require CEO or the internal SYSTEM role
(used by the CLI for admin operations).
"""

import asyncio
from typing import Final

from litestar import Controller, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import HumanRole, require_roles
from synthorg.api.pagination import encode_repo_seek_meta
from synthorg.api.rate_limits import per_op_concurrency, per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.domain_errors import (
    ConflictError,
    FeatureNotImplementedError,
    NotFoundError,
    ValidationError,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRequest,
    FineTuneRun,
    FineTuneStatus,
    PreflightCheck,
    PreflightResult,
)
from synthorg.memory.errors import FineTuneDependencyError
from synthorg.memory.fine_tune_plan import BackendUnsupportedError
from synthorg.memory.service import (
    CheckpointNotFoundError,
    CheckpointRollbackCorruptError,
    CheckpointRollbackUnavailableError,
    MemoryService,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_CHECKPOINT_DELETE_FAILED,
    MEMORY_CHECKPOINT_DEPLOY_FAILED,
    MEMORY_CHECKPOINT_NOT_FOUND,
    MEMORY_CHECKPOINT_ROLLBACK_FAILED,
    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
    MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
    MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED,
    MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED,
    MEMORY_FINE_TUNE_REQUESTED,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,  # noqa: TC001
    FineTuneRunRepository,  # noqa: TC001
)

logger = get_logger(__name__)

# Fine-tune preflight: batch-size recommendation table by available VRAM.
# Tiers are checked in descending order; first threshold whose VRAM ceiling
# is reached wins.  CPU-only / sub-threshold falls back to _DEFAULT_BATCH_SIZE.
_DEFAULT_BATCH_SIZE: Final[int] = 16


def _build_memory_service(
    app_state: AppState,
    *,
    require_fine_tune: bool = True,
) -> MemoryService:
    """Construct a :class:`MemoryService` from the current AppState.

    Kept on the controller module rather than :class:`AppState` so the
    service layer depends on AppState (and not vice-versa) and the
    AppState slot inventory stays stable. Resolves the fine-tune
    repositories through :class:`PersistenceBackend` so the controller
    does not hard-wire the SQLite implementation.

    The ``require_fine_tune`` flag separates the fine-tune-admin
    endpoints (which need both checkpoint + run repos and translate a
    missing backend implementation into HTTP 501) from memory-only
    endpoints such as the ``DELETE /memory/entries/...`` path,
    which only need the ``MemoryBackend``. Without this carve-out a
    Postgres deployment that wires a memory backend without fine-tune
    support would 501 on every entry deletion even though
    :class:`MemoryService.delete_memory_entry` can run without the
    fine-tune repos.

    Args:
        app_state: Active application state.
        require_fine_tune: When ``True`` (default), eagerly resolve
            ``fine_tune_checkpoints`` / ``fine_tune_runs`` and raise
            :class:`FeatureNotImplementedError` (HTTP 501) when they
            are absent.  When ``False``, leave the repos as ``None``
            so the service constructs cleanly for memory-only
            endpoints.

    Raises:
        FeatureNotImplementedError: When ``require_fine_tune`` is
            ``True`` and the backend does not implement the fine-tune
            repositories (HTTP 501).
    """
    backend = app_state.persistence
    checkpoint_repo: FineTuneCheckpointRepository | None = None
    run_repo: FineTuneRunRepository | None = None
    if require_fine_tune:
        try:
            checkpoint_repo = backend.fine_tune_checkpoints
            run_repo = backend.fine_tune_runs
        except NotImplementedError as exc:
            msg = (
                "Fine-tune admin endpoints are not supported by the "
                "active persistence backend."
            )
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                backend=type(backend).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise FeatureNotImplementedError(msg) from exc
    return MemoryService(
        checkpoint_repo=checkpoint_repo,
        run_repo=run_repo,
        settings_service=(
            app_state.settings_service if app_state.has_settings_service else None
        ),
        memory_backend=(
            app_state.memory_backend if app_state.has_memory_backend else None
        ),
    )


_BATCH_SIZE_BY_VRAM_GB: Final[tuple[tuple[float, int], ...]] = (
    (40.0, 128),
    (16.0, 64),
    (8.0, 32),
)

# Fine-tune preflight: document-count thresholds for the source corpus.
_MIN_DOCS_REQUIRED: Final[int] = 10
_MIN_DOCS_RECOMMENDED: Final[int] = 50


class ActiveEmbedderResponse(BaseModel):
    """Active embedder configuration read from settings."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    provider: NotBlankStr | None = Field(
        default=None,
        description="Embedding provider name",
    )
    model: NotBlankStr | None = Field(
        default=None,
        description="Embedding model identifier",
    )
    dims: int | None = Field(
        default=None,
        ge=1,
        description="Embedding vector dimensions",
    )


class MemoryAdminController(Controller):
    """Admin endpoints for memory management.

    Provides fine-tuning pipeline control, checkpoint management,
    and embedder configuration queries.  All endpoints require
    CEO or SYSTEM role.
    """

    path = "/admin/memory"
    tags = ("admin", "memory")
    guards = [require_roles(HumanRole.CEO, HumanRole.SYSTEM)]  # noqa: RUF012

    # -- Fine-tuning pipeline ----------------------------------------

    @post(
        "/fine-tune",
        guards=[
            per_op_rate_limit_from_policy("memory.fine_tune", key="user"),
        ],
        opt=per_op_concurrency(
            "memory.fine_tune",
            max_inflight=1,
            key="user",
        ),
    )
    async def start_fine_tune(
        self,
        state: State,
        data: FineTuneRequest,
    ) -> ApiResponse[FineTuneStatus]:
        """Trigger a fine-tuning pipeline run."""
        app_state: AppState = state.app_state
        logger.info(
            MEMORY_FINE_TUNE_REQUESTED,
            source_dir=data.source_dir,
            base_model=data.base_model,
        )
        if not app_state.has_fine_tune_orchestrator:
            msg = "Fine-tuning is not available"
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                operation="start",
                reason="orchestrator_not_configured",
                backend=type(app_state.persistence).__name__,
            )
            raise FeatureNotImplementedError(msg)
        orchestrator = app_state.fine_tune_orchestrator
        try:
            run = await orchestrator.start(data)
        except RuntimeError as exc:
            logger.warning(
                MEMORY_FINE_TUNE_REQUESTED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "A fine-tuning run is already active"
            raise ConflictError(msg) from exc
        return ApiResponse(
            data=FineTuneStatus(
                run_id=run.id,
                stage=run.stage,
                progress=run.progress,
            ),
        )

    @post(
        "/fine-tune/resume/{run_id:str}",
        guards=[
            per_op_rate_limit_from_policy("memory.fine_tune_resume", key="user"),
        ],
        # Shares the inflight bucket with ``memory.fine_tune`` so a user
        # cannot resume while a fresh start is still in flight; the
        # sliding-window guard above still uses the distinct operation
        # name so operators can tune resume rates independently.
        opt=per_op_concurrency(
            "memory.fine_tune",
            max_inflight=1,
            key="user",
        ),
    )
    async def resume_fine_tune(
        self,
        state: State,
        run_id: str,
    ) -> ApiResponse[FineTuneStatus]:
        """Resume a failed/cancelled pipeline run."""
        app_state: AppState = state.app_state
        if not app_state.has_fine_tune_orchestrator:
            msg = "Fine-tuning is not available"
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                operation="resume",
                run_id=run_id,
                reason="orchestrator_not_configured",
                backend=type(app_state.persistence).__name__,
            )
            raise FeatureNotImplementedError(msg)
        orchestrator = app_state.fine_tune_orchestrator
        try:
            run = await orchestrator.resume(run_id)
        except RuntimeError as exc:
            logger.warning(
                MEMORY_FINE_TUNE_REQUESTED,
                run_id=run_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "A fine-tuning run is already active"
            raise ConflictError(msg) from exc
        except ValueError as exc:
            logger.warning(
                MEMORY_FINE_TUNE_REQUESTED,
                run_id=run_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Run not found or not resumable"
            raise NotFoundError(msg) from exc
        return ApiResponse(
            data=FineTuneStatus(
                run_id=run.id,
                stage=run.stage,
                progress=run.progress,
            ),
        )

    @get("/fine-tune/status")
    async def get_fine_tune_status(
        self,
        state: State,
    ) -> ApiResponse[FineTuneStatus]:
        """Get the current fine-tuning pipeline status."""
        app_state: AppState = state.app_state
        if not app_state.has_fine_tune_orchestrator:
            return ApiResponse(
                data=FineTuneStatus(stage=FineTuneStage.IDLE),
            )
        orchestrator = app_state.fine_tune_orchestrator
        status = await orchestrator.get_status()
        return ApiResponse(data=status)

    @post(
        "/fine-tune/cancel",
        guards=[
            per_op_rate_limit_from_policy("memory.fine_tune_cancel", key="user"),
        ],
    )
    async def cancel_fine_tune(
        self,
        state: State,
    ) -> ApiResponse[FineTuneStatus]:
        """Cancel the active pipeline run."""
        app_state: AppState = state.app_state
        if not app_state.has_fine_tune_orchestrator:
            msg = "Fine-tuning is not available"
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                operation="cancel",
                reason="orchestrator_not_configured",
                backend=type(app_state.persistence).__name__,
            )
            raise FeatureNotImplementedError(msg)
        orchestrator = app_state.fine_tune_orchestrator
        await orchestrator.cancel()
        status = await orchestrator.get_status()
        return ApiResponse(data=status)

    @post(
        "/fine-tune/preflight",
        guards=[
            per_op_rate_limit_from_policy(
                "memory.fine_tune_preflight",
                key="user",
            ),
        ],
    )
    async def run_preflight(
        self,
        state: State,  # noqa: ARG002
        data: FineTuneRequest,
    ) -> ApiResponse[PreflightResult]:
        """Run pre-flight validation checks."""
        async with asyncio.TaskGroup() as tg:
            checks_task = tg.create_task(
                asyncio.to_thread(_run_preflight_checks, data),
            )
            batch_task = tg.create_task(
                asyncio.to_thread(_recommend_batch_size),
            )
        checks = list(checks_task.result())
        batch_size = batch_task.result()
        result = PreflightResult(
            checks=tuple(checks),
            recommended_batch_size=batch_size,
        )
        logger.info(
            MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED,
            can_proceed=result.can_proceed,
            check_count=len(checks),
        )
        return ApiResponse(data=result)

    # -- Checkpoint management ---------------------------------------

    @get("/fine-tune/checkpoints")
    async def list_checkpoints(
        self,
        state: State,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedResponse[CheckpointRecord]:
        """List fine-tuning checkpoints."""
        limit = min(max(limit, 1), 200)
        offset = max(offset, 0)
        service = _build_memory_service(state.app_state)
        cps, total = await service.list_checkpoints(limit=limit, offset=offset)
        meta = encode_repo_seek_meta(
            offset=offset,
            page_len=len(cps),
            total=total,
            limit=limit,
            secret=state.app_state.cursor_secret,
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
        opt=per_op_concurrency(
            "memory.checkpoint_deploy",
            max_inflight=1,
            key="user",
        ),
    )
    async def deploy_checkpoint(
        self,
        state: State,
        checkpoint_id: str,
    ) -> ApiResponse[CheckpointRecord]:
        """Deploy a specific checkpoint.

        Exception mapping:

        - ``CheckpointNotFoundError`` -> HTTP 404
        - ``QueryError`` (persistence-level failure during activation
          or re-read) -> HTTP 409 with a safe message
        - Any other exception propagates so unexpected server bugs
          surface as HTTP 500 instead of being silenced as 409
          "conflict".
        """
        service = _build_memory_service(state.app_state)
        try:
            updated = await service.deploy_checkpoint(NotBlankStr(checkpoint_id))
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
            raise ConflictError(msg) from exc
        return ApiResponse(data=updated)

    @post(
        "/fine-tune/checkpoints/{checkpoint_id:str}/rollback",
        guards=[
            per_op_rate_limit_from_policy(
                "memory.checkpoint_rollback",
                key="user",
            ),
        ],
        opt=per_op_concurrency(
            "memory.checkpoint_rollback",
            max_inflight=1,
            key="user",
        ),
    )
    async def rollback_checkpoint(
        self,
        state: State,
        checkpoint_id: str,
    ) -> ApiResponse[CheckpointRecord]:
        """Rollback: restore pre-deployment config from backup.

        Exception mapping:

        - ``CheckpointNotFoundError`` -> HTTP 404 via ``NotFoundError``
        - ``CheckpointRollbackUnavailableError``,
          ``CheckpointRollbackCorruptError`` -> HTTP 422 via
          ``ValidationError`` (operator error / corrupt backup)
        - Any other exception propagates as HTTP 500
        """
        service = _build_memory_service(state.app_state)
        try:
            updated = await service.rollback_checkpoint(NotBlankStr(checkpoint_id))
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
            raise ValidationError(msg) from exc
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
            raise ValidationError(msg) from exc
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
        checkpoint_id: str,
    ) -> ApiResponse[None]:
        """Delete a checkpoint (rejects active checkpoint).

        Exception mapping mirrors deploy/rollback so all checkpoint
        endpoints share the same contract:

        - ``CheckpointNotFoundError`` -> HTTP 404
        - ``QueryError`` (e.g. attempt to delete the active checkpoint)
          -> HTTP 409
        - anything else propagates as HTTP 500
        """
        service = _build_memory_service(state.app_state)
        try:
            await service.delete_checkpoint(NotBlankStr(checkpoint_id))
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
            raise ConflictError(msg) from exc
        return ApiResponse(data=None)

    # -- Memory entries -------------------------------------------------

    @delete(
        "/agents/{agent_id:str}/memories/{memory_id:str}",
        status_code=200,
        guards=[
            per_op_rate_limit_from_policy(
                "memory.entry_delete",
                key="user",
            ),
        ],
    )
    async def delete_memory_entry(
        self,
        state: State,
        agent_id: str,
        memory_id: str,
    ) -> ApiResponse[None]:
        """Delete a single memory entry owned by an agent.

        Returns ``200 OK`` on success and ``404 Not Found`` when the
        memory entry does not exist (or the agent has no entry with
        that id). Returns ``501 Not Implemented`` when no memory
        backend is wired on the active app state.
        """
        # ``require_fine_tune=False`` -- entry deletion only needs the
        # ``MemoryBackend``; eagerly resolving the fine-tune repos
        # would 501 every memory-only deployment, which the memory-entry
        # delete path must support.
        service = _build_memory_service(state.app_state, require_fine_tune=False)
        try:
            deleted = await service.delete_memory_entry(
                NotBlankStr(agent_id),
                NotBlankStr(memory_id),
            )
        except BackendUnsupportedError as exc:
            # ``MemoryService.delete_memory_entry`` already emits
            # ``MEMORY_ENTRY_DELETE_FAILED`` for this branch, so the
            # controller stays in the layering role of HTTP
            # translation only and does not double-record the event.
            raise FeatureNotImplementedError(
                safe_error_description(exc),
            ) from exc
        if not deleted:
            # ``MemoryService.delete_memory_entry`` emits
            # ``MEMORY_ENTRY_DELETE_FAILED`` with ``reason="not_found"``
            # for this branch, so the controller stays in the layering
            # role of HTTP translation only.
            msg = f"memory entry {memory_id!r} not found"
            raise NotFoundError(msg)
        return ApiResponse(data=None)

    # -- Run history -------------------------------------------------

    @get("/fine-tune/runs")
    async def list_runs(
        self,
        state: State,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedResponse[FineTuneRun]:
        """List historical pipeline runs with pagination metadata."""
        limit = min(max(limit, 1), 200)
        offset = max(offset, 0)
        service = _build_memory_service(state.app_state)
        runs, total = await service.list_runs(limit=limit, offset=offset)
        return PaginatedResponse(
            data=runs,
            pagination=encode_repo_seek_meta(
                offset=offset,
                page_len=len(runs),
                total=total,
                limit=limit,
                secret=state.app_state.cursor_secret,
                reject_stale_cursor=False,
            ),
        )

    # -- Embedder config ---------------------------------------------

    @get("/embedder")
    async def get_active_embedder(
        self,
        state: State,
    ) -> ApiResponse[ActiveEmbedderResponse]:
        """Get the active embedder configuration."""
        app_state: AppState = state.app_state
        result = ActiveEmbedderResponse()
        if app_state.has_settings_service:
            svc = app_state.settings_service
            try:
                provider_sv = await svc.get(
                    "memory",
                    "embedder_provider",
                )
                model_sv = await svc.get("memory", "embedder_model")
                dims_sv = await svc.get("memory", "embedder_dims")
                dims_value: int | None = None
                if dims_sv.value:
                    try:
                        dims_value = int(dims_sv.value)
                    except ValueError, TypeError:
                        logger.warning(
                            MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
                            setting="embedder_dims",
                            value=dims_sv.value,
                            reason="invalid integer value",
                        )
                result = ActiveEmbedderResponse(
                    provider=provider_sv.value or None,
                    model=model_sv.value or None,
                    dims=dims_value,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # Re-raise after logging instead of silently
                # swallowing -- a settings-service failure here would
                # otherwise look like "no embedder configured" to the
                # caller, masking the broken backend.
                logger.warning(
                    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
        return ApiResponse(data=result)


# -- Preflight helpers ------------------------------------------------


def _run_preflight_checks(
    request: FineTuneRequest,
) -> list[PreflightCheck]:
    """Run all pre-flight validation checks."""
    checks: list[PreflightCheck] = []
    checks.append(_check_dependencies())
    checks.append(_check_gpu())
    checks.append(_check_documents(request.source_dir))
    output_dir = request.output_dir or request.source_dir
    checks.append(_check_disk_space(output_dir))
    return checks


def _check_documents(source_dir: str) -> PreflightCheck:
    """Check source directory has enough documents."""
    from pathlib import Path  # noqa: PLC0415

    src = Path(source_dir)
    if not src.exists():
        return PreflightCheck(
            name="documents",
            status="fail",
            message="Source directory not found",
        )
    count = sum(1 for ext in ("*.txt", "*.md", "*.rst") for _ in src.rglob(ext))
    if count < _MIN_DOCS_REQUIRED:
        return PreflightCheck(
            name="documents",
            status="fail",
            message=(
                f"Too few documents ({count}), minimum {_MIN_DOCS_REQUIRED} required"
            ),
        )
    if count < _MIN_DOCS_RECOMMENDED:
        return PreflightCheck(
            name="documents",
            status="warn",
            message=(
                f"Low document count ({count}), {_MIN_DOCS_RECOMMENDED}+ recommended"
            ),
        )
    return PreflightCheck(
        name="documents",
        status="pass",
        message=f"{count} documents found",
    )


def _check_dependencies() -> PreflightCheck:
    """Check if fine-tuning ML dependencies are installed."""
    try:
        from synthorg.memory.embedding.fine_tune import (  # noqa: PLC0415
            _import_sentence_transformers,
            _import_torch,
        )

        _import_torch()
        _import_sentence_transformers()
    except (ImportError, FineTuneDependencyError) as exc:
        return PreflightCheck(
            name="dependencies",
            status="fail",
            message="Missing ML dependencies",
            detail=str(exc),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        return PreflightCheck(
            name="dependencies",
            status="fail",
            message=f"Dependency check failed: {type(exc).__name__}",
            detail=str(exc),
        )
    return PreflightCheck(
        name="dependencies",
        status="pass",
        message="ML dependencies installed",
    )


def _check_gpu() -> PreflightCheck:
    """Best-effort GPU availability check."""
    try:
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            vram_gb = props.total_memory / (1024**3)
            return PreflightCheck(
                name="gpu",
                status="pass",
                message=f"GPU available: {props.name}",
                detail=f"VRAM: {vram_gb:.1f} GB",
            )
        return PreflightCheck(
            name="gpu",
            status="warn",
            message="No GPU detected -- training will be slow",
            detail="CPU-only mode",
        )
    except ImportError:
        return PreflightCheck(
            name="gpu",
            status="warn",
            message="Cannot detect GPU (torch not installed)",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        return PreflightCheck(
            name="gpu",
            status="warn",
            message=f"GPU detection error: {type(exc).__name__}",
            detail=str(exc),
        )


def _recommend_batch_size() -> int | None:
    """Recommend batch size based on available VRAM."""
    try:
        import torch  # noqa: PLC0415

        if not torch.cuda.is_available():
            return _DEFAULT_BATCH_SIZE
        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / (1024**3)
        for threshold_gb, batch_size in _BATCH_SIZE_BY_VRAM_GB:
            if vram_gb >= threshold_gb:
                return batch_size
        return _DEFAULT_BATCH_SIZE  # noqa: TRY300
    except MemoryError, RecursionError:
        raise
    except ImportError:
        # torch is optional -- absence is expected on CPU-only installs.
        return None
    except Exception as exc:
        # SEC-1: drop ``exc_info=True``.  The full traceback bypasses
        # ``safe_error_description`` and can leak environment paths /
        # backend metadata; the redacted form is sufficient for triage.
        logger.warning(
            MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def _check_disk_space(source_dir: str) -> PreflightCheck:
    """Check available disk space for fine-tuning output."""
    import shutil  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    try:
        path = Path(source_dir) if Path(source_dir).exists() else Path()
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        if free_gb < 1:
            return PreflightCheck(
                name="disk_space",
                status="fail",
                message="Insufficient disk space",
                detail=f"{free_gb:.1f} GB free",
            )
        if free_gb < 5:  # noqa: PLR2004
            return PreflightCheck(
                name="disk_space",
                status="warn",
                message="Low disk space",
                detail=f"{free_gb:.1f} GB free, 5+ GB recommended",
            )
        return PreflightCheck(
            name="disk_space",
            status="pass",
            message=f"{free_gb:.1f} GB available",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        return PreflightCheck(
            name="disk_space",
            status="warn",
            message=f"Could not check disk space: {type(exc).__name__}",
        )
