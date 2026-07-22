"""Tests for per-family action-type binding on the governed-connection base.

The base pinned every family to ``comms:external``, which conflated a chat
message with a production deploy: the risk classifier, the ``deploy:``
approval-requiring constraint and autonomy auto-approval all key off the
action type. These tests hold the per-family binding in place, and pin the
auto-approve isolation that makes it load-bearing.
"""

from dataclasses import dataclass
from typing import ClassVar, Literal, override
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.clock import Clock
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.security.timeout.protocol import RiskTierClassifier
from synthorg.tools._governed_connection_tool import (
    GovernedConnectionTool,
    json_result,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.errors import ToolError
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_COMMS_EXTERNAL = ActionType.COMMS_EXTERNAL.value
_DEPLOY_PRODUCTION = ActionType.DEPLOY_PRODUCTION.value


class _ProbeError(ToolError):
    """Typed leaf for the probe family."""


class _ProbeArgs(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["mutate"] = "mutate"

    @property
    def is_write(self) -> bool:
        """Whether this action mutates the upstream system.

        Returns:
            Always ``True``: the probe exercises the gated path.
        """
        return True


class _ProbeClient:
    async def aclose(self) -> None:
        """Release the client. The probe holds no transport."""


@dataclass(frozen=True)
class _ProbeRuntime:
    """Boot-scoped collaborators for the probe family."""

    connection_catalog: ConnectionCatalog
    connection_name: str
    timeout_seconds: float


@dataclass(frozen=True)
class _ProbeDeps:
    """Per-run collaborators for the probe family."""

    runtime: _ProbeRuntime
    approval_store: ApprovalStoreProtocol
    agent_id: str
    task_id: str | None = None
    effective_autonomy: EffectiveAutonomy | None = None
    risk_classifier: RiskTierClassifier | None = None
    clock: Clock | None = None


class _ProbeTool(GovernedConnectionTool[_ProbeClient, _ProbeRuntime]):
    """Minimal governed tool used to observe the action type the gate uses."""

    args_model: ClassVar[type[BaseModel] | None] = _ProbeArgs
    _KIND: ClassVar[str] = "Probe"
    _CONNECTION_FAILED_EVENT: ClassVar[str] = "probe.connection_failed"
    _CREDENTIAL_FAILED_EVENT: ClassVar[str] = "probe.credential_failed"
    _REQUIRE_BASE_URL: ClassVar[bool] = False
    _UNSUPPORTED_MSG: ClassVar[str] = "unsupported {ctype!r}"
    _UNSUPPORTED_REASON: ClassVar[str] = "unsupported_probe"
    _not_found_error: ClassVar[type[ToolError]] = _ProbeError
    _unsupported_error: ClassVar[type[ToolError]] = _ProbeError
    _argument_error: ClassVar[type[ToolError]] = _ProbeError
    _credential_error: ClassVar[type[ToolError]] = _ProbeError
    _rate_limited_error: ClassVar[type[ToolError]] = _ProbeError

    def __init__(self, *, deps: _ProbeDeps) -> None:
        super().__init__(
            name="probe_tool",
            description="Probe tool.",
            args_model=_ProbeArgs,
            runtime=deps.runtime,
            gate_deps=deps,
        )

    @override
    def _supported(self, connection_type: ConnectionType) -> bool:
        return True

    @override
    def _build_client(
        self,
        *,
        connection_type: ConnectionType,
        base_url: str,
        token: str,
        timeout: float,
    ) -> _ProbeClient:
        return _ProbeClient()

    @override
    async def _dispatch_guarded(
        self, client: _ProbeClient, args: BaseModel
    ) -> ToolExecutionResult:
        return await self._dispatch(client, args)

    @override
    async def _dispatch(
        self, client: _ProbeClient, args: BaseModel
    ) -> ToolExecutionResult:
        return json_result({"dispatched": True})


class _DeployTypedProbeTool(_ProbeTool):
    """A probe family binding a deploy action type instead of comms."""

    _ACTION_TYPE: ClassVar[str] = _DEPLOY_PRODUCTION


def _connection() -> Connection:
    return Connection(
        name="probe",
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.BEARER_TOKEN,
        base_url="https://probe.example.com",
    )


def _auto_approve(action: str) -> EffectiveAutonomy:
    return EffectiveAutonomy(
        level=AutonomyLevel.FULL,
        auto_approve_actions=frozenset({action}),
        human_approval_actions=frozenset(),
        security_agent=False,
    )


def _deps(
    *,
    store: ApprovalStore,
    autonomy: EffectiveAutonomy | None = None,
) -> _ProbeDeps:
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=_connection()),
        get_credentials=AsyncMock(
            spec=ConnectionCatalog.get_credentials, return_value={"token": "t0ken"}
        ),
    )
    runtime = _ProbeRuntime(
        connection_catalog=catalog,
        connection_name="probe",
        timeout_seconds=5.0,
    )
    return _ProbeDeps(
        runtime=runtime,
        approval_store=store,
        agent_id="agent-1",
        task_id="task-1",
        effective_autonomy=autonomy,
    )


class TestDeclaredActionType:
    def test_default_family_declares_comms_external(self) -> None:
        tool = _ProbeTool(deps=_deps(store=ApprovalStore()))
        assert tool.action_type == _COMMS_EXTERNAL

    def test_overriding_family_declares_its_own_action_type(self) -> None:
        tool = _DeployTypedProbeTool(deps=_deps(store=ApprovalStore()))
        assert tool.action_type == _DEPLOY_PRODUCTION


class TestGateActionType:
    async def test_parked_approval_carries_the_family_action_type(self) -> None:
        store = ApprovalStore()
        tool = _DeployTypedProbeTool(deps=_deps(store=store))
        result = await tool.execute(arguments={"action": "mutate"})
        assert result.metadata["requires_parking"] is True
        assert result.metadata["action_type"] == _DEPLOY_PRODUCTION

    async def test_default_family_parks_under_comms_external(self) -> None:
        store = ApprovalStore()
        tool = _ProbeTool(deps=_deps(store=store))
        result = await tool.execute(arguments={"action": "mutate"})
        assert result.metadata["requires_parking"] is True
        assert result.metadata["action_type"] == _COMMS_EXTERNAL


class TestAutoApproveIsolation:
    async def test_comms_auto_approval_does_not_cover_a_deploy_typed_tool(
        self,
    ) -> None:
        """The hole this binding closes: chat autonomy must not grant deploys."""
        store = ApprovalStore()
        tool = _DeployTypedProbeTool(
            deps=_deps(store=store, autonomy=_auto_approve(_COMMS_EXTERNAL))
        )
        result = await tool.execute(arguments={"action": "mutate"})
        assert result.metadata["requires_parking"] is True

    async def test_matching_auto_approval_dispatches_without_parking(self) -> None:
        store = ApprovalStore()
        tool = _DeployTypedProbeTool(
            deps=_deps(store=store, autonomy=_auto_approve(_DEPLOY_PRODUCTION))
        )
        result = await tool.execute(arguments={"action": "mutate"})
        assert result.is_error is False
        assert "requires_parking" not in result.metadata
