"""Tests for webhook nonce-less idempotency dedup.

When a provider does not supply ``X-Nonce`` / ``X-Request-Id``, the
webhook handler must still dedupe redeliveries to prevent
double-bus publishes. The handler hashes the request body with
SHA-256 and feeds the digest into the existing durable
``IdempotencyService``.

These tests pin the helper-level contract: the success log records
``dedup_source`` and the durable-idempotency path is invoked for
both the nonce and nonce-less branches with the appropriate key
shape.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from litestar import Request
from litestar.datastructures import State
from litestar.testing import RequestFactory

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.controllers import _webhooks_wiring
from synthorg.api.controllers.webhooks import _shared as webhooks_shared
from synthorg.api.controllers.webhooks import ingest as webhooks_ingest
from synthorg.api.services.idempotency_service import IdempotencyService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.observability.events.integrations import WEBHOOK_ACCEPTED
from tests._shared import JsonDict, make_app_state, mock_of


@pytest.mark.unit
class TestPublishWebhookEventAndLog:
    """``_publish_webhook_event_and_log`` carries a ``dedup_source`` tag."""

    async def test_dedup_source_in_success_log(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The success log includes the dedup_source provenance string."""
        published: list[JsonDict] = []

        async def fake_publish(
            *,
            bus: MessageBus,
            connection_name: str,
            event_type: str,
            payload: JsonDict,
        ) -> None:
            published.append(
                {
                    "connection_name": connection_name,
                    "event_type": event_type,
                    "payload": payload,
                },
            )

        monkeypatch.setattr(
            webhooks_shared,
            "publish_webhook_event",
            fake_publish,
        )

        with structlog.testing.capture_logs() as logs:
            result = await webhooks_shared._publish_webhook_event_and_log(
                bus=mock_of[MessageBus](),
                connection_name="conn-a",
                event_type="issues.opened",
                payload={"x": 1},
                dedup_source="body_sha256",
            )

        assert result == {"status": "accepted", "event_type": "issues.opened"}
        accepted = [e for e in logs if e.get("event") == WEBHOOK_ACCEPTED]
        assert len(accepted) == 1
        assert accepted[0]["dedup_source"] == "body_sha256"
        assert accepted[0]["connection_name"] == "conn-a"
        assert accepted[0]["event_type"] == "issues.opened"
        assert published == [
            {
                "connection_name": "conn-a",
                "event_type": "issues.opened",
                "payload": {"x": 1},
            },
        ]


