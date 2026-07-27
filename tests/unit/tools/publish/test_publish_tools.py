"""Unit tests for the governed publish tools.

Exercises the destructive push path (guardrail triple -> park -> approve ->
re-issue -> consume), the read surface (which never parks unless the target
is sensitive), the target allowlist checked before any credential is brokered,
the per-connection channel binding, setup-required reporting, actor
fail-closed, and egress binding (respx only mocks the target's host, so a call
to another host would fail to match).
"""

import hashlib
import json
from datetime import date
from pathlib import Path
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
from synthorg.tools.publish._runtime import PublishToolDeps, PublishToolsRuntime
from synthorg.tools.publish.publish_tools import PublishInspectTool, PublishPushTool
from tests._shared.ids import as_uuid
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_HOST = "https://registry.example.com"
_TARGET = "prod-images"
_REPO = "acme/app"
_MANIFEST = b'{"schemaVersion":2,"config":{},"layers":[]}'
# The reference must genuinely hash to the returned bytes: the client verifies
# a manifest fetched by digest against its content.
_DIGEST = "sha256:" + hashlib.sha256(_MANIFEST).hexdigest()
_PUBLISH_PRODUCTION = ActionType.PUBLISH_PRODUCTION.value
_PUBLISH_STAGING = ActionType.PUBLISH_STAGING.value
_COMMS_EXTERNAL = ActionType.COMMS_EXTERNAL.value


def _actor() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("publisher"),
        name="publisher",
        role="engineer",
        department="engineering",
        model=ModelConfig(provider="example-provider", model_id="example-medium-001"),
        hiring_date=date(2026, 1, 1),
    )


def _connection(
    *,
    name: str = _TARGET,
    ctype: ConnectionType = ConnectionType.REGISTRY,
    base_url: str | None = _HOST,
    channel: str = "production",
    provider: str = "generic_oci",
    repository: str = _REPO,
    sensitive: bool = False,
) -> Connection:
    metadata = {
        k: v
        for k, v in (
            ("channel", channel),
            ("provider", provider),
            ("repository", repository),
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
    workspace_root: Path | None = None,
    max_manifest_bytes: int = 1_000_000,
    credentials_error: Exception | None = None,
) -> PublishToolDeps:
    get_credentials = (
        AsyncMock(spec=ConnectionCatalog.get_credentials, side_effect=credentials_error)
        if credentials_error is not None
        else AsyncMock(
            spec=ConnectionCatalog.get_credentials, return_value={"token": "t0ken"}
        )
    )
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=conn),
        get_credentials=get_credentials,
    )
    return PublishToolDeps(
        runtime=PublishToolsRuntime(
            connection_catalog=catalog,
            allowed_targets=targets,
            timeout_seconds=5.0,
            max_manifest_bytes=max_manifest_bytes,
            max_image_bytes=1_000_000_000,
            workspace_root=workspace_root or Path.cwd(),
        ),
        approval_store=store or ApprovalStore(),
        agent_id="agent-1",
        task_id="task-1",
        effective_autonomy=autonomy,
    )


def _push_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "action": "push",
        "target": _TARGET,
        "dest_tag": "latest",
        "method": "digest_promote",
        "source_digest": _DIGEST,
        "confirm": True,
        "reason": "ship the release",
    }
    args.update(overrides)
    return args


def _mock_promote() -> None:
    """Mock the manifest read-by-digest and the tag PUT a promote performs."""
    respx.get(f"{_HOST}/v2/{_REPO}/manifests/{_DIGEST}").mock(
        return_value=httpx.Response(
            200,
            content=_MANIFEST,
            headers={
                "Docker-Content-Digest": _DIGEST,
                "Content-Type": "application/vnd.oci.image.manifest.v1+json",
            },
        )
    )
    respx.put(f"{_HOST}/v2/{_REPO}/manifests/latest").mock(
        return_value=httpx.Response(201, headers={"Docker-Content-Digest": _DIGEST})
    )


