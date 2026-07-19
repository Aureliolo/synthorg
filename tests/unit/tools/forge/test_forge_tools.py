"""Unit tests for the resource-grouped forge agent tools.

Exercises the read surface, the write approval flow (park -> approve ->
re-issue -> consume), auto-approval, connection / unsupported-forge /
argument guards, and egress binding (respx only mocks the connection's
host, so a call to another host would fail to match). Uses a vendor-
neutral Forgejo-typed connection; vendor hostnames stay in the client
layer tests.
"""

import base64
import json
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalStatus
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.engine.errors import GitBackendConfigError
from synthorg.engine.workspace.git_backend.forge_api import ForgeAgentApiClient
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.tools.forge._runtime import ForgeToolDeps, ForgeToolsRuntime
from synthorg.tools.forge.forge_tools import (
    ForgeCiTool,
    ForgeIssueTool,
    ForgePullRequestTool,
    ForgeRepoTool,
)
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit

_FJ = "https://code.example.com/api/v1"
_COMMS_EXTERNAL = "comms:external"


def _auto_autonomy() -> EffectiveAutonomy:
    return EffectiveAutonomy(
        level=AutonomyLevel.FULL,
        auto_approve_actions=frozenset({_COMMS_EXTERNAL}),
        human_approval_actions=frozenset(),
        security_agent=False,
    )


def _connection(
    *,
    ctype: ConnectionType = ConnectionType.FORGEJO,
    base_url: str = "https://code.example.com",
) -> Connection:
    return Connection(
        name="forge",
        connection_type=ctype,
        auth_method=AuthMethod.BEARER_TOKEN,
        base_url=base_url,
    )


def _deps(  # noqa: PLR0913 -- test helper mirrors the tool's collaborators
    *,
    conn: Connection | None,
    store: ApprovalStore | None = None,
    autonomy: EffectiveAutonomy | None = None,
    credentials: dict[str, str] | None = None,
    credentials_error: Exception | None = None,
    max_read_chars: int = 1000,
) -> ForgeToolDeps:
    get_credentials = AsyncMock(spec=ConnectionCatalog.get_credentials)
    if credentials_error is not None:
        get_credentials.side_effect = credentials_error
    else:
        get_credentials.return_value = credentials or {"token": "t0ken"}
    catalog = mock_of[ConnectionCatalog](
        get=AsyncMock(spec=ConnectionCatalog.get, return_value=conn),
        get_credentials=get_credentials,
    )
    runtime = ForgeToolsRuntime(
        connection_catalog=catalog,
        connection_name="forge",
        timeout_seconds=5.0,
        max_read_chars=max_read_chars,
    )
    return ForgeToolDeps(
        runtime=runtime,
        approval_store=store or ApprovalStore(),
        agent_id="agent-1",
        task_id="task-1",
        effective_autonomy=autonomy,
    )


class TestForgeRepoTool:
    @respx.mock
    async def test_get_repo(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "full_name": "acme/proj-1",
                    "default_branch": "main",
                    "private": True,
                    "clone_url": "https://code.example.com/acme/proj-1.git",
                },
            ),
        )
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is False
        assert json.loads(result.content)["full_name"] == "acme/proj-1"

    @respx.mock
    async def test_read_file(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1/contents/README.md").mock(
            return_value=httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "encoding": "base64",
                    "content": base64.b64encode(b"hello world").decode(),
                    "sha": "a",
                    "size": 11,
                },
            ),
        )
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={
                "action": "read_file",
                "owner": "acme",
                "repo": "proj-1",
                "path": "README.md",
            }
        )
        assert result.content == "hello world"
        assert result.metadata["truncated"] is False

    @respx.mock
    async def test_read_file_truncates(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1/contents/big.txt").mock(
            return_value=httpx.Response(
                200,
                json={
                    "path": "big.txt",
                    "encoding": "base64",
                    "content": base64.b64encode(b"x" * 50).decode(),
                    "sha": "a",
                    "size": 50,
                },
            ),
        )
        tool = ForgeRepoTool(deps=_deps(conn=_connection(), max_read_chars=10))
        result = await tool.execute(
            arguments={
                "action": "read_file",
                "owner": "acme",
                "repo": "proj-1",
                "path": "big.txt",
            }
        )
        assert result.metadata["truncated"] is True
        assert result.content.startswith("xxxxxxxxxx")


