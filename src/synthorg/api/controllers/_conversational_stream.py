# module-kind: code
"""SSE stream builders for the conversational streaming endpoints.

Kept out of ``conversational.py`` so the controller stays under its size
tier. Once the ``text/event-stream`` response headers are on the wire the
central RFC 9457 handler can no longer run, so a failure after stream
start can only surface as an in-stream ``event: error`` frame: both
builders trap non-cancellation failures and emit one. The frame schema
(``progress`` / ``complete`` / ``error``) matches the provider-pull SSE
convention the model-pull endpoint already uses.
"""

import asyncio
import contextlib
import json as _json
from collections.abc import AsyncIterator
from typing import Final

from synthorg.api._feature_gate import ensure_feature_enabled
from synthorg.api.controllers._meta_chat_org_state import resolve_chat_org_state
from synthorg.api.controllers._meta_chat_routing import chat_answer_payload
from synthorg.api.controllers._meta_chat_window import resolve_chat_snapshot_window
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError, ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.actor import (
    ActProgress,
    ConversationalActArgs,
    ConversationalActor,
)
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.models import ChatAnswerDelta, ChatQuery
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.state import MetaStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CHAT_DEPENDENCY_UNAVAILABLE,
    META_CHAT_STREAM_FAILED,
)

logger = get_logger(__name__)

# 5xx responses carry only a scrubbed fallback detail; below it the domain
# error's own message is client-safe (the RFC 9457 handler's rule).
_HTTP_SERVER_ERROR_FLOOR: Final[int] = 500


async def resolve_chat_stream_backends(
    app_state: AppState,
) -> tuple[ChiefOfStaffChat, SignalsService]:
    """Live-gate and resolve the explain-chat + signals backends, or 503.

    Runs before the SSE headers are on the wire, so a disabled feature or
    an unwired dependency still surfaces as a normal RFC 9457 body.

    Returns:
        The ``(chat_backend, signals_service)`` pair.

    Raises:
        ServiceUnavailableError: When the feature is off or a dependency
            is not wired.
    """
    await ensure_feature_enabled(
        app_state,
        "chief_of_staff",
        "explain_chat_enabled",
        feature_label="Chief of Staff chat",
    )
    meta = app_state.slice(MetaStateSlice)
    chat_backend = meta.chief_of_staff_chat
    if chat_backend is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="chief_of_staff_chat",
            hint="Register an LLM provider so the chat backend can be built.",
        )
        msg = (
            "Chief of Staff chat is not configured. Register an LLM "
            "provider so the chat backend can be built."
        )
        raise ServiceUnavailableError(msg)
    signals_service = meta.signals_service
    if signals_service is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="signals_service",
            hint="SignalsService must be wired during AppState startup.",
        )
        msg = "SignalsService is not configured; cannot build a snapshot."
        raise ServiceUnavailableError(msg)
    return chat_backend, signals_service


async def resolve_act_stream_actor(app_state: AppState) -> ConversationalActor:
    """Live-gate and resolve the conversational actor for streaming, or 503.

    The ``direct_mcp_enabled`` gate is re-checked per request, so the
    security kill-switch takes effect on the next request without a restart.

    Returns:
        The wired :class:`ConversationalActor`.

    Raises:
        ServiceUnavailableError: When acting is disabled or the actor is
            not wired.
    """
    await ensure_feature_enabled(
        app_state,
        "chief_of_staff",
        "direct_mcp_enabled",
        feature_label="Direct MCP acting",
    )
    actor = app_state.slice(MetaStateSlice).conversational_actor
    if actor is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="conversational_actor",
            hint=(
                "Set meta.chief_of_staff.direct_mcp_enabled, register an "
                "LLM provider, and enable the MCP self-consumer "
                "(security.mcp_self_consumer.mode=trust_scoped)."
            ),
        )
        msg = (
            "Direct MCP acting is not configured. Enable "
            "``meta.chief_of_staff.direct_mcp_enabled`` in settings, "
            "register an LLM provider, and set "
            "``security.mcp_self_consumer.mode`` to ``trust_scoped``."
        )
        raise ServiceUnavailableError(msg)
    return actor


