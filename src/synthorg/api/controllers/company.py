"""Company configuration controller."""

import asyncio

from litestar import Controller, Request, Response, get, patch, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.channels import (
    CHANNEL_COMPANY,
    CHANNEL_DEPARTMENTS,
    publish_ws_event,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_org import (
    ReorderDepartmentsRequest,
    UpdateCompanyRequest,
)
from synthorg.api.guards import (
    require_org_mutation,
    require_read_access,
)
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.core.company import Department
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class CompanyController(Controller):
    """Company configuration -- reads and mutations."""

    path = "/company"
    tags = ("company",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def get_company(
        self,
        state: State,
    ) -> ApiResponse[dict[str, object]]:
        """Return a curated subset of company configuration.

        Returns an explicit field dict to control the response
        shape and avoid exposing internal configuration details.

        Args:
            state: Application state.

        Returns:
            Company configuration envelope.
        """
        app_state: AppState = state.app_state
        resolver = config_resolver_of(app_state)
        try:
            async with asyncio.TaskGroup() as tg:
                t_name = tg.create_task(resolver.get_str("company", "company_name"))
                t_agents = tg.create_task(resolver.get_agents())
                t_depts = tg.create_task(resolver.get_departments())
        except ExceptionGroup as eg:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace="company",
                key="_composed",
                error_count=len(eg.exceptions),
            )
            raise eg.exceptions[0] from eg
        data: dict[str, object] = {
            "company_name": t_name.result(),
            "agents": [a.model_dump(mode="json") for a in t_agents.result()],
            "departments": [d.model_dump(mode="json") for d in t_depts.result()],
        }
        return ApiResponse(data=data)

    @patch(
        "/",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("company.update", key="user"),
        ],
    )
    async def update_company(
        self,
        request: Request[object, object, State],
        state: State,
        data: UpdateCompanyRequest,
    ) -> Response[ApiResponse[dict[str, object]]]:
        """Update company-level settings.

        Supports optimistic concurrency via ``If-Match`` header.

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            data: Partial update request.

        Returns:
            Updated fields envelope with ETag header.
        """
        app_state: AppState = state.app_state
        if_match = request.headers.get("if-match")
        updated, new_etag = await require_service(
            app_state.slice(ApiCoreStateSlice).org_mutation_service,
            "Org Mutation Service",
        ).update_company(
            data,
            if_match=if_match,
            saved_by=get_authenticated_user_id(),
        )
        publish_ws_event(
            request,
            WsEventType.COMPANY_UPDATED,
            CHANNEL_COMPANY,
            updated,
        )
        return Response(
            content=ApiResponse(data=updated),
            headers={"ETag": new_etag},
        )

    @post(
        "/reorder-departments",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("company.reorder_departments", key="user"),
        ],
        status_code=200,
    )
    async def reorder_departments(
        self,
        request: Request[object, object, State],
        state: State,
        data: ReorderDepartmentsRequest,
    ) -> ApiResponse[tuple[Department, ...]]:
        """Reorder departments.

        The payload must be an exact permutation of existing
        department names (no additions or removals).

        Args:
            request: Incoming request (for WS publishing).
            state: Application state.
            data: Ordered department names.

        Returns:
            Reordered departments envelope.
        """
        app_state: AppState = state.app_state
        reordered = await require_service(
            app_state.slice(ApiCoreStateSlice).org_mutation_service,
            "Org Mutation Service",
        ).reorder_departments(
            data,
            saved_by=get_authenticated_user_id(),
        )
        publish_ws_event(
            request,
            WsEventType.DEPARTMENTS_REORDERED,
            CHANNEL_DEPARTMENTS,
            {"department_names": [d.name for d in reordered]},
        )
        return ApiResponse(data=reordered)

    @get("/departments")
    async def list_departments(
        self,
        state: State,
    ) -> ApiResponse[tuple[Department, ...]]:
        """List departments (convenience alias).

        Args:
            state: Application state.

        Returns:
            Departments envelope.
        """
        app_state: AppState = state.app_state
        departments = await config_resolver_of(app_state).get_departments()
        return ApiResponse(data=departments)
