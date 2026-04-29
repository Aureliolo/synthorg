"""Typed argument models for single-tool domains.

Houses the args models for tools that don't naturally cluster into a
larger domain package (terminal, code-runner, approval, the discovery
trio, the example echo, the MCP bridge, the context-compactor).  Each
section is a small, self-contained group; the file groups them so we
do not need a dozen one-tool packages just to host a single Pydantic
model.

Tools wired to consume these models:

* :class:`~synthorg.tools.terminal.shell_command.ShellCommandTool`
  -> :class:`ShellCommandArgs`
* :class:`~synthorg.tools.code_runner.CodeRunnerTool` -> :class:`CodeRunnerArgs`
* :class:`~synthorg.tools.approval_tool.RequestHumanApprovalTool`
  -> :class:`RequestHumanApprovalArgs`
* :class:`~synthorg.tools.discovery.ListToolsTool` -> :class:`ListToolsArgs`
* :class:`~synthorg.tools.discovery.LoadToolTool` -> :class:`LoadToolArgs`
* :class:`~synthorg.tools.discovery.LoadToolResourceTool`
  -> :class:`LoadToolResourceArgs`
* :class:`~synthorg.tools.examples.echo.EchoTool` -> :class:`EchoArgs`
* :class:`~synthorg.tools.mcp.bridge_tool.MCPBridgeTool` -> :class:`MCPBridgeArgs`
* :class:`~synthorg.tools.context.compact_context.CompactContextTool`
  -> :class:`CompactContextArgs`
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


# ── Terminal ────────────────────────────────────────────────────────


class ShellCommandArgs(BaseModel):
    """Args for ``shell_command``.

    Allowlist / blocklist enforcement and ``working_directory`` policy
    stay inside the tool body because they depend on per-instance
    sandbox configuration.
    """

    model_config = _ARGS_CONFIG

    command: NotBlankStr = Field(description="Shell command to execute")
    working_directory: NotBlankStr | None = Field(
        default=None,
        description="Working directory (relative to workspace)",
    )
    timeout: float | None = Field(
        default=None,
        ge=1,
        le=600,
        description="Command timeout in seconds",
    )


# ── Code runner ─────────────────────────────────────────────────────


CodeRunnerLanguage = Literal["python", "javascript", "bash"]


class CodeRunnerArgs(BaseModel):
    """Args for ``code_runner``."""

    model_config = _ARGS_CONFIG

    code: str = Field(description="Source code to execute")
    language: CodeRunnerLanguage = Field(description="Programming language of the code")
    timeout: float | None = Field(
        default=None,
        ge=0,
        le=600,
        description="Optional timeout in seconds",
    )


# ── Approval ────────────────────────────────────────────────────────


class RequestHumanApprovalArgs(BaseModel):
    """Args for ``request_human_approval``.

    The ``action_type`` must be in ``category:action`` format; that
    structural check (presence of exactly one ``:`` with non-empty
    halves) lives inside the tool body where the message can name the
    expected format and link to ``DEFAULT_CATEGORY_ACTION_MAP``.
    """

    model_config = _ARGS_CONFIG

    action_type: NotBlankStr = Field(
        max_length=128,
        description="Action type in category:action format",
    )
    title: NotBlankStr = Field(
        max_length=256,
        description="Short summary of the approval request",
    )
    description: NotBlankStr = Field(
        max_length=4096,
        description="Detailed explanation of what needs approval",
    )


# ── Discovery ───────────────────────────────────────────────────────


class ListToolsArgs(BaseModel):
    """Args for ``list_tools``: optional category filter."""

    model_config = _ARGS_CONFIG

    category: NotBlankStr | None = Field(
        default=None,
        description="Optional tool-category filter",
    )


class LoadToolArgs(BaseModel):
    """Args for ``load_tool``."""

    model_config = _ARGS_CONFIG

    tool_name: NotBlankStr = Field(description="Tool name to load")


class LoadToolResourceArgs(BaseModel):
    """Args for ``load_tool_resource``."""

    model_config = _ARGS_CONFIG

    tool_name: NotBlankStr = Field(description="Tool name")
    resource_id: NotBlankStr = Field(description="L3 resource identifier")


# ── Examples ────────────────────────────────────────────────────────


class EchoArgs(BaseModel):
    """Args for the example ``echo`` tool."""

    model_config = _ARGS_CONFIG

    message: str = Field(description="Message to echo back")


# ── MCP bridge ──────────────────────────────────────────────────────


class MCPBridgeArgs(BaseModel):
    """Args for ``mcp_bridge`` -- forwards to a remote MCP tool.

    The ``arguments`` payload is genuinely tool-specific (chosen by the
    target MCP server, not us); it stays a ``dict[str, object]`` here
    and is validated against the remote tool's input schema by the
    MCP client at dispatch time.
    """

    model_config = _ARGS_CONFIG

    server_name: NotBlankStr = Field(description="Registered MCP server name")
    tool_name: NotBlankStr = Field(description="Remote tool name to invoke")
    arguments: dict[str, object] = Field(
        default_factory=dict,
        description="Arguments forwarded to the remote tool",
    )


# ── Context compactor ───────────────────────────────────────────────


CompactContextStrategy = Literal["summarize"]


class CompactContextArgs(BaseModel):
    """Args for ``compact_context``."""

    model_config = _ARGS_CONFIG

    strategy: CompactContextStrategy = Field(description="Compaction strategy")
    reason: NotBlankStr = Field(
        min_length=10,
        max_length=256,
        description="Brief explanation for why compaction is needed",
    )
    preserve_markers: bool = Field(
        default=True,
        description="Preserve epistemic markers in the compaction summary",
    )


__all__ = [
    "CodeRunnerArgs",
    "CodeRunnerLanguage",
    "CompactContextArgs",
    "CompactContextStrategy",
    "EchoArgs",
    "ListToolsArgs",
    "LoadToolArgs",
    "LoadToolResourceArgs",
    "MCPBridgeArgs",
    "RequestHumanApprovalArgs",
    "ShellCommandArgs",
]
