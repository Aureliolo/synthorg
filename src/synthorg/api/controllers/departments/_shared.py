"""Shared ceremony-policy helpers for the department controllers.

Holds the ``dept_ceremony_policies`` JSON load/save machinery and the
CAS read-modify-write retry loop used by the ceremony-policy endpoints.
Kept out of the controller sub-modules so the CRUD, health, and
ceremony-policy controllers stay thin while sharing one source of truth
for the override-store mechanics.
"""

import copy
import json
from typing import Final

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError, ServiceUnavailableError
from synthorg.core.normalization import find_by_name_ci
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_policy import CeremonyPolicyConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
    API_SERVICE_UNAVAILABLE,
)
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)


async def _require_department_exists(
    app_state: AppState,
    name: str,
) -> str:
    """Raise NotFoundError if the department does not exist.

    Args:
        app_state: Application state with config resolver.
        name: Department name (case-insensitive lookup).

    Returns:
        The canonical department name as stored.

    Raises:
        NotFoundError: If the department is not found.
        ServiceUnavailableError: If the config resolver is not available.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        msg = "Config resolver not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="config_resolver")
        raise ServiceUnavailableError(msg)
    departments = await config_resolver_of(app_state).get_departments()
    found = find_by_name_ci(departments, name)
    if found is not None:
        return found.name
    msg = f"Department {name!r} not found"
    logger.warning(API_RESOURCE_NOT_FOUND, resource="department", name=name)
    raise NotFoundError(msg)


async def _load_dept_policies_json(
    app_state: AppState,
    *,
    raise_on_error: bool = False,
) -> dict[str, object]:
    """Load the dept_ceremony_policies JSON setting.

    Args:
        app_state: Application state with settings service.
        raise_on_error: If ``True``, propagate exceptions instead
            of returning an empty dict.  Must be ``True`` for
            read-modify-write callers to prevent data loss.

    Returns:
        Parsed dict of department overrides. Empty dict if the
        setting is not persisted or unreadable (only when
        ``raise_on_error`` is ``False``).

    Raises:
        ServiceUnavailableError: If settings service is unavailable
            and ``raise_on_error`` is ``True``.
    """
    if app_state.slice(SettingsStateSlice).settings_service is None:
        if raise_on_error:
            msg = "Settings service not available"
            logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
            raise ServiceUnavailableError(msg)
        return {}
    try:
        entry = await require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        ).get(
            "coordination",
            "dept_ceremony_policies",
        )
        parsed = json.loads(entry.value)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.ceremony_policy.load",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if raise_on_error:
            msg = "Failed to load department ceremony policies"
            raise ServiceUnavailableError(msg) from exc
        return {}

    if not isinstance(parsed, dict):
        msg = f"dept_ceremony_policies is not a dict: {type(parsed).__name__}"
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.ceremony_policy.load",
            error=msg,
        )
        if raise_on_error:
            raise ServiceUnavailableError(msg)
        return {}
    return parsed


async def _get_dept_ceremony_override(
    app_state: AppState,
    department_name: NotBlankStr,
) -> dict[str, object] | None:
    """Get the ceremony policy override for a department.

    Checks the settings-based overrides first, then falls back to
    the department's config ``ceremony_policy`` field.

    Args:
        app_state: Application state.
        department_name: Department name.

    Returns:
        The override dict, or None if the department inherits.

    Raises:
        NotFoundError: If the department does not exist.
        ServiceUnavailableError: If the settings service is not
            available or the JSON blob is unreadable.
    """
    # Check settings-based overrides first (raise on error to
    # surface service failures instead of silently showing "inherit")
    policies = await _load_dept_policies_json(
        app_state,
        raise_on_error=True,
    )
    if department_name in policies:
        val = policies[department_name]
        # None sentinel means "explicitly inheriting"
        if val is None:
            return None
        if isinstance(val, dict):
            # Validate structure before returning to catch corrupt data
            try:
                CeremonyPolicyConfig.model_validate(val)
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    API_REQUEST_ERROR,
                    endpoint="departments.ceremony_policy.get",
                    department=department_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Corrupt ceremony policy override for {department_name!r}"
                raise ServiceUnavailableError(msg) from exc
            return val
        return None

    # Fall back to config-based ceremony_policy
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        msg = "Config resolver not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="config_resolver")
        raise ServiceUnavailableError(msg)
    departments = await config_resolver_of(app_state).get_departments()
    for dept in departments:
        if dept.name == department_name:
            return dept.ceremony_policy
    msg = f"Department {department_name!r} not found"
    logger.warning(
        API_RESOURCE_NOT_FOUND,
        resource="department",
        name=department_name,
    )
    raise NotFoundError(msg)


# Cross-worker concurrency on the ``dept_ceremony_policies`` JSON blob
# is handled via settings-service CAS (compare-and-swap on ``updated_at``).
# Every mutation reads the current versioned value, mutates in-memory,
# then writes with ``expected_updated_at``; a losing writer gets
# ``VersionConflictError`` and retries. The retry budget resolves through
# ``coordination.department_policy_cas_retry_attempts`` so an operator
# can tune a sustained-contention burst without restarting the process;
# the constant below is the fallback when the resolver is unavailable.
_DEPT_POLICY_CAS_FALLBACK_ATTEMPTS: Final[int] = 3


async def _resolve_dept_policy_cas_attempts(app_state: AppState) -> int:
    """Resolve the CAS retry budget through the settings chain.

    Falls back to :data:`_DEPT_POLICY_CAS_FALLBACK_ATTEMPTS` when the
    application has no resolver wired or the lookup fails. A
    transient settings outage must not collapse the retry budget to
    zero, so the fallback equals the registered default.

    Returns:
        Resulting integer.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return _DEPT_POLICY_CAS_FALLBACK_ATTEMPTS
    try:
        return await config_resolver_of(app_state).get_int(
            "coordination",
            "department_policy_cas_retry_attempts",
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.ceremony_policy.cas_retry_resolve",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=_DEPT_POLICY_CAS_FALLBACK_ATTEMPTS,
        )
        return _DEPT_POLICY_CAS_FALLBACK_ATTEMPTS


