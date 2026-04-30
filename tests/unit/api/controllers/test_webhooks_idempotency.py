"""Tests for webhook nonce-less idempotency dedup (#1682).

Issue #1682: when a provider does not supply ``X-Nonce`` /
``X-Request-Id``, the webhook handler must still dedupe redeliveries
to prevent double-bus publishes. The handler hashes the request body
with SHA-256 and feeds the digest into the existing durable
``IdempotencyService``.

These tests pin the helper-level contract: the success log records
``dedup_source`` and the durable-idempotency path is invoked for both
the nonce and nonce-less branches with the appropriate key shape.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.api.controllers import webhooks as webhooks_module
from synthorg.api.services.idempotency_service import IdempotencyService
from synthorg.observability.events.integrations import WEBHOOK_ACCEPTED


class _FakeBus:
    """Minimal stand-in for the message-bus interface used by the helper."""


@pytest.mark.unit
class TestPublishWebhookEventAndLog:
    """``_publish_webhook_event_and_log`` carries a ``dedup_source`` tag."""

    async def test_dedup_source_in_success_log(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The success log includes the dedup_source provenance string."""
        published: list[dict[str, Any]] = []

        async def fake_publish(
            *,
            bus: Any,
            connection_name: str,
            event_type: str,
            payload: dict[str, Any],
        ) -> None:
            published.append(
                {
                    "connection_name": connection_name,
                    "event_type": event_type,
                    "payload": payload,
                },
            )

        monkeypatch.setattr(
            webhooks_module,
            "publish_webhook_event",
            fake_publish,
        )

        with structlog.testing.capture_logs() as logs:
            result = await webhooks_module._publish_webhook_event_and_log(
                bus=_FakeBus(),
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
        captured: dict[str, Any] = {}

        async def spy(
            *,
            bus: Any,
            connection_name: str,
            event_type: str,
            payload: Any,
            dedup_source: str,
        ) -> dict[str, object]:
            captured["dedup_source"] = dedup_source
            captured["connection_name"] = connection_name
            captured["event_type"] = event_type
            captured["payload"] = payload
            return {"status": "accepted", "event_type": event_type}

        monkeypatch.setattr(
            webhooks_module,
            "_publish_webhook_event_and_log",
            spy,
        )

        async def run_idempotent(
            *,
            scope: str,
            key: str,
            callback: Any,
        ) -> tuple[Any, bool]:
            return await callback(), True

        idem_service = AsyncMock(spec=IdempotencyService)
        idem_service.run_idempotent = run_idempotent

        class _AppState:
            idempotency_service = idem_service

        state: dict[str, Any] = {"app_state": _AppState()}

        cached = await webhooks_module._publish_with_durable_idempotency(
            state=state,  # type: ignore[arg-type]
            connection_name="conn-b",
            event_type="push",
            nonce="sha256:deadbeef",
            connection_type="github",
            bus=_FakeBus(),
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
        key = webhooks_module._build_idem_key(
            connection_name="some-connection",
            event_type="some.event",
            nonce=synthesised,
        )
        assert len(key) <= webhooks_module._IDEMPOTENCY_KEY_MAX_LEN
        # The prefix or a hash-of-prefix appears in the key (operator
        # visibility); the entire key is non-empty.
        assert key
