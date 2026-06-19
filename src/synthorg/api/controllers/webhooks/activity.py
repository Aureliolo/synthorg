# module-kind: controller
"""Webhook activity-listing endpoint."""

from typing import Annotated

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import PathName
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.integrations.state import webhook_activity_service_of


class WebhooksActivityController(Controller):
    """Webhook activity-log listing endpoint."""

    path = "/webhooks"
    tags = ["Integrations"]  # noqa: RUF012

    @get(
        "/{connection_name:str}/activity",
        guards=[require_read_access],
        summary="List webhook activity for a connection",
    )
    async def list_activity(
        self,
        state: State,
        connection_name: PathName,
        limit: Annotated[
            int,
            QueryParameter(ge=1, le=500, description="Max results"),
        ] = 100,  # lint-allow: magic-numbers -- pre-2.22 default preserved
    ) -> ApiResponse[tuple[WebhookReceipt, ...]]:
        """List recent webhook receipts for a connection.

        Returns:
            ``ApiResponse[tuple[WebhookReceipt, ...]]`` instance.
        """
        service = webhook_activity_service_of(state["app_state"])
        receipts = await service.list_activity(
            connection_name=connection_name,
            limit=limit,
        )
        return ApiResponse(data=receipts)
