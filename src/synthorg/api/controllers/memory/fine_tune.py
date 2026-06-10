# module-kind: controller
"""Memory fine-tune pipeline control endpoints (CEO / SYSTEM only)."""

import asyncio

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg.api.controllers.memory._preflight import (
    _PREFLIGHT_HARD_TIMEOUT_MARGIN_S,
    _recommend_batch_size,
    _resolve_fine_tune_thresholds,
    _run_preflight_checks,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_roles
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import (
    FeatureNotImplementedError,
    NotFoundError,
    ServiceUnavailableError,
)
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    FineTuneStatus,
    PreflightResult,
)
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
    MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED,
    MEMORY_FINE_TUNE_PREFLIGHT_TIMED_OUT,
    MEMORY_FINE_TUNE_REQUESTED,
)
from synthorg.persistence.state import persistence_backend_label
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


class MemoryFineTuneController(Controller):
    """Fine-tune pipeline control: start, resume, status, cancel, preflight."""

    path = "/admin/memory"
    tags = ("admin", "memory")
    guards = [require_roles(HumanRole.CEO, HumanRole.SYSTEM)]  # noqa: RUF012

    @post(
        "/fine-tune",
        guards=[
            per_op_rate_limit_from_policy("memory.fine_tune", key="user"),
        ],
        opt=per_op_concurrency_from_policy(
            "memory.fine_tune",
            key="user",
        ),
    )
    async def start_fine_tune(
        self,
        state: State,
        data: FineTuneRequest,
    ) -> ApiResponse[FineTuneStatus]:
        """Trigger a fine-tuning pipeline run.

        Returns:
            ``ApiResponse[FineTuneStatus]`` instance.

        Raises:
            FeatureNotImplementedError: Raised on the corresponding failure path.
            FineTuneRunActiveError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        logger.info(
            MEMORY_FINE_TUNE_REQUESTED,
            source_dir=data.source_dir,
            base_model=data.base_model,
        )
        orchestrator = app_state.slice(MemoryStateSlice).fine_tune_orchestrator
        if orchestrator is None:
            msg = "Fine-tuning is not available"
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                operation="start",
                reason="orchestrator_not_configured",
                backend=persistence_backend_label(app_state),
            )
            raise FeatureNotImplementedError(msg)
        # ``orchestrator.start`` raises ``FineTuneRunActiveError`` directly when
        # a run is already active; it propagates to the 409 handler unchanged.
        run = await orchestrator.start(data)
        return ApiResponse(
            data=FineTuneStatus(
                run_id=str(run.id),
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
        opt=per_op_concurrency_from_policy(
            "memory.fine_tune",
            key="user",
        ),
    )
    async def resume_fine_tune(
        self,
        state: State,
        run_id: PathId,
    ) -> ApiResponse[FineTuneStatus]:
        """Resume a failed or cancelled fine-tune pipeline run.

        Args:
            state: Application state.
            run_id: Fine-tune run identifier (1-128 chars, enforced at
                the path-parameter boundary by ``PathId``).

        Raises:
            FeatureNotImplementedError: Orchestrator not configured
                (HTTP 501).
            FineTuneRunActiveError: Another run is already active
                (HTTP 409).
            NotFoundError: Run does not exist or is not resumable
                (HTTP 404).

        Returns:
            ``ApiResponse[FineTuneStatus]`` instance.
        """
        app_state: AppState = state.app_state
        orchestrator = app_state.slice(MemoryStateSlice).fine_tune_orchestrator
        if orchestrator is None:
            msg = "Fine-tuning is not available"
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                operation="resume",
                run_id=run_id,
                reason="orchestrator_not_configured",
                backend=persistence_backend_label(app_state),
            )
            raise FeatureNotImplementedError(msg)
        # ``orchestrator.resume`` raises ``FineTuneRunActiveError`` (another run
        # active) directly -- it propagates to the 409 handler unchanged. Only
        # the ``ValueError`` (not found / not resumable) needs translating.
        try:
            run = await orchestrator.resume(run_id)
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
                run_id=str(run.id),
                stage=run.stage,
                progress=run.progress,
            ),
        )

    @get("/fine-tune/status")
    async def get_fine_tune_status(
        self,
        state: State,
    ) -> ApiResponse[FineTuneStatus]:
        """Get the current fine-tuning pipeline status.

        Returns:
            ``ApiResponse[FineTuneStatus]`` instance.
        """
        app_state: AppState = state.app_state
        orchestrator = app_state.slice(MemoryStateSlice).fine_tune_orchestrator
        if orchestrator is None:
            return ApiResponse(
                data=FineTuneStatus(stage=FineTuneStage.IDLE),
            )
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
        """Cancel the active pipeline run.

        Returns:
            ``ApiResponse[FineTuneStatus]`` instance.

        Raises:
            FeatureNotImplementedError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        orchestrator = app_state.slice(MemoryStateSlice).fine_tune_orchestrator
        if orchestrator is None:
            msg = "Fine-tuning is not available"
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                operation="cancel",
                reason="orchestrator_not_configured",
                backend=persistence_backend_label(app_state),
            )
            raise FeatureNotImplementedError(msg)
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
        state: State,
        data: FineTuneRequest,
    ) -> ApiResponse[PreflightResult]:
        """Run pre-flight validation checks.

        Returns:
            ``ApiResponse[PreflightResult]`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        settings_service = app_state.slice(SettingsStateSlice).settings_service
        thresholds = await _resolve_fine_tune_thresholds(settings_service)
        # The walk's in-thread monotonic deadline only starts counting
        # once the ``to_thread`` job is scheduled; a saturated default
        # executor could otherwise leave this request awaiting
        # indefinitely. The outer ``asyncio.timeout`` is a hard,
        # cancellation-aware ceiling so a stuck pool surfaces as a
        # clean 503 the operator can retry rather than a hung request.
        hard_ceiling = (
            thresholds.preflight_walk_timeout_s + _PREFLIGHT_HARD_TIMEOUT_MARGIN_S
        )
        try:
            async with (
                asyncio.timeout(hard_ceiling),
                asyncio.TaskGroup() as tg,
            ):
                checks_task = tg.create_task(
                    asyncio.to_thread(
                        _run_preflight_checks,
                        data,
                        min_required=thresholds.min_docs_required,
                        min_recommended=thresholds.min_docs_recommended,
                        max_depth=thresholds.preflight_max_depth,
                        walk_timeout_s=thresholds.preflight_walk_timeout_s,
                    ),
                )
                batch_task = tg.create_task(
                    asyncio.to_thread(
                        _recommend_batch_size,
                        default_batch_size=thresholds.default_batch_size,
                        vram_table=(
                            app_state.memory_bridge_config.fine_tune_vram_batch_table
                        ),
                    ),
                )
        except TimeoutError as exc:
            logger.warning(
                MEMORY_FINE_TUNE_PREFLIGHT_TIMED_OUT,
                hard_ceiling_s=hard_ceiling,
                walk_timeout_s=thresholds.preflight_walk_timeout_s,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Preflight validation timed out"
            raise ServiceUnavailableError(msg) from exc
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
