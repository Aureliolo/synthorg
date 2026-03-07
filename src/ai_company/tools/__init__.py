"""Tool system — base abstraction, registry, invoker, permissions, and errors."""

from .base import BaseTool, ToolExecutionResult
from .errors import (
    GitToolError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterError,
    ToolPermissionDeniedError,
)
from .examples.echo import EchoTool
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

__all__ = [
    "BaseTool",
    "EchoTool",
    "GitBranchTool",
    "GitCloneTool",
    "GitCommitTool",
    "GitDiffTool",
    "GitLogTool",
    "GitStatusTool",
    "GitToolError",
    "ToolError",
    "ToolExecutionError",
    "ToolExecutionResult",
    "ToolInvoker",
    "ToolNotFoundError",
    "ToolParameterError",
    "ToolPermissionChecker",
    "ToolPermissionDeniedError",
    "ToolRegistry",
]
