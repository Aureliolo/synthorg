"""The governed external-API tool reaches the agent's per-run registry.

The governed-external-access tool is wired per run inside
``AgentEngine._make_tool_invoker`` via ``registry_with_external_api_tool``.
These tests pin that engine-level wiring: when the boot-scoped
``ExternalApiRuntime`` and an approval store are present, an agent building a
deliverable gets the ``external_api`` tool in its permitted set; when the
runtime is absent the tool is not registered (the feature is off). Governance
behaviour of the tool itself is covered end-to-end in
``tests/e2e/test_external_api_governance_e2e.py``.
"""

from typing import Any, override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.persistence.integration_stubs import InMemoryConnectionRepository
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.external_api._runtime import ExternalApiRuntime
from synthorg.tools.external_api.provider import (
    ExternalAccessRequest,
    ExternalAccessResponse,
)
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.registry import ToolRegistry
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

pytestmark = pytest.mark.unit

_MAX_RESPONSE_BYTES = 1_048_576
_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RPM = 60


class _StubSecretBackend:
    """Minimal ``SecretBackend`` so the catalog constructs."""

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("stub")

    async def store(self, secret_id: NotBlankStr, value: bytes) -> None:
        del secret_id, value

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        del secret_id
        return None

    async def delete(self, secret_id: NotBlankStr) -> bool:
        del secret_id
        return False

    async def rotate(self, old_id: NotBlankStr, new_value: bytes) -> NotBlankStr:
        del new_value
        return old_id

    async def close(self) -> None:
        return None


class _StubProvider:
    """Deterministic ``ExternalAccessProvider`` (never invoked in these tests)."""

    async def request(self, req: ExternalAccessRequest) -> ExternalAccessResponse:
        return ExternalAccessResponse(
            status_code=200,
            headers={},
            body=f"DATA::{req.url}",
            truncated=False,
        )


class _StubTool(BaseTool):
    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="stub")


def _runtime() -> ExternalApiRuntime:
    catalog = ConnectionCatalog(InMemoryConnectionRepository(), _StubSecretBackend())
    return ExternalApiRuntime(
        connection_catalog=catalog,
        provider=_StubProvider(),
        network_policy=NetworkPolicy(block_private_ips=False),
        max_response_bytes=_MAX_RESPONSE_BYTES,
        timeout_seconds=_TIMEOUT_SECONDS,
        default_max_rpm=_DEFAULT_MAX_RPM,
    )


def _engine(*, with_runtime: bool) -> AgentEngine:
    registry = ToolRegistry([_StubTool(name="stub", category=ToolCategory.OTHER)])
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=registry,
        approval_store=ApprovalStore(),
        external_api_runtime=_runtime() if with_runtime else None,
    )


class TestAgentEngineExternalApiWiring:
    """``external_api`` is registered per run only when its runtime is wired."""

    def test_external_api_tool_registered_when_runtime_wired(self) -> None:
        engine = _engine(with_runtime=True)

        invoker = engine._make_tool_invoker(make_e2e_identity())

        assert invoker is not None
        names = [d.name for d in invoker.get_permitted_definitions()]
        assert "external_api" in names

    def test_no_external_api_tool_when_runtime_absent(self) -> None:
        engine = _engine(with_runtime=False)

        invoker = engine._make_tool_invoker(make_e2e_identity())

        assert invoker is not None
        names = [d.name for d in invoker.get_permitted_definitions()]
        assert "external_api" not in names
