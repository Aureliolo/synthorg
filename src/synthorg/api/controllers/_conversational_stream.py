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
import json as _json
from collections.abc import AsyncIterator

from synthorg.api.controllers._meta_chat_window import resolve_chat_snapshot_window
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.actor import (
    ActProgress,
    ConversationalActArgs,
    ConversationalActor,
)
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.models import ChatAnswerDelta, ChatQuery
from synthorg.meta.signals.service import SignalsService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_CHAT_STREAM_FAILED

logger = get_logger(__name__)


def _frame(event: str, payload: dict[str, object]) -> dict[str, str]:
    """Render an SSE frame with a JSON data body.

    Returns:
        The ``{event, data}`` mapping the SSE transport expects.
    """
    return {"event": event, "data": _json.dumps(payload)}


def _error_frame(exc: Exception) -> dict[str, str]:
    """Build an ``error`` frame that never leaks the exception message.

    Returns:
        An ``error`` frame carrying only the exception class name.
    """
    return _frame("error", {"error": f"Internal error: {type(exc).__name__}"})


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
        async for event in chat_backend.ask_stream(query, snapshot):
            if isinstance(event, ChatAnswerDelta):
                yield _frame("progress", {"delta": event.delta})
            else:
                yield _frame(
                    "complete",
                    {
                        "answer": event.answer,
                        "sources": list(event.sources),
                        "confidence": event.confidence,
                    },
                )
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
        async for event in actor.act_stream(args):
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


__all__ = ["act_progress_stream", "chat_answer_stream"]
