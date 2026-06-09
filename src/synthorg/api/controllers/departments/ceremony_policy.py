# module-kind: controller
"""Department ceremony-policy override controller."""

from litestar import Controller, delete, get, put
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.controllers.departments._shared import (
    _get_dept_ceremony_override,
    _mutate_dept_policies_with_retry,
    _require_department_exists,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_org_mutation, require_read_access
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.engine.workflow.ceremony_policy import CeremonyPolicyConfig
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_CEREMONY_POLICY_DEPT_CLEARED,
    API_CEREMONY_POLICY_DEPT_UPDATED,
)

logger = get_logger(__name__)


class DepartmentCeremonyPolicyController(Controller):
    """Department-level ceremony-policy overrides."""

    path = "/departments"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/{name:str}/ceremony-policy")
    async def get_department_ceremony_policy(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[dict[str, object] | None]:
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
        data: CeremonyPolicyConfig,
    ) -> ApiResponse[dict[str, object]]:
        """Set the ceremony policy override for a department.

        Litestar validates the body as a partial
        ``CeremonyPolicyConfig`` at the request boundary (invalid
        payloads are rejected with HTTP 400 before the handler runs).
        Stores the override in the settings system under the
        ``dept_ceremony_policies`` JSON key.

        Args:
            state: Application state.
            name: Department name.
            data: Partial ceremony policy override.

        Returns:
            The stored ceremony policy dict.

        Raises:
            NotFoundError: If the department does not exist.
        """
        app_state: AppState = state.app_state

        # Verify the department exists and get canonical name
        canonical = await _require_department_exists(app_state, name)

        clean_data = data.model_dump(mode="json", exclude_none=True)

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
