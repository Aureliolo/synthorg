"""Unit tests for the opt-in chat idempotency wrapper (BE#11).

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
            key=None,
            endpoint="/meta/chat",
            request_fingerprint="fp",
            build=_build,
        )
    assert calls == 1
    assert dumped["data"] == {"answer": "hi"}


def test_fingerprint_is_stable_and_payload_sensitive() -> None:
    a = ChatQuery(question="what is the budget?")
    a_again = ChatQuery(question="what is the budget?")
    b = ChatQuery(question="what is the runway?")
    assert chat_request_fingerprint(a) == chat_request_fingerprint(a_again)
    assert chat_request_fingerprint(a) != chat_request_fingerprint(b)
