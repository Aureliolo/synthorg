"""MCP-facing ceremony policy service.

Wraps the controller-level helpers from
``api/controllers/ceremony_policy.py`` so the MCP tools
``synthorg_ceremony_policy_get`` / ``_get_resolved`` /
``_get_active_strategy`` can share a single service boundary.

The heavy lifting (settings parsing, 3-level resolution, origin
tracking, scheduler lookup) stays in the engine module + controller
helpers; this service is the narrow glue the handler layer needs
plus a stable frozen ``ActiveCeremonyStrategy`` response model.
"""

from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.coordination.ceremony_policy.policy_resolver import (
    ResolvedCeremonyPolicyResponse,
    _build_resolved_response,
    _fetch_department_policy,
    _fetch_project_policy,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,  # noqa: TC001 -- runtime annotation
    CeremonyStrategyType,  # noqa: TC001 -- Pydantic runtime
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_CEREMONY_POLICY_ACTIVE_QUERIED,
    API_CEREMONY_POLICY_QUERIED,
    API_CEREMONY_POLICY_RESOLVED,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


class ActiveCeremonyStrategy(BaseModel):
    """Currently locked ceremony strategy for the active sprint.

    Mirrors :class:`ActiveCeremonyStrategyResponse` in the controller
    layer but lives in the service module so MCP handlers can import
    from a stable service API instead of reaching into a controller.

    Attributes:
        strategy: Active strategy enum, or ``None`` if no sprint is
            running / the scheduler is not configured.
        sprint_id: Active sprint id, or ``None``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: CeremonyStrategyType | None = Field(
        default=None,
        description="Active sprint strategy",
    )
    sprint_id: NotBlankStr | None = Field(
        default=None,
        description="Active sprint id",
    )

    @model_validator(mode="after")
    def _validate_coupling(self) -> Self:
        """Reject logically inconsistent combinations.

        ``strategy`` and ``sprint_id`` are always either both known
        (a sprint is active and locked onto a strategy) or both absent
        (no sprint is running). Constructing a snapshot with one set
        and the other ``None`` would hand callers an ambiguous state
        the scheduler never produces.
        """
        if (self.strategy is None) != (self.sprint_id is None):
            msg = (
                "strategy and sprint_id must be both set or both None "
                "(cannot have one without the other)"
            )
            raise ValueError(msg)
        return self


class CeremonyPolicyService:
    """Read-side facade over the ceremony policy helpers.

    Constructor:
        app_state: The application state. The service re-uses the
            existing controller-level helpers which read
            ``settings_service`` + ``config_resolver`` +
            ``ceremony_scheduler`` off the state. Injecting the full
            state keeps the service as thin as possible without
            forcing a new protocol.
    """

    __slots__ = ("_app_state",)

    def __init__(
        self,
        *,
        app_state: AppState,
    ) -> None:
        """Initialise with the application state dependency."""
        self._app_state = app_state

    async def get_policy(self) -> CeremonyPolicyConfig:
        """Return the project-level ceremony policy from settings.

        Raises:
            ServiceUnavailableError: If the settings service is not
                wired or a setting value is malformed.
        """
        policy = await _fetch_project_policy(self._app_state)
        logger.debug(
            API_CEREMONY_POLICY_QUERIED,
            strategy=policy.strategy.value if policy.strategy else None,
            surface="mcp.ceremony_policy.get",
        )
        return policy

    async def get_resolved_policy(
        self,
        *,
        department: NotBlankStr | None = None,
    ) -> ResolvedCeremonyPolicyResponse:
        """Return the fully resolved policy with per-field origins.

        Args:
            department: Optional department name; when provided, the
                department-level override participates in resolution.

        Raises:
            ServiceUnavailableError: If services are unavailable.
            NotFoundError: If *department* is given but does not
                exist.
        """
        project = await _fetch_project_policy(self._app_state)
        dept_policy: CeremonyPolicyConfig | None = None
        if department is not None:
            dept_policy = await _fetch_department_policy(
                self._app_state,
                department,
            )
        response = _build_resolved_response(project, dept_policy)
        logger.debug(
            API_CEREMONY_POLICY_RESOLVED,
            department=department,
            strategy=response.strategy.value,
            surface="mcp.ceremony_policy.get_resolved",
        )
        return response

    async def get_active_strategy(self) -> ActiveCeremonyStrategy:
        """Return the active sprint strategy.

        When no ceremony scheduler is configured or no sprint is
        running, both fields are ``None``.
        """
        scheduler = self._app_state.slice(EngineStateSlice).ceremony_scheduler
        if scheduler is None or not scheduler.running:
            logger.debug(
                API_CEREMONY_POLICY_ACTIVE_QUERIED,
                strategy=None,
                sprint_id=None,
                surface="mcp.ceremony_policy.get_active_strategy",
            )
            return ActiveCeremonyStrategy()
        strategy, sprint = await scheduler.get_active_info()
        if strategy is None or sprint is None:
            logger.debug(
                API_CEREMONY_POLICY_ACTIVE_QUERIED,
                strategy=None,
                sprint_id=None,
                surface="mcp.ceremony_policy.get_active_strategy",
            )
            return ActiveCeremonyStrategy()
        response = ActiveCeremonyStrategy(
            strategy=strategy.strategy_type,
            sprint_id=NotBlankStr(str(sprint.id)),
        )
        logger.debug(
            API_CEREMONY_POLICY_ACTIVE_QUERIED,
            strategy=response.strategy.value if response.strategy else None,
            sprint_id=response.sprint_id,
            surface="mcp.ceremony_policy.get_active_strategy",
        )
        return response


__all__ = ["ActiveCeremonyStrategy", "CeremonyPolicyService"]
