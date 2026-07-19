# module-kind: controller
"""Unified conversational turn controller.

One endpoint, ``POST /meta/chat/turn``, behind which an operator's message is
classified and dispatched to the right capability (explain / propose / group /
act / charter). Replaces the five mode endpoints as the single "talk to your
org" surface; the capability services and their gates are unchanged, so the
per-capability opt-ins, idempotency, and state machines still hold.
"""

from litestar import Controller, Request, post
from litestar.datastructures import State
from litestar.response import ServerSentEvent

from synthorg.api._feature_gate import ensure_feature_enabled
from synthorg.api.controllers._chat_idempotency import (
    ChatIdempotencyKeyHeader,
    chat_request_fingerprint,
    run_chat_idempotent,
)
from synthorg.api.controllers._turn_dispatch import (
    TurnRequest,
    TurnResult,
    dispatch_turn,
)
from synthorg.api.controllers._turn_stream import stream_turn_events
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import assert_org_mutation, require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.actor_context import require_actor


class TurnController(Controller):
    """The unified conversational turn API endpoint."""

    path = "/meta/chat"
    tags = ["meta"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/turn",
        # One turn dispatches to a capability that may append conversation
        # turns or park an approval, but creates no addressable resource at
        # this URL, so 200 (not 201) is correct.
        status_code=200,
        # No blanket mutation guard: EXPLAIN is a read any authenticated actor
        # may run, so mutation permission is enforced inside dispatch once a
        # side-effecting intent is resolved (see ``require_mutation`` below).
        guards=[per_op_rate_limit_from_policy("meta.chat.turn", key="user")],
    )
    async def turn(
        self,
        data: TurnRequest,
        state: State,
        request: Request[object, object, State],
        idempotency_key: ChatIdempotencyKeyHeader = None,
    ) -> ApiResponse[TurnResult]:
        """Classify and dispatch one unified conversational turn.

        Gated live on ``meta.chief_of_staff.turn_router_enabled`` so the
        surface toggles without a restart. Read-only actors may run EXPLAIN;
        mutation permission is enforced only once a side-effecting intent is
        resolved. The dispatched capability then re-checks its own gate, so an
        act turn while ``direct_mcp_enabled`` is off fails closed.

        Returns:
            ``ApiResponse[TurnResult]`` carrying the resolved intent and its
            single capability payload.

        Raises:
            ServiceUnavailableError: When the unified surface, or the
                dispatched capability, is not configured.
            PermissionDeniedException: When a side-effecting intent is resolved
                for an actor without org-mutation permission.
        """
        app_state = state.app_state
        await ensure_feature_enabled(
            app_state,
            "chief_of_staff",
            "turn_router_enabled",
            feature_label="Unified chat",
        )
        actor = require_actor()

        async def _build() -> ApiResponse[TurnResult]:
            result = await dispatch_turn(
                app_state,
                data=data,
                actor_id=actor.actor_id,
                require_mutation=lambda: assert_org_mutation(request),
            )
            return ApiResponse[TurnResult](data=result)

        dumped = await run_chat_idempotent(
            app_state,
            scope="meta.chat.turn",
            actor_id=actor.actor_id,
            key=idempotency_key,
            endpoint="/meta/chat/turn",
            request_fingerprint=chat_request_fingerprint(data),
            build=_build,
        )
        return ApiResponse[TurnResult].model_validate(dumped)

    @post(
        "/turn/stream",
        media_type="text/event-stream",
        # No mutation guard: the stream only classifies + streams a read; every
        # side-effecting intent defers to the buffered ``/turn`` (which enforces
        # mutation), so a read-only actor may stream an EXPLAIN answer. Its own
        # rate-limit bucket keeps classification from consuming the execution
        # budget on the buffered endpoint.
        guards=[per_op_rate_limit_from_policy("meta.chat.turn_stream", key="user")],
    )
    async def turn_stream(
        self,
        data: TurnRequest,
        state: State,
    ) -> ServerSentEvent:
        """Stream an EXPLAIN turn; defer every other intent to the buffered POST.

        Gated live on ``turn_router_enabled`` like the buffered turn. An EXPLAIN
        turn streams token-by-token then delivers chime-ins; any side-effecting
        intent yields one ``deferred`` frame and never executes here, so an
        acting turn always runs on the idempotent buffered endpoint (a dropped
        stream can never re-run its tools). Read-only actors may stream a read;
        mutation permission is enforced on the buffered reissue. No idempotency
        key: a read stream caches nothing, and the reissue carries its own.

        Returns:
            An SSE stream of ``delta`` / ``complete`` / ``chime`` frames for an
            EXPLAIN turn, or a single ``deferred`` frame otherwise.

        Raises:
            ServiceUnavailableError: When the unified surface is disabled.
        """
        app_state = state.app_state
        await ensure_feature_enabled(
            app_state,
            "chief_of_staff",
            "turn_router_enabled",
            feature_label="Unified chat",
        )
        return ServerSentEvent(content=stream_turn_events(app_state, data=data))
