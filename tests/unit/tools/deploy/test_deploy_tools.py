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
from pydantic import BaseModel, ConfigDict

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
from synthorg.integrations.deploy_api import DeployApiClient
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.deploy._args import DeployRunArgs
from synthorg.tools.deploy._runtime import DeployToolDeps, DeployToolsRuntime
from synthorg.tools.deploy.deploy_tools import DeployReleaseTool, DeployRunTool
from synthorg.tools.deploy.errors import (
    DeployToolArgumentError,
    DeployUnsupportedError,
)
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
        model=ModelConfig(provider="example-provider", model_id="example-capable-001"),
        hiring_date=date(2026, 1, 1),
    )


def _connection(
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


def _deps(
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


class _NotDeployArgs(BaseModel):
    """An args model with no ``target``, standing in for a future rename."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: str = "list"
    # Satisfies the admin guardrail so a precondition test reaches the
    # shape check rather than stopping at the confirm/reason triple.
    confirm: bool = True
    reason: str = "ship the release"


def _client() -> DeployApiClient:
    """A stand-in client for the guards that reject before any call."""
    return cast("DeployApiClient", mock_of[DeployApiClient]())


def _platform_deployment(**overrides: object) -> dict[str, object]:
    """A platform payload owned by the fixture connection's project + env."""
    payload: dict[str, object] = {
        "id": "dpl_1",
        "readyState": "READY",
        "name": "acme-web",
        "target": "production",
    }
    payload.update(overrides)
    return payload


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

    async def test_non_deploy_args_fail_loudly_not_as_a_denial(self) -> None:
        """A wrong args shape is a defect, not a governance refusal.

        Reading ``target`` off a bare model would degrade a renamed or
        removed field into "not allowlisted", disguising a programming
        error as a legitimate policy denial.
        """
        tool = DeployRunTool(deps=_deps(conn=_connection()))
        with pytest.raises(DeployToolArgumentError):
            await tool._resolve_connection(_NotDeployArgs())


class TestArgumentShapeInvariants:
    """The defensive narrowing guards must fail loudly, never degrade.

    Every one of these is unreachable through ``execute`` because the args
    model enforces the shape first. They exist so a future rename surfaces
    as a typed argument error instead of a silent wrong-field read, and
    they are exercised directly for exactly that reason.
    """

    def test_release_preconditions_reject_a_non_release_shape(self) -> None:
        tool = DeployReleaseTool(deps=_deps(conn=_connection()), actor=_actor())
        with pytest.raises(DeployToolArgumentError):
            tool._check_preconditions(_NotDeployArgs())

    async def test_release_dispatch_rejects_a_non_release_shape(self) -> None:
        tool = DeployReleaseTool(deps=_deps(conn=_connection()), actor=_actor())
        with pytest.raises(DeployToolArgumentError):
            await tool._dispatch(_client(), _NotDeployArgs())

    async def test_read_dispatch_rejects_a_non_read_shape(self) -> None:
        tool = DeployRunTool(deps=_deps(conn=_connection()))
        with pytest.raises(DeployToolArgumentError):
            await tool._dispatch(_client(), _NotDeployArgs())

    def test_missing_deployment_id_fails_loudly(self) -> None:
        """The args validator enforces this; the boundary must not assume it."""
        with pytest.raises(DeployToolArgumentError):
            DeployRunTool._deployment_id(DeployRunArgs(action="list", target=_TARGET))


class TestRuntimeBinding:
    def test_no_single_bound_connection(self) -> None:
        """The allowlist is this family's bound surface, not one connection."""
        runtime = _deps(conn=_connection()).runtime
        assert runtime.connection_name == ""
        assert runtime.allowed_targets == frozenset({_TARGET})


class TestPlatformBinding:
    async def test_plain_http_target_is_rejected(self) -> None:
        """Plain HTTP would put the brokered platform token on the wire."""
        result = await DeployRunTool(
            deps=_deps(conn=_connection(base_url="http://deploy.example.com"))
        ).execute(arguments={"action": "list", "target": _TARGET})
        assert result.is_error is True

    def test_platform_without_a_wired_client_is_refused(self) -> None:
        """A preset added ahead of its client must refuse, never guess."""
        tool = DeployRunTool(deps=_deps(conn=_connection()))
        with pytest.raises(DeployUnsupportedError):
            tool._build_client(
                conn=_connection(platform=""), token="t0ken", timeout=5.0
            )


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
                200, json=_platform_deployment(url="x.example.com")
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
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(200, json=_platform_deployment())
        )
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

    @respx.mock
    async def test_a_staging_target_cannot_read_a_production_deployment(self) -> None:
        """A remembered production id must not cross the target binding."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(200, json=_platform_deployment())
        )
        result = await DeployRunTool(
            deps=_deps(conn=_connection(environment="staging"))
        ).execute(
            arguments={"action": "get", "target": _TARGET, "deployment_id": "dpl_1"}
        )
        assert result.is_error is True

    @respx.mock
    async def test_a_staging_target_cannot_read_a_production_build_log(self) -> None:
        """Build logs echo environment detail, so they gate on the record."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(200, json=_platform_deployment())
        )
        events = respx.get(f"{_HOST}/v3/deployments/dpl_1/events").mock(
            return_value=httpx.Response(200, json=[{"created": 1, "text": "secret"}])
        )
        result = await DeployRunTool(
            deps=_deps(conn=_connection(environment="staging"))
        ).execute(
            arguments={"action": "logs", "target": _TARGET, "deployment_id": "dpl_1"}
        )
        assert result.is_error is True
        assert events.call_count == 0
        assert "secret" not in result.content

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

    @respx.mock
    @pytest.mark.parametrize("status", [401, 500])
    async def test_platform_failures_surface_as_a_tool_error(self, status: int) -> None:
        """A revoked token and a platform outage both stay inside the family."""
        respx.get(f"{_HOST}/v13/deployments/dpl_1").mock(
            return_value=httpx.Response(status)
        )
        result = await DeployRunTool(deps=_deps(conn=_connection())).execute(
            arguments={"action": "get", "target": _TARGET, "deployment_id": "dpl_1"}
        )
        assert result.is_error is True
        assert "t0ken" not in result.content


class TestArgumentSafety:
    @pytest.mark.parametrize(
        "deployment_id",
        ["/absolute", "dpl?admin=1", "dpl#frag", "user@host", "dpl%2e%2e", "dpl\x00id"],
        ids=["leading-slash", "query", "fragment", "userinfo", "encoded", "control"],
    )
    async def test_url_structure_characters_are_rejected(
        self, deployment_id: str
    ) -> None:
        """A segment reaching a REST path must not smuggle URL structure."""
        result = await DeployRunTool(deps=_deps(conn=_connection())).execute(
            arguments={
                "action": "get",
                "target": _TARGET,
                "deployment_id": deployment_id,
            }
        )
        assert result.is_error is True

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
