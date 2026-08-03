# module-kind: controller
"""Streamable-http transport for the credentialed-tool MCP server.

Authenticates the per-run gateway bearer (the same signer the LLM gateway
uses), reads the per-request scoping, and forwards each JSON-RPC message to
:func:`dispatch_mcp`. What a ``tools/call`` executes against is resolved in
``_request_context``. Reachable only over the sandbox sidecar egress allowlist.
The ``tools.credentialed_mcp_enabled`` setting gates the surface per request;
while off it 503s.
"""

import json
from typing import Final

from litestar import Controller, Request, post
from litestar.datastructures import State
from litestar.response import Response

from synthorg._core.features import require_service
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.api.mcp_gateway._request_context import (
    _context_opener,
    _resolve_kill_switches,
)
from synthorg.api.mcp_gateway.protocol import dispatch_mcp
from synthorg.api.mcp_gateway.scoping import deploy_denials, publish_denials
from synthorg.core.domain_errors import (
    DomainError,
    ServiceUnavailableError,
    ValidationError,
)
from synthorg.core.normalization import extract_bearer_token
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.gateway import GATEWAY_DISPATCH_FAILED
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
            DomainError: Any domain error from auth / body parsing /
                dispatch, logged here then re-raised for the global handler.
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
        try:
            claims = signer.verify(token)
            messages, is_batch = await _read_messages(request)
            capabilities = _parse_capabilities(
                await resolver.get_str(_TOOLS_NS, "credentialed_mcp_capabilities")
            )
            kill_switches = await _resolve_kill_switches(resolver)
            denied = deploy_denials(
                deploy_enabled=kill_switches.deploy_enabled
            ) + publish_denials(publish_enabled=kill_switches.publish_enabled)
            open_context = _context_opener(app_state, claims=claims)
            responses = [
                response
                for message in messages
                if (
                    response := await dispatch_mcp(
                        message,
                        open_context=open_context,
                        agent_id=claims.agent_id,
                        capabilities=capabilities,
                        denied=denied,
                    )
                )
                is not None
            ]
        except DomainError as exc:
            # Sibling gateway controller logs its error path; mirror it so an
            # auth / body / wiring failure is observable before the global
            # handler forms the (credential-redacted) response.
            logger.warning(
                GATEWAY_DISPATCH_FAILED,
                surface="mcp-gateway",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
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

    A malformed / oversized body is a client error (non-retryable 422), not
    a transient server condition, so it raises :class:`ValidationError` (like
    the gateway controller's ``_read_json_body``) rather than a retryable 503.

    Returns:
        A ``(messages, is_batch)`` pair; a single object yields a one-element
        list with ``is_batch=False``.

    Raises:
        ValidationError: If the body is oversized or malformed.
    """
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        msg = "request body exceeds the MCP gateway size ceiling"
        logger.warning(
            GATEWAY_DISPATCH_FAILED,
            surface="mcp-gateway",
            reason="oversize",
            body_bytes=len(body),
            max_bytes=_MAX_BODY_BYTES,
        )
        raise ValidationError(msg)
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError) as exc:
        msg = "request body is not valid JSON"
        logger.warning(
            GATEWAY_DISPATCH_FAILED, surface="mcp-gateway", reason="invalid_json"
        )
        raise ValidationError(msg) from exc
    if isinstance(parsed, list):
        return [m for m in parsed if isinstance(m, dict)], True
    if isinstance(parsed, dict):
        return [parsed], False
    logger.warning(GATEWAY_DISPATCH_FAILED, surface="mcp-gateway", reason="not_object")
    msg = "request body must be a JSON object or array"
    raise ValidationError(msg)


def _parse_capabilities(raw: str) -> tuple[str, ...]:
    """Parse the comma-separated capability grant string.

    Returns:
        The tuple of non-blank capability patterns.
    """
    return tuple(part.strip() for part in raw.split(",") if part.strip())
