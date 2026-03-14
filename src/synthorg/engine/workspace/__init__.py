"""Workspace isolation for concurrent agent execution.

Provides git-worktree-based workspace isolation so multiple agents
can work on the same repository without interfering with each other.
"""

from synthorg.engine.workspace.config import (
    PlannerWorktreesConfig,
    WorkspaceIsolationConfig,
)
from synthorg.engine.workspace.git_worktree import (
    PlannerWorktreeStrategy,
)
from synthorg.engine.workspace.merge import MergeOrchestrator
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    Workspace,
    WorkspaceGroupResult,
    WorkspaceRequest,
)
from synthorg.engine.workspace.protocol import (
    WorkspaceIsolationStrategy,
)
from synthorg.engine.workspace.service import (
    WorkspaceIsolationService,
)

__all__ = [
    "MergeConflict",
    "MergeOrchestrator",
    "MergeResult",
    "PlannerWorktreeStrategy",
    "PlannerWorktreesConfig",
    "Workspace",
    "WorkspaceGroupResult",
    "WorkspaceIsolationConfig",
    "WorkspaceIsolationService",
    "WorkspaceIsolationStrategy",
    "WorkspaceRequest",
]