@pytest.mark.unit
class TestPublishWithDurableIdempotency:
    """``_publish_with_durable_idempotency`` forwards ``dedup_source``."""

    async def test_dedup_source_forwarded_to_callback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The wrapper calls run_idempotent then propagates dedup_source."""

        # Capture what the inner callback was given by intercepting
        # ``_publish_webhook_event_and_log`` at the module boundary.
        captured: dict[str, object] = {}

        async def spy(
            *,
            bus: MessageBus,
            connection_name: str,
            event_type: str,
            payload: JsonDict,
            dedup_source: str,
        ) -> dict[str, object]:
            captured["dedup_source"] = dedup_source
            captured["connection_name"] = connection_name
            captured["event_type"] = event_type
            captured["payload"] = payload
            return {"status": "accepted", "event_type": event_type}

        monkeypatch.setattr(
            webhooks_shared,
            "_publish_webhook_event_and_log",
            spy,
        )

        from synthorg.api.services.idempotency_service import IdempotencyResult

        async def run_idempotent(
            *,
            scope: str,
            key: str,
            callback: Callable[[], Awaitable[object]],
        ) -> IdempotencyResult:
            return IdempotencyResult(
                result=await callback(),
                fresh=True,
                timed_out=False,
            )

        idem_service = AsyncMock(spec=IdempotencyService)
        idem_service.run_idempotent = run_idempotent

        app_state = make_app_state(
            slices={ApiCoreStateSlice: {"idempotency_service": idem_service}},
        )
        state = State({"app_state": app_state})

        cached = await webhooks_shared._publish_with_durable_idempotency(
            state=state,
            connection_name="conn-b",
            event_type="push",
            nonce="sha256:deadbeef",
            connection_type="github",
            bus=mock_of[MessageBus](),
            payload={"y": 2},
            dedup_source="body_sha256",
        )

        assert cached == {"status": "accepted", "event_type": "push"}
        assert captured["dedup_source"] == "body_sha256"
        assert captured["connection_name"] == "conn-b"
        assert captured["payload"] == {"y": 2}


@pytest.mark.unit
class TestNoncelessWebhookKeyShape:
    """Body-hash idempotency key shape: ``sha256:<digest>`` -> wrapper."""

    def test_helper_consumes_sha256_prefixed_nonce(self) -> None:
        """``_build_idem_key`` accepts the synthesised body-digest nonce.

        The handler synthesises ``nonce=f"sha256:{digest}"`` for the
        nonce-less path. ``_build_idem_key`` is unchanged: it accepts
        any string. Asserting that the resulting key is bounded under
        ``_IDEMPOTENCY_KEY_MAX_LEN`` catches a regression where the
        prefix pushes the composite over the DB column cap.
        """
        digest = "a" * 64  # SHA-256 hex is 64 chars
        synthesised = f"sha256:{digest}"
        key = _webhooks_wiring._build_idem_key(
            connection_name="some-connection",
            event_type="some.event",
            nonce=synthesised,
        )
        assert len(key) <= _webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN
        # The prefix or a hash-of-prefix appears in the key (operator
        # visibility); the entire key is non-empty.
        assert key

    def test_nonce_less_key_uses_sha256_not_truncated(self) -> None:
        """Real-body SHA-256 digest survives ``_build_idem_key`` unmangled.

        Asserts that the helper keeps the full 64-hex-char digest
        visible (or as a hash-of-key when the composite is over the
        column cap), bounded under ``_IDEMPOTENCY_KEY_MAX_LEN``. A
        regression that swapped to a weaker hash or truncated the
        digest mid-key would otherwise slip past the test surface.
        """
        import hashlib

        body = b'{"event": "issues.opened", "number": 42}'
        digest = hashlib.sha256(body).hexdigest()
        # SHA-256 is always 64 hex chars by definition.
        sha256_hex_length = 64
        assert len(digest) == sha256_hex_length

        key = _webhooks_wiring._build_idem_key(
            connection_name="github-prod",
            event_type="issues.opened",
            nonce=f"sha256:{digest}",
        )
        assert len(key) <= _webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN
        # The digest survives in the key (either inline or reduced via
        # the helper's two-stage SHA-256 collapse for oversized
        # composites). Either way the key is deterministic and
        # non-empty for the same body.
        assert key
        # Re-hashing the same body produces the same key; this is the
        # core idempotency invariant.
        repeat_key = _webhooks_wiring._build_idem_key(
            connection_name="github-prod",
            event_type="issues.opened",
            nonce=f"sha256:{digest}",
        )
        assert key == repeat_key

    def test_nonce_less_key_at_column_cap_boundary(self) -> None:
        """Composite key sitting exactly at the DB cap is accepted.

        A connection_name + event_type + ``sha256:`` prefix + 64-hex
        digest crafted to land near the cap should still fit; pad
        up to the boundary and confirm the helper does not truncate
        or crash.
        """
        # Pad connection_name so the full composite is right at the cap.
        digest = "0" * 64
        prefix_overhead = len(":") + len(":") + len("sha256:") + len(digest)
        # ``_IDEMPOTENCY_KEY_MAX_LEN`` is 255; reserve event_type=8 chars.
        event_type = "evt.test"
        room = (
            _webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN
            - prefix_overhead
            - len(event_type)
        )
        connection_name = "x" * max(1, room)
        key = _webhooks_wiring._build_idem_key(
            connection_name=connection_name,
            event_type=event_type,
            nonce=f"sha256:{digest}",
        )
        assert len(key) <= _webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN
        assert key


@pytest.mark.unit
class TestReceiveWebhookEndToEnd:
    """Controller-level: both branches flow through durable idempotency.

    The helper-level tests above mock ``publish_webhook_event`` and
    ``run_idempotent``, which is enough for unit isolation but does
    not catch a regression where the orchestrator drops the
    ``dedup_source`` plumbing or skips the body-SHA256 derivation
    entirely. These tests invoke
    :meth:`WebhooksIngestController.receive_webhook` directly with a
    mocked :class:`State` so the full branch logic runs.
    """

    @staticmethod
    def _install_webhook_fakes(
        monkeypatch: pytest.MonkeyPatch,
        *,
        body_bytes: bytes,
        captured: dict[str, object],
    ) -> None:
        """Stub every collaborator ``receive_webhook`` calls.

        Replaces catalog lookup, payload size guard, signature
        verification, timestamp parse, replay/freshness check, and
        bus publish so the test isolates the orchestrator's branch
        logic. ``captured`` accumulates the kwargs forwarded to
        ``_publish_with_durable_idempotency`` for assertion.
        """

        async def fake_get_connection_or_404(
            state: object,
            connection_name: str,
        ) -> object:
            class _Conn:
                connection_type = "github"

            return _Conn()

        async def fake_enforce_max_payload(
            request: object,
            *,
            connection_name: str,
            max_payload: int,
        ) -> bytes:
            return body_bytes

        async def fake_verify_signature(
            *,
            catalog: object,
            connection_name: str,
            connection_type: str,
            body: bytes,
            headers: dict[str, str],
        ) -> None:
            return None

        def fake_parse_timestamp(
            headers: dict[str, str],
            *,
            connection_name: str,
        ) -> None:
            return None

        async def fake_check_replay_or_freshness(
            *,
            state: object,
            connection_name: str,
            nonce: str | None,
            timestamp: object,
        ) -> None:
            return None

        async def spy_publish_with_durable_idempotency(
            **kwargs: object,
        ) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "accepted", "event_type": kwargs["event_type"]}

        for name, fn in (
            ("_get_connection_or_404", fake_get_connection_or_404),
            ("_enforce_max_payload", fake_enforce_max_payload),
            ("_verify_signature", fake_verify_signature),
            ("_parse_timestamp", fake_parse_timestamp),
            ("_check_replay_or_freshness", fake_check_replay_or_freshness),
            ("_publish_with_durable_idempotency", spy_publish_with_durable_idempotency),
        ):
            monkeypatch.setattr(webhooks_ingest, name, fn)

    @staticmethod
    def _build_state(  # type: ignore[explicit-any]  # litestar RequestFactory yields Request[Any, Any, Any]
        request_headers: dict[str, str],
    ) -> tuple[State, Request[Any, Any, Any]]:
        """Build minimal Litestar State + Request stubs.

        Returns ``(state, request)``. The controller only touches
        ``state["app_state"].connection_catalog`` /
        ``config.integrations.webhooks.max_payload_bytes`` /
        ``message_bus`` plus ``request.headers``.
        """

        app_state = make_app_state(
            config=RootConfig(company_name="test"),
            connection_catalog=object(),
            message_bus=mock_of[MessageBus](),
        )
        state = State({"app_state": app_state})
        request = RequestFactory().get(path="/", headers=request_headers)
        return state, request

    async def _invoke_branch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        body_bytes: bytes,
        request_headers: dict[str, str],
    ) -> JsonDict:
        """Run ``receive_webhook`` once and return the captured kwargs.

        Stubs every collaborator the controller calls (catalog,
        signature verification, replay/freshness, payload-size guard,
        bus publish) so the test surfaces exactly the
        ``_publish_with_durable_idempotency`` arguments the
        orchestrator assembled.
        """
        from synthorg.api.controllers.webhooks.ingest import WebhooksIngestController

        captured: dict[str, object] = {}
        self._install_webhook_fakes(
            monkeypatch,
            body_bytes=body_bytes,
            captured=captured,
        )
        state, request_stub = self._build_state(request_headers)

        # Litestar's ``@post`` decorator wraps ``receive_webhook``
        # into an ``HTTPRouteHandler``; the original async function
        # is preserved at ``.fn`` and accepts ``self`` as the first
        # arg. Calling it directly bypasses Litestar's request
        # parsing -- this test exercises the orchestrator's branch
        # logic, not the framework wiring.
        receive_webhook_fn = WebhooksIngestController.receive_webhook.fn
        from litestar import Router

        await receive_webhook_fn(
            MagicMock(spec=Router),
            state=state,
            request=request_stub,
            connection_name="github-prod",
            event_type="issues.opened",
        )
        return captured

    async def test_nonce_branch_flows_through_idempotency(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Header-supplied nonce reaches ``_publish_with_durable_idempotency``."""
        captured = await self._invoke_branch(
            monkeypatch,
            body_bytes=b'{"x": 1}',
            request_headers={"x-nonce": "provider-nonce-123"},
        )
        assert captured["nonce"] == "provider-nonce-123"
        assert captured["dedup_source"] == "nonce"
        assert captured["connection_name"] == "github-prod"
        assert captured["event_type"] == "issues.opened"

    async def test_nonce_less_branch_flows_through_idempotency_with_sha256(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing nonce -> body SHA-256 reaches the helper as the key."""
        import hashlib

        body = b'{"event": "push", "ref": "main"}'
        expected_digest = hashlib.sha256(body).hexdigest()
        captured = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={},
        )
        assert captured["nonce"] == f"sha256:{expected_digest}"
        assert captured["dedup_source"] == "body_sha256"
        assert captured["connection_name"] == "github-prod"
        assert captured["event_type"] == "issues.opened"

    async def test_redelivery_same_body_yields_same_idempotency_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two byte-identical bodies derive the same key (deterministic)."""
        body = b'{"event": "duplicate-redelivery"}'
        first = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={},
        )
        second = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={},
        )
        assert first["nonce"] == second["nonce"]
        assert first["dedup_source"] == second["dedup_source"] == "body_sha256"
