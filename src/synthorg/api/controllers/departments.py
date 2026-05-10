"""Department controller -- listing, health, ceremony policy, and CRUD mutations."""

import copy
import json
from typing import Any, Final

from litestar import Controller, Request, Response, delete, get, patch, post, put
from litestar.datastructures import State  # noqa: TC002
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.channels import CHANNEL_DEPARTMENTS, publish_ws_event
from synthorg.api.concurrency import compute_etag
from synthorg.api.controllers._department_health import (
    DepartmentHealth,
    assemble_department_health,
    filter_agents_by_department,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_org import (  # noqa: TC001
    CreateDepartmentRequest,
    ReorderAgentsRequest,
    UpdateDepartmentRequest,
)
from synthorg.api.guards import (
    require_org_mutation,
    require_read_access,
)
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import PathName  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.api.ws_models import WsEventType
from synthorg.config.schema import AgentConfig  # noqa: TC001
from synthorg.core.company import Department  # noqa: TC001
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.domain_errors import (
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from synthorg.core.normalization import find_by_name_ci
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.workflow.ceremony_policy import CeremonyPolicyConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_CEREMONY_POLICY_DEPT_CLEARED,
    API_CEREMONY_POLICY_DEPT_UPDATED,
    API_DEPARTMENT_HEALTH_QUERIED,
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
    API_SERVICE_UNAVAILABLE,
)

logger = get_logger(__name__)


# ── Department ceremony policy helpers ────────────────────────


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
    if not app_state.has_config_resolver:
        msg = "Config resolver not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="config_resolver")
        raise ServiceUnavailableError(msg)
    departments = await app_state.config_resolver.get_departments()
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
) -> dict[str, Any]:
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
    if not app_state.has_settings_service:
        if raise_on_error:
            msg = "Settings service not available"
            logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
            raise ServiceUnavailableError(msg)
        return {}
    try:
        entry = await app_state.settings_service.get(
            "coordination",
            "dept_ceremony_policies",
        )
        parsed = json.loads(entry.value)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
) -> dict[str, Any] | None:
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
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
    if not app_state.has_config_resolver:
        msg = "Config resolver not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="config_resolver")
        raise ServiceUnavailableError(msg)
    departments = await app_state.config_resolver.get_departments()
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
    """
    if not getattr(app_state, "has_config_resolver", False):
        return _DEPT_POLICY_CAS_FALLBACK_ATTEMPTS
    try:
        return await app_state.config_resolver.get_int(
            "coordination",
            "department_policy_cas_retry_attempts",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
) -> tuple[dict[str, Any], str]:
    """Load policies JSON with its ``updated_at`` for CAS.

    Bypasses the fallback chain -- CAS only cares about DB state.
    Returns ``({}, "")`` when the setting has no persisted value yet
    (first-write semantics).

    Raises:
        ServiceUnavailableError: If the settings service is unavailable
            or the persisted JSON is corrupt.
    """
    if not app_state.has_settings_service:
        msg = "Settings service not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
        raise ServiceUnavailableError(msg)
    try:
        value, updated_at = await app_state.settings_service.get_versioned(
            "coordination",
            "dept_ceremony_policies",
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
    policies: dict[str, Any],
    *,
    expected_updated_at: str,
) -> None:
    """Persist the dept_ceremony_policies JSON with CAS.

    Raises:
        ServiceUnavailableError: If the settings service is not available.
        VersionConflictError: If the persisted ``updated_at`` no longer
            matches ``expected_updated_at`` (concurrent writer won).
    """
    if not app_state.has_settings_service:
        msg = "Settings service not available"
        logger.warning(API_SERVICE_UNAVAILABLE, service="settings")
        raise ServiceUnavailableError(msg)
    await app_state.settings_service.set(
        "coordination",
        "dept_ceremony_policies",
        json.dumps(policies, separators=(",", ":")),
        expected_updated_at=expected_updated_at,
    )


async def _mutate_dept_policies_with_retry(
    app_state: AppState,
    department_name: NotBlankStr,
    new_value: dict[str, Any] | None,
) -> None:
    """Read-modify-write the policies JSON with bounded CAS retry.

    ``new_value`` of ``None`` persists the explicit-inherit sentinel;
    a dict sets the override.  Retries up to the
    ``coordination.department_policy_cas_retry_attempts`` setting on
    :class:`VersionConflictError` before surfacing the last conflict.
    """
    max_attempts = await _resolve_dept_policy_cas_attempts(app_state)

    async def read() -> tuple[dict[str, Any], str]:
        policies, expected = await _load_dept_policies_versioned(app_state)
        policies[department_name] = (
            None if new_value is None else copy.deepcopy(new_value)
        )
        return policies, expected

    async def write(policies: dict[str, Any], expected: str) -> None:
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


# ── Controller ────────────────────────────────────────────────


class DepartmentController(Controller):
    """Departments -- CRUD, health aggregation, ceremony policy."""

    path = "/departments"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_departments(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[Department]:
        """List all departments.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated department list.
        """
        app_state: AppState = state.app_state
        departments = await app_state.config_resolver.get_departments()
        page, meta = paginate_cursor(
            departments,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{name:str}")
    async def get_department(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[Department]:
        """Get a department by name.

        Args:
            state: Application state.
            name: Department name.

        Returns:
            Department envelope.

        Raises:
            NotFoundError: If the department is not found.
        """
        app_state: AppState = state.app_state
        departments = await app_state.config_resolver.get_departments()
        found = find_by_name_ci(departments, name)
        if found is not None:
            return ApiResponse(data=found)
        msg = f"Department {name!r} not found"
        logger.warning(API_RESOURCE_NOT_FOUND, resource="department", name=name)
        raise NotFoundError(msg)

    @post(
        "/",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("departments.create", key="user"),
        ],
        status_code=201,
    )
    async def create_department(
        self,
        request: Request[Any, Any, Any],
        state: State,
        data: CreateDepartmentRequest,
    ) -> ApiResponse[Department]:
        """Create a new department.

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            data: Department creation request.

        Returns:
            Created department envelope (HTTP 201).
        """
        app_state: AppState = state.app_state
        dept = await app_state.org_mutation_service.create_department(
            data,
            saved_by=get_authenticated_user_id(),
        )
        publish_ws_event(
            request,
            WsEventType.DEPARTMENT_CREATED,
            CHANNEL_DEPARTMENTS,
            {"name": dept.name, "budget_percent": dept.budget_percent},
        )
        return ApiResponse(data=dept)

    @patch(
        "/{name:str}",
        guards=[
            require_org_mutation(department_param="name"),
            per_op_rate_limit_from_policy("departments.update", key="user"),
        ],
    )
    async def update_department(
        self,
        request: Request[Any, Any, Any],
        state: State,
        name: PathName,
        data: UpdateDepartmentRequest,
    ) -> Response[ApiResponse[Department]]:
        """Update an existing department.

        Supports optimistic concurrency via ``If-Match`` header.

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            name: Department name.
            data: Partial update request.

        Returns:
            Updated department envelope with ETag header.
        """
        app_state: AppState = state.app_state
        if_match = request.headers.get("if-match")
        updated = await app_state.org_mutation_service.update_department(
            name,
            data,
            saved_by=get_authenticated_user_id(),
            if_match=if_match,
        )
        publish_ws_event(
            request,
            WsEventType.DEPARTMENT_UPDATED,
            CHANNEL_DEPARTMENTS,
            {"name": updated.name},
        )
        new_etag = compute_etag(
            json.dumps(
                updated.model_dump(mode="json"),
                sort_keys=True,
            ),
            "",
        )
        return Response(
            content=ApiResponse(data=updated),
            headers={"ETag": new_etag},
        )

    @delete(
        "/{name:str}",
        guards=[
            require_org_mutation(department_param="name"),
            per_op_rate_limit_from_policy("departments.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_department(
        self,
        request: Request[Any, Any, Any],
        state: State,
        name: PathName,
    ) -> None:
        """Delete a department.

        Rejects deletion if agents are attached (HTTP 409).

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            name: Department name.
        """
        app_state: AppState = state.app_state
        await app_state.org_mutation_service.delete_department(
            name,
            saved_by=get_authenticated_user_id(),
        )
        publish_ws_event(
            request,
            WsEventType.DEPARTMENT_DELETED,
            CHANNEL_DEPARTMENTS,
            {"name": name},
        )

    @post(
        "/{name:str}/reorder-agents",
        guards=[
            require_org_mutation(department_param="name"),
            per_op_rate_limit_from_policy(
                "departments.reorder_agents",
                key="user",
            ),
        ],
    )
    async def reorder_agents(
        self,
        request: Request[Any, Any, Any],
        state: State,
        name: PathName,
        data: ReorderAgentsRequest,
    ) -> ApiResponse[tuple[AgentConfig, ...]]:
        """Reorder agents within a department.

        The payload must be an exact permutation of agents in the
        department (no additions or removals).

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            name: Department name.
            data: Ordered agent names.

        Returns:
            Reordered agents envelope.
        """
        app_state: AppState = state.app_state
        reordered = await app_state.org_mutation_service.reorder_agents(
            name,
            data,
        )
        publish_ws_event(
            request,
            WsEventType.AGENTS_REORDERED,
            CHANNEL_DEPARTMENTS,
            {
                "department": name,
                "agent_names": [a.name for a in reordered],
            },
        )
        return ApiResponse(data=reordered)

    @get("/{name:str}/health")
    async def get_department_health(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[DepartmentHealth]:
        """Get department health aggregation.

        Aggregates agent count, utilization, cost, performance, and
        collaboration data for the named department.

        Args:
            state: Application state.
            name: Department name.

        Returns:
            Department health envelope.

        Raises:
            NotFoundError: If the department is not found.
        """
        app_state: AppState = state.app_state

        # Fetch departments and agents (both are config reads)
        departments = await app_state.config_resolver.get_departments()
        dept = find_by_name_ci(departments, name)
        if dept is None:
            msg = f"Department {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="department",
                name=name,
            )
            raise NotFoundError(msg)
        canonical_name = dept.name

        agents = await app_state.config_resolver.get_agents()
        dept_agents = filter_agents_by_department(agents, canonical_name)
        budget_cfg = await app_state.config_resolver.get_budget_config()
        health = await assemble_department_health(
            app_state,
            canonical_name,
            dept_agents,
            currency=budget_cfg.currency,
        )

        logger.debug(
            API_DEPARTMENT_HEALTH_QUERIED,
            department=canonical_name,
            agent_count=health.agent_count,
            active_count=health.active_agent_count,
            cost_7d=health.department_cost_7d,
        )
        return ApiResponse(data=health)

    @get("/{name:str}/ceremony-policy")
    async def get_department_ceremony_policy(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[dict[str, Any] | None]:
        """Get the department-level ceremony policy override.

        Returns the override dict if the department has one, or
        ``null`` if the department inherits the project-level policy.

        Args:
            state: Application state.
            name: Department name.

        Returns:
            Ceremony policy dict or null envelope.

        Raises:
            NotFoundError: If the department is not found.
        """
        app_state: AppState = state.app_state
        canonical = await _require_department_exists(app_state, name)
        policy = await _get_dept_ceremony_override(app_state, canonical)
        return ApiResponse(data=policy)

    @put(
        "/{name:str}/ceremony-policy",
        guards=[
            require_org_mutation(department_param="name"),
            per_op_rate_limit_from_policy(
                "departments.update_ceremony_policy",
                key="user",
            ),
        ],
    )
    async def update_department_ceremony_policy(
        self,
        state: State,
        name: PathName,
        data: dict[str, Any],
    ) -> ApiResponse[dict[str, Any]]:
        """Set the ceremony policy override for a department.

        Validates the input as a partial ``CeremonyPolicyConfig``.
        Stores the override in the settings system under the
        ``dept_ceremony_policies`` JSON key.

        Args:
            state: Application state.
            name: Department name.
            data: Partial ceremony policy dict.

        Returns:
            The stored ceremony policy dict.

        Raises:
            NotFoundError: If the department does not exist.
            ValidationError: If the policy data is invalid.
        """
        app_state: AppState = state.app_state

        # Verify the department exists and get canonical name
        canonical = await _require_department_exists(app_state, name)

        # Validate policy data via Pydantic
        try:
            validated = CeremonyPolicyConfig.model_validate(data)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            msg = "Invalid ceremony policy data"
            logger.warning(
                API_REQUEST_ERROR,
                endpoint="departments.ceremony_policy.update",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(msg) from exc

        clean_data = validated.model_dump(mode="json", exclude_none=True)

        # Merge into the dept_ceremony_policies JSON setting
        await _mutate_dept_policies_with_retry(app_state, canonical, clean_data)

        logger.info(
            API_CEREMONY_POLICY_DEPT_UPDATED,
            department=canonical,
            strategy=clean_data.get("strategy"),
        )
        return ApiResponse(data=clean_data)

    @delete(
        "/{name:str}/ceremony-policy",
        guards=[
            require_org_mutation(department_param="name"),
            per_op_rate_limit_from_policy(
                "departments.delete_ceremony_policy",
                key="user",
            ),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_department_ceremony_policy(
        self,
        state: State,
        name: PathName,
    ) -> None:
        """Clear the department ceremony policy override.

        The department will revert to inheriting the project-level
        policy.

        Args:
            state: Application state.
            name: Department name.

        Raises:
            NotFoundError: If the department does not exist.
        """
        app_state: AppState = state.app_state
        canonical = await _require_department_exists(app_state, name)
        await _mutate_dept_policies_with_retry(app_state, canonical, None)
        logger.info(
            API_CEREMONY_POLICY_DEPT_CLEARED,
            department=canonical,
        )
