"""Unit tests for the governed deploy tools.

Exercises the destructive release path (guardrail triple -> park -> approve
-> re-issue -> consume), the read surface, the target allowlist, the
per-connection environment binding, setup-required reporting, and egress
binding (respx only mocks the target's host, so a call to another host
would fail to match). The platform is a vendor-neutral fixture connection;
vendor hostnames stay in the client-layer tests.
"""

import json
from datetime import date
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalStatus
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.deploy._runtime import DeployToolDeps, DeployToolsRuntime
from synthorg.tools.deploy.deploy_tools import DeployReleaseTool, DeployRunTool
from tests._shared.ids import as_uuid
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_HOST = "https://deploy.example.com"
_TARGET = "production-web"
_COMMS_EXTERNAL = ActionType.COMMS_EXTERNAL.value
_DEPLOY_PRODUCTION = ActionType.DEPLOY_PRODUCTION.value
_DEPLOY_STAGING = ActionType.DEPLOY_STAGING.value


def _actor() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("deployer"),
        name="deployer",
        role="engineer",
        department="engineering",
        model=ModelConfig(provider="example-provider", model_id="example-medium-001"),
        hiring_date=date(2026, 1, 1),
    )


def _connection(  # noqa: PLR0913 -- test helper mirrors the connection record
    *,
    name: str = _TARGET,
    ctype: ConnectionType = ConnectionType.DEPLOY,
    base_url: str | None = _HOST,
    environment: str = "production",
    platform: str = "vercel",
    project: str = "acme-web",
    sensitive: bool = False,
) -> Connection:
    metadata = {
        k: v
        for k, v in (
            ("environment", environment),
            ("platform", platform),
            ("project", project),
        )
        if v
    }
    return Connection(
        name=name,
        connection_type=ctype,
        auth_method=AuthMethod.BEARER_TOKEN,
        base_url=base_url,
        metadata=metadata,
        sensitive=sensitive,
    )


def _auto_approve(action: str) -> EffectiveAutonomy:
    return EffectiveAutonomy(
        level=AutonomyLevel.FULL,
        auto_approve_actions=frozenset({action}),
        human_approval_actions=frozenset(),
        security_agent=False,
    )


def _deps(  # noqa: PLR0913 -- test helper mirrors the tool's collaborators
    *,
    conn: Connection | None,
    store: ApprovalStore | None = None,
    autonomy: EffectiveAutonomy | None = None,
    targets: frozenset[str] = frozenset({_TARGET}),
    credentials_error: Exception | None = None,
    max_log_chars: int = 20000,
) -> DeployToolDeps:
    get_credentials = AsyncMock(spec=ConnectionCatalog.get_credentials)
    if credentials_error is not None:
        get_credentials.side_effect = credentials_error
    else:
        get_credentials.return_value = {"token": "t0ken"}
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=conn),
        get_credentials=get_credentials,
    )
    return DeployToolDeps(
        runtime=DeployToolsRuntime(
            connection_catalog=catalog,
            allowed_targets=targets,
            timeout_seconds=5.0,
            max_log_chars=max_log_chars,
        ),
        approval_store=store or ApprovalStore(),
        agent_id="agent-1",
        task_id="task-1",
        effective_autonomy=autonomy,
    )


def _release_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "action": "trigger",
        "target": _TARGET,
        "git_ref": "main",
        "confirm": True,
        "reason": "ship the release",
    }
    args.update(overrides)
    return args


class TestReleaseApprovalFlow:
    @respx.mock
    async def test_release_parks_then_consumes_on_approval(self) -> None:
        route = respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "QUEUED"}
            )
        )
        store = ApprovalStore()
        parked = await DeployReleaseTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_release_args())
        assert parked.metadata["requires_parking"] is True
        assert route.call_count == 0  # no egress on a parked release

        approval_id = cast("str", parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))

        resumed = await DeployReleaseTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_release_args())
        assert resumed.is_error is False
        assert json.loads(resumed.content)["id"] == "dpl_1"
        assert route.call_count == 1

    @respx.mock
    async def test_approved_grant_does_not_cover_a_mutated_payload(self) -> None:
        """The signature binds the arguments, not just the tool and target."""
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "QUEUED"}
            )
        )
        store = ApprovalStore()
        parked = await DeployReleaseTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_release_args())
        approval_id = cast("str", parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))

        mutated = await DeployReleaseTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_release_args(git_ref="attacker-branch"))
        assert mutated.metadata["requires_parking"] is True


