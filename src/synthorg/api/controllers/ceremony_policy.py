"""Ceremony policy controller -- query and resolve ceremony policies."""

import asyncio
import json
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.dto import ApiResponse
from synthorg.api.errors import NotFoundError, ServiceUnavailableError
from synthorg.api.guards import require_read_access
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
    resolve_ceremony_policy,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_CEREMONY_POLICY_ACTIVE_QUERIED,
    API_CEREMONY_POLICY_QUERIED,
    API_CEREMONY_POLICY_RESOLVED,
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
    API_SERVICE_UNAVAILABLE,
)

logger = get_logger(__name__)


# ── Response models ──────────────────────────────────────────


class PolicyFieldOrigin(StrEnum):
    """Origin level for a resolved ceremony policy field."""

    PROJECT = "project"
    DEPARTMENT = "department"
    DEFAULT = "default"


class ResolvedPolicyField(BaseModel):
    """A single resolved field with its origin level.

    Attributes:
        value: The resolved value (serialized as JSON-compatible).
        source: Which level provided this value.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    value: str | dict[str, Any] | bool | float = Field(
        description="Resolved field value",
    )
    source: PolicyFieldOrigin = Field(
        description="Level that provided this value",
    )


class ResolvedCeremonyPolicyResponse(BaseModel):
    """Fully resolved ceremony policy with per-field origin tracking.

    Attributes:
        strategy: Resolved scheduling strategy with origin.
        strategy_config: Resolved strategy-specific config with origin.
        velocity_calculator: Resolved velocity calculator with origin.
        auto_transition: Resolved auto-transition flag with origin.
        transition_threshold: Resolved transition threshold with origin.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    strategy: ResolvedPolicyField = Field(
        description="Ceremony scheduling strategy",
    )
    strategy_config: ResolvedPolicyField = Field(
        description="Strategy-specific configuration",
    )
    velocity_calculator: ResolvedPolicyField = Field(
        description="Velocity calculator type",
    )
    auto_transition: ResolvedPolicyField = Field(
        description="Auto-transition enabled flag",
    )
    transition_threshold: ResolvedPolicyField = Field(
        description="Auto-transition threshold fraction",
    )


