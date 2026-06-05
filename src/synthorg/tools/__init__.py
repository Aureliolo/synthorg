"""Tool system -- base abstraction, registry, invoker, permissions, and errors."""

from .approval_tool import RequestHumanApprovalTool
from .base import BaseTool, ToolExecutionResult
from .code_runner import CodeRunnerTool
from .database import (
    BaseDatabaseTool,
    DatabaseConfig,
    DatabaseConnectionConfig,
    SchemaInspectTool,
    SqlQueryTool,
)
from .disclosure_config import ToolDisclosureConfig
from .disclosure_metrics import ToolDisclosureMetrics
from .discovery import (
    ListToolsTool,
    LoadToolResourceTool,
    LoadToolTool,
    ToolDisclosureManager,
    build_discovery_tools,
)
from .errors import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterError,
    ToolPermissionDeniedError,
)
from .examples.echo import EchoTool
from .factory import build_default_tools, build_default_tools_from_config
from .file_system import (
    BaseFileSystemTool,
    DeleteFileTool,
    EditFileTool,
    ListDirectoryTool,
    PathValidator,
    ReadFileTool,
    WriteFileTool,
)
from .git_tools import (
    GitBranchTool,
    GitCloneTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from .git_url_validator import GitCloneNetworkPolicy
from .invoker import ToolInvoker
from .network_validator import NetworkPolicy
from .permissions import ToolPermissionChecker
from .registry import ToolRegistry
from .sandbox import (
    DockerSandbox,
    DockerSandboxConfig,
    SandboxBackend,
    SandboxError,
    SandboxingConfig,
    SandboxResult,
    SandboxStartError,
    SandboxTimeoutError,
    SubprocessSandbox,
    SubprocessSandboxConfig,
)
from .terminal import BaseTerminalTool, ShellCommandTool, TerminalConfig
from .web import (
    BaseWebTool,
    HtmlParserTool,
    HttpRequestTool,
    SearchResult,
    WebSearchProvider,
    WebSearchTool,
    WebToolsConfig,
)

# MCP types are re-exported from synthorg.tools.mcp to avoid
# circular imports (config.schema -> tools.mcp -> tools.base).

__all__ = [
    "BaseDatabaseTool",
    "BaseFileSystemTool",
    "BaseTerminalTool",
    "BaseTool",
    "BaseWebTool",
    "CodeRunnerTool",
    "DatabaseConfig",
    "DatabaseConnectionConfig",
    "DeleteFileTool",
    "DockerSandbox",
    "DockerSandboxConfig",
    "EchoTool",
    "EditFileTool",
    "GitBranchTool",
    "GitCloneNetworkPolicy",
    "GitCloneTool",
    "GitCommitTool",
    "GitDiffTool",
    "GitLogTool",
    "GitStatusTool",
    "HtmlParserTool",
    "HttpRequestTool",
    "ListDirectoryTool",
    "ListToolsTool",
    "LoadToolResourceTool",
    "LoadToolTool",
    "NetworkPolicy",
    "PathValidator",
    "ReadFileTool",
    "RequestHumanApprovalTool",
    "SandboxBackend",
    "SandboxError",
    "SandboxResult",
    "SandboxStartError",
    "SandboxTimeoutError",
    "SandboxingConfig",
    "SchemaInspectTool",
    "SearchResult",
    "ShellCommandTool",
    "SqlQueryTool",
    "SubprocessSandbox",
    "SubprocessSandboxConfig",
    "TerminalConfig",
    "ToolDisclosureConfig",
    "ToolDisclosureManager",
    "ToolDisclosureMetrics",
    "ToolError",
    "ToolExecutionError",
    "ToolExecutionResult",
    "ToolInvoker",
    "ToolNotFoundError",
    "ToolParameterError",
    "ToolPermissionChecker",
    "ToolPermissionDeniedError",
    "ToolRegistry",
    "WebSearchProvider",
    "WebSearchTool",
    "WebToolsConfig",
    "WriteFileTool",
    "build_default_tools",
    "build_default_tools_from_config",
    "build_discovery_tools",
]