class TestActionTypeBinding:
    async def test_production_target_parks_under_production_action(self) -> None:
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection(environment="production")), actor=_actor()
        ).execute(arguments=_release_args())
        assert result.metadata["action_type"] == _DEPLOY_PRODUCTION

    async def test_staging_target_parks_under_staging_action(self) -> None:
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection(environment="staging")), actor=_actor()
        ).execute(arguments=_release_args())
        assert result.metadata["action_type"] == _DEPLOY_STAGING

    async def test_unknown_environment_falls_back_to_production(self) -> None:
        """A mislabelled target is over-gated, never treated as throwaway."""
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection(environment="whatever")), actor=_actor()
        ).execute(arguments=_release_args())
        assert result.metadata["action_type"] == _DEPLOY_PRODUCTION

    async def test_chat_autonomy_does_not_auto_approve_a_release(self) -> None:
        """Granting comms autonomy must never grant production deploys."""
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection(), autonomy=_auto_approve(_COMMS_EXTERNAL)),
            actor=_actor(),
        ).execute(arguments=_release_args())
        assert result.metadata["requires_parking"] is True

    @respx.mock
    async def test_staging_autonomy_does_not_auto_approve_production(self) -> None:
        result = await DeployReleaseTool(
            deps=_deps(
                conn=_connection(environment="production"),
                autonomy=_auto_approve(_DEPLOY_STAGING),
            ),
            actor=_actor(),
        ).execute(arguments=_release_args())
        assert result.metadata["requires_parking"] is True

    @respx.mock
    async def test_matching_environment_autonomy_dispatches(self) -> None:
        respx.post(f"{_HOST}/v13/deployments").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_2", "readyState": "READY"}
            )
        )
        result = await DeployReleaseTool(
            deps=_deps(
                conn=_connection(environment="staging"),
                autonomy=_auto_approve(_DEPLOY_STAGING),
            ),
            actor=_actor(),
        ).execute(arguments=_release_args())
        assert result.is_error is False
        assert "requires_parking" not in result.metadata


class TestGuardrails:
    async def test_missing_confirm_is_refused(self) -> None:
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection()), actor=_actor()
        ).execute(arguments=_release_args(confirm=False))
        assert result.is_error is True

    async def test_blank_reason_is_refused(self) -> None:
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection()), actor=_actor()
        ).execute(arguments=_release_args(reason="   "))
        assert result.is_error is True

    async def test_unresolvable_actor_is_refused(self) -> None:
        """A token outliving its agent record must not be able to deploy."""
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection()), actor=None
        ).execute(arguments=_release_args())
        assert result.is_error is True


class TestTargetAllowlist:
    async def test_unlisted_target_is_refused_before_credentials(self) -> None:
        deps = _deps(conn=_connection(), targets=frozenset({"staging-web"}))
        result = await DeployReleaseTool(deps=deps, actor=_actor()).execute(
            arguments=_release_args()
        )
        assert result.is_error is True
        catalog = deps.runtime.connection_catalog
        cast("AsyncMock", catalog.get_credentials).assert_not_awaited()
        cast("AsyncMock", catalog.get).assert_not_awaited()

    async def test_empty_allowlist_allows_nothing(self) -> None:
        result = await DeployRunTool(
            deps=_deps(conn=_connection(), targets=frozenset())
        ).execute(arguments={"action": "list", "target": _TARGET})
        assert result.is_error is True


