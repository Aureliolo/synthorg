"""Unit tests for the credentialed-tool governance path."""

from pathlib import Path

import pytest

from synthorg.api.mcp_gateway import tools as tools_module
from synthorg.api.mcp_gateway.scoping import (
    deploy_denials,
    publish_denials,
    tool_schemas,
)
from synthorg.api.mcp_gateway.tools import (
    CREDENTIALED_TOOLS,
    CredentialedToolContext,
    _CredentialedTool,
    _render,
    invoke_credentialed_tool,
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


def _ctx(
    *,
    deny: bool = False,
    deploy_targets: frozenset[str] = frozenset(),
    publish_targets: frozenset[str] | None = None,
    catalog: ConnectionCatalog | None = None,
) -> CredentialedToolContext:
    async def _pre_check(_name: str, _arguments: dict[str, object]) -> None:
        raise _SecurityDeniedError

    return CredentialedToolContext(
        connection_catalog=catalog or mock_of[ConnectionCatalog](),
        approval_store=mock_of[ApprovalStoreProtocol](),
        clock=FakeClock(),
        forge_connection="forge-conn",
        chat_connection="chat-conn",
        forge_timeout_seconds=30.0,
        chat_timeout_seconds=30.0,
        forge_max_read_chars=2000,
        deploy_targets=deploy_targets,
        deploy_timeout_seconds=30.0,
        deploy_max_log_chars=20000,
        publish_targets=deploy_targets if publish_targets is None else publish_targets,
        publish_timeout_seconds=60.0,
        publish_max_manifest_bytes=4_000_000,
        publish_max_image_bytes=2_000_000_000,
        workspace_root=Path.cwd(),
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
        "publish_inspect",
        "publish_push",
    }


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        (("forge:read",), frozenset({"forge_repo", "forge_ci"})),
        (
            ("forge:*",),
            frozenset({"forge_repo", "forge_issue", "forge_pull_request", "forge_ci"}),
        ),
        ((), frozenset()),
        (
            ("*:read",),
            frozenset(
                {
                    "forge_repo",
                    "forge_ci",
                    "chat_directory",
                    "deploy_run",
                    "publish_inspect",
                }
            ),
        ),
    ],
)
def test_visible_tools_scopes_by_capability(
    capabilities: tuple[str, ...], expected: frozenset[str]
) -> None:
    assert visible_tool_names(capabilities=capabilities) == expected


def test_wildcard_capability_exposes_every_tool() -> None:
    assert visible_tool_names(capabilities=("*",)) == {
        spec.name for spec in CREDENTIALED_TOOLS
    }


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


def test_deploy_denials_reflects_the_kill_switch() -> None:
    assert deploy_denials(deploy_enabled=True) == ()
    assert set(deploy_denials(deploy_enabled=False)) == {"deploy_run", "deploy_release"}


def test_publish_read_grant_does_not_expose_the_push_tool() -> None:
    """Inspecting a registry must never imply the ability to publish to it."""
    scoped = visible_tool_names(capabilities=("publish:read",))
    assert scoped == frozenset({"publish_inspect"})
    assert "publish_push" not in scoped


def test_publish_write_grant_exposes_the_push_tool() -> None:
    assert visible_tool_names(capabilities=("publish:write",)) == frozenset(
        {"publish_push"}
    )


def test_publish_denials_reflects_the_kill_switch() -> None:
    assert publish_denials(publish_enabled=True) == ()
    assert set(publish_denials(publish_enabled=False)) == {
        "publish_inspect",
        "publish_push",
    }


def test_tool_schemas_omit_denied_tools() -> None:
    # With the kill switch off, a deploy:* grant still yields no deploy tools.
    schemas = tool_schemas(("deploy:*",), denied=deploy_denials(deploy_enabled=False))
    assert schemas == []


async def test_deploy_run_rejects_unlisted_target_before_brokering() -> None:
    # The full gateway path (scope -> parse -> _deploy_deps -> governed tool):
    # an unlisted target is refused before any credential is brokered.
    catalog = mock_of[ConnectionCatalog]()
    out = await invoke_credentialed_tool(
        "deploy_run",
        {"action": "list", "target": "prod"},
        ctx=_ctx(deploy_targets=frozenset(), catalog=catalog),
        agent_id="agent-1",
        capabilities=("deploy:read",),
    )
    assert "allowlist" in out.lower()
    catalog.get.assert_not_called()
    catalog.get_credentials.assert_not_called()


async def test_deploy_target_traversal_is_rejected() -> None:
    # A traversal target is rejected at the typed boundary, before the
    # allowlist check, even when it is (absurdly) allowlisted.
    with pytest.raises(ValidationError):
        await invoke_credentialed_tool(
            "deploy_run",
            {"action": "list", "target": "../secrets"},
            ctx=_ctx(deploy_targets=frozenset({"../secrets"})),
            agent_id="agent-1",
            capabilities=("deploy:read",),
        )


async def test_publish_inspect_rejects_unlisted_target_before_brokering() -> None:
    # The full gateway path (scope -> parse -> _publish_deps -> governed tool):
    # an unlisted publish target is refused before any credential is brokered.
    catalog = mock_of[ConnectionCatalog]()
    out = await invoke_credentialed_tool(
        "publish_inspect",
        {"action": "list_tags", "target": "prod-images"},
        ctx=_ctx(publish_targets=frozenset(), catalog=catalog),
        agent_id="agent-1",
        capabilities=("publish:read",),
    )
    assert "allowlist" in out.lower()
    catalog.get.assert_not_called()
    catalog.get_credentials.assert_not_called()


async def test_publish_target_traversal_is_rejected() -> None:
    # A traversal target is rejected at the typed boundary, before the
    # allowlist check, even when it is (absurdly) allowlisted.
    with pytest.raises(ValidationError):
        await invoke_credentialed_tool(
            "publish_inspect",
            {"action": "list_tags", "target": "../secrets"},
            ctx=_ctx(publish_targets=frozenset({"../secrets"})),
            agent_id="agent-1",
            capabilities=("publish:read",),
        )


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
