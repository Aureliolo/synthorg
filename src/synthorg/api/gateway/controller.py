# module-kind: controller
"""OpenAI-compatible LLM gateway controller.

Exposes ``POST /gateway/v1/chat/completions`` so an embedded harness's
LiteLLM client can point its ``base_url`` here. The endpoint authenticates
with the per-run signed bearer (validated inside the pipeline, never the
session cookie), routes through :class:`GatewayService`, and renders
OpenAI-shaped success and error bodies. Streaming requests return a
``text/event-stream`` of pre-framed SSE ``data:`` lines.
"""

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Final

from litestar import Controller, Request, post
from litestar.datastructures import State
from litestar.response import Response, Stream

from synthorg._core.features import require_service
from synthorg.api.gateway.service import GatewayService, ProviderResolver
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.budget.state import cost_tracker_of
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.domain_errors import DomainError, ValidationError
from synthorg.core.normalization import extract_bearer_token
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.gateway import GATEWAY_DISPATCH_FAILED
from synthorg.providers.state import provider_registry_of
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_MAX_BODY_BYTES: Final[int] = 8_388_608  # 8 MiB request-body ceiling.
_GATEWAY_ENABLED_KEY: Final[str] = "gateway_enabled"
_PROVIDERS_NS: Final[str] = "providers"


class GatewayController(Controller):
    """OpenAI-compatible chat-completions endpoint."""

    path = "/gateway"
    tags = ["Gateway"]  # noqa: RUF012 -- Litestar Controller class attribute

    @post(
        "/v1/chat/completions",
        summary="OpenAI-compatible chat completions",
        description=(
            "Routes an embedded harness's LLM call through the provider "
            "registry with cost attribution, Explicit Provider Binding and a "
            "hard token budget. Authenticated by a per-run signed bearer."
        ),
        status_code=200,
        # Reject oversized payloads at the Litestar layer so a body past the
        # gateway ceiling is refused before it is fully buffered, rather than
        # relying on the post-read `_read_json_body` check alone.
        request_max_body_size=_MAX_BODY_BYTES,
    )
    async def chat_completions(
        self,
        state: State,
        request: Request[object, object, State],
    ) -> Response[object] | Stream:
        """Dispatch a buffered or streaming OpenAI chat completion.

        Returns:
            An OpenAI ``chat.completion`` JSON response, an SSE stream, or an
            OpenAI-shaped error body with the mapped HTTP status.
        """
        app_state = state["app_state"]
        service = require_service(
            app_state.slice(GatewayStateSlice).service, "LLM gateway"
        )
        token = extract_bearer_token(request.headers.get("authorization", "")) or ""
        try:
            # Resolve the toggle before reading the body: the route is
            # auth-excluded so it can check its own bearer, which means an
            # unauthenticated caller can reach here, and parsing megabytes of
            # their JSON first is work done on behalf of a request that is
            # about to be refused.
            enabled = await config_resolver_of(app_state).get_bool(
                _PROVIDERS_NS, _GATEWAY_ENABLED_KEY
            )
            body = await _read_json_body(request)
            registry = provider_registry_of(app_state)
            cost_tracker = cost_tracker_of(app_state)
            if bool(body.get("stream")):
                return await _stream_response(
                    service,
                    token=token,
                    body=body,
                    registry=registry,
                    cost_tracker=cost_tracker,
                    enabled=enabled,
                )
            result = await service.complete(
                token=token,
                raw_request=body,
                registry=registry,
                cost_tracker=cost_tracker,
                enabled=enabled,
            )
            return Response[object](
                result, media_type="application/json", status_code=200
            )
        except DomainError as exc:
            logger.warning(
                GATEWAY_DISPATCH_FAILED,
                error_type=type(exc).__name__,
                status=exc.status_code,
            )
            return _error_response(exc)


async def _read_json_body(request: Request[object, object, State]) -> dict[str, object]:
    """Read and JSON-parse the request body into a dict.

    Returns:
        The parsed request object.

    Raises:
        ValidationError: If the body exceeds the size ceiling, is not valid
            JSON, or is not a JSON object.
    """
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        msg = "request body exceeds the gateway size ceiling"
        raise ValidationError(msg)
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError) as exc:
        msg = "request body is not valid JSON"
        raise ValidationError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "request body must be a JSON object"
        raise ValidationError(msg)
    return parsed


async def _stream_response(
    service: GatewayService,
    *,
    token: str,
    body: dict[str, object],
    registry: ProviderResolver,
    cost_tracker: CostTrackerProtocol | None,
    enabled: bool,
) -> Stream:
    """Build an SSE streaming response, surfacing setup errors as HTTP status.

    The pipeline's token / binding / budget checks run on the first
    iteration, so the first frame is fetched eagerly: a setup failure raises a
    ``DomainError`` the handler renders as a normal error response instead of a
    half-open stream.

    Returns:
        A ``text/event-stream`` :class:`Stream` of pre-framed SSE lines.
    """
    frames = service.stream(
        token=token,
        raw_request=body,
        registry=registry,
        cost_tracker=cost_tracker,
        enabled=enabled,
    )
    # Peek the first frame so an early error (auth / binding / budget) raises
    # here and maps to a proper status, before the 200 Stream is returned.
    first = await frames.__anext__()

    async def _bytes() -> AsyncIterator[bytes]:
        # aclosing drives the service generator's teardown (cost-scope exit +
        # provider stream close) promptly when the ASGI layer closes this body
        # on a client disconnect, instead of deferring it to generator GC.
        async with contextlib.aclosing(frames):
            yield first.encode("utf-8")
            async for frame in frames:
                yield frame.encode("utf-8")

    return Stream(_bytes(), media_type="text/event-stream")


def _error_response(exc: DomainError) -> Response[object]:
    """Render a DomainError as an OpenAI-shaped error body.

    Returns:
        A JSON error response carrying the credential-redacted message and the
        error's mapped HTTP status.
    """
    return Response[object](
        {
            "error": {
                "message": safe_error_description(exc),
                "type": exc.error_category.value,
                "code": int(exc.error_code),
            }
        },
        media_type="application/json",
        status_code=exc.status_code,
    )
