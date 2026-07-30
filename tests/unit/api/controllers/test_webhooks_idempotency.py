"""Tests for webhook delivery-identity dedup.

A delivery is identified by the connection it addressed and the bytes it
carried, and by nothing else: the body is the only part of the request a
verifier inspects at all, and the connection is what keeps two connections sent
the same bytes from suppressing each other. Both dedup gates take that key, so these
tests pin what it does and does not include, particularly the two attacker-chosen
inputs deliberately left out of it: any header id, and the URL ``event_type``.
"""

import hashlib
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
from synthorg.api.controllers._webhooks_wiring import build_delivery_key
from synthorg.api.controllers.webhooks import _shared as webhooks_shared
from synthorg.api.controllers.webhooks import ingest as webhooks_ingest
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.idempotency import IdempotencyService
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

        from synthorg.idempotency import IdempotencyResult

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
            delivery_key="6:conn-b:sha256:deadbeef",
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
class TestDeliveryKeyIdentity:
    """The delivery key is the connection and the signed bytes, nothing else.

    Both dedup gates take this one key, so what it does and does not include is
    the whole dedup contract. The ``event_type`` exclusion is the load-bearing
    part: it comes from the URL and no verifier signs the path.
    """

    def test_the_same_body_to_a_different_event_type_keys_the_same(self) -> None:
        # The attack this closes: one captured signed body, posted to a second
        # event name, used to mint a second verified publish because the durable
        # key included the name from the URL.
        body = b'{"action": "opened"}'
        first = _webhooks_wiring.build_delivery_key(
            connection_name="github-prod",
            body=body,
        )
        assert first == _webhooks_wiring.build_delivery_key(
            connection_name="github-prod",
            body=body,
        )

    def test_the_same_body_to_a_different_connection_keys_differently(self) -> None:
        # The other direction: two connections can legitimately be sent the same
        # bytes, and the first must not suppress the second.
        body = b'{"action": "opened"}'
        assert _webhooks_wiring.build_delivery_key(
            connection_name="github-prod",
            body=body,
        ) != _webhooks_wiring.build_delivery_key(
            connection_name="github-staging",
            body=body,
        )

    def test_a_different_body_keys_differently(self) -> None:
        assert _webhooks_wiring.build_delivery_key(
            connection_name="c",
            body=b'{"n": 1}',
        ) != _webhooks_wiring.build_delivery_key(
            connection_name="c",
            body=b'{"n": 2}',
        )

    def test_the_connection_name_cannot_be_confused_with_the_digest(self) -> None:
        # Length-prefixed, so a name containing the separator cannot shift the
        # boundary and collide two distinct deliveries onto one key.
        body = b"x"
        assert _webhooks_wiring.build_delivery_key(
            connection_name="a:b",
            body=body,
        ) != _webhooks_wiring.build_delivery_key(
            connection_name="a",
            body=body,
        )


