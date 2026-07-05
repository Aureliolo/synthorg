"""Unit tests for the opt-in chat idempotency wrapper.

``run_chat_idempotent`` delegates the fresh/cached/fingerprint semantics
to :class:`IdempotencyService` (covered in ``tests/unit/idempotency``);
this file covers the wrapper's own logic: the opt-out path (no key runs
the build directly without touching the idempotency service) and the
fingerprint helper's stability.
"""

from types import SimpleNamespace
from typing import cast

import pytest
from typeguard import suppress_type_checks

from synthorg.api.controllers._chat_idempotency import (
    chat_request_fingerprint,
    run_chat_idempotent,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.state import AppState
from synthorg.engine.chat_action import ChatActionResult
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.meta.chief_of_staff.actor import ConversationalActResult
from synthorg.meta.chief_of_staff.models import ChatQuery

pytestmark = pytest.mark.unit


async def test_no_key_runs_build_once_without_idempotency_service() -> None:
    """A caller that omits the key runs the build directly.

    The dummy app_state has no idempotency service; reaching for one
    would raise, so a clean run proves the opt-out path never touches it.
    """
    calls = 0

    async def _build() -> ApiResponse[dict[str, object]]:
        nonlocal calls
        calls += 1
        return ApiResponse[dict[str, object]](data={"answer": "hi"})

    dummy = cast("AppState", SimpleNamespace())
    with suppress_type_checks():
        dumped = await run_chat_idempotent(
            dummy,
            scope="meta.chat",
            actor_id="user-1",
            key=None,
            endpoint="/meta/chat",
            request_fingerprint="fp",
            build=_build,
        )
    assert calls == 1
    assert dumped["data"] == {"answer": "hi"}


async def test_dump_excludes_nested_computed_fields_so_replay_revalidates() -> None:
    """The cached dump omits computed fields at every level, not just the top.

    ``ConversationalActResult.action`` is a ``ChatActionResult`` whose
    ``parked`` is a computed field; a top-level-only exclusion would leave
    ``action.parked`` in the stored JSON and ``model_validate`` would reject
    it under ``extra="forbid"`` on replay.
    """
    result = ConversationalActResult(
        agent_id="agent-cfo",
        agent_name="Casey",
        conversation_id="conv-1",
        action=ChatActionResult(
            termination_reason=TerminationReason.COMPLETED,
            final_message="Done.",
        ),
    )

    async def _build() -> ApiResponse[ConversationalActResult]:
        return ApiResponse[ConversationalActResult](data=result)

    dummy = cast("AppState", SimpleNamespace())
    with suppress_type_checks():
        dumped = await run_chat_idempotent(
            dummy,
            scope="meta.chat.act",
            actor_id="user-1",
            key=None,
            endpoint="/meta/chat/act",
            request_fingerprint="fp",
            build=_build,
        )

    # Top-level (ApiResponse.success) and nested (action.parked) computed
    # fields are both stripped from the stored payload.
    assert "success" not in dumped
    data = cast("dict[str, object]", dumped["data"])
    action = cast("dict[str, object]", data["action"])
    assert "parked" not in action
    # The stored dump round-trips back through the frozen extra="forbid"
    # models without a ValidationError.
    restored = ApiResponse[ConversationalActResult].model_validate(dumped)
    assert restored.data is not None
    assert restored.data.action.parked is False


def test_fingerprint_is_stable_and_payload_sensitive() -> None:
    a = ChatQuery(question="what is the budget?")
    a_again = ChatQuery(question="what is the budget?")
    b = ChatQuery(question="what is the runway?")
    assert chat_request_fingerprint(a) == chat_request_fingerprint(a_again)
    assert chat_request_fingerprint(a) != chat_request_fingerprint(b)
