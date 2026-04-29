"""Review pipeline endpoints at /reviews."""

from datetime import UTC, datetime
from typing import Any

from litestar import Controller, Request, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.channels import CHANNEL_REVIEWS, publish_ws_event
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.api.ws_models import WsEventType
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.review.models import (
    PipelineResult,
    ReviewStageResult,
    ReviewVerdict,
)
from synthorg.observability import get_logger
from synthorg.observability.events.review_pipeline import (
    REVIEW_STAGE_DECIDED,
    REVIEW_TASK_LOOKUP_FAILED,
)

logger = get_logger(__name__)


class StageDecisionPayload(BaseModel):
    """Human override for a single review stage."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    verdict: ReviewVerdict = Field(description="Overriding verdict")
    reason: NotBlankStr | None = Field(
        default=None,
        description="Rationale for the decision",
    )


class StageDecisionResult(BaseModel):
    """Response describing an applied stage decision."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_id: NotBlankStr
    stage_name: NotBlankStr
    stage_result: ReviewStageResult
    pipeline_result: PipelineResult


def _find_stage(
    pipeline_stages: tuple[Any, ...],
    stage_name: str,
) -> Any | None:
    """Return the stage instance matching ``stage_name`` if present."""
    for stage in pipeline_stages:
        if getattr(stage, "name", None) == stage_name:
            return stage
    return None


class ReviewController(Controller):
    """Review pipeline visibility + manual override endpoints."""

    path = "/reviews"
    tags = ("reviews",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/{task_id:str}/pipeline")
    async def get_pipeline(
        self,
        state: State,
        task_id: str,
    ) -> ApiResponse[PipelineResult]:
        """Run the configured review pipeline against a task.

        The pipeline is fetched from ``AppState`` and invoked
        synchronously so the caller immediately sees the per-stage
        breakdown. Falls back to ``NotFoundError`` if the task
        cannot be resolved and ``ServiceUnavailableError`` if no
        pipeline is configured.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        pipeline = sim_state.review_pipeline
        if pipeline is None:
            msg = "Review pipeline not configured"
            raise ServiceUnavailableError(msg)
        try:
            task = await app_state.task_engine.get_task(task_id)
        except (KeyError, ValueError) as exc:
            logger.warning(REVIEW_TASK_LOOKUP_FAILED, task_id=task_id)
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg) from exc
        if task is None:
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg)
        result = await pipeline.run(task)
        return ApiResponse(data=result)

    @post(
        "/{task_id:str}/stages/{stage_name:str}/decide",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("reviews.decide_stage", key="user"),
        ],
    )
    async def decide_stage(
        self,
        request: Request[Any, Any, Any],
        state: State,
        task_id: str,
        stage_name: str,
        data: StageDecisionPayload,
    ) -> ApiResponse[StageDecisionResult]:
        """Record a manual verdict override for a single review stage.

        The decision is recorded as a synthetic ``ReviewStageResult``
        and emitted on the ``reviews`` channel so operators can
        react in real time. Does not rewrite the existing pipeline
        history -- subsequent ``GET /reviews/{id}/pipeline`` calls
        continue to reflect the pipeline's own evaluation.

        Raises:
            NotFoundError: If the task is not known.
            ConflictError: If the stage name is unknown for the
                configured pipeline.
            ServiceUnavailableError: If no pipeline is configured.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        pipeline = sim_state.review_pipeline
        if pipeline is None:
            msg = "Review pipeline not configured"
            raise ServiceUnavailableError(msg)
        try:
            task = await app_state.task_engine.get_task(task_id)
        except (KeyError, ValueError) as exc:
            logger.warning(REVIEW_TASK_LOOKUP_FAILED, task_id=task_id)
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg) from exc
        if task is None:
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg)
        stage = _find_stage(pipeline.stages, stage_name)
        if stage is None:
            msg = f"Stage {stage_name!r} not found in pipeline"
            raise ConflictError(msg)
        decided_by = getattr(
            request.scope.get("user"),
            "id",
            "system",
        )
        decided_reason = data.reason or f"Manual decision by {decided_by}"
        stage_result = ReviewStageResult(
            stage_name=stage_name,
            verdict=data.verdict,
            reason=decided_reason,
            duration_ms=0,
            metadata={
                "decided_by": decided_by,
                "manual_override": True,
                "decided_at": datetime.now(UTC).isoformat(),
            },
        )
        pipeline_result = PipelineResult(
            task_id=task_id,
            final_verdict=data.verdict,
            stage_results=(stage_result,),
            total_duration_ms=0,
        )
        logger.info(
            REVIEW_STAGE_DECIDED,
            task_id=task_id,
            stage_name=stage_name,
            verdict=data.verdict.value,
            decided_by=decided_by,
        )
        publish_ws_event(
            request,
            WsEventType.REVIEW_STAGE_DECIDED,
            CHANNEL_REVIEWS,
            {
                "task_id": task_id,
                "stage_name": stage_name,
                "verdict": data.verdict.value,
                "decided_by": decided_by,
            },
        )
        return ApiResponse(
            data=StageDecisionResult(
                task_id=task_id,
                stage_name=stage_name,
                stage_result=stage_result,
                pipeline_result=pipeline_result,
            ),
        )
