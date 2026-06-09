# module-kind: controller
"""Department CRUD controller -- listing and mutations."""

import json
from typing import Final

from litestar import Controller, Request, Response, delete, get, patch, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg._core.features import require_service
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.channels import CHANNEL_DEPARTMENTS, publish_ws_event
from synthorg.api.concurrency import compute_etag
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_org import (
    CreateDepartmentRequest,
    ReorderAgentsRequest,
    UpdateDepartmentRequest,
)
from synthorg.api.guards import require_org_mutation, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.config.schema import AgentConfig
from synthorg.core.company import Department
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.normalization import find_by_name_ci
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class DepartmentController(Controller):
    """Departments -- listing and CRUD mutations."""

    path = "/departments"
    tags = ("departments",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_departments(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
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
        departments = await config_resolver_of(app_state).get_departments()
        page, meta = paginate_cursor(
            departments,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
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
        departments = await config_resolver_of(app_state).get_departments()
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
        request: Request[object, object, State],
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
        dept = await require_service(
            app_state.slice(ApiCoreStateSlice).org_mutation_service,
            "Org Mutation Service",
        ).create_department(
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
        request: Request[object, object, State],
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
        updated = await require_service(
            app_state.slice(ApiCoreStateSlice).org_mutation_service,
            "Org Mutation Service",
        ).update_department(
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
        request: Request[object, object, State],
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
        await require_service(
            app_state.slice(ApiCoreStateSlice).org_mutation_service,
            "Org Mutation Service",
        ).delete_department(
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
        request: Request[object, object, State],
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
        reordered = await require_service(
            app_state.slice(ApiCoreStateSlice).org_mutation_service,
            "Org Mutation Service",
        ).reorder_agents(
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
