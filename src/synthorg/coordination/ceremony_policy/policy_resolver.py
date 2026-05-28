"""Coordination-domain ceremony policy resolution.

Owns the read-side helpers that build the project-level
:class:`CeremonyPolicyConfig` from settings, fetch the optional
department-level override, and merge them into a resolved response with
per-field origin tracking. The HTTP controller and the MCP service
layer both depend on this module so neither has to depend on the other.
"""

import asyncio
import enum
import json
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from synthorg.core.domain_errors import NotFoundError, ServiceUnavailableError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
    resolve_ceremony_policy,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
    API_SERVICE_UNAVAILABLE,
)
from synthorg.settings.errors import SettingNotFoundError, SettingsError
from synthorg.settings.state import (
    SettingsStateSlice,
    config_resolver_of,
    settings_service_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.api.state import AppState

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    Wire-shape used by the controller's ``/ceremony-policy/active``
    endpoint. The MCP service layer has its own narrower
    :class:`ActiveCeremonyStrategy` model in ``service.py`` because the
    handler protocol does not consume the same response envelope.

    Attributes:
        strategy: Active strategy type, or None if no sprint active.
        sprint_id: ID of the active sprint, or None.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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


# ── Setting-value parsers ────────────────────────────────────


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
    """Build a resolved response with per-field origins."""
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
        if isinstance(value, StrEnum):
            value = value.value
        origin = _determine_field_origin(name, project, department)
        result[name] = ResolvedPolicyField(value=value, source=origin)
    return ResolvedCeremonyPolicyResponse(**result)


# ── Settings + config-resolver fetch ─────────────────────────


async def _fetch_project_policy(app_state: AppState) -> CeremonyPolicyConfig:
    """Fetch project-level ceremony policy from settings.

    Fetches all five ceremony settings concurrently via a TaskGroup.
    Individual setting-fetch failures are caught and surfaced as a
    single ``ServiceUnavailableError``.

    Raises:
        ServiceUnavailableError: If the settings service is not
            available or one or more settings cannot be fetched.
    """
    if app_state.slice(SettingsStateSlice).settings_service is None:
        msg = "Settings service not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
        raise ServiceUnavailableError(msg)

    settings = settings_service_of(app_state)
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
            error_type=type(first).__name__,
            error=safe_error_description(first),
        )
        msg = "Failed to fetch ceremony settings"
        raise ServiceUnavailableError(msg) from None

    try:
        return _build_project_policy(data)
    except ValueError as exc:
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="settings",
            note="malformed ceremony policy settings",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Malformed ceremony policy settings"
        raise ServiceUnavailableError(msg) from exc


class _SettingsLookup(enum.Enum):
    """Disjoint outcomes for a department settings-override lookup.

    The lookup function returns one of three logical states:
    a parsed :class:`CeremonyPolicyConfig`, ``None`` (the operator
    explicitly cleared the override), or :data:`_SETTINGS_NOT_FOUND`
    (no settings-based override is configured at all). Encoding the
    third state as an enum member instead of a custom sentinel class
    keeps the discriminated union obvious to mypy and survives
    pickling / repr round-trips cleanly.
    """

    NOT_FOUND = enum.auto()


_SETTINGS_NOT_FOUND: Final[_SettingsLookup] = _SettingsLookup.NOT_FOUND