class TestForgeIssueApprovalFlow:
    @respx.mock
    async def test_open_parks_then_consumes_on_approval(self) -> None:
        route = respx.post(f"{_FJ}/repos/acme/proj-1/issues").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 5,
                    "title": "bug",
                    "state": "open",
                    "user": {"login": "a"},
                },
            ),
        )
        store = ApprovalStore()
        open_args = {
            "action": "open",
            "owner": "acme",
            "repo": "proj-1",
            "title": "bug",
            "body": "desc",
        }
        parked = await ForgeIssueTool(
            deps=_deps(conn=_connection(), store=store)
        ).execute(arguments=dict(open_args))
        assert parked.metadata["requires_parking"] is True
        assert route.call_count == 0  # no egress on a parked write

        approval_id = cast("str", parked.metadata["approval_id"])
        item = await store.get(approval_id)
        assert item is not None
        await store.save(item.model_copy(update={"status": ApprovalStatus.APPROVED}))

        resumed = await ForgeIssueTool(
            deps=_deps(conn=_connection(), store=store)
        ).execute(arguments=dict(open_args))
        assert resumed.is_error is False
        assert route.call_count == 1
        assert json.loads(resumed.content)["number"] == 5

    @respx.mock
    async def test_auto_approved_write_skips_parking(self) -> None:
        route = respx.post(f"{_FJ}/repos/acme/proj-1/issues/5/comments").mock(
            return_value=httpx.Response(
                200, json={"id": 1, "user": {"login": "a"}, "body": "hi"}
            ),
        )
        autonomy = _auto_autonomy()
        tool = ForgeIssueTool(deps=_deps(conn=_connection(), autonomy=autonomy))
        result = await tool.execute(
            arguments={
                "action": "comment",
                "owner": "acme",
                "repo": "proj-1",
                "number": 5,
                "body": "hi",
            }
        )
        assert result.is_error is False
        assert route.call_count == 1

    @respx.mock
    async def test_read_action_never_parks(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1/issues/5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 5,
                    "title": "bug",
                    "state": "open",
                    "user": {"login": "a"},
                },
            ),
        )
        tool = ForgeIssueTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "get", "owner": "acme", "repo": "proj-1", "number": 5}
        )
        assert result.is_error is False
        assert "requires_parking" not in result.metadata


class TestForgePullRequestTool:
    @respx.mock
    async def test_review_auto_approved(self) -> None:
        route = respx.post(f"{_FJ}/repos/acme/proj-1/pulls/7/reviews").mock(
            return_value=httpx.Response(
                200, json={"id": 1, "state": "APPROVED", "user": {"login": "r"}}
            ),
        )
        autonomy = _auto_autonomy()
        tool = ForgePullRequestTool(deps=_deps(conn=_connection(), autonomy=autonomy))
        result = await tool.execute(
            arguments={
                "action": "review",
                "owner": "acme",
                "repo": "proj-1",
                "number": 7,
                "decision": "approve",
            }
        )
        assert result.is_error is False
        assert b'"event":"APPROVED"' in route.calls.last.request.content

    @respx.mock
    async def test_merge_auto_approved(self) -> None:
        respx.post(f"{_FJ}/repos/acme/proj-1/pulls/7/merge").mock(
            return_value=httpx.Response(200),
        )
        autonomy = _auto_autonomy()
        tool = ForgePullRequestTool(deps=_deps(conn=_connection(), autonomy=autonomy))
        result = await tool.execute(
            arguments={
                "action": "merge",
                "owner": "acme",
                "repo": "proj-1",
                "number": 7,
                "method": "squash",
            }
        )
        assert result.is_error is False
        assert json.loads(result.content)["merged"] is True


