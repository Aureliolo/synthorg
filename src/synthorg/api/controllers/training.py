"""Training mode API controller.

Provides endpoints for creating, executing, previewing, and
querying training plans for agent onboarding.
"""

from datetime import UTC, datetime

from litestar import Controller, get, post, put
from litestar.status_codes import HTTP_200_OK

from synthorg.api.dto import ApiResponse
from synthorg.api.dto_training import (
    CreateTrainingPlanRequest,
    TrainingPlanResponse,
    TrainingResultResponse,
    UpdateTrainingOverridesRequest,
)
from synthorg.api.guards import require_org_mutation, require_read_access
from synthorg.api.path_params import PathName  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.agent import AgentIdentity  # noqa: TC001
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import (
    ContentType,
    TrainingPlan,
    TrainingPlanStatus,
    TrainingResult,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.observability.events.training import (
    HR_TRAINING_PLAN_CREATED,
    HR_TRAINING_PLAN_EXECUTED,
    HR_TRAINING_PLAN_FAILED,
)

logger = get_logger(__name__)


async def _resolve_agent(
    app_state: AppState,
    agent_name: PathName,
) -> AgentIdentity:
    """Resolve agent name to identity, raising NotFoundError."""
    identity = await app_state.agent_registry.get_by_name(agent_name)
    if identity is None:
        logger.warning(
            API_RESOURCE_NOT_FOUND,
            resource="agent",
            name=str(agent_name),
        )
        msg = "Agent not found"
        raise NotFoundError(msg)
    return identity


def _parse_content_types(
    raw: tuple[ContentType, ...] | None,
) -> frozenset[ContentType]:
    """Convert validated content types, defaulting to all when empty."""
    if not raw:
        return frozenset(ContentType)
    return frozenset(raw)


def _parse_custom_caps(
    raw: dict[ContentType, int] | None,
    *,
    defaults: tuple[tuple[ContentType, int], ...],
) -> tuple[tuple[ContentType, int], ...] | None:
    """Merge validated custom caps with defaults for any unspecified types.

    Args:
        raw: Validated caps dict from the request DTO.  Keys are
            ``ContentType`` enums (parsed by Pydantic), values are
            positive integers (validated by DTO ``PositiveInt``).
            May be ``None`` when no override is supplied.
        defaults: The fallback caps for any content type not present
            in ``raw`` -- typically the plan's default volume caps.

    Returns:
        A merged caps tuple covering every known content type, or
        ``None`` when ``raw`` was not provided.
    """
    if not raw:
        return None
    merged: dict[ContentType, int] = dict(defaults)
    merged.update(raw)
    return tuple(merged.items())


def _coerce_override_sources(
    raw: tuple[str, ...],
) -> tuple[NotBlankStr, ...]:
    """Validate override_sources as non-blank identifier strings."""
    coerced: list[NotBlankStr] = []
    for raw_id in raw:
        stripped = raw_id.strip()
        if not stripped:
            msg = "override_sources entries must be non-blank"
            logger.warning(
                API_REQUEST_ERROR,
                error_type=ValidationError.__name__,
                error=msg,
            )
            raise ValidationError(msg)
        coerced.append(NotBlankStr(stripped))
    return tuple(coerced)


def _result_to_response(result: TrainingResult) -> TrainingResultResponse:
    """Convert a domain ``TrainingResult`` to the API response DTO.

    Maps ``ContentType`` enums to string values in the count tuples
    and ``TrainingApprovalHandle`` instances to serializable tuples.
    """
    return TrainingResultResponse(
        id=result.id,
        plan_id=result.plan_id,
        new_agent_id=result.new_agent_id,
        source_agents_used=result.source_agents_used,
        items_extracted=tuple(
            (NotBlankStr(ct.value), n) for ct, n in result.items_extracted
        ),
        items_after_curation=tuple(
            (NotBlankStr(ct.value), n) for ct, n in result.items_after_curation
        ),
        items_after_guards=tuple(
            (NotBlankStr(ct.value), n) for ct, n in result.items_after_guards
        ),
        items_stored=tuple((NotBlankStr(ct.value), n) for ct, n in result.items_stored),
        approval_item_id=result.approval_item_id,
        pending_approvals=tuple(
            (
                h.approval_item_id,
                NotBlankStr(h.content_type.value),
                h.item_count,
            )
            for h in result.pending_approvals
        ),
        review_pending=result.review_pending,
        errors=result.errors,
        started_at=result.started_at,
        completed_at=result.completed_at,
    )


class TrainingController(Controller):
    """Training mode API endpoints."""

    path = "/agents/{agent_name:str}/training"

    @post(
        "/plan",
        guards=[require_org_mutation()],
        status_code=HTTP_200_OK,
    )
    async def create_plan(
        self,
        app_state: AppState,
        agent_name: PathName,
        data: CreateTrainingPlanRequest,
    ) -> ApiResponse[TrainingPlanResponse]:
        """Create a training plan for the specified agent.

        Args:
            app_state: Litestar application state.
            agent_name: Agent identifier from the URL path.
            data: Request body (content types, caps, overrides, flags).

        Returns:
            ``ApiResponse`` wrapping the created plan response DTO.

        Raises:
            ValidationError: If the request contains invalid
                content types, caps, or override sources.
            NotFoundError: If the agent name does not resolve.
        """
        identity = await _resolve_agent(app_state, agent_name)
        enabled_types = _parse_content_types(data.content_types)
        override_sources = _coerce_override_sources(data.override_sources)

        plan_kwargs: dict[str, object] = {
            "new_agent_id": str(identity.id),
            "new_agent_role": str(identity.role),
            "new_agent_level": identity.level,
            "new_agent_department": str(identity.department),
            "override_sources": override_sources,
            "enabled_content_types": enabled_types,
            "skip_training": data.skip_training,
            "require_review": data.require_review,
            "created_at": datetime.now(UTC),
        }

        # Use TrainingPlan's default caps for the merge baseline so
        # omitted content types remain capped at the documented default
        # instead of becoming unlimited.
        default_caps = TrainingPlan.model_fields["volume_caps"].default
        caps = _parse_custom_caps(data.custom_caps, defaults=default_caps)
        if caps is not None:
            plan_kwargs["volume_caps"] = caps

        plan = TrainingPlan(**plan_kwargs)  # type: ignore[arg-type]
        await app_state.persistence.training_plans.save(plan)

        logger.info(
            HR_TRAINING_PLAN_CREATED,
            plan_id=str(plan.id),
            agent_name=str(agent_name),
        )

        return ApiResponse(data=_plan_to_response(plan))

    @post(
        "/execute",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("training.execute", key="user"),
        ],
        status_code=HTTP_200_OK,
    )
    async def execute_plan(
        self,
        app_state: AppState,
        agent_name: PathName,
    ) -> ApiResponse[TrainingResultResponse]:
        """Execute the latest pending training plan.

        Args:
            app_state: Litestar application state.
            agent_name: Agent identifier from the URL path.

        Raises:
            NotFoundError: If no pending plan exists for the agent.
            ServiceUnavailableError: If the training service is not
                yet wired into ``AppState`` for this deployment.
        """
        identity = await _resolve_agent(app_state, agent_name)
        agent_id = str(identity.id)

        plan = await app_state.persistence.training_plans.latest_pending(
            NotBlankStr(agent_id),
        )
        if plan is None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="training_plan",
                agent_id=agent_id,
            )
            msg = "No pending training plan found"
            raise NotFoundError(msg)

        # Raises 503 ServiceUnavailableError when not wired.
        service = app_state.training_service

        try:
            result = await service.execute(plan)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Transition plan to FAILED on pipeline error.
            failed_plan = plan.model_copy(
                update={
                    "status": TrainingPlanStatus.FAILED,
                    "executed_at": datetime.now(UTC),
                }
            )
            try:
                await app_state.persistence.training_plans.save(
                    failed_plan,
                )
            except Exception as save_exc:
                logger.warning(
                    HR_TRAINING_PLAN_FAILED,
                    plan_id=str(plan.id),
                    error="Failed to persist FAILED status",
                    error_type=type(save_exc).__name__,
                    persistence_error=safe_error_description(save_exc),
                    exc_info=True,
                )
            logger.warning(
                HR_TRAINING_PLAN_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                exc_info=True,
            )
            raise

        # Transition plan to EXECUTED and persist the result.
        executed_plan = plan.model_copy(
            update={
                "status": TrainingPlanStatus.EXECUTED,
                "executed_at": result.completed_at,
            }
        )
        await app_state.persistence.training_plans.save(executed_plan)
        await app_state.persistence.training_results.save(result)

        logger.info(
            HR_TRAINING_PLAN_EXECUTED,
            plan_id=str(plan.id),
            agent_id=agent_id,
        )

        return ApiResponse(data=_result_to_response(result))

    @get(
        "/result",
        guards=[require_read_access],
        status_code=HTTP_200_OK,
    )
    async def get_result(
        self,
        app_state: AppState,
        agent_name: PathName,
    ) -> ApiResponse[TrainingResultResponse]:
        """Get the latest training result for an agent.

        Args:
            app_state: Litestar application state.
            agent_name: Agent identifier from the URL path.

        Raises:
            NotFoundError: If no result is stored for the agent.
        """
        identity = await _resolve_agent(app_state, agent_name)
        agent_id = str(identity.id)

        result = await app_state.persistence.training_results.get_latest(
            NotBlankStr(agent_id),
        )
        if result is None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="training_result",
                agent_id=agent_id,
            )
            msg = "No training result found"
            raise NotFoundError(msg)

        return ApiResponse(data=_result_to_response(result))

    @get(
        "/plan",
        guards=[require_read_access],
        status_code=HTTP_200_OK,
    )
    async def get_latest_plan(
        self,
        app_state: AppState,
        agent_name: PathName,
    ) -> ApiResponse[TrainingPlanResponse]:
        """Get the most recently created training plan for an agent.

        Unlike ``/execute``, this returns the latest plan regardless of
        status so the dashboard can rehydrate its view after a reload
        (the "Create Plan" form should not reappear once a plan has
        been executed).

        Args:
            app_state: Litestar application state.
            agent_name: Agent identifier from the URL path.

        Raises:
            NotFoundError: If no plan has been created for the agent.
        """
        identity = await _resolve_agent(app_state, agent_name)
        agent_id = str(identity.id)

        plan = await app_state.persistence.training_plans.latest_by_agent(
            NotBlankStr(agent_id),
        )
        if plan is None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="training_plan",
                agent_id=agent_id,
            )
            msg = "No training plan found"
            raise NotFoundError(msg)

        return ApiResponse(data=_plan_to_response(plan))

    @post(
        "/preview",
        guards=[require_read_access],
        status_code=HTTP_200_OK,
    )
    async def preview_plan(
        self,
        app_state: AppState,
        agent_name: PathName,
    ) -> ApiResponse[TrainingResultResponse]:
        """Preview a training plan (dry run).

        Args:
            app_state: Litestar application state.
            agent_name: Agent identifier from the URL path.

        Raises:
            NotFoundError: If no pending plan exists for the agent.
            ServiceUnavailableError: If the training service is not
                yet wired into ``AppState`` for this deployment.
        """
        identity = await _resolve_agent(app_state, agent_name)
        agent_id = str(identity.id)

        plan = await app_state.persistence.training_plans.latest_pending(
            NotBlankStr(agent_id),
        )
        if plan is None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="training_plan",
                agent_id=agent_id,
            )
            msg = "No pending training plan found"
            raise NotFoundError(msg)

        # Raises 503 when not wired; preview does NOT persist.
        result = await app_state.training_service.preview(plan)
        return ApiResponse(data=_result_to_response(result))

    @put(
        "/plan/{plan_id:str}/overrides",
        guards=[require_org_mutation()],
        status_code=HTTP_200_OK,
    )
    async def update_overrides(
        self,
        app_state: AppState,
        agent_name: PathName,
        plan_id: str,
        data: UpdateTrainingOverridesRequest,
    ) -> ApiResponse[TrainingPlanResponse]:
        """Update training plan overrides.

        Args:
            app_state: Litestar application state.
            agent_name: Agent identifier from the URL path.
            plan_id: Training plan id from the URL path.
            data: Request body with optional override updates.

        Returns:
            ``ApiResponse`` wrapping the updated plan response.

        Raises:
            NotFoundError: If the plan or agent cannot be resolved.
            ValidationError: If the request contains invalid caps
                or override source ids.
        """
        identity = await _resolve_agent(app_state, agent_name)

        plan = await app_state.persistence.training_plans.get(
            NotBlankStr(plan_id),
        )
        if plan is None:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="training_plan",
                plan_id=plan_id,
            )
            msg = "Training plan not found"
            raise NotFoundError(msg)

        if str(plan.new_agent_id) != str(identity.id):
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="training_plan",
                plan_id=plan_id,
                reason="wrong_owner",
            )
            msg = "Training plan does not belong to this agent"
            raise NotFoundError(msg)

        if plan.status != TrainingPlanStatus.PENDING:
            logger.warning(
                API_REQUEST_ERROR,
                plan_id=str(plan.id),
                agent_id=str(identity.id),
                status=plan.status.value,
                error="Attempt to modify non-pending training plan",
            )
            msg = "Cannot modify plan after execution or failure"
            raise ConflictError(msg)

        updates: dict[str, object] = {}
        if data.override_sources is not None:
            updates["override_sources"] = _coerce_override_sources(
                data.override_sources,
            )
        caps = _parse_custom_caps(data.custom_caps, defaults=plan.volume_caps)
        if caps is not None:
            updates["volume_caps"] = caps
        if data.content_types is not None:
            updates["enabled_content_types"] = frozenset(data.content_types)
        if data.skip_training is not None:
            updates["skip_training"] = data.skip_training

        updated = plan.model_copy(update=updates)
        await app_state.persistence.training_plans.save(updated)

        return ApiResponse(data=_plan_to_response(updated))


def _plan_to_response(plan: TrainingPlan) -> TrainingPlanResponse:
    """Convert a TrainingPlan to a response DTO."""
    return TrainingPlanResponse(
        id=plan.id,
        new_agent_id=plan.new_agent_id,
        new_agent_role=plan.new_agent_role,
        source_selector_type=plan.source_selector_type,
        enabled_content_types=tuple(
            sorted(ct.value for ct in plan.enabled_content_types),
        ),
        curation_strategy_type=plan.curation_strategy_type,
        volume_caps=tuple((ct.value, cap) for ct, cap in plan.volume_caps),
        override_sources=plan.override_sources,
        skip_training=plan.skip_training,
        require_review=plan.require_review,
        status=plan.status,
        created_at=plan.created_at,
        executed_at=plan.executed_at,
    )
