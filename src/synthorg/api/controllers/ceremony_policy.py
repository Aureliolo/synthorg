"""Ceremony policy controller -- query and resolve ceremony policies.

The read-side helpers (settings parsing, project / department policy
fetch, resolution + per-field origin tracking) live in
:mod:`synthorg.coordination.ceremony_policy.policy_resolver` so the
MCP service layer can import them without depending on this controller
module.
"""

from typing import Annotated, Any

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import QueryParameter

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import QUERY_MAX_LENGTH
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.coordination.ceremony_policy.policy_resolver import (
    ActiveCeremonyStrategyResponse,
    ResolvedCeremonyPolicyResponse,
    _build_resolved_response,
    _fetch_department_policy,
    _fetch_project_policy,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_CEREMONY_POLICY_ACTIVE_QUERIED,
    API_CEREMONY_POLICY_QUERIED,
    API_CEREMONY_POLICY_RESOLVED,
)

logger = get_logger(__name__)


class CeremonyPolicyController(Controller):
    """Query and resolve ceremony scheduling policies."""

    path = "/ceremony-policy"
    tags = ("ceremony-policy",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def get_project_policy(
        self,
        state: State,
    ) -> ApiResponse[dict[str, Any]]:
        """Return the project-level ceremony policy from settings.

        Returns:
            ``ApiResponse[dict[str, Any]]`` instance.
        """
        app_state: AppState = state.app_state
        policy = await _fetch_project_policy(app_state)
        logger.debug(
            API_CEREMONY_POLICY_QUERIED,
            strategy=policy.strategy.value if policy.strategy else None,
        )
        return ApiResponse(data=policy.model_dump(mode="json"))

    @get("/resolved")
    async def get_resolved_policy(
        self,
        state: State,
        department: Annotated[
            NotBlankStr | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                description="Department to resolve against; omit for project policy.",
            ),
        ] = None,
    ) -> ApiResponse[ResolvedCeremonyPolicyResponse]:
        """Return the fully resolved ceremony policy with field origins.

        Returns:
            Result matching the declared return annotation.
        """
        app_state: AppState = state.app_state
        project = await _fetch_project_policy(app_state)
        dept_policy = None
        if department is not None:
            dept_policy = await _fetch_department_policy(
                app_state,
                department,
            )
        response = _build_resolved_response(project, dept_policy)
        logger.debug(
            API_CEREMONY_POLICY_RESOLVED,
            department=department,
            strategy=response.strategy.value,
        )
        return ApiResponse(data=response)

    @get("/active")
    async def get_active_strategy(
        self,
        state: State,
    ) -> ApiResponse[ActiveCeremonyStrategyResponse]:
        """Return the currently locked strategy for the active sprint.

        Returns:
            Result matching the declared return annotation.
        """
        app_state: AppState = state.app_state
        scheduler = app_state.ceremony_scheduler
        response = ActiveCeremonyStrategyResponse()

        if scheduler is not None and scheduler.running:
            strategy, sprint = await scheduler.get_active_info()
            if strategy is not None and sprint is not None:
                response = ActiveCeremonyStrategyResponse(
                    strategy=strategy.strategy_type,
                    sprint_id=NotBlankStr(str(sprint.id)),
                )

        logger.debug(
            API_CEREMONY_POLICY_ACTIVE_QUERIED,
            strategy=response.strategy.value if response.strategy else None,
            sprint_id=response.sprint_id,
        )
        return ApiResponse(data=response)