class TestPushApprovalFlow:
    @respx.mock
    async def test_push_parks_then_consumes_on_approval(self) -> None:
        put_route = respx.put(f"{_HOST}/v2/{_REPO}/manifests/latest").mock(
            return_value=httpx.Response(201, headers={"Docker-Content-Digest": _DIGEST})
        )
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/{_DIGEST}").mock(
            return_value=httpx.Response(
                200, content=_MANIFEST, headers={"Docker-Content-Digest": _DIGEST}
            )
        )
        store = ApprovalStore()
        parked = await PublishPushTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_push_args())
        assert parked.metadata["requires_parking"] is True
        assert put_route.call_count == 0  # no egress on a parked push

        approval_id = cast("str", parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))

        resumed = await PublishPushTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_push_args())
        assert resumed.is_error is False
        assert json.loads(resumed.content)["digest"] == _DIGEST
        assert put_route.call_count == 1

    @respx.mock
    async def test_approved_grant_does_not_cover_a_mutated_payload(self) -> None:
        """The signature binds the arguments, not just the tool and target."""
        _mock_promote()
        store = ApprovalStore()
        parked = await PublishPushTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_push_args())
        approval_id = cast("str", parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))

        mutated = await PublishPushTool(
            deps=_deps(conn=_connection(), store=store), actor=_actor()
        ).execute(arguments=_push_args(dest_tag="production"))
        assert mutated.metadata["requires_parking"] is True


class TestChannelBinding:
    async def test_production_target_parks_under_production_action(self) -> None:
        result = await PublishPushTool(
            deps=_deps(conn=_connection(channel="production")), actor=_actor()
        ).execute(arguments=_push_args())
        assert result.metadata["action_type"] == _PUBLISH_PRODUCTION

    async def test_staging_target_parks_under_staging_action(self) -> None:
        result = await PublishPushTool(
            deps=_deps(conn=_connection(channel="staging")), actor=_actor()
        ).execute(arguments=_push_args())
        assert result.metadata["action_type"] == _PUBLISH_STAGING

    async def test_unknown_channel_falls_back_to_production(self) -> None:
        """A mislabelled target is over-gated, never treated as throwaway."""
        result = await PublishPushTool(
            deps=_deps(conn=_connection(channel="whatever")), actor=_actor()
        ).execute(arguments=_push_args())
        assert result.metadata["action_type"] == _PUBLISH_PRODUCTION

    async def test_comms_autonomy_does_not_auto_approve_a_push(self) -> None:
        result = await PublishPushTool(
            deps=_deps(conn=_connection(), autonomy=_auto_approve(_COMMS_EXTERNAL)),
            actor=_actor(),
        ).execute(arguments=_push_args())
        assert result.metadata["requires_parking"] is True

    async def test_staging_autonomy_does_not_auto_approve_production(self) -> None:
        result = await PublishPushTool(
            deps=_deps(
                conn=_connection(channel="production"),
                autonomy=_auto_approve(_PUBLISH_STAGING),
            ),
            actor=_actor(),
        ).execute(arguments=_push_args())
        assert result.metadata["requires_parking"] is True

    @respx.mock
    async def test_matching_channel_autonomy_dispatches(self) -> None:
        _mock_promote_staging()
        result = await PublishPushTool(
            deps=_deps(
                conn=_connection(channel="staging"),
                autonomy=_auto_approve(_PUBLISH_STAGING),
            ),
            actor=_actor(),
        ).execute(arguments=_push_args())
        assert result.is_error is False
        assert "requires_parking" not in result.metadata


def _mock_promote_staging() -> None:
    respx.get(f"{_HOST}/v2/{_REPO}/manifests/{_DIGEST}").mock(
        return_value=httpx.Response(
            200, content=_MANIFEST, headers={"Docker-Content-Digest": _DIGEST}
        )
    )
    respx.put(f"{_HOST}/v2/{_REPO}/manifests/latest").mock(
        return_value=httpx.Response(201, headers={"Docker-Content-Digest": _DIGEST})
    )


class TestGuardrails:
    async def test_missing_confirm_is_refused(self) -> None:
        result = await PublishPushTool(
            deps=_deps(conn=_connection()), actor=_actor()
        ).execute(arguments=_push_args(confirm=False))
        assert result.is_error is True
        assert "confirm" in result.content

    async def test_blank_reason_is_refused(self) -> None:
        result = await PublishPushTool(
            deps=_deps(conn=_connection()), actor=_actor()
        ).execute(arguments=_push_args(reason="   "))
        assert result.is_error is True
        assert "reason" in result.content

    async def test_missing_actor_is_refused(self) -> None:
        """Actor fail-closed: an unattributable push never reaches the gate."""
        result = await PublishPushTool(
            deps=_deps(conn=_connection()), actor=None
        ).execute(arguments=_push_args())
        assert result.is_error is True
        assert "actor" in result.content


