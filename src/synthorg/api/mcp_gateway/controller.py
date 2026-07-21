# module-kind: controller
"""Streamable-http transport for the credentialed-tool MCP server.

Authenticates the per-run gateway bearer (the same signer the LLM gateway
uses), resolves the host-side collaborators from ``AppState``, and
forwards each JSON-RPC message to :func:`dispatch_mcp`. Reachable only over
the sandbox sidecar egress allowlist. The ``tools.credentialed_mcp_enabled``
setting gates the surface per request; while off it 503s.
"""

import json
from typing import Final

from litestar import Controller, Request, post
from litestar.datastructures import State
from litestar.response import Response

from synthorg._core.features import require_service
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.api.mcp_gateway.protocol import dispatch_mcp
from synthorg.api.mcp_gateway.tools import CredentialedToolContext
from synthorg.api.state import AppState
from synthorg.approval.state import approval_store_of
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.normalization import extract_bearer_token
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_MAX_BODY_BYTES: Final[int] = 4_194_304  # 4 MiB request-body ceiling.
_TOOLS_NS: Final[str] = "tools"


class CredentialedMcpController(Controller):
    """MCP JSON-RPC endpoint exposing the governed forge / chat tools."""

    path = "/mcp-gateway"
    tags = ["Gateway"]  # noqa: RUF012 -- Litestar Controller class attribute

    @post(
        "/mcp",
        summary="Credentialed-tool MCP endpoint",
        description=(
            "Serves the governed forge / chat tools over MCP JSON-RPC to an "
            "embedded harness. Authenticated by the per-run gateway bearer."
        ),
        status_code=200,
    )
    async def handle(
        self,
        state: State,
        request: Request[object, object, State],
    ) -> Response[object]:
        """Authenticate, then dispatch the MCP JSON-RPC body.

        Returns:
            A JSON-RPC response (object or batch array), or a 202 for a
            notification-only body.

        Raises:
            ServiceUnavailableError: If the gateway signer / connection
                catalog is unwired or the server is disabled.
            GatewayTokenInvalidError: If the per-run bearer is invalid.
        """
        app_state = state["app_state"]
        signer = require_service(
            app_state.slice(GatewayStateSlice).signer, "gateway signer"
        )
        resolver = config_resolver_of(app_state)
        _require_enabled(
            enabled=await resolver.get_bool(_TOOLS_NS, "credentialed_mcp_enabled")
        )
        token = extract_bearer_token(request.headers.get("authorization", "")) or ""
        claims = signer.verify(token)
        messages, is_batch = await _read_messages(request)
        ctx = await _build_context(app_state)
        capabilities = _parse_capabilities(
            await resolver.get_str(_TOOLS_NS, "credentialed_mcp_capabilities")
        )
        responses = [
            response
            for message in messages
            if (
                response := await dispatch_mcp(
                    message,
                    ctx=ctx,
                    agent_id=claims.agent_id,
                    capabilities=capabilities,
                )
            )
            is not None
        ]
        if not responses:
            return Response[object](None, status_code=202)
        payload: object = responses if is_batch else responses[0]
        return Response[object](payload, media_type="application/json", status_code=200)


def _require_enabled(*, enabled: bool) -> None:
    """Raise when the credentialed-tool MCP server is disabled.

    Raises:
        ServiceUnavailableError: If *enabled* is ``False``.
    """
    if not enabled:
        msg = "credentialed-tool MCP server is disabled"
        raise ServiceUnavailableError(msg)


async def _read_messages(
    request: Request[object, object, State],
) -> tuple[list[dict[str, object]], bool]:
    """Read the JSON-RPC body into a list of messages.

    Returns:
        A ``(messages, is_batch)`` pair; a single object yields a one-element
        list with ``is_batch=False``.

    Raises:
        ServiceUnavailableError: If the body is oversized or malformed.
    """
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        msg = "request body exceeds the MCP gateway size ceiling"
        raise ServiceUnavailableError(msg)
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError) as exc:
        msg = "request body is not valid JSON"
        raise ServiceUnavailableError(msg) from exc
    if isinstance(parsed, list):
        return [m for m in parsed if isinstance(m, dict)], True
    if isinstance(parsed, dict):
        return [parsed], False
    msg = "request body must be a JSON object or array"
    raise ServiceUnavailableError(msg)


async def _build_context(app_state: AppState) -> CredentialedToolContext:
    """Assemble the host-side credentialed-tool context from app state.

    Returns:
        The :class:`CredentialedToolContext` for this request.
    """
    resolver = config_resolver_of(app_state)
    catalog = require_service(
        app_state.slice(IntegrationsStateSlice).connection_catalog,
        "connection catalog",
    )
    return CredentialedToolContext(
        connection_catalog=catalog,
        approval_store=approval_store_of(app_state),
        clock=app_state.clock,
        forge_connection=await resolver.get_str(_TOOLS_NS, "forge_tools_connection"),
        chat_connection=await resolver.get_str(_TOOLS_NS, "chat_tools_connection"),
        forge_timeout_seconds=await resolver.get_float(
            _TOOLS_NS, "forge_tools_timeout_seconds"
        ),
        chat_timeout_seconds=await resolver.get_float(
            _TOOLS_NS, "chat_tools_timeout_seconds"
        ),
        forge_max_read_chars=await resolver.get_int(
            _TOOLS_NS, "forge_tools_max_read_chars"
        ),
    )


def _parse_capabilities(raw: str) -> tuple[str, ...]:
    """Parse the comma-separated capability grant string.

    Returns:
        The tuple of non-blank capability patterns.
    """
    return tuple(part.strip() for part in raw.split(",") if part.strip())
