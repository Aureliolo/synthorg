"""Keystone test: a captured secret never leaks through the create path.

Drives the out-of-band path (capture -> handle -> connections.create) and
asserts the raw secret is resolved into the create credentials in-process
while never appearing in the MCP tool-call ``arguments`` dict, the handler
response, or ANY emitted log line -- the sinks the create path could persist
it through. Parametrised over credential shapes. The transcript / LLM-prompt
sinks for a *pasted* secret are covered by the redact-before-persist backstop
(``tests/unit/security/test_credential_redaction.py`` + the turn/park redaction
call sites); the in-chat capture turn lands its own end-to-end coverage with
the chat capture affordance.
"""

import json
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.mcp_service import ConnectionService
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionType,
    SecretRef,
)
from synthorg.integrations.connections.secret_capture import SecretCaptureService
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.meta.mcp.handlers.communication import COMMUNICATION_HANDLERS
from tests._shared import InMemorySecretBackend, make_app_state, mock_of
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.integration

_SENTINELS = (
    "ghp_KEYSTONEsecret0000000000000000000000AB",
    "Bearer eyJKEYSTONEsecrettokenpayloadpayloadpayloadAB",
    "password=KEYSTONEsuperSecretValue1234567890",
    "sk-KEYSTONEsecretkeyvalue00000000000000000000",
)
_DRAFT = "draft-keystone-1"


def _connection() -> Connection:
    return Connection(
        name=NotBlankStr("gh"),
        connection_type=ConnectionType.GITHUB,
        auth_method=AuthMethod.BEARER_TOKEN,
        secret_refs=(
            SecretRef(secret_id=NotBlankStr("s-1"), backend=NotBlankStr("memory")),
        ),
        health=ConnectionHealth(status=ConnectionStatus.UNKNOWN),
    )


@pytest.mark.parametrize("sentinel", _SENTINELS)
async def test_captured_secret_resolves_without_leaking(sentinel: str) -> None:
    backend = InMemorySecretBackend()
    capture = SecretCaptureService(secret_backend=backend)
    handle = await capture.capture(
        draft_id=NotBlankStr(_DRAFT),
        field_name=NotBlankStr("token"),
        secret_kind=NotBlankStr("token"),
        value=sentinel,
    )

    connection_service = mock_of[ConnectionService](
        create_connection=AsyncMock(return_value=_connection()),
    )
    app_state = make_app_state(
        slices={
            IntegrationsStateSlice: {
                "connection_service": connection_service,
                "secret_capture_service": capture,
            },
        },
    )

    arguments = {
        "name": "gh",
        "connection_type": "github",
        "auth_method": "bearer_token",
        "credential_handles": {"token": handle},
        "connection_draft_id": _DRAFT,
        "confirm": True,
        "reason": "operator setup via chat",
    }
    handler = COMMUNICATION_HANDLERS["synthorg_connections_create"]
    with structlog.testing.capture_logs() as logs:
        response = await handler(
            app_state=app_state,
            arguments=arguments,
            actor=make_test_actor(),
        )

    assert json.loads(response)["status"] == "ok"
    # The handle resolved to the real secret in-process for the create call.
    passed_credentials = connection_service.create_connection.await_args.kwargs[
        "credentials"
    ]
    assert passed_credentials == {"token": sentinel}
    # The raw secret never appears in the tool-call arguments, the handler
    # response, or ANY emitted log line -- every sink the create path could
    # persist it through.
    assert sentinel not in json.dumps(arguments)
    assert sentinel not in response
    for entry in logs:
        assert sentinel not in json.dumps(entry, default=str), entry
    # The handle is single-use: the temp backing secret was consumed.
    assert backend.stored_count() == 0


async def test_create_rejects_handles_without_draft_id() -> None:
    backend = InMemorySecretBackend()
    capture = SecretCaptureService(secret_backend=backend)
    handle = await capture.capture(
        draft_id=NotBlankStr(_DRAFT),
        field_name=NotBlankStr("token"),
        secret_kind=NotBlankStr("token"),
        value=_SENTINELS[0],
    )
    connection_service = mock_of[ConnectionService](
        create_connection=AsyncMock(return_value=_connection()),
    )
    app_state = make_app_state(
        slices={
            IntegrationsStateSlice: {
                "connection_service": connection_service,
                "secret_capture_service": capture,
            },
        },
    )
    handler = COMMUNICATION_HANDLERS["synthorg_connections_create"]
    response = await handler(
        app_state=app_state,
        arguments={
            "name": "gh",
            "connection_type": "github",
            "auth_method": "bearer_token",
            "credential_handles": {"token": handle},
            "confirm": True,
            "reason": "operator setup via chat",
        },
        actor=make_test_actor(),
    )
    assert json.loads(response)["status"] == "error"
    connection_service.create_connection.assert_not_awaited()
