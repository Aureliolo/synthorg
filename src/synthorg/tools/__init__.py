"""Tool system — base abstraction, registry, invoker, permissions, and errors."""

from .approval_tool import RequestHumanApprovalTool
from .base import BaseTool, ToolExecutionResult
from .code_runner import CodeRunnerTool
from .errors import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterError,
    ToolPermissionDeniedError,
)
from .examples.echo import EchoTool
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
from .invoker import ToolInvoker
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

# MCP types are re-exported from synthorg.tools.mcp to avoid
# circular imports (config.schema -> tools.mcp -> tools.base).

__all__ = [
    "BaseFileSystemTool",
    "BaseTool",
    "CodeRunnerTool",
    "DeleteFileTool",
    "DockerSandbox",
    "DockerSandboxConfig",
    "EchoTool",
    "EditFileTool",
    "GitBranchTool",
    "GitCloneTool",
    "GitCommitTool",
    "GitDiffTool",
    "GitLogTool",
    "GitStatusTool",
    "ListDirectoryTool",
    "PathValidator",
    "ReadFileTool",
    "RequestHumanApprovalTool",
    "SandboxBackend",
    "SandboxError",
    "SandboxResult",
    "SandboxStartError",
    "SandboxTimeoutError",
    "SandboxingConfig",
    "SubprocessSandbox",
    "SubprocessSandboxConfig",
    "ToolError",
    "ToolExecutionError",
    "ToolExecutionResult",
    "ToolInvoker",
    "ToolNotFoundError",
    "ToolParameterError",
    "ToolPermissionChecker",
    "ToolPermissionDeniedError",
    "ToolRegistry",
    "WriteFileTool",
]
