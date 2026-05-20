"""End-to-end governance test for the external_api tool (#1991).

Drives the tool through the real ``ToolInvoker`` escalation path, a real
``ConnectionCatalog`` (in-memory repository + stub secret backend, so
credential brokering round-trips), and a real ``ApprovalStore``, proving the
full acceptance criteria: an agent consumes an external API while building a
deliverable, with credentials brokered, rate limits enforced, egress
constrained, and a sensitive call gated to approval, then resumed and consumed
exactly once.

The upstream HTTP egress is replaced by a deterministic stub
``ExternalAccessProvider`` so the test is hermetic; everything else (catalog,
secret round-trip, invoker escalation detection, approval consumption) is the
real production path.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.enums import ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.errors import ConnectionRateLimitError
from synthorg.persistence.integration_stubs import InMemoryConnectionRepository
from synthorg.providers.models import ToolCall
from synthorg.tools.external_api.external_api_tool import ExternalApiTool
from synthorg.tools.external_api.provider import (
    ExternalAccessRequest,
    ExternalAccessResponse,
)
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.e2e

_AGENT_ID = "agent-e2e"


class _StubSecretBackend:
    """In-memory ``SecretBackend`` so credential brokering round-trips."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("stub")

    async def store(self, secret_id: NotBlankStr, value: bytes) -> None:
        self._store[str(secret_id)] = value

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        return self._store.get(str(secret_id))

    async def delete(self, secret_id: NotBlankStr) -> bool:
        return self._store.pop(str(secret_id), None) is not None

    async def rotate(self, old_id: NotBlankStr, new_value: bytes) -> NotBlankStr:
        new_id = NotBlankStr(f"{old_id}-rotated")
        self._store[str(new_id)] = new_value
        self._store.pop(str(old_id), None)
        return new_id

    async def close(self) -> None:
        return None


class _StubProvider:
    """Deterministic ``ExternalAccessProvider`` recording every request."""

    def __init__(self) -> None:
        self.requests: list[ExternalAccessRequest] = []
        self.error: Exception | None = None

    async def request(self, req: ExternalAccessRequest) -> ExternalAccessResponse:
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        return ExternalAccessResponse(
            status_code=200,
            headers={},
            body=f"DATA::{req.url}",
            truncated=False,
        )


async def _make_catalog() -> ConnectionCatalog:
    """Build an in-memory catalog with a public and a sensitive connection."""
    catalog = ConnectionCatalog(InMemoryConnectionRepository(), _StubSecretBackend())
    await catalog.create(
        name="public-api",
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.BEARER_TOKEN.value,
        credentials={"base_url": "https://api.example.com", "token": "public-token"},
        base_url="https://api.example.com",
    )
    await catalog.create(
        name="crm-api",
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.BEARER_TOKEN.value,
        credentials={"base_url": "https://crm.example.com", "token": "crm-token"},
        base_url="https://crm.example.com",
        sensitive=True,
    )
    return catalog


def _make_invoker(
    catalog: ConnectionCatalog,
    store: ApprovalStore,
    provider: _StubProvider,
) -> ToolInvoker:
    tool = ExternalApiTool(
        connection_catalog=catalog,
        approval_store=store,
        provider=provider,
        agent_id=_AGENT_ID,
        task_id="task-e2e",
        network_policy=NetworkPolicy(block_private_ips=False),
        max_response_bytes=1_048_576,
        timeout_seconds=30.0,
        default_max_rpm=60,
    )
    return ToolInvoker(ToolRegistry([tool]), agent_id=_AGENT_ID, task_id="task-e2e")


def _call(call_id: str, **arguments: object) -> ToolCall:
    return ToolCall(id=call_id, name="external_api", arguments=arguments)


@pytest.mark.e2e
class TestExternalApiGovernanceE2E:
    async def test_full_governance_lifecycle(self) -> None:
        catalog = await _make_catalog()
        store = ApprovalStore()
        provider = _StubProvider()
        invoker = _make_invoker(catalog, store, provider)

        # 1. Build a deliverable: a non-sensitive read proceeds, with the
        #    connection's credential brokered into the request and egress
        #    constrained to the connection's host.
        read = await invoker.invoke(
            _call("c1", connection="public-api", path="/widgets"),
        )
        assert read.is_error is False
        assert read.content == "DATA::https://api.example.com/widgets"
        assert len(provider.requests) == 1
        assert provider.requests[0].headers["Authorization"] == "Bearer public-token"
        # Credential never surfaces to the agent-visible result.
        assert "public-token" not in read.content
        assert not invoker.pending_escalations

        # 2. A sensitive connection gates to approval BEFORE any egress; the
        #    invoker surfaces the parking escalation.
        gated = await invoker.invoke(
            _call("c2", connection="crm-api", path="/customers"),
        )
        assert gated.is_error is True
        assert len(invoker.pending_escalations) == 1
        approval_id = invoker.pending_escalations[0].approval_id
        # No egress to the sensitive host occurred.
        assert all("crm.example.com" not in r.url for r in provider.requests)

        # 3. A human approves the parked request.
        item = await store.get(approval_id)
        assert item is not None
        await store.save(
            item.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "decided_at": datetime.now(UTC),
                    "decided_by": "operator",
                },
            ),
        )

        # 4. On resume the agent re-issues the same call; the grant is matched
        #    by content signature, consumed exactly once, and egress proceeds.
        resumed = await invoker.invoke(
            _call("c3", connection="crm-api", path="/customers"),
        )
        assert resumed.is_error is False
        assert resumed.content == "DATA::https://crm.example.com/customers"
        assert provider.requests[-1].headers["Authorization"] == "Bearer crm-token"
        consumed = await store.get(approval_id)
        assert consumed is not None
        assert consumed.consumed_at is not None

        # 5. Replaying the now-consumed approval id is rejected (no egress).
        egress_count = len(provider.requests)
        replay = await invoker.invoke(
            _call(
                "c4",
                connection="crm-api",
                path="/customers",
                approval_id=approval_id,
            ),
        )
        assert replay.is_error is True
        assert len(provider.requests) == egress_count

    async def test_egress_constrained_to_connection_host(self) -> None:
        catalog = await _make_catalog()
        provider = _StubProvider()
        invoker = _make_invoker(catalog, ApprovalStore(), provider)

        blocked = await invoker.invoke(
            _call(
                "e1",
                connection="public-api",
                url="https://evil.example.net/exfil",
            ),
        )
        assert blocked.is_error is True
        assert provider.requests == []

    async def test_rate_limit_surfaces_gracefully(self) -> None:
        catalog = await _make_catalog()
        provider = _StubProvider()
        provider.error = ConnectionRateLimitError("window full")
        invoker = _make_invoker(catalog, ApprovalStore(), provider)

        limited = await invoker.invoke(
            _call("r1", connection="public-api", path="/widgets"),
        )
        assert limited.is_error is True
        assert "rate limit" in limited.content.lower()
