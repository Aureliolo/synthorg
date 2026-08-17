# module-kind: code
"""The WS event a client-request lifecycle transition puts on the bus."""

from litestar import Request
from litestar.datastructures import State

from synthorg.api.channels import CHANNEL_REQUESTS, publish_ws_event
from synthorg.api.ws_models import WsEventType
from synthorg.client.models import ClientRequest


def publish_request_event(
    request: Request[object, object, State],
    event_type: WsEventType,
    client_request: ClientRequest,
) -> None:
    """Best-effort publish a request lifecycle event.

    ``REQUEST_TASK_CREATED`` additionally carries ``task_id`` so the
    frontend can navigate straight to the spawned task without a
    second ``GET /requests/{id}``. ``_reconcile_success`` always stamps
    ``metadata["task_id"]`` before publishing this event, so the field
    is contract-required for that event type and contract-absent for
    every other request lifecycle event.
    """
    payload: dict[str, object] = {
        "request_id": client_request.request_id,
        "client_id": client_request.client_id,
        "status": client_request.status.value,
    }
    if event_type is WsEventType.REQUEST_TASK_CREATED:
        task_id = client_request.metadata.get("task_id")
        if isinstance(task_id, str) and task_id:
            payload["task_id"] = task_id
    publish_ws_event(request, event_type, CHANNEL_REQUESTS, payload)
