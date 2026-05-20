"""Unit tests for the governed ExternalApiTool."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tests._shared.mock_of import mock_of

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.enums import ApprovalStatus, AutonomyLevel
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.integrations.errors import ConnectionRateLimitError
from synthorg.security.autonomy.models import EffectiveAutonomy
from synthorg.tools.external_api.errors import ExternalApiResponseError
from synthorg.tools.external_api.external_api_tool import ExternalApiTool
from synthorg.tools.external_api.provider import (
    ExternalAccessRequest,
    ExternalAccessResponse,
)
from synthorg.tools.network_validator import NetworkPolicy

_ACTION_TYPE = "external_data:request"


class StubProvider:
    """Records requests and returns a canned response (or raises)."""

    def __init__(
        self,
        response: ExternalAccessResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response or ExternalAccessResponse(
            status_code=200,
            headers={},
            body="payload",
            truncated=False,
        )
        self.error = error
        self.requests: list[ExternalAccessRequest] = []

    async def request(self, req: ExternalAccessRequest) -> ExternalAccessResponse:
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        return self.response


def _connection(
    *,
    auth_method: AuthMethod = AuthMethod.BEARER_TOKEN,
    sensitive: bool = False,
) -> Connection:
    return Connection(
        name="crm-api",
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=auth_method,
        base_url="https://api.example.com",
        sensitive=sensitive,
    )


def _build_tool(  # noqa: PLR0913 -- test helper mirrors the tool's collaborators
    *,
    conn: Connection | None,
    credentials: dict[str, str] | None = None,
    provider: StubProvider | None = None,
    autonomy: EffectiveAutonomy | None = None,
    network_policy: NetworkPolicy | None = None,
    approval_store: ApprovalStore | None = None,
) -> ExternalApiTool:
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=conn),
        get_credentials=AsyncMock(
            spec=ConnectionCatalog.get_credentials,
            return_value=credentials or {"token": "sekret-token"},
        ),
    )
    return ExternalApiTool(
        connection_catalog=catalog,
        approval_store=approval_store or ApprovalStore(),
        provider=provider or StubProvider(),
        agent_id="agent-1",
        task_id="task-1",
        network_policy=network_policy or NetworkPolicy(block_private_ips=False),
        effective_autonomy=autonomy,
        max_response_bytes=1_048_576,
        timeout_seconds=30.0,
        default_max_rpm=60,
    )


@pytest.mark.unit
class TestExternalApiToolHappyPath:
    async def test_get_proceeds_and_returns_body(self) -> None:
        provider = StubProvider()
        tool = _build_tool(conn=_connection(), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/v2/contacts"},
        )
        assert result.is_error is False
        assert result.content == "payload"
        assert result.metadata["status_code"] == 200
        assert len(provider.requests) == 1
        assert provider.requests[0].url == "https://api.example.com/v2/contacts"

    async def test_returns_upstream_status_without_raising(self) -> None:
        provider = StubProvider(
            ExternalAccessResponse(status_code=404, body="missing", headers={}),
        )
        tool = _build_tool(conn=_connection(), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/v2/missing"},
        )
        assert result.is_error is False
        assert result.metadata["status_code"] == 404


@pytest.mark.unit
class TestExternalApiToolCredentials:
    async def test_bearer_token_injected_not_leaked(self) -> None:
        provider = StubProvider()
        tool = _build_tool(
            conn=_connection(auth_method=AuthMethod.BEARER_TOKEN),
            credentials={"token": "sekret-token"},
            provider=provider,
        )
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/data"},
        )
        sent = provider.requests[0].headers
        assert sent["Authorization"] == "Bearer sekret-token"
        # Credential never surfaces to the agent.
        assert "sekret-token" not in result.content
        assert "sekret-token" not in str(result.metadata)

    async def test_api_key_header(self) -> None:
        provider = StubProvider()
        tool = _build_tool(
            conn=_connection(auth_method=AuthMethod.API_KEY),
            credentials={"api_key": "key-123"},
            provider=provider,
        )
        await tool.execute(arguments={"connection": "crm-api", "path": "/data"})
        assert provider.requests[0].headers["X-API-Key"] == "key-123"

    async def test_basic_auth_header(self) -> None:
        provider = StubProvider()
        tool = _build_tool(
            conn=_connection(auth_method=AuthMethod.BASIC_AUTH),
            credentials={"username": "u", "password": "p"},
            provider=provider,
        )
        await tool.execute(arguments={"connection": "crm-api", "path": "/data"})
        # base64("u:p") == "dTpw"
        assert provider.requests[0].headers["Authorization"] == "Basic dTpw"


@pytest.mark.unit
class TestExternalApiToolEgress:
    async def test_connection_not_found(self) -> None:
        provider = StubProvider()
        tool = _build_tool(conn=None, provider=provider)
        result = await tool.execute(
            arguments={"connection": "ghost", "path": "/x"},
        )
        assert result.is_error is True
        assert "not found" in result.content.lower()
        assert provider.requests == []

    async def test_url_outside_connection_blocked(self) -> None:
        provider = StubProvider()
        tool = _build_tool(conn=_connection(), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "url": "https://evil.example.net/x"},
        )
        assert result.is_error is True
        assert provider.requests == []

    async def test_path_traversal_blocked(self) -> None:
        provider = StubProvider()
        tool = _build_tool(conn=_connection(), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/v2/../../admin"},
        )
        assert result.is_error is True
        assert provider.requests == []

    async def test_ssrf_private_ip_blocked(self) -> None:
        provider = StubProvider()
        conn = Connection(
            name="crm-api",
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.CUSTOM,
            base_url="http://10.0.0.1",
        )
        tool = _build_tool(
            conn=conn,
            provider=provider,
            network_policy=NetworkPolicy(),  # blocks private IPs
        )
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/x"},
        )
        assert result.is_error is True
        assert provider.requests == []


@pytest.mark.unit
class TestExternalApiToolApprovalGating:
    async def test_sensitive_connection_parks(self) -> None:
        provider = StubProvider()
        tool = _build_tool(conn=_connection(sensitive=True), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/data"},
        )
        assert result.metadata.get("requires_parking") is True
        assert result.metadata["approval_id"].startswith("approval-")
        assert result.metadata["action_type"] == _ACTION_TYPE
        assert provider.requests == []

    async def test_write_method_parks(self) -> None:
        provider = StubProvider()
        tool = _build_tool(conn=_connection(sensitive=False), provider=provider)
        result = await tool.execute(
            arguments={
                "connection": "crm-api",
                "method": "POST",
                "path": "/data",
                "body": "{}",
            },
        )
        assert result.metadata.get("requires_parking") is True
        assert provider.requests == []

    async def test_full_autonomy_bypasses_park(self) -> None:
        provider = StubProvider()
        autonomy = EffectiveAutonomy(
            level=AutonomyLevel.FULL,
            auto_approve_actions=frozenset({_ACTION_TYPE}),
            human_approval_actions=frozenset(),
            security_agent=False,
        )
        tool = _build_tool(
            conn=_connection(sensitive=True),
            provider=provider,
            autonomy=autonomy,
        )
        result = await tool.execute(
            arguments={"connection": "crm-api", "method": "POST", "path": "/d"},
        )
        assert result.metadata.get("requires_parking") is None
        assert len(provider.requests) == 1

    async def test_approval_consumed_then_proceeds(self) -> None:
        store = ApprovalStore()
        provider = StubProvider()
        tool = _build_tool(
            conn=_connection(sensitive=True),
            provider=provider,
            approval_store=store,
        )
        args = {"connection": "crm-api", "path": "/data"}
        parked = await tool.execute(arguments=args)
        approval_id = parked.metadata["approval_id"]

        # Human approves.
        item = await store.get(approval_id)
        assert item is not None
        approved = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": datetime.now(UTC),
                "decided_by": "human",
            },
        )
        await store.save(approved)

        # Resume: re-issue the same call.
        resumed = await tool.execute(arguments=args)
        assert resumed.is_error is False
        assert len(provider.requests) == 1
        consumed = await store.get(approval_id)
        assert consumed is not None
        assert consumed.consumed_at is not None

    async def test_replay_with_consumed_approval_id_errors(self) -> None:
        store = ApprovalStore()
        provider = StubProvider()
        tool = _build_tool(
            conn=_connection(sensitive=True),
            provider=provider,
            approval_store=store,
        )
        args: dict[str, Any] = {"connection": "crm-api", "path": "/data"}
        parked = await tool.execute(arguments=args)
        approval_id = parked.metadata["approval_id"]
        item = await store.get(approval_id)
        assert item is not None
        await store.save(
            item.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": "human",
                },
            ),
        )
        await tool.execute(arguments=args)  # consumes
        # Replay with the now-consumed approval id is rejected.
        replay = await tool.execute(arguments={**args, "approval_id": approval_id})
        assert replay.is_error is True
        assert len(provider.requests) == 1

    async def test_unknown_approval_id_errors(self) -> None:
        provider = StubProvider()
        tool = _build_tool(conn=_connection(sensitive=True), provider=provider)
        result = await tool.execute(
            arguments={
                "connection": "crm-api",
                "path": "/data",
                "approval_id": "approval-does-not-exist",
            },
        )
        assert result.is_error is True
        assert provider.requests == []


@pytest.mark.unit
class TestExternalApiToolResilience:
    async def test_rate_limited_returns_graceful_result(self) -> None:
        provider = StubProvider(error=ConnectionRateLimitError("window full"))
        tool = _build_tool(conn=_connection(), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/data"},
        )
        assert result.is_error is True
        assert result.metadata.get("rate_limited") is True

    async def test_transport_error_returns_error(self) -> None:
        provider = StubProvider(error=ExternalApiResponseError("connection refused"))
        tool = _build_tool(conn=_connection(), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/data"},
        )
        assert result.is_error is True

    async def test_truncation_flag_passthrough(self) -> None:
        provider = StubProvider(
            ExternalAccessResponse(
                status_code=200,
                body="abc",
                headers={},
                truncated=True,
            ),
        )
        tool = _build_tool(conn=_connection(), provider=provider)
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/data"},
        )
        assert result.metadata["truncated"] is True


@pytest.mark.unit
class TestExternalApiToolArgs:
    async def test_both_path_and_url_rejected(self) -> None:
        tool = _build_tool(conn=_connection())
        result = await tool.execute(
            arguments={
                "connection": "crm-api",
                "path": "/a",
                "url": "https://api.example.com/b",
            },
        )
        assert result.is_error is True

    async def test_body_on_get_rejected(self) -> None:
        tool = _build_tool(conn=_connection())
        result = await tool.execute(
            arguments={"connection": "crm-api", "path": "/a", "body": "x"},
        )
        assert result.is_error is True
