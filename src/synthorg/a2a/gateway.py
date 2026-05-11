"""A2A JSON-RPC 2.0 gateway controller.

Handles inbound A2A requests dispatched by method name:
``message/send``, ``tasks/get``, ``tasks/cancel``.  All inbound
requests are validated against the peer allowlist and connection
catalog credentials.
"""

import asyncio
import hmac
import json
from typing import Any, ClassVar, Final

from litestar import Controller, Request, post
from litestar.datastructures import State  # noqa: TC002
from litestar.response import Response
from pydantic import ValidationError

from synthorg.a2a.models import (
    A2A_AUTH_REQUIRED,
    A2A_PAYLOAD_TOO_LARGE,
    A2A_PEER_NOT_ALLOWED,
    A2A_TASK_NOT_CANCELABLE,
    A2A_TASK_NOT_FOUND,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    A2ATextPart,
    JsonRpcErrorData,
    JsonRpcRequest,
    JsonRpcResponse,
)
from synthorg.a2a.rpc_params import (
    A2AMessageSendParams,
    A2ATaskCancelParams,
    A2ATaskGetParams,
    parse_rpc_params,
)
from synthorg.a2a.security import validate_peer
from synthorg.a2a.task_mapper import to_a2a
from synthorg.api.rate_limits.policies import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.a2a import (
    A2A_INBOUND_AUTH_FAILED,
    A2A_INBOUND_DISPATCHED,
    A2A_INBOUND_RECEIVED,
    A2A_INBOUND_REJECTED,
    A2A_JSONRPC_INVALID_PARAMS,
    A2A_JSONRPC_METHOD_NOT_FOUND,
    A2A_JSONRPC_PARSE_ERROR,
    A2A_TASK_CANCELLED,
    A2A_TASK_CREATED,
)
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED

logger = get_logger(__name__)

_SUPPORTED_METHODS = frozenset(
    {
        "message/send",
        "tasks/get",
        "tasks/cancel",
    }
)

# Internal constant by design: fallback maximum number of message
# parts in a single message/send request when the resolver is
# unavailable.  The canonical operator-tunable value is
# ``a2a.max_message_parts``.  This constant mirrors that registry
# default so a test harness or a boot path that bypasses
# :class:`AppState` still enforces the documented ceiling.
_MAX_MESSAGE_PARTS_FALLBACK: Final[int] = 100


async def _resolve_max_message_parts(app_state: Any) -> int:
    """Resolve the maximum message-parts cap through the settings chain.

    Falls back to :data:`_MAX_MESSAGE_PARTS_FALLBACK` when the
    application state has no :class:`ConfigResolver` wired or the
    resolver lookup fails.  A transient settings outage must not let
    an oversized message slip through, so the fallback is the same
    value as the registered default.
    """
    if app_state is None or not getattr(app_state, "has_config_resolver", False):
        return _MAX_MESSAGE_PARTS_FALLBACK
    try:
        value: int = await app_state.config_resolver.get_int("a2a", "max_message_parts")
        return value  # noqa: TRY300 -- explicit return type; not a try-success path
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        # SETTINGS_FETCH_FAILED is the correct surface for an
        # internal settings-resolution failure; A2A_JSONRPC_INVALID_PARAMS
        # would mislead operators into thinking a client request was
        # malformed.
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace="a2a",
            key="max_message_parts",
            note="failed to resolve a2a.max_message_parts; using fallback",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=_MAX_MESSAGE_PARTS_FALLBACK,
        )
        return _MAX_MESSAGE_PARTS_FALLBACK