class TestSetupRequired:
    async def test_missing_connection_reports_not_found(self) -> None:
        result = await DeployRunTool(deps=_deps(conn=None)).execute(
            arguments={"action": "list", "target": _TARGET}
        )
        assert result.is_error is True
        assert "not found" in result.content

    async def test_incomplete_target_names_what_a_human_must_supply(self) -> None:
        result = await DeployRunTool(
            deps=_deps(conn=_connection(project="", platform=""))
        ).execute(arguments={"action": "list", "target": _TARGET})
        assert result.is_error is True
        assert "needs setup" in result.content
        assert "a supported platform" in result.content
        assert "the project identifier" in result.content

    async def test_wrong_connection_type_reports_setup_required(self) -> None:
        result = await DeployRunTool(
            deps=_deps(conn=_connection(ctype=ConnectionType.GENERIC_HTTP))
        ).execute(arguments={"action": "list", "target": _TARGET})
        assert result.is_error is True
        assert "not a deploy target" in result.content

    async def test_missing_base_url_reports_setup_required(self) -> None:
        result = await DeployRunTool(
            deps=_deps(conn=_connection(base_url=None))
        ).execute(arguments={"action": "list", "target": _TARGET})
        assert result.is_error is True
        assert "the platform API URL" in result.content

    async def test_credential_failure_is_reported(self) -> None:
        result = await DeployRunTool(
            deps=_deps(
                conn=_connection(),
                credentials_error=SecretRetrievalError("vault down"),
            )
        ).execute(arguments={"action": "list", "target": _TARGET})
        assert result.is_error is True


class TestReadSurface:
    @respx.mock
    async def test_get_deployment_never_parks(self) -> None:
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(
                200, json={"id": "dpl_1", "readyState": "READY", "url": "x.example.com"}
            )
        )
        result = await DeployRunTool(deps=_deps(conn=_connection())).execute(
            arguments={"action": "get", "target": _TARGET, "deployment_id": "dpl_1"}
        )
        assert result.is_error is False
        assert "requires_parking" not in result.metadata
        assert json.loads(result.content)["state"] == "ready"

    @respx.mock
    async def test_list_deployments(self) -> None:
        respx.get(f"{_HOST}/v6/deployments").mock(
            return_value=httpx.Response(
                200,
                json={
                    "deployments": [
                        {"uid": "dpl_2", "readyState": "BUILDING"},
                        {"uid": "dpl_1", "readyState": "READY"},
                    ]
                },
            )
        )
        result = await DeployRunTool(deps=_deps(conn=_connection())).execute(
            arguments={"action": "list", "target": _TARGET}
        )
        payload = json.loads(result.content)
        assert [d["id"] for d in payload] == ["dpl_2", "dpl_1"]

    @respx.mock
    async def test_logs_are_truncated_to_the_operator_budget(self) -> None:
        respx.get(f"{_HOST}/v3/deployments/dpl_1/events").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"created": 1, "text": "a" * 30},
                    {"created": 2, "text": "b" * 30},
                ],
            )
        )
        result = await DeployRunTool(
            deps=_deps(conn=_connection(), max_log_chars=40)
        ).execute(
            arguments={"action": "logs", "target": _TARGET, "deployment_id": "dpl_1"}
        )
        payload = json.loads(result.content)
        assert len(payload["lines"]) == 1
        assert payload["truncated"] is True

    async def test_get_without_deployment_id_is_rejected(self) -> None:
        result = await DeployRunTool(deps=_deps(conn=_connection())).execute(
            arguments={"action": "get", "target": _TARGET}
        )
        assert result.is_error is True

    async def test_sensitive_connection_gates_a_read(self) -> None:
        """An operator-marked-sensitive target parks even a read."""
        result = await DeployRunTool(
            deps=_deps(conn=_connection(sensitive=True))
        ).execute(arguments={"action": "list", "target": _TARGET})
        assert result.metadata["requires_parking"] is True


class TestResilience:
    @respx.mock
    async def test_rate_limit_is_surfaced_with_retry_after(self) -> None:
        """A 429 becomes a typed rate-limit result carrying the cooldown."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "12"})
        )
        result = await DeployRunTool(deps=_deps(conn=_connection())).execute(
            arguments={"action": "get", "target": _TARGET, "deployment_id": "dpl_1"}
        )
        assert result.is_error is True
        assert result.metadata["retry_after_seconds"] == 12.0


class TestArgumentSafety:
    async def test_traversal_in_deployment_id_is_rejected(self) -> None:
        result = await DeployRunTool(deps=_deps(conn=_connection())).execute(
            arguments={
                "action": "get",
                "target": _TARGET,
                "deployment_id": "../../admin",
            }
        )
        assert result.is_error is True

    async def test_environment_argument_is_rejected(self) -> None:
        """The environment is the connection's to declare, never the caller's."""
        result = await DeployReleaseTool(
            deps=_deps(conn=_connection()), actor=_actor()
        ).execute(arguments=_release_args(environment="staging"))
        assert result.is_error is True
