# module-kind: controller
"""Unified conversational turn controller.

One endpoint, ``POST /meta/chat/turn``, behind which an operator's message is
classified and dispatched to the right capability (explain / propose / group /
act / charter). Replaces the five mode endpoints as the single "talk to your
org" surface; the capability services and their gates are unchanged, so the
per-capability opt-ins, idempotency, and state machines still hold.
"""

from litestar import Controller, post
from litestar.datastructures import State

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
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_org_mutation, require_read_access
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
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.chat.turn", key="user"),
        ],
    )
    async def turn(
        self,
        data: TurnRequest,
        state: State,
        idempotency_key: ChatIdempotencyKeyHeader = None,
    ) -> ApiResponse[TurnResult]:
        """Classify and dispatch one unified conversational turn.

        Gated live on ``meta.chief_of_staff.turn_router_enabled`` so the
        surface toggles without a restart. The dispatched capability then
        re-checks its own gate, so an act turn while ``direct_mcp_enabled`` is
        off fails closed rather than being answered as a read.

        Returns:
            ``ApiResponse[TurnResult]`` carrying the resolved intent and its
            single capability payload.

        Raises:
            ServiceUnavailableError: When the unified surface, or the
                dispatched capability, is not configured.
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
            result = await dispatch_turn(app_state, data=data, actor_id=actor.actor_id)
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