def _error_response(
    request_id: str | int | None,
    code: int,
    message: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC error response dict.

    Args:
        request_id: Echoed request ID.
        code: JSON-RPC error code.
        message: Human-readable error description.
        data: Additional error data.

    Returns:
        Serialized JSON-RPC error response.
    """
    resp = JsonRpcResponse(
        id=request_id,
        error=JsonRpcErrorData(
            code=code,
            message=message,
            data=data,
        ),
    )
    return resp.model_dump()


def _success_response(
    request_id: str | int | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Build a JSON-RPC success response dict.

    Args:
        request_id: Echoed request ID.
        result: Success payload.

    Returns:
        Serialized JSON-RPC success response.
    """
    resp = JsonRpcResponse(id=request_id, result=result)
    return resp.model_dump()


class A2AGatewayController(Controller):
    """A2A JSON-RPC 2.0 gateway endpoint."""

    path = "/a2a"
    tags = ["A2A"]  # noqa: RUF012

    @post(
        "/",
        summary="A2A JSON-RPC 2.0 endpoint",
        description=(
            "Receives JSON-RPC 2.0 requests and dispatches "
            "to the appropriate A2A method handler."
        ),
        status_code=200,
        guards=[
            per_op_rate_limit_from_policy("a2a.gateway", key="user_or_ip"),
        ],
    )
    async def handle_jsonrpc(  # noqa: PLR0911
        self,
        state: State,
        request: Request[Any, Any, Any],
    ) -> Response[dict[str, Any]]:
        """Dispatch an inbound JSON-RPC 2.0 request."""
        app_state = state["app_state"]
        a2a_config = app_state.config.a2a

        # Validate Content-Type
        content_type = (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        if content_type != "application/json":
            return Response(
                content=_error_response(
                    None,
                    JSONRPC_PARSE_ERROR,
                    "Content-Type must be application/json",
                ),
                media_type="application/json",
                status_code=415,
            )

        # Pre-check Content-Length before buffering the body.
        content_length_str = request.headers.get("content-length", "")
        if content_length_str.isdigit():
            declared_size = int(content_length_str)
            if declared_size > a2a_config.max_request_body_bytes:
                return Response(
                    content=_error_response(
                        None,
                        A2A_PAYLOAD_TOO_LARGE,
                        "Request body exceeds maximum size",
                    ),
                    media_type="application/json",
                    status_code=413,
                )

        # Read body with incremental size enforcement.
        max_bytes = a2a_config.max_request_body_bytes
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes:
                return Response(
                    content=_error_response(
                        None,
                        A2A_PAYLOAD_TOO_LARGE,
                        "Request body exceeds maximum size",
                    ),
                    media_type="application/json",
                    status_code=413,
                )
            chunks.append(chunk)
        body = b"".join(chunks)

        # Parse JSON-RPC envelope
        rpc_request = _parse_jsonrpc(body)
        if rpc_request is None:
            return Response(
                content=_error_response(
                    None,
                    JSONRPC_PARSE_ERROR,
                    "Invalid JSON-RPC request",
                ),
                media_type="application/json",
            )

        request_id = rpc_request.id

        logger.info(
            A2A_INBOUND_RECEIVED,
            method=rpc_request.method,
            request_id=request_id,
        )

        # Require peer identification -- mandatory
        peer_name = _extract_peer_name(request)
        if not peer_name:
            logger.warning(
                A2A_INBOUND_AUTH_FAILED,
                reason="missing peer identification",
            )
            return Response(
                content=_error_response(
                    request_id,
                    A2A_AUTH_REQUIRED,
                    "Peer identification required (X-A2A-Peer-Name header)",
                ),
                media_type="application/json",
                status_code=401,
            )

        if not validate_peer(
            peer_name,
            tuple(str(p) for p in a2a_config.allowed_peers),
        ):
            return Response(
                content=_error_response(
                    request_id,
                    A2A_PEER_NOT_ALLOWED,
                    "Peer not on allowlist",
                ),
                media_type="application/json",
                status_code=403,
            )

        # Verify peer credentials against the connection catalog.
        if not await _verify_peer_credentials(
            app_state,
            request,
            peer_name,
        ):
            return Response(
                content=_error_response(
                    request_id,
                    A2A_AUTH_REQUIRED,
                    "Invalid peer credentials",
                ),
                media_type="application/json",
                status_code=401,
            )

        return await _dispatch_method(
            app_state,
            rpc_request,
            peer_name,
        )


def _parse_jsonrpc(body: bytes) -> JsonRpcRequest | None:
    """Parse a JSON-RPC 2.0 request from raw bytes.

    Args:
        body: Raw request body.

    Returns:
        Parsed request, or ``None`` on parse failure.
    """
    try:
        raw = json.loads(body)
        return JsonRpcRequest.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(
            A2A_JSONRPC_PARSE_ERROR,
            reason="json_decode_error",
            error=safe_error_description(exc),
        )
        return None
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        # No ``exc_info=True``: a traceback attached here would
        # serialise frame-local variables (including the raw request
        # body bytes) into the log event. ``error_type`` + scrubbed
        # ``error`` preserves triage detail without the stack walk.
        logger.warning(
            A2A_JSONRPC_PARSE_ERROR,
            reason="validation_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _dispatch_method(
    app_state: Any,
    rpc_request: JsonRpcRequest,
    peer_name: str,
) -> Response[dict[str, Any]]:
    """Dispatch a validated JSON-RPC request to its handler.

    The envelope's method is checked against the supported set, then
    :func:`parse_rpc_params` validates the params dict against the
    method-specific :class:`~synthorg.a2a.rpc_params.A2ARpcParams`
    discriminated union.  Each typed variant routes to a dedicated
    handler via ``match`` -- the dispatch table that previously held
    string-keyed callables is no longer needed.

    Args:
        app_state: Application state container.
        rpc_request: Validated JSON-RPC request.
        peer_name: Authenticated peer name.

    Returns:
        JSON-RPC response wrapped in an HTTP response.
    """
    request_id = rpc_request.id
    method = str(rpc_request.method)

    if method not in _SUPPORTED_METHODS:
        logger.warning(A2A_JSONRPC_METHOD_NOT_FOUND, method=method)
        return Response(
            content=_error_response(
                request_id,
                JSONRPC_METHOD_NOT_FOUND,
                f"Method not found: {method}",
            ),
            media_type="application/json",
        )

    try:
        params = parse_rpc_params(rpc_request)
    except ValidationError as exc:
        # Strip ``input`` and ``url`` from each error -- input would
        # echo peer-supplied payload (potentially credential-bearing),
        # and the Pydantic docs URL is noise for a wire client.
        errors = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors(include_input=False, include_url=False)
        ]
        logger.warning(
            A2A_JSONRPC_INVALID_PARAMS,
            method=method,
            peer_name=peer_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return Response(
            content=_error_response(
                request_id,
                JSONRPC_INVALID_PARAMS,
                "Invalid params",
                data={"errors": errors},
            ),
            media_type="application/json",
            status_code=400,
        )

    logger.info(
        A2A_INBOUND_DISPATCHED,
        method=method,
        request_id=request_id,
        peer_name=peer_name,
    )

    try:
        # ``A2ARpcParams`` is a closed discriminated union of three
        # variants.  Each match arm returns directly so adding a new
        # variant without wiring it here is a hard fail (``AssertionError``
        # from the ``case _`` arm) rather than an unbound ``result`` that
        # silently falls through to the generic 500 path.
        match params:
            case A2AMessageSendParams():
                result = await _handle_message_send(app_state, params, peer_name)
            case A2ATaskGetParams():
                result = await _handle_tasks_get(app_state, params, peer_name)
            case A2ATaskCancelParams():
                result = await _handle_tasks_cancel(app_state, params, peer_name)
            case _:  # pragma: no cover -- enforced exhaustive over A2ARpcParams
                # mypy proves this branch is unreachable via the
                # closed ``A2ARpcParams`` union; we still keep it as a
                # runtime guard for the day someone adds a fourth
                # variant without updating this dispatch.  The
                # ``type: ignore`` is local and tied to that future
                # change actually breaking the cover.
                msg = (  # type: ignore[unreachable]
                    f"A2ARpcParams variant {type(params).__name__!r} "
                    f"is not wired in the dispatch ``match``"
                )
                raise AssertionError(msg)  # noqa: TRY301
        return Response(
            content=_success_response(request_id, result),
            media_type="application/json",
        )
    except _A2AMethodError as exc:
        return Response(
            content=_error_response(
                request_id,
                exc.code,
                exc.message,
            ),
            media_type="application/json",
            status_code=exc.http_status,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        # A2A is a credential-bearing path: a traceback attached via
        # ``logger.exception`` would serialise frame-local values from
        # the validated ``rpc_request`` (peer payloads, auth tokens).
        # ``warning`` + ``safe_error_description`` keeps operator
        # triage detail without the stack walk.
        logger.warning(
            A2A_INBOUND_REJECTED,
            method=method,
            peer_name=peer_name,
            reason="unhandled exception",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return Response(
            content=_error_response(
                request_id,
                JSONRPC_INTERNAL_ERROR,
                "Internal error",
            ),
            media_type="application/json",
            status_code=500,
        )


def _credentials_match(stored: str, presented: str) -> bool:
    """Constant-time comparison of two credential strings.

    Wraps :func:`hmac.compare_digest` so the call sites read
    ``_credentials_match(...)`` instead of repeated bytes-encoding +
    HMAC plumbing. Both inputs are encoded to UTF-8 once before
    comparison; this keeps the timing characteristic stable for
    Unicode credentials and avoids ``compare_digest``'s
    ``str``-vs-``bytes`` type sensitivity.

    ``errors="replace"`` is used on both encodes (matching the
    pattern used elsewhere in this codebase, e.g.
    :func:`synthorg.api.controllers.webhooks._build_idem_key`) so a
    presented credential that contains broken UTF-8 (mojibake,
    unpaired surrogates from a misbehaving transport) does not
    raise ``UnicodeEncodeError`` mid-comparison. The replacement
    bytes still differ from any well-formed stored credential, so
    the comparison correctly returns ``False`` rather than crashing
    the auth path.

    Length mismatches are tolerated: ``compare_digest`` returns
    ``False`` in time proportional to the shorter input, which is
    the closest constant-time behaviour available without
    pre-padding (and pre-padding would itself leak the longer
    input's length).

    Callers MUST treat empty stored credentials as a missing-
    credentials condition before calling here -- this helper is
    purely byte-wise and reports two empty strings as equal.
    """
    return hmac.compare_digest(
        stored.encode("utf-8", errors="replace"),
        presented.encode("utf-8", errors="replace"),
    )


async def _verify_peer_credentials(  # noqa: PLR0911
    app_state: Any,
    request: Request[Any, Any, Any],
    peer_name: str,
) -> bool:
    """Verify the peer's credentials against the connection catalog.

    Looks up the peer's stored API key and compares it to the
    ``Authorization`` or ``X-API-Key`` header from the request.
    Returns ``True`` when credentials match or when no connection
    catalog is available (graceful degradation -- allowlist is
    still enforced).

    Args:
        app_state: Application state container.
        request: Inbound HTTP request.
        peer_name: Declared peer name from header.

    Returns:
        ``True`` if credentials are valid or catalog unavailable.
    """
    try:
        catalog = app_state._connection_catalog  # noqa: SLF001
        if catalog is None:
            return True
        credentials = await catalog.get_credentials(peer_name)
        if not credentials:
            return True

        scheme = credentials.get("auth_scheme", "api_key")
        if scheme == "api_key":
            stored_key = credentials.get("api_key", "")
            request_key = request.headers.get("x-api-key", "") or request.headers.get(
                "authorization", ""
            ).removeprefix("Bearer ")
            if stored_key and not request_key:
                logger.warning(
                    A2A_INBOUND_AUTH_FAILED,
                    peer_name=peer_name,
                    reason="missing credentials in request",
                )
                return False
            if stored_key and not _credentials_match(stored_key, request_key):
                logger.warning(
                    A2A_INBOUND_AUTH_FAILED,
                    peer_name=peer_name,
                    reason="credential mismatch",
                )
                return False
        elif scheme in ("bearer", "oauth2"):
            stored_token = credentials.get("access_token", "")
            auth_header = request.headers.get("authorization", "")
            request_token = auth_header.removeprefix("Bearer ").strip()
            if stored_token and not _credentials_match(stored_token, request_token):
                logger.warning(
                    A2A_INBOUND_AUTH_FAILED,
                    peer_name=peer_name,
                    reason="token mismatch",
                )
                return False
        # mTLS/none: no header-level check needed
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        # Credential verification sits alongside ``request``,
        # ``credentials``, and raw auth headers in the local frame;
        # attaching ``exc_info=True`` would have structlog serialise
        # those into the event and reintroduce the credential leak.
        # Log the scrubbed type+message only.
        logger.warning(
            A2A_INBOUND_AUTH_FAILED,
            peer_name=peer_name,
            reason="credential verification failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False

    return True


def _extract_peer_name(
    request: Request[Any, Any, Any],
) -> str | None:
    """Extract the peer name from request headers.

    Looks for the ``X-A2A-Peer-Name`` header.

    Args:
        request: The inbound HTTP request.

    Returns:
        Peer name string, or ``None`` if not provided.
    """
    peer = request.headers.get("x-a2a-peer-name")
    if peer:
        return peer.strip()
    return None


class _A2AMethodError(DomainError):
    """Error raised by method handlers for JSON-RPC error responses."""

    default_message: ClassVar[str] = "A2A method dispatch failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500

    def __init__(
        self,
        code: int,
        message: str,
        *,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _require_task_engine(app_state: Any) -> Any:
    """Return the task engine or raise 503.

    ``AppState.task_engine`` raises ``ServiceUnavailableError``
    when the engine is not wired.  We catch that and re-raise
    as ``_A2AMethodError`` so the JSON-RPC dispatcher can format
    a proper error response.
    """
    from synthorg.core.domain_errors import ServiceUnavailableError  # noqa: PLC0415

    try:
        return app_state.task_engine
    except ServiceUnavailableError:
        raise _A2AMethodError(
            JSONRPC_INTERNAL_ERROR,
            "Task engine unavailable",
            http_status=503,
        ) from None


def _validate_task_ownership(
    task: Any,
    peer_name: str,
) -> None:
    """Verify the peer created or is assigned this task.

    Tasks created by the A2A gateway carry ``created_by =
    "a2a-gateway"`` and are associated with the requesting peer
    via the ``a2a-inbound`` project.  For now, all A2A tasks are
    accessible to any authenticated peer (the peer allowlist is
    the authorization boundary).  A stricter per-peer ownership
    model can be layered on when multi-peer isolation is needed.

    Args:
        task: The task to check.
        peer_name: Authenticated peer name.
    """
    # All authenticated peers currently share the a2a task namespace.
    # Per-peer isolation (task.metadata["a2a_peer"] == peer_name)
    # is a follow-up once task metadata propagation is wired.


async def _handle_message_send(
    app_state: Any,
    params: A2AMessageSendParams,
    peer_name: str,
) -> dict[str, Any]:
    """Handle ``message/send`` -- create a task.

    The :class:`A2AMessageSendParams` model has already validated that
    ``message`` is a typed :class:`~synthorg.a2a.models.A2AMessage`
    with at least one part of a known variant; this handler only
    enforces the runtime, config-driven part-count cap.

    Args:
        app_state: Application state container.
        params: Typed ``message/send`` parameters.
        peer_name: Authenticated peer name.

    Returns:
        Task state dict.
    """
    parts = params.message.parts

    # Config-driven cap (async settings lookup); cannot live in the
    # Pydantic model because ``a2a.max_message_parts`` is mutable at
    # runtime via the settings service.
    max_parts = await _resolve_max_message_parts(app_state)
    if len(parts) > max_parts:
        logger.warning(
            A2A_JSONRPC_INVALID_PARAMS,
            reason="too many parts",
            count=len(parts),
            max=max_parts,
        )
        raise _A2AMethodError(
            JSONRPC_INVALID_PARAMS,
            f"Too many message parts (max {max_parts})",
        )

    text_parts = [p.text for p in parts if isinstance(p, A2ATextPart)]
    description = "\n".join(text_parts) or "A2A inbound task"

    task_engine = _require_task_engine(app_state)

    from uuid import uuid4 as _uuid4  # noqa: PLC0415

    from synthorg.core.enums import Priority, TaskType  # noqa: PLC0415
    from synthorg.engine.task_engine_models import (  # noqa: PLC0415
        CreateTaskData,
        CreateTaskMutation,
    )

    task_data = CreateTaskData(
        title=f"A2A: {description[:80]}",
        description=description,
        type=TaskType.ADMIN,
        priority=Priority.MEDIUM,
        project="a2a-inbound",
        created_by="a2a-gateway",
    )
    mutation = CreateTaskMutation(
        request_id=_uuid4().hex,
        requested_by=f"a2a-gateway:{peer_name}",
        task_data=task_data,
    )
    created = await task_engine.submit(mutation)

    logger.info(
        A2A_TASK_CREATED,
        task_id=created.id,
        peer_name=peer_name,
    )

    return {
        "id": created.id,
        "state": to_a2a(created.status).value,
    }


async def _handle_tasks_get(
    app_state: Any,
    params: A2ATaskGetParams,
    peer_name: str,
) -> dict[str, Any]:
    """Handle ``tasks/get`` -- retrieve task state.

    Args:
        app_state: Application state container.
        params: Typed ``tasks/get`` parameters.
        peer_name: Authenticated peer name.

    Returns:
        Task state dict.
    """
    task_engine = _require_task_engine(app_state)
    task = await task_engine.get(params.id)
    if task is None:
        raise _A2AMethodError(
            A2A_TASK_NOT_FOUND,
            "Task not found",
            http_status=404,
        )

    _validate_task_ownership(task, peer_name)

    return {
        "id": task.id,
        "state": to_a2a(task.status).value,
    }


async def _handle_tasks_cancel(
    app_state: Any,
    params: A2ATaskCancelParams,
    peer_name: str,
) -> dict[str, Any]:
    """Handle ``tasks/cancel`` -- cancel a running task.

    Args:
        app_state: Application state container.
        params: Typed ``tasks/cancel`` parameters.
        peer_name: Authenticated peer name.

    Returns:
        Updated task state dict.
    """
    task_engine = _require_task_engine(app_state)
    task = await task_engine.get(params.id)
    if task is None:
        raise _A2AMethodError(
            A2A_TASK_NOT_FOUND,
            "Task not found",
            http_status=404,
        )

    _validate_task_ownership(task, peer_name)

    from synthorg.core.enums import TaskStatus  # noqa: PLC0415

    terminal = {
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
    }
    if task.status in terminal:
        raise _A2AMethodError(
            A2A_TASK_NOT_CANCELABLE,
            "Task is in terminal state",
        )

    cancelled = await task_engine.cancel(params.id)

    logger.info(
        A2A_TASK_CANCELLED,
        task_id=params.id,
        peer_name=peer_name,
    )

    return {
        "id": cancelled.id,
        "state": to_a2a(cancelled.status).value,
    }