class TestAllowlistAndSetup:
    async def test_unlisted_target_refused_before_credentials(self) -> None:
        deps = _deps(conn=_connection(), targets=frozenset({"other"}))
        result = await PublishPushTool(deps=deps, actor=_actor()).execute(
            arguments=_push_args()
        )
        assert result.is_error is True
        # The credential broker is never reached for a disallowed target.
        catalog = cast("AsyncMock", deps.runtime.connection_catalog.get_credentials)
        assert catalog.await_count == 0

    async def test_missing_repository_reports_setup_required(self) -> None:
        result = await PublishPushTool(
            deps=_deps(conn=_connection(repository="")), actor=_actor()
        ).execute(arguments=_push_args())
        assert result.is_error is True
        assert "needs setup" in result.content

    async def test_wrong_connection_type_reports_setup_required(self) -> None:
        result = await PublishInspectTool(
            deps=_deps(conn=_connection(ctype=ConnectionType.GITHUB))
        ).execute(arguments={"action": "list_tags", "target": _TARGET})
        assert result.is_error is True
        assert "not a registry target" in result.content


class TestReads:
    @respx.mock
    async def test_list_tags_does_not_park(self) -> None:
        route = respx.get(f"{_HOST}/v2/{_REPO}/tags/list").mock(
            return_value=httpx.Response(200, json={"name": _REPO, "tags": ["v1"]})
        )
        result = await PublishInspectTool(deps=_deps(conn=_connection())).execute(
            arguments={"action": "list_tags", "target": _TARGET}
        )
        assert "requires_parking" not in result.metadata
        assert route.call_count == 1
        assert json.loads(result.content)["tags"] == ["v1"]

    @respx.mock
    async def test_get_manifest_returns_digest_without_raw(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/v1").mock(
            return_value=httpx.Response(
                200,
                content=_MANIFEST,
                headers={
                    "Docker-Content-Digest": _DIGEST,
                    "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                },
            )
        )
        result = await PublishInspectTool(deps=_deps(conn=_connection())).execute(
            arguments={"action": "get_manifest", "target": _TARGET, "reference": "v1"}
        )
        body = json.loads(result.content)
        assert body["digest"] == _DIGEST
        assert "raw" not in body

    async def test_read_on_sensitive_target_parks(self) -> None:
        result = await PublishInspectTool(
            deps=_deps(conn=_connection(sensitive=True))
        ).execute(arguments={"action": "list_tags", "target": _TARGET})
        assert result.metadata["requires_parking"] is True

    @respx.mock
    async def test_get_manifest_over_cap_is_refused(self) -> None:
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/v1").mock(
            return_value=httpx.Response(
                200,
                content=_MANIFEST,
                headers={"Docker-Content-Digest": _DIGEST},
            )
        )
        result = await PublishInspectTool(
            deps=_deps(conn=_connection(), max_manifest_bytes=1)
        ).execute(
            arguments={"action": "get_manifest", "target": _TARGET, "reference": "v1"}
        )
        assert result.is_error is True
        assert "manifest size cap" in result.content


class TestAutoMethod:
    @respx.mock
    async def test_auto_with_digest_dispatches_promote(self) -> None:
        """method='auto' with a source digest resolves to a promote e2e."""
        _mock_promote_staging()
        result = await PublishPushTool(
            deps=_deps(
                conn=_connection(channel="staging"),
                autonomy=_auto_approve(_PUBLISH_STAGING),
            ),
            actor=_actor(),
        ).execute(arguments=_push_args(method="auto"))
        assert result.is_error is False
        assert json.loads(result.content)["method"] == "digest_promote"


class TestUpstreamErrors:
    @respx.mock
    async def test_rate_limit_surfaces_retry_after(self) -> None:
        """A 429 surfaces retry_after_seconds in the result metadata."""
        respx.get(f"{_HOST}/v2/{_REPO}/manifests/{_DIGEST}").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "12"})
        )
        result = await PublishPushTool(
            deps=_deps(
                conn=_connection(channel="production"),
                autonomy=_auto_approve(_PUBLISH_PRODUCTION),
            ),
            actor=_actor(),
        ).execute(arguments=_push_args())
        assert result.is_error is True
        assert result.metadata["retry_after_seconds"] == 12.0

    async def test_credential_broker_failure_is_surfaced(self) -> None:
        """A secret-retrieval failure maps to a credential error, not a crash."""
        deps = _deps(
            conn=_connection(),
            autonomy=_auto_approve(_PUBLISH_PRODUCTION),
            credentials_error=SecretRetrievalError("vault unreachable"),
        )
        result = await PublishPushTool(deps=deps, actor=_actor()).execute(
            arguments=_push_args()
        )
        assert result.is_error is True
        assert "broker credentials" in result.content