class ActiveCeremonyStrategyResponse(BaseModel):
    """Currently active (locked) ceremony strategy for the running sprint.

    Attributes:
        strategy: Active strategy type, or None if no sprint active.
        sprint_id: ID of the active sprint, or None.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    strategy: CeremonyStrategyType | None = Field(
        default=None,
        description="Active sprint strategy, null if no sprint running",
    )
    sprint_id: NotBlankStr | None = Field(
        default=None,
        description="Active sprint ID, null if no sprint running",
    )

    @model_validator(mode="after")
    def _validate_strategy_sprint_consistency(self) -> Self:
        """Ensure strategy and sprint_id are both set or both None."""
        if (self.strategy is None) != (self.sprint_id is None):
            msg = "strategy and sprint_id must both be set or both be None"
            raise ValueError(msg)
        return self


# ── Helpers ──────────────────────────────────────────────────


def _parse_strategy(raw: str | None) -> CeremonyStrategyType | None:
    """Parse a ceremony strategy from its raw setting value."""
    if not raw:
        return None
    try:
        return CeremonyStrategyType(raw)
    except ValueError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="ceremony_policy.build",
            error=f"Invalid ceremony_strategy: {raw!r}",
        )
        raise


def _parse_strategy_config(raw: str | None) -> dict[str, Any] | None:
    """Parse strategy config JSON from its raw setting value."""
    if not raw or raw == "{}":
        return None
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="ceremony_policy.build",
            error="Malformed ceremony_strategy_config JSON",
        )
        raise


def _parse_velocity_calculator(raw: str | None) -> VelocityCalcType | None:
    """Parse a velocity calculator type from its raw setting value."""
    if not raw:
        return None
    try:
        return VelocityCalcType(raw)
    except ValueError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="ceremony_policy.build",
            error=f"Invalid ceremony_velocity_calculator: {raw!r}",
        )
        raise


def _parse_auto_transition(raw: str | None) -> bool | None:
    """Parse auto-transition boolean from its raw setting value.

    Raises:
        ValueError: If the value is not ``"true"`` or ``"false"``
            (case-insensitive).
    """
    if raw is None or raw == "":
        return None
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    logger.warning(
        API_REQUEST_ERROR,
        endpoint="ceremony_policy.build",
        error=f"Invalid ceremony_auto_transition: {raw!r}",
    )
    msg = f"Invalid ceremony_auto_transition: {raw!r}"
    raise ValueError(msg)


def _parse_transition_threshold(raw: str | None) -> float | None:
    """Parse transition threshold from its raw setting value."""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="ceremony_policy.build",
            error=f"Invalid ceremony_transition_threshold: {raw!r}",
        )
        raise


def _build_project_policy(
    settings_data: Mapping[str, str],
) -> CeremonyPolicyConfig:
    """Construct a CeremonyPolicyConfig from settings key-value pairs.

    Args:
        settings_data: Mapping of setting keys to their resolved
            string values (as returned by the settings service).

    Returns:
        A CeremonyPolicyConfig populated from the settings.

    Raises:
        ValueError: If a setting value cannot be parsed (e.g. invalid
            enum member, malformed JSON, non-numeric threshold).
    """
    return CeremonyPolicyConfig(
        strategy=_parse_strategy(
            settings_data.get("ceremony_strategy"),
        ),
        strategy_config=_parse_strategy_config(
            settings_data.get("ceremony_strategy_config"),
        ),
        velocity_calculator=_parse_velocity_calculator(
            settings_data.get("ceremony_velocity_calculator"),
        ),
        auto_transition=_parse_auto_transition(
            settings_data.get("ceremony_auto_transition"),
        ),
        transition_threshold=_parse_transition_threshold(
            settings_data.get("ceremony_transition_threshold"),
        ),
    )


def _determine_field_origin(
    field_name: str,
    project: CeremonyPolicyConfig,
    department: CeremonyPolicyConfig | None,
) -> PolicyFieldOrigin:
    """Determine which level provided a resolved field value.

    Checks from most specific (department) to least (project),
    falling back to default if neither provides the field.

    Args:
        field_name: Attribute name on CeremonyPolicyConfig.
        project: Project-level policy.
        department: Department-level override, or None.

    Returns:
        The origin level for this field.
    """
    if department is not None and getattr(department, field_name) is not None:
        return PolicyFieldOrigin.DEPARTMENT
    if getattr(project, field_name) is not None:
        return PolicyFieldOrigin.PROJECT
    return PolicyFieldOrigin.DEFAULT


def _build_resolved_response(
    project: CeremonyPolicyConfig,
    department: CeremonyPolicyConfig | None,
) -> ResolvedCeremonyPolicyResponse:
    """Build a resolved response with per-field origins.

    Args:
        project: Project-level policy.
        department: Department-level override, or None.

    Returns:
        Fully resolved policy with origin tracking.
    """
    resolved = resolve_ceremony_policy(
        project=project,
        department=department,
    )
    fields = (
        "strategy",
        "strategy_config",
        "velocity_calculator",
        "auto_transition",
        "transition_threshold",
    )
    result: dict[str, ResolvedPolicyField] = {}
    for name in fields:
        value = getattr(resolved, name)
        # Serialize StrEnum members to their string value for JSON
        if isinstance(value, StrEnum):
            value = value.value
        origin = _determine_field_origin(name, project, department)
        result[name] = ResolvedPolicyField(value=value, source=origin)
    return ResolvedCeremonyPolicyResponse(**result)


async def _fetch_project_policy(app_state: AppState) -> CeremonyPolicyConfig:
    """Fetch project-level ceremony policy from settings.

    Fetches all five ceremony settings concurrently via a TaskGroup.
    Individual setting-fetch failures are caught and surfaced as a
    single ``ServiceUnavailableError``.

    Args:
        app_state: Application state with settings service.

    Returns:
        CeremonyPolicyConfig from settings values.

    Raises:
        ServiceUnavailableError: If the settings service is not
            available or one or more settings cannot be fetched.
    """
    if not app_state.has_settings_service:
        msg = "Settings service not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
        raise ServiceUnavailableError(msg)

    settings = app_state.settings_service
    keys = (
        "ceremony_strategy",
        "ceremony_strategy_config",
        "ceremony_velocity_calculator",
        "ceremony_auto_transition",
        "ceremony_transition_threshold",
    )
    data: dict[str, str] = {}

    async def _fetch(key: str) -> None:
        entry = await settings.get("coordination", key)
        data[key] = entry.value

    try:
        async with asyncio.TaskGroup() as tg:
            for key in keys:
                tg.create_task(_fetch(key))
    except* Exception as eg:
        for exc in eg.exceptions:
            if isinstance(exc, (MemoryError, RecursionError)):
                raise exc from None
        first = eg.exceptions[0]
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="settings",
            error=str(first),
        )
        msg = "Failed to fetch ceremony settings"
        raise ServiceUnavailableError(msg) from None

    try:
        return _build_project_policy(data)
    except ValueError as exc:
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="settings",
            error=f"Malformed ceremony policy settings: {exc}",
        )
        msg = "Malformed ceremony policy settings"
        raise ServiceUnavailableError(msg) from exc


class _SettingsNotFound:
    """Sentinel indicating no settings-based override was found."""


_SETTINGS_NOT_FOUND = _SettingsNotFound()


async def _lookup_dept_override_from_settings(
    app_state: AppState,
    department_name: NotBlankStr,
) -> CeremonyPolicyConfig | None | _SettingsNotFound:
    """Try to find a department override in the settings service.

    Returns:
        ``CeremonyPolicyConfig`` if found, ``None`` if explicitly
        inheriting, or the sentinel ``_SETTINGS_NOT_FOUND`` if the
        settings service is unavailable or the department has no
        settings-based override.
    """
    if not app_state.has_settings_service:
        return _SETTINGS_NOT_FOUND
    try:
        entry = await app_state.settings_service.get(
            "coordination",
            "dept_ceremony_policies",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="ceremony_policy.fetch_dept",
            department=department_name,
            note="settings_lookup_failed_falling_back_to_config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _SETTINGS_NOT_FOUND

    try:
        policies = json.loads(entry.value)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="ceremony_policy.fetch_dept",
            error=f"Corrupt dept_ceremony_policies value: {exc}",
        )
        msg = "Malformed ceremony policies data"
        raise ServiceUnavailableError(msg) from exc

    if not isinstance(policies, dict) or department_name not in policies:
        return _SETTINGS_NOT_FOUND
    val = policies[department_name]
    if val is None:
        return None
    if isinstance(val, dict):
        return CeremonyPolicyConfig.model_validate(val)
    return _SETTINGS_NOT_FOUND


async def _fetch_department_policy(
    app_state: AppState,
    department_name: NotBlankStr,
) -> CeremonyPolicyConfig | None:
    """Fetch department-level ceremony policy override.

    Checks settings-based overrides first (written by the department
    ceremony policy PUT endpoint), then falls back to the config
    resolver's ``ceremony_policy`` field.

    Args:
        app_state: Application state.
        department_name: Department name to look up.

    Returns:
        CeremonyPolicyConfig if department has an override, else None.

    Raises:
        NotFoundError: If the department does not exist.
        ServiceUnavailableError: If required services are unavailable.
    """
    result = await _lookup_dept_override_from_settings(
        app_state,
        department_name,
    )
    if not isinstance(result, _SettingsNotFound):
        return result

    # Fall back to config-based ceremony_policy
    if not app_state.has_config_resolver:
        msg = "Config resolver not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="config_resolver")
        raise ServiceUnavailableError(msg)

    departments = await app_state.config_resolver.get_departments()
    for dept in departments:
        if dept.name == department_name:
            if dept.ceremony_policy is None:
                return None
            return CeremonyPolicyConfig.model_validate(
                dept.ceremony_policy,
            )
    msg = f"Department {department_name!r} not found"
    logger.warning(
        API_RESOURCE_NOT_FOUND,
        resource="department",
        name=department_name,
    )
    raise NotFoundError(msg)


# ── Controller ───────────────────────────────────────────────


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

        Args:
            state: Application state.

        Returns:
            CeremonyPolicyConfig as a JSON dict.
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
            Parameter(max_length=128),
        ] = None,
    ) -> ApiResponse[ResolvedCeremonyPolicyResponse]:
        """Return the fully resolved ceremony policy with field origins.

        When ``department`` is provided, the resolution includes the
        department-level override (if any).  The response indicates
        which level provided each field value.

        Args:
            state: Application state.
            department: Optional department name for department-level
                resolution.

        Returns:
            Resolved policy with per-field origin tracking.
        """
        app_state: AppState = state.app_state
        project = await _fetch_project_policy(app_state)
        dept_policy: CeremonyPolicyConfig | None = None
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

        If no sprint is active or the ceremony scheduler is not
        configured, returns null fields.

        Args:
            state: Application state.

        Returns:
            Active strategy and sprint ID (both nullable).
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
