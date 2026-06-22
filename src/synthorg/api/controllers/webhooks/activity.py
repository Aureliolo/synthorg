# module-kind: controller
"""Webhook activity-listing endpoint."""

from litestar import Controller, get
from litestar.datastructures import State

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import DEFAULT_LIMIT, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
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
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[WebhookReceipt]:
        """List webhook receipts for a connection, newest-first (paginated).

        Receipts accumulate for the lifetime of a connection, so the endpoint
        pages with an opaque HMAC cursor (``cursor`` + ``limit``) rather than
        capping at a fixed window the client cannot advance past.

        Returns:
            A page of :class:`WebhookReceipt` rows plus cursor metadata.
        """
        app_state = state["app_state"]
        service = webhook_activity_service_of(app_state)
        offset = (
            0
            if cursor is None
            else decode_cursor(cursor, secret=cursor_secret_of(app_state))
        )
        receipts = await service.list_activity(
            connection_name=connection_name,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(receipts),
            limit=limit,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=tuple(receipts[:limit]), pagination=meta)
