"""Unit tests for the communication MCP handlers.

Covers 21 tools across messages, meetings, connections, webhooks,
tunnel.  Uses AsyncMock-backed facades so tests exercise handler
parsing + envelope shaping without standing up real services.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.communication.meeting.enums import (
    MeetingProtocolType,
    MeetingStatus,
)
from synthorg.communication.meeting.models import MeetingRecord
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
    SecretRef,
)
from synthorg.integrations.tunnel.mcp_service import TunnelStatus
from synthorg.integrations.webhooks.models import (
    WebhookDefinition,
    WebhookVerifierKind,
)
from synthorg.meta.mcp.handlers.communication import COMMUNICATION_HANDLERS
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


def _connection(name: str = "c1") -> Connection:
    return Connection(
        name=NotBlankStr(name),
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        secret_refs=(
            SecretRef(
                secret_id=NotBlankStr("s-1"),
                backend=NotBlankStr("memory"),
            ),
        ),
        health_status=ConnectionStatus.UNKNOWN,
    )


def _meeting(meeting_id: str = "m-1") -> MeetingRecord:
    return MeetingRecord(
        meeting_id=NotBlankStr(meeting_id),
        meeting_type_name=NotBlankStr("standup"),
        protocol_type=MeetingProtocolType.ROUND_ROBIN,
        status=MeetingStatus.FAILED,
        error_message=NotBlankStr("test failure"),
        token_budget=1000,
    )


def _webhook(name: str = "wh") -> WebhookDefinition:
    return WebhookDefinition(
        name=NotBlankStr(name),
        issuer=NotBlankStr("test-issuer"),
        verifier_kind=WebhookVerifierKind.HMAC_SHA256,
        secret_ref=NotBlankStr("ref"),
        channel=NotBlankStr("webhooks.inbound"),
    )


@pytest.fixture
def fake_message_service() -> AsyncMock:
    service = AsyncMock()
    service.list_messages = AsyncMock(return_value=((), 0))
    service.get_message = AsyncMock(return_value=None)
    service.send_message = AsyncMock(return_value=None)
    # ``delete_message`` is now a real operation; default to True so
    # happy-path destructive-op tests succeed. Per-test overrides set
    # False to simulate not-found.
    service.delete_message = AsyncMock(return_value=True)
    return service


@pytest.fixture
def fake_meeting_service() -> AsyncMock:
    service = AsyncMock()
    service.list_meetings = AsyncMock(return_value=((), 0))
    service.get_meeting = AsyncMock(return_value=None)
    service.create_meeting = AsyncMock(
        side_effect=CapabilityNotSupportedError("meeting_create", "reason"),
    )
    service.update_meeting = AsyncMock(
        side_effect=CapabilityNotSupportedError("meeting_update", "reason"),
    )
    # ``delete_meeting`` is now a real operation; default to True so
    # the happy-path destructive-op test succeeds. Per-test overrides
    # set False to simulate not-found.
    service.delete_meeting = AsyncMock(return_value=True)
    return service


@pytest.fixture
def fake_connection_service() -> AsyncMock:
    service = AsyncMock()
    service.list_connections = AsyncMock(return_value=((), 0))
    service.get_connection = AsyncMock(return_value=None)
    service.create_connection = AsyncMock(return_value=_connection())
    service.delete_connection = AsyncMock(return_value=None)
    service.check_health = AsyncMock(return_value=_connection())
    return service


@pytest.fixture
def fake_webhook_service() -> AsyncMock:
    service = AsyncMock()
    service.list_webhooks = AsyncMock(return_value=((), 0))
    service.get_webhook = AsyncMock(return_value=None)
    service.create_webhook = AsyncMock(return_value=_webhook())
    service.update_webhook = AsyncMock(return_value=_webhook())
    service.delete_webhook = AsyncMock(return_value=True)
    return service


@pytest.fixture
def fake_tunnel_service() -> AsyncMock:
    service = AsyncMock()
    service.get_status = AsyncMock(
        return_value=TunnelStatus(running=False, url=None),
    )
    service.connect = AsyncMock(
        return_value=TunnelStatus(running=True, url="https://example.test"),
    )
    return service


@pytest.fixture
def fake_app_state(
    fake_message_service: AsyncMock,
    fake_meeting_service: AsyncMock,
    fake_connection_service: AsyncMock,
    fake_webhook_service: AsyncMock,
    fake_tunnel_service: AsyncMock,
) -> SimpleNamespace:
    return SimpleNamespace(
        message_service=fake_message_service,
        meeting_service=fake_meeting_service,
        connection_service=fake_connection_service,
        webhook_service=fake_webhook_service,
        tunnel_service=fake_tunnel_service,
    )


# ── Messages ────────────────────────────────────────────────────────


class TestMessagesHandlers:
    async def test_list_empty(self, fake_app_state: SimpleNamespace) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_messages_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"] == []

    async def test_get_missing_channel_rejected(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_messages_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"message_id": "m-1"},
        )
        assert json.loads(response)["status"] == "error"

    async def test_get_not_found(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_messages_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"channel": "ch", "message_id": "m-1"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_send_invalid_payload_rejected(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_messages_send"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"message": {"not": "valid"}},
        )
        assert json.loads(response)["status"] == "error"

    async def test_delete_happy_path_returns_removed_true(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_messages_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "message_id": "m-1",
                "confirm": True,
                "reason": "cleanup",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["removed"] is True

    async def test_delete_returns_removed_false_when_not_found(
        self,
        fake_app_state: SimpleNamespace,
        fake_message_service: AsyncMock,
    ) -> None:
        fake_message_service.delete_message = AsyncMock(return_value=False)
        handler = COMMUNICATION_HANDLERS["synthorg_messages_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "message_id": "missing",
                "confirm": True,
                "reason": "cleanup",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["removed"] is False

    async def test_delete_missing_confirm_rejected(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_messages_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "message_id": "m-1",
                "reason": "c",
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_delete_unexpected_service_exception_returns_error(
        self,
        fake_app_state: SimpleNamespace,
        fake_message_service: AsyncMock,
    ) -> None:
        """An unexpected exception bubbles into the generic error envelope."""
        fake_message_service.delete_message = AsyncMock(
            side_effect=RuntimeError("connection pool exhausted"),
        )
        handler = COMMUNICATION_HANDLERS["synthorg_messages_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "message_id": "m-1",
                "confirm": True,
                "reason": "cleanup",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "error"


# ── Meetings ────────────────────────────────────────────────────────


class TestMeetingsHandlers:
    async def test_list_happy_path(
        self,
        fake_app_state: SimpleNamespace,
        fake_meeting_service: AsyncMock,
    ) -> None:
        fake_meeting_service.list_meetings = AsyncMock(
            return_value=((_meeting(),), 1),
        )
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["pagination"]["total"] == 1

    async def test_list_status_filter_invalid(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_list"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"status": "bogus"},
        )
        assert json.loads(response)["status"] == "error"

    async def test_get_not_found(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"meeting_id": "m-xxx"},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_create_capability_gap(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_create"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "not_supported"

    async def test_delete_requires_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_delete"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["domain_code"] == "guardrail_violated"

    async def test_delete_happy_path_returns_removed_true(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "meeting_id": "m-1",
                "confirm": True,
                "reason": "operator gdpr cleanup",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["removed"] is True

    async def test_delete_returns_removed_false_when_not_found(
        self,
        fake_app_state: SimpleNamespace,
        fake_meeting_service: AsyncMock,
    ) -> None:
        fake_meeting_service.delete_meeting = AsyncMock(return_value=False)
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "meeting_id": "missing",
                "confirm": True,
                "reason": "cleanup",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["removed"] is False

    async def test_delete_unexpected_service_exception_returns_error(
        self,
        fake_app_state: SimpleNamespace,
        fake_meeting_service: AsyncMock,
    ) -> None:
        """An unexpected exception bubbles into the generic error envelope."""
        fake_meeting_service.delete_meeting = AsyncMock(
            side_effect=RuntimeError("orchestrator unavailable"),
        )
        handler = COMMUNICATION_HANDLERS["synthorg_meetings_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "meeting_id": "m-1",
                "confirm": True,
                "reason": "cleanup",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "error"


# ── Connections ─────────────────────────────────────────────────────


class TestConnectionsHandlers:
    async def test_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_connections_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_create_happy_path(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_connections_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "name": "c1",
                "connection_type": "generic_http",
                "auth_method": "api_key",
                "credentials": {"key": "v"},
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"

    async def test_create_invalid_type_rejected(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_connections_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "name": "c1",
                "connection_type": "not-a-type",
                "auth_method": "api_key",
                "credentials": {"key": "v"},
            },
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "error"

    async def test_delete_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_connections_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"name": "c1", "confirm": True, "reason": "cleanup"},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"

    async def test_check_health(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_connections_check_health"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"name": "c1"},
        )
        assert json.loads(response)["status"] == "ok"


# ── Webhooks ────────────────────────────────────────────────────────


class TestWebhooksHandlers:
    async def test_list(self, fake_app_state: SimpleNamespace) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_webhooks_list"]
        response = await handler(app_state=fake_app_state, arguments={})
        assert json.loads(response)["status"] == "ok"

    async def test_get_not_found(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_webhooks_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"webhook_id": str(uuid4())},
        )
        assert json.loads(response)["domain_code"] == "not_found"

    async def test_create_happy_path(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_webhooks_create"]
        definition = _webhook().model_dump(mode="json")
        definition.pop("id", None)
        response = await handler(
            app_state=fake_app_state,
            arguments={"definition": definition},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "ok"

    async def test_create_invalid_rejected(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_webhooks_create"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"definition": {"incomplete": True}},
            actor=make_test_actor(),
        )
        assert json.loads(response)["status"] == "error"

    async def test_delete_guardrails(
        self,
        fake_app_state: SimpleNamespace,
    ) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_webhooks_delete"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "webhook_id": str(uuid4()),
                "confirm": True,
                "reason": "cleanup",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["removed"] is True


# ── Tunnel ──────────────────────────────────────────────────────────


class TestTunnelHandlers:
    async def test_status(self, fake_app_state: SimpleNamespace) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_tunnel_get_status"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["running"] is False

    async def test_connect(self, fake_app_state: SimpleNamespace) -> None:
        handler = COMMUNICATION_HANDLERS["synthorg_tunnel_connect"]
        response = await handler(app_state=fake_app_state, arguments={})
        payload = json.loads(response)
        assert payload["data"]["url"] == "https://example.test"
