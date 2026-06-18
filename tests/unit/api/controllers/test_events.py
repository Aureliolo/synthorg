"""Tests for EventStreamController."""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_READ_HEADERS = make_auth_headers("observer")

# Shared malformed session-id matrix used by both the SSE stream and
# the polling interrupts endpoint. Any value here must be rejected by
# the shared ``_SESSION_ID_PATTERN`` regex; keeping the list in one
# place prevents the two endpoints from drifting apart.
_MALFORMED_SESSION_IDS: tuple[tuple[str, str], ...] = (
    ("../etc/passwd", "path_traversal"),
    ("session id", "whitespace"),
    ("session/with/slash", "slash"),
    ("session\nbreak", "newline"),
    ("x" * 129, "too_long"),
    ("s$dollar", "special_char"),
)
_MALFORMED_SESSION_ID_VALUES = tuple(v for v, _ in _MALFORMED_SESSION_IDS)
_MALFORMED_SESSION_ID_IDS = tuple(i for _, i in _MALFORMED_SESSION_IDS)


@pytest.mark.unit
class TestEventStreamSSE:
    async def test_stream_requires_session_id(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/events/stream",
            headers=_READ_HEADERS,
        )
        # Missing required session_id query param -> 400
        assert resp.status_code == 400

    @pytest.mark.parametrize(
        "bad_id",
        _MALFORMED_SESSION_ID_VALUES,
        ids=_MALFORMED_SESSION_ID_IDS,
    )
    async def test_stream_rejects_malformed_session_id(
        self,
        async_test_client: LoopAsyncClient,
        bad_id: str,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/events/stream",
            params={"session_id": bad_id},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 400, (
            f"session_id={bad_id!r} should be rejected, got {resp.status_code}"
        )

    @pytest.mark.parametrize(
        "good_id",
        [
            "s-1",
            "session_id_123",
            "A-Z_0-9abc",
            "x" * 128,  # exact length cap
            "single",
        ],
        ids=[
            "short_dash",
            "underscore_digits",
            "mixed_case",
            "exact_cap",
            "single_word",
        ],
    )
    async def test_interrupts_accepts_valid_session_id(
        self,
        async_test_client: LoopAsyncClient,
        good_id: str,
    ) -> None:
        """The regex happy path must *not* reject well-formed session ids.

        Tested via ``/api/v1/interrupts`` (a non-streaming GET) rather
        than ``/events/stream`` so the request unblocks promptly -- the
        SSE stream holds the connection open indefinitely once the
        validator admits the id.
        """
        resp = await async_test_client.get(
            "/api/v1/interrupts",
            params={"session_id": good_id},
            headers=_READ_HEADERS,
        )
        # Lock the success path explicitly -- asserting != 400 would
        # happily accept a 500 or 422.
        assert resp.status_code == 200, (
            f"session_id={good_id!r} should return 200, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.parametrize(
        "bad_id",
        _MALFORMED_SESSION_ID_VALUES,
        ids=_MALFORMED_SESSION_ID_IDS,
    )
    async def test_interrupts_rejects_malformed_session_id(
        self,
        async_test_client: LoopAsyncClient,
        bad_id: str,
    ) -> None:
        # Parametrized to mirror the coverage of the streams variant:
        # the regex gate must apply identically to both endpoints.
        resp = await async_test_client.get(
            "/api/v1/interrupts",
            params={"session_id": bad_id},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 400, (
            f"session_id={bad_id!r} should be rejected, got {resp.status_code}"
        )