async def _lookup_dept_override_from_settings(
    app_state: AppState,
    department_name: NotBlankStr,
) -> CeremonyPolicyConfig | None | _SettingsLookup:
    """Try to find a department override in the settings service."""
    if app_state.slice(SettingsStateSlice).settings_service is None:
        return _SETTINGS_NOT_FOUND
    try:
        entry = await settings_service_of(app_state).get(
            "coordination",
            "dept_ceremony_policies",
        )
    except SettingNotFoundError:
        # The setting key is genuinely absent. Fall back to the
        # config-resolver path silently; a missing override is the
        # default state for departments that have not customised
        # ceremony policy.
        return _SETTINGS_NOT_FOUND
    except SettingsError as exc:
        # Domain-level settings failure (validation, encryption, etc.).
        # Surface as ``ServiceUnavailableError`` so callers see the
        # outage explicitly rather than receiving a lower-precedence
        # config-resolver fallback that silently masks corrupt or
        # unreadable settings. The malformed-JSON branch below uses
        # the same error contract; both failure modes are
        # observability incidents the operator must see.
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="settings",
            department=department_name,
            note="dept_ceremony_policies lookup failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Failed to fetch ceremony policies data"
        raise ServiceUnavailableError(msg) from exc

    try:
        policies = json.loads(entry.value)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="ceremony_policy.fetch_dept",
            note="corrupt dept_ceremony_policies value",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Malformed ceremony policies data"
        raise ServiceUnavailableError(msg) from exc

    if not isinstance(policies, dict):
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="settings",
            department=department_name,
            note="malformed dept_ceremony_policies root payload (non-dict)",
        )
        msg = "Malformed ceremony policies data"
        raise ServiceUnavailableError(msg)
    if department_name not in policies:
        return _SETTINGS_NOT_FOUND
    val = policies[department_name]
    if val is None:
        return None
    if not isinstance(val, dict):
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="settings",
            department=department_name,
            note="malformed dept_ceremony_policies override (non-dict value)",
        )
        msg = "Malformed ceremony policies data"
        raise ServiceUnavailableError(msg)
    try:
        return CeremonyPolicyConfig.model_validate(val)
    except ValidationError as exc:
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="settings",
            department=department_name,
            note="invalid dept_ceremony_policies override",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Malformed ceremony policies data"
        raise ServiceUnavailableError(msg) from exc


async def _fetch_department_policy(
    app_state: AppState,
    department_name: NotBlankStr,
) -> CeremonyPolicyConfig | None:
    """Fetch department-level ceremony policy override.

    Checks settings-based overrides first, then falls back to the
    config resolver's ``ceremony_policy`` field.

    Raises:
        NotFoundError: If the department does not exist.
        ServiceUnavailableError: If required services are unavailable.
    """
    result = await _lookup_dept_override_from_settings(
        app_state,
        department_name,
    )
    # ``_SettingsLookup.NOT_FOUND`` means "no settings-based override
    # configured at all"; everything else (a parsed config or an
    # explicit ``None`` clear) is the override the caller asked for.
    if not isinstance(result, _SettingsLookup):
        return result

    if app_state.slice(SettingsStateSlice).config_resolver is None:
        msg = "Config resolver not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="config_resolver")
        raise ServiceUnavailableError(msg)

    departments = await config_resolver_of(app_state).get_departments()
    for dept in departments:
        if dept.name == department_name:
            if dept.ceremony_policy is None:
                return None
            try:
                return CeremonyPolicyConfig.model_validate(
                    dept.ceremony_policy,
                )
            except ValidationError as exc:
                logger.warning(
                    API_SERVICE_UNAVAILABLE,
                    service="config_resolver",
                    department=department_name,
                    note="invalid department ceremony_policy",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = "Malformed ceremony policy data"
                raise ServiceUnavailableError(msg) from exc
    msg = f"Department {department_name!r} not found"
    logger.warning(
        API_RESOURCE_NOT_FOUND,
        resource="department",
        name=department_name,
    )
    raise NotFoundError(msg)


__all__ = [
    "ActiveCeremonyStrategyResponse",
    "PolicyFieldOrigin",
    "ResolvedCeremonyPolicyResponse",
    "ResolvedPolicyField",
    "_build_project_policy",
    "_build_resolved_response",
    "_determine_field_origin",
    "_fetch_department_policy",
    "_fetch_project_policy",
    "_lookup_dept_override_from_settings",
    "_parse_auto_transition",
    "_parse_strategy",
    "_parse_strategy_config",
    "_parse_transition_threshold",
    "_parse_velocity_calculator",
]