async def _load_dept_policies_versioned(
    app_state: AppState,
) -> tuple[dict[str, object], str]:
    """Load policies JSON with its ``updated_at`` for CAS.

    Bypasses the fallback chain -- CAS only cares about DB state.
    Returns ``({}, "")`` when the setting has no persisted value yet
    (first-write semantics).

    Raises:
        ServiceUnavailableError: If the settings service is unavailable
            or the persisted JSON is corrupt.

    Returns:
        Tuple of the declared element types.
    """
    if app_state.slice(SettingsStateSlice).settings_service is None:
        msg = "Settings service not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
        raise ServiceUnavailableError(msg)
    try:
        value, updated_at = await require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        ).get_versioned(
            "coordination",
            "dept_ceremony_policies",
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.ceremony_policy.load_versioned",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Failed to load department ceremony policies"
        raise ServiceUnavailableError(msg) from exc
    if not value:
        return {}, ""
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError) as exc:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.ceremony_policy.load_versioned",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Failed to parse department ceremony policies"
        raise ServiceUnavailableError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"dept_ceremony_policies is not a dict: {type(parsed).__name__}"
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.ceremony_policy.load_versioned",
            error=msg,
        )
        raise ServiceUnavailableError(msg)
    return parsed, updated_at


async def _save_dept_policies_with_cas(
    app_state: AppState,
    policies: dict[str, object],
    *,
    expected_updated_at: str,
) -> None:
    """Persist the dept_ceremony_policies JSON with CAS.

    Raises:
        ServiceUnavailableError: If the settings service is not available.
        VersionConflictError: If the persisted ``updated_at`` no longer
            matches ``expected_updated_at`` (concurrent writer won).
    """
    if app_state.slice(SettingsStateSlice).settings_service is None:
        msg = "Settings service not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
        raise ServiceUnavailableError(msg)
    await require_service(
        app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
    ).set(
        "coordination",
        "dept_ceremony_policies",
        json.dumps(policies, separators=(",", ":")),
        expected_updated_at=expected_updated_at,
    )


async def _mutate_dept_policies_with_retry(
    app_state: AppState,
    department_name: NotBlankStr,
    new_value: dict[str, object] | None,
) -> None:
    """Read-modify-write the policies JSON with bounded CAS retry.

    ``new_value`` of ``None`` persists the explicit-inherit sentinel;
    a dict sets the override.  Retries up to the
    ``coordination.department_policy_cas_retry_attempts`` setting on
    :class:`VersionConflictError` before surfacing the last conflict.
    """
    max_attempts = await _resolve_dept_policy_cas_attempts(app_state)

    async def read() -> tuple[dict[str, object], str]:
        """Return read."""
        policies, expected = await _load_dept_policies_versioned(app_state)
        policies[department_name] = (
            None if new_value is None else copy.deepcopy(new_value)
        )
        return policies, expected

    async def write(policies: dict[str, object], expected: str) -> None:
        """Run write."""
        await _save_dept_policies_with_cas(
            app_state,
            policies,
            expected_updated_at=expected,
        )

    handler = CASRetryHandler(
        resource="dept_policy",
        max_attempts=max_attempts,
    )
    await handler.execute(read, write)
