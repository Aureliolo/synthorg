"""Unit tests for the credentialed-MCP JSON-RPC dispatch."""

from pathlib import Path

import pytest

from synthorg.api.mcp_gateway.protocol import dispatch_mcp
from synthorg.api.mcp_gateway.tools import CredentialedToolContext
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.integrations.connections.catalog import ConnectionCatalog
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


def _ctx() -> CredentialedToolContext:
    return CredentialedToolContext(
        connection_catalog=mock_of[ConnectionCatalog](),
        approval_store=mock_of[ApprovalStoreProtocol](),
        clock=FakeClock(),
        forge_connection="forge-conn",
        chat_connection="chat-conn",
        forge_timeout_seconds=30.0,
        chat_timeout_seconds=30.0,
        forge_max_read_chars=2000,
        deploy_targets=frozenset({"prod"}),
        deploy_timeout_seconds=30.0,
        deploy_max_log_chars=20000,
        publish_targets=frozenset({"prod-images"}),
        publish_timeout_seconds=60.0,
        publish_max_manifest_bytes=4_000_000,
        publish_max_image_bytes=2_000_000_000,
        workspace_root=Path.cwd(),
    )


async def _dispatch(
    message: dict[str, object],
    *,
    capabilities: tuple[str, ...],
    denied: tuple[str, ...] = (),
    opens: list[str] | None = None,
) -> dict[str, object] | None:
    async def _open() -> CredentialedToolContext:
        if opens is not None:
            opens.append("opened")
        return _ctx()

    return await dispatch_mcp(
        message,
        open_context=_open,
        agent_id="agent-1",
        capabilities=capabilities,
        denied=denied,
    )


async def test_handshake_never_opens_the_credentialed_context() -> None:
    # A deployment with no forge or chat connection cannot build one, and the
    # embedded harness cannot construct its agent without completing this
    # handshake, so opening it here would make the capability unusable by
    # default on the very deployments that ship it enabled.
    opens: list[str] = []

    for method in ("initialize", "tools/list"):
        await _dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": method},
            capabilities=("forge:read",),
            opens=opens,
        )

    assert opens == []


async def test_tools_call_opens_the_credentialed_context() -> None:
    opens: list[str] = []

    await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "forge_repo", "arguments": {}},
        },
        capabilities=("forge:read",),
        opens=opens,
    )

    assert opens == ["opened"]


async def test_initialize_advertises_tools() -> None:
    response = await _dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, capabilities=()
    )

    assert response is not None
    assert "tools" in response["result"]["capabilities"]  # type: ignore[index]


async def test_tools_list_is_capability_scoped() -> None:
    response = await _dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        capabilities=("forge:read",),
    )

    assert response is not None
    names = {t["name"] for t in response["result"]["tools"]}  # type: ignore[index]
    assert names == {"forge_repo", "forge_ci"}


async def test_tools_list_honours_denied_kill_switch() -> None:
    # A deploy:* grant would expose the deploy tools, but the kill switch
    # (threaded as ``denied``) must hide them from discovery too, not merely
    # from dispatch.
    response = await _dispatch(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
        capabilities=("deploy:*",),
        denied=("deploy_run", "deploy_release"),
    )

    assert response is not None
    names = {t["name"] for t in response["result"]["tools"]}  # type: ignore[index]
    assert names == frozenset()


async def test_tools_call_denied_tool_returns_forbidden_iserror() -> None:
    # Even with a deploy:write grant, a denied deploy tool must not dispatch.
    response = await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "deploy_release",
                "arguments": {"action": "trigger", "target": "prod"},
            },
        },
        capabilities=("deploy:write",),
        denied=("deploy_release",),
    )

    assert response is not None
    assert response["result"]["isError"] is True  # type: ignore[index]


async def test_notification_returns_no_response() -> None:
    response = await _dispatch(
        {"jsonrpc": "2.0", "method": "tools/list"}, capabilities=("forge:*",)
    )

    assert response is None


async def test_unknown_method_is_a_jsonrpc_error() -> None:
    response = await _dispatch(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, capabilities=()
    )

    assert response is not None
    assert response["error"]["code"] == -32601  # type: ignore[index]


async def test_tools_call_forbidden_returns_iserror_result() -> None:
    response = await _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "forge_repo",
                "arguments": {"owner": "o", "repo": "r", "action": "get_repo"},
            },
        },
        capabilities=("chat:read",),
    )

    assert response is not None
    assert response["result"]["isError"] is True  # type: ignore[index]


async def test_tools_call_bad_params_is_jsonrpc_error() -> None:
    response = await _dispatch(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": "nope"},
        capabilities=("forge:*",),
    )

    assert response is not None
    assert response["error"]["code"] == -32602  # type: ignore[index]
