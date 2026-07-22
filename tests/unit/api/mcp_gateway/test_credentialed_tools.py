"""Unit tests for the credentialed-tool governance path."""

import pytest

from synthorg.api.mcp_gateway import tools as tools_module
from synthorg.api.mcp_gateway.tools import (
    CREDENTIALED_TOOLS,
    CredentialedToolContext,
    _CredentialedTool,
    _render,
    invoke_credentialed_tool,
    tool_schemas,
    visible_tool_names,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.domain_errors import (
    ForbiddenError,
    ResourceNotFoundError,
    ValidationError,
)
from synthorg.engine.prompt_safety import TAG_TOOL_RESULT
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.forge._args import ForgeRepoArgs
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit

_VALID_FORGE_ARGS: dict[str, object] = {
    "owner": "o",
    "repo": "r",
    "action": "get_repo",
}


class _SecurityDeniedError(Exception):
    """Sentinel raised by a security pre-check to prove it was reached."""


def _ctx(*, deny: bool = False) -> CredentialedToolContext:
    async def _pre_check(_name: str, _arguments: dict[str, object]) -> None:
        raise _SecurityDeniedError

    return CredentialedToolContext(
        connection_catalog=mock_of[ConnectionCatalog](),
        approval_store=mock_of[ApprovalStoreProtocol](),
        clock=FakeClock(),
        forge_connection="forge-conn",
        chat_connection="chat-conn",
        forge_timeout_seconds=30.0,
        chat_timeout_seconds=30.0,
        forge_max_read_chars=2000,
        security_pre_check=_pre_check if deny else None,
    )


def test_all_credentialed_tools_present() -> None:
    names = {spec.name for spec in CREDENTIALED_TOOLS}
    assert names == {
        "forge_repo",
        "forge_issue",
        "forge_pull_request",
        "forge_ci",
        "chat_messages",
        "chat_directory",
        "deploy_run",
        "deploy_release",
    }


def test_visible_tools_scopes_by_capability() -> None:
    assert visible_tool_names(capabilities=("forge:read",)) == frozenset(
        {"forge_repo", "forge_ci"}
    )
    assert visible_tool_names(capabilities=("forge:*",)) == frozenset(
        {"forge_repo", "forge_issue", "forge_pull_request", "forge_ci"}
    )
    assert visible_tool_names(capabilities=()) == frozenset()
    assert "chat_messages" in visible_tool_names(capabilities=("*",))
    assert visible_tool_names(capabilities=("*:read",)) == frozenset(
        {"forge_repo", "forge_ci", "chat_directory", "deploy_run"}
    )


def test_deploy_read_grant_does_not_expose_the_release_tool() -> None:
    """Observing deployments must never imply the ability to cause one."""
    scoped = visible_tool_names(capabilities=("deploy:read",))
    assert scoped == frozenset({"deploy_run"})
    assert "deploy_release" not in scoped


def test_deploy_write_grant_exposes_the_release_tool() -> None:
    assert visible_tool_names(capabilities=("deploy:write",)) == frozenset(
        {"deploy_release"}
    )


def test_denied_name_overrides_capability() -> None:
    scoped = visible_tool_names(
        capabilities=("forge:*",), denied=("forge_pull_request",)
    )
    assert "forge_pull_request" not in scoped


def test_allowed_name_overrides_missing_capability() -> None:
    scoped = visible_tool_names(capabilities=(), allowed=("forge_repo",))
    assert scoped == frozenset({"forge_repo"})


def test_tool_schemas_expose_input_schema_for_visible_tools() -> None:
    schemas = tool_schemas(("forge:read",))

    names = {s["name"] for s in schemas}
    assert names == {"forge_repo", "forge_ci"}
    assert all("inputSchema" in s for s in schemas)


async def test_unknown_tool_raises_not_found() -> None:
    with pytest.raises(ResourceNotFoundError):
        await invoke_credentialed_tool(
            "nope",
            {},
            ctx=_ctx(),
            agent_id="agent-1",
            capabilities=("forge:*",),
        )


async def test_tool_not_in_scope_raises_forbidden() -> None:
    with pytest.raises(ForbiddenError):
        await invoke_credentialed_tool(
            "forge_repo",
            {"owner": "o", "repo": "r", "action": "get_repo"},
            ctx=_ctx(),
            agent_id="agent-1",
            capabilities=("chat:read",),
        )


async def test_scoped_in_call_reaches_security_pre_check() -> None:
    with pytest.raises(_SecurityDeniedError):
        await invoke_credentialed_tool(
            "forge_repo",
            {"owner": "o", "repo": "r", "action": "get_repo"},
            ctx=_ctx(deny=True),
            agent_id="agent-1",
            capabilities=("forge:read",),
        )


async def test_malformed_arguments_raise_domain_validation_error() -> None:
    # A malformed argument must surface as a domain ValidationError (kept
    # inside the JSON-RPC envelope), never a raw pydantic ValidationError.
    with pytest.raises(ValidationError):
        await invoke_credentialed_tool(
            "forge_repo",
            {"owner": "o"},  # missing required repo/action
            ctx=_ctx(),
            agent_id="agent-1",
            capabilities=("forge:read",),
        )


async def test_result_is_wrap_untrusted_fenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeTool:
        async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
            del arguments
            return ToolExecutionResult(content="RESULT-BODY")

    fake = _CredentialedTool(
        name="forge_repo",
        description="fake",
        capability="forge:read",
        category=ToolCategory.VERSION_CONTROL,
        args_model=ForgeRepoArgs,
        build=lambda _ctx, _aid: _FakeTool(),  # type: ignore[arg-type,return-value]
    )
    monkeypatch.setattr(tools_module, "CREDENTIALED_TOOLS", (fake,))
    monkeypatch.setattr(tools_module, "_TOOLS_BY_NAME", {"forge_repo": fake})

    out = await invoke_credentialed_tool(
        "forge_repo",
        _VALID_FORGE_ARGS,
        ctx=_ctx(),
        agent_id="agent-1",
        capabilities=("forge:read",),
    )
    # The untrusted tool body is wrap_untrusted-fenced, not returned raw.
    assert "RESULT-BODY" in out
    assert out != "RESULT-BODY"
    assert TAG_TOOL_RESULT in out


def test_render_flags_error_results() -> None:
    ok = ToolExecutionResult(content="done")
    err = ToolExecutionResult(content="boom", is_error=True)

    assert _render(ok, tool="forge_repo", agent_id="agent-1") == "done"
    assert _render(err, tool="forge_repo", agent_id="agent-1").startswith(
        "tool error: "
    )