class TestForgeCiTool:
    async def test_ci_unsupported_on_forgejo(self) -> None:
        tool = ForgeCiTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "list_runs", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True
        assert "not" in result.content.lower()


class TestForgeToolGuards:
    async def test_connection_not_found(self) -> None:
        tool = ForgeRepoTool(deps=_deps(conn=None))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True
        assert "not found" in result.content.lower()

    async def test_unsupported_forge_type(self) -> None:
        tool = ForgeRepoTool(deps=_deps(conn=_connection(ctype=ConnectionType.GITLAB)))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True

    async def test_read_file_without_path_rejected(self) -> None:
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "read_file", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True
        assert "invalid arguments" in result.content.lower()

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc",
            "..",
            "a/b",
            "a%2e%2e",
            "a?x",
            "a#x",
            "a@x",
            "a%00",
            "a\\b",
        ],
    )
    async def test_owner_traversal_rejected(self, bad: str) -> None:
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": bad, "repo": "proj-1"}
        )
        assert result.is_error is True
        assert "invalid arguments" in result.content.lower()

    @pytest.mark.parametrize("bad", ["../../etc", "a/b", "a%2e%2e", "a?x", "a#x"])
    async def test_repo_traversal_rejected(self, bad: str) -> None:
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": bad}
        )
        assert result.is_error is True
        assert "invalid arguments" in result.content.lower()


class TestForgeToolErrorMapping:
    async def test_credential_retrieval_failure_no_egress(self) -> None:
        tool = ForgeRepoTool(
            deps=_deps(
                conn=_connection(),
                credentials_error=SecretRetrievalError("backend down"),
            )
        )
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True

    async def test_missing_token_errors(self) -> None:
        tool = ForgeRepoTool(deps=_deps(conn=_connection(), credentials={"nope": "x"}))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True
        assert "token" in result.content.lower()

    @respx.mock
    async def test_auth_401_maps_to_error(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1").mock(
            return_value=httpx.Response(401, json={"message": "bad creds"})
        )
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True

    @respx.mock
    async def test_upstream_500_maps_to_error(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1").mock(
            return_value=httpx.Response(500, json={"message": "boom"})
        )
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True

    @respx.mock
    async def test_rate_limit_surfaces_retry_after(self) -> None:
        respx.get(f"{_FJ}/repos/acme/proj-1").mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "17"}, json={"message": "slow"}
            )
        )
        tool = ForgeRepoTool(deps=_deps(conn=_connection()))
        result = await tool.execute(
            arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
        )
        assert result.is_error is True
        assert result.metadata["retry_after_seconds"] == 17.0

    async def test_build_config_error_maps_to_argument_error(self) -> None:
        target = "synthorg.tools.forge._base.build_forge_agent_api_client"
        with patch(target, side_effect=GitBackendConfigError("bad url")):
            tool = ForgeRepoTool(deps=_deps(conn=_connection()))
            result = await tool.execute(
                arguments={"action": "get_repo", "owner": "acme", "repo": "proj-1"}
            )
        assert result.is_error is True

    async def test_aclose_runs_in_finally(self) -> None:
        client = mock_of[ForgeAgentApiClient](
            get_repo=AsyncMock(side_effect=RuntimeError("upstream blew up")),
            aclose=AsyncMock(),
        )
        target = "synthorg.tools.forge._base.build_forge_agent_api_client"
        with patch(target, return_value=client):
            tool = ForgeRepoTool(deps=_deps(conn=_connection()))
            with pytest.raises(RuntimeError, match="upstream blew up"):
                await tool.execute(
                    arguments={
                        "action": "get_repo",
                        "owner": "acme",
                        "repo": "proj-1",
                    }
                )
        client.aclose.assert_awaited_once()
