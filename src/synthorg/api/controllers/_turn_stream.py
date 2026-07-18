# module-kind: service
"""Streaming half of the unified conversational turn.

``POST /meta/chat/turn/stream`` streams an EXPLAIN answer token-by-token so a
question feels live, then delivers any multi-voice chime-ins as they resolve.
Only a read streams: every side-effecting capability (propose / group / act /
charter) is *deferred* here with a single ``deferred`` frame carrying the
resolved intent, and the client re-issues it against the buffered, idempotent
``POST /meta/chat/turn`` with that intent as an explicit override. An acting
turn therefore never runs on the streaming path, so a dropped SSE connection
can never re-execute its tools, the invariant the buffered endpoint exists to
hold.

SSE carve-out (mirrors the provider-pull stream): the response headers are on
the wire before the first frame, so an error after stream start cannot reach the
RFC 9457 handler; it is emitted as an in-stream ``error`` frame instead.
"""

import asyncio
import json
from collections.abc import AsyncIterator

from synthorg.api.controllers._provider_helpers import sse_error
from synthorg.api.controllers._turn_dispatch import (
    TurnRequest,
    TurnResult,
    prepare_explain_context,
    resolve_chime_ins,
)
from synthorg.api.controllers._turn_intent import resolve_turn_intent
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.chief_of_staff.intent_router import IntentOutcome, TurnIntent
from synthorg.meta.chief_of_staff.models import ChatAnswerComplete, ChatResponse
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import COS_CHAT_FAILED

logger = get_logger(__name__)


def _frame(event: str, payload: dict[str, object]) -> dict[str, str]:
    """Build one SSE frame with a JSON-encoded data field.

    Returns:
        A ``{"event", "data"}`` mapping in the shape ``ServerSentEvent`` emits.
    """
    return {"event": event, "data": json.dumps(payload)}


async def _explain_frames(
    app_state: AppState, *, data: TurnRequest, outcome: IntentOutcome
) -> AsyncIterator[dict[str, str]]:
    """Stream an EXPLAIN answer's deltas, its terminal result, then chime-ins.

    Yields:
        A ``delta`` frame per content chunk, one ``complete`` frame carrying the
        full :class:`TurnResult`, then a ``chime`` frame per resolved specialist
        chime-in (delivered after the answer so a slow chime never delays it).
    """
    ctx = await prepare_explain_context(app_state, body=data.message)
    answer: ChatResponse | None = None
    async for event in ctx.chat_backend.ask_stream(
        ctx.query, ctx.snapshot, org_state=ctx.org_state
    ):
        if isinstance(event, ChatAnswerComplete):
            answer = ChatResponse(
                answer=event.answer,
                sources=event.sources,
                cited_records=event.cited_records,
                confidence=event.confidence,
            )
            result = TurnResult(
                intent=TurnIntent.EXPLAIN,
                intent_reason=outcome.reason,
                intent_confidence=outcome.confidence,
                answer=answer,
            )
            yield {"event": "complete", "data": result.model_dump_json()}
        else:
            yield _frame("delta", {"delta": event.delta})
    if answer is not None:
        for chime in await resolve_chime_ins(
            app_state, question=data.message, answer=answer.answer
        ):
            yield {"event": "chime", "data": chime.model_dump_json()}


async def stream_turn_events(
    app_state: AppState, *, data: TurnRequest
) -> AsyncIterator[dict[str, str]]:
    """Yield the SSE frames for one unified turn.

    Classifies the turn once. An EXPLAIN turn streams (see
    :func:`_explain_frames`); any other intent yields a single ``deferred``
    frame so the client re-issues it against the buffered idempotent endpoint.
    A failure after the stream opened is surfaced as an in-stream ``error``
    frame, since the response headers are already on the wire.

    Yields:
        SSE frame mappings in arrival order.

    Raises:
        CancelledError: Propagated on a client disconnect so the stream tears
            down promptly rather than being converted to an ``error`` frame.
    """
    try:
        outcome = await resolve_turn_intent(
            app_state,
            body=data.message,
            override=data.intent_override,
            conversation_id=data.conversation_id,
        )
        if outcome.intent is not TurnIntent.EXPLAIN:
            yield _frame(
                "deferred",
                {
                    "intent": outcome.intent.value,
                    "intent_reason": outcome.reason.value,
                    "intent_confidence": outcome.confidence,
                },
            )
            return
        async for frame in _explain_frames(app_state, data=data, outcome=outcome):
            yield frame
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; SSE cannot raise post-header
        reraise_critical(exc)
        logger.warning(
            COS_CHAT_FAILED,
            reason="turn_stream_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        yield _frame("error", sse_error(f"Internal error: {type(exc).__name__}"))


__all__ = ["stream_turn_events"]