@pytest.mark.unit
class TestDurableKeyBounding:
    """``_build_idem_key`` bounds a delivery key to the DB column cap."""

    def test_a_normal_delivery_key_is_bounded_and_deterministic(self) -> None:
        import hashlib

        body = b'{"event": "issues.opened", "number": 42}'
        digest = hashlib.sha256(body).hexdigest()
        # SHA-256 is always 64 hex chars by definition.
        sha256_hex_length = 64
        assert len(digest) == sha256_hex_length

        delivery_key = _webhooks_wiring.build_delivery_key(
            connection_name="github-prod",
            body=body,
        )
        key = _webhooks_wiring._build_idem_key(delivery_key=delivery_key)
        assert len(key) <= _webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN
        assert key
        assert key == _webhooks_wiring._build_idem_key(delivery_key=delivery_key)

    def test_an_oversized_delivery_key_collapses_rather_than_truncating(self) -> None:
        # A connection name long enough to push the composite past the column
        # cap must still yield a distinct, bounded key rather than a prefix two
        # different deliveries could share.
        long_name = "x" * (_webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN * 2)
        first = _webhooks_wiring._build_idem_key(
            delivery_key=_webhooks_wiring.build_delivery_key(
                connection_name=long_name,
                body=b'{"n": 1}',
            ),
        )
        second = _webhooks_wiring._build_idem_key(
            delivery_key=_webhooks_wiring.build_delivery_key(
                connection_name=long_name,
                body=b'{"n": 2}',
            ),
        )
        assert len(first) <= _webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN
        assert len(second) <= _webhooks_wiring._IDEMPOTENCY_KEY_MAX_LEN
        assert first != second


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

        async def fake_get_verified_connection(
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
            connection: object,
            body: bytes,
            headers: dict[str, str],
        ) -> object:
            # Stands in for the resolved verifier the real one hands back; this
            # branch never reads it, only forwards it to the delivery-id read.
            return object()

        def fake_read_delivery_id(
            headers: dict[str, str],
            verifier: object,
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
            dedup_key: str,
            delivery_id: str | None,
            timestamp: object,
        ) -> None:
            return None

        async def spy_publish_with_durable_idempotency(
            **kwargs: object,
        ) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "accepted", "event_type": kwargs["event_type"]}

        for name, fn in (
            ("get_verified_connection", fake_get_verified_connection),
            ("_enforce_max_payload", fake_enforce_max_payload),
            ("verify_signature", fake_verify_signature),
            ("read_delivery_id", fake_read_delivery_id),
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
        event_type: str = "issues.opened",
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
            event_type=event_type,
        )
        return captured

    async def test_a_header_nonce_cannot_change_the_idempotency_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A supplied nonce is ignored for keying, which is what stops replay.

        No verifier signs ``X-Nonce`` (the HMAC schemes cover the body only, and
        GitLab's token scheme signs nothing), so keying on it let one captured
        signed body publish repeatedly under fresh values. The key is the body
        digest, so the header cannot move it.
        """
        body = b'{"x": 1}'
        expected = build_delivery_key(connection_name="github-prod", body=body)
        captured = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={"x-nonce": "attacker-chosen-value"},
        )
        assert captured["delivery_key"] == expected
        assert captured["dedup_source"] == "body_sha256"
        assert captured["connection_name"] == "github-prod"
        assert captured["event_type"] == "issues.opened"

    async def test_varying_the_nonce_reuses_one_key_for_one_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The replay attempt collapses onto the original delivery's key."""
        body = b'{"event": "push", "ref": "main"}'
        first = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={"x-nonce": "first"},
        )
        replay = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={"x-nonce": "second"},
        )
        assert first["delivery_key"] == replay["delivery_key"]

    async def test_the_url_event_type_cannot_change_the_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The event name comes from the URL, which no verifier signs.

        With it in the key, one captured signed body bought a fresh verified
        publish per name an attacker chose to post it to, since a different key
        meant the durable claim did not suppress it.
        """
        body = b'{"event": "push", "ref": "main"}'
        first = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={},
            event_type="issues.opened",
        )
        elsewhere = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={},
            event_type="deploy.finished",
        )
        assert first["delivery_key"] == elsewhere["delivery_key"]
        # The name is still published, it just does not key the dedup.
        assert first["event_type"] == "issues.opened"
        assert elsewhere["event_type"] == "deploy.finished"

    async def test_a_nonce_less_delivery_keys_on_the_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No provider sends the generic nonce headers, so this is the real path."""
        body = b'{"event": "push", "ref": "main"}'
        captured = await self._invoke_branch(
            monkeypatch,
            body_bytes=body,
            request_headers={},
        )
        assert captured["delivery_key"] == build_delivery_key(
            connection_name="github-prod",
            body=body,
        )
        assert hashlib.sha256(body).hexdigest() in str(captured["delivery_key"])
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
        assert first["delivery_key"] == second["delivery_key"]
        assert first["dedup_source"] == second["dedup_source"] == "body_sha256"