def _frame(event: str, payload: dict[str, object]) -> dict[str, str]:
    """Render an SSE frame with a JSON data body.

    Returns:
        The ``{event, data}`` mapping the SSE transport expects.
    """
    return {"event": event, "data": _json.dumps(payload)}


def _error_frame(exc: Exception) -> dict[str, str]:
    """Build an ``error`` frame, mirroring the RFC 9457 handler's scrub.

    A typed :class:`DomainError` carries a client-safe detail, machine
    code, and retry semantics, so those cross the wire (message scrubbed
    to the 5xx-safe fallback for a 500-class error, exactly as the
    central handler would). Anything else stays opaque so an unexpected
    internal fault cannot leak its message once the SSE headers are on
    the wire and the central handler can no longer run.

    Returns:
        An ``error`` frame carrying the safe detail plus, for a domain
        error, its ``error_code`` / ``retryable`` / ``retry_after``.
    """
    if not isinstance(exc, DomainError):
        return _frame("error", {"error": f"Internal error: {type(exc).__name__}"})
    client_safe = exc.status_code < _HTTP_SERVER_ERROR_FLOOR
    payload: dict[str, object] = {
        "error": str(exc) if client_safe else exc.default_message,
        "error_code": exc.error_code.value,
        "retryable": exc.retryable,
    }
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, int) and retry_after > 0:
        payload["retry_after"] = retry_after
    return _frame("error", payload)


async def chat_answer_stream(
    *,
    app_state: AppState,
    chat_backend: ChiefOfStaffChat,
    signals_service: SignalsService,
    question: NotBlankStr,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE frames for a streamed free-form Chief-of-Staff answer.

    A ``progress`` frame per token delta, then one ``complete`` frame
    carrying the assembled answer / sources / confidence. The question is
    fenced inside ``ask_stream`` itself, so it is passed through raw here.

    Yields:
        SSE frames, terminating on ``complete`` or (on failure) ``error``.

    Raises:
        CancelledError: Propagated on client disconnect so the stream
            tears down promptly.
    """
    query = ChatQuery(question=question)
    try:
        snapshot = await signals_service.get_org_snapshot(
            since=app_state.clock.now() - await resolve_chat_snapshot_window(app_state),
        )
        org_state = await resolve_chat_org_state(app_state)
        # aclosing() guarantees the generator's finally runs when the
        # consumer disconnects mid-stream (the send() raises), so the
        # cost_recording_scope inside ask_stream tears down synchronously
        # instead of waiting on async-generator GC.
        async with contextlib.aclosing(
            chat_backend.ask_stream(query, snapshot, org_state=org_state)
        ) as stream:
            async for event in stream:
                if isinstance(event, ChatAnswerDelta):
                    yield _frame("progress", {"delta": event.delta})
                else:
                    yield _frame("complete", chat_answer_payload(event))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            META_CHAT_STREAM_FAILED,
            surface="chat",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        yield _error_frame(exc)


async def act_progress_stream(
    *,
    actor: ConversationalActor,
    args: ConversationalActArgs,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE frames for a streamed direct MCP action.

    A ``progress`` frame per completed action turn (carrying the tools it
    requested), then one ``complete`` frame carrying the full
    :class:`~synthorg.meta.chief_of_staff.actor.ConversationalActResult`.

    Yields:
        SSE frames, terminating on ``complete`` or (on failure) ``error``.

    Raises:
        CancelledError: Propagated on client disconnect so the running
            action is cancelled.
    """
    try:
        # aclosing() guarantees act_stream's finally (which cancels the
        # child action task doing the real governed tool execution) runs
        # promptly when the consumer disconnects, rather than depending
        # on async-generator GC to reclaim it.
        async with contextlib.aclosing(actor.act_stream(args)) as stream:
            async for event in stream:
                if isinstance(event, ActProgress):
                    payload: dict[str, object] = {
                        "turn": event.turn,
                        "tools": list(event.tools),
                    }
                    yield _frame("progress", payload)
                else:
                    yield _frame("complete", event.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            META_CHAT_STREAM_FAILED,
            surface="act",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        yield _error_frame(exc)


__all__ = [
    "act_progress_stream",
    "chat_answer_stream",
    "resolve_act_stream_actor",
    "resolve_chat_stream_backends",
]
