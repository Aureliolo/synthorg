"""Workspace isolation strategy protocol."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.engine.workspace.models import (
        MergeResult,
        Workspace,
        WorkspaceRequest,
    )


@runtime_checkable
class WorkspaceIsolationStrategy(Protocol):
    """Protocol for workspace isolation strategies.

    Implementations provide the ability to create, merge, and tear down
    isolated workspaces for concurrent agent execution.
    """

    async def setup_workspace(
        self,
        *,
        request: WorkspaceRequest,
    ) -> Workspace:
        """Create an isolated workspace for an agent task.

        Args:
            request: Workspace creation request.

        Returns:
            The created workspace.

        Raises:
            WorkspaceLimitError: When max concurrent worktrees reached.
            WorkspaceSetupError: When git operations fail.
        """
        ...

    async def teardown_workspace(
        self,
        *,
        workspace: Workspace,
    ) -> None:
        """Remove an isolated workspace and clean up resources.

        Args:
            workspace: The workspace to tear down.

        Raises:
            WorkspaceCleanupError: When git cleanup operations fail.
        """
        ...

    async def merge_workspace(
        self,
        *,
        workspace: Workspace,
    ) -> MergeResult:
        """Merge a workspace branch back into the base branch.

        Merge conflicts are returned as a ``MergeResult`` with
        ``success=False`` rather than raised as exceptions.

        Args:
            workspace: The workspace to merge.

        Returns:
            The merge result with conflict details if any.

        Raises:
            WorkspaceMergeError: When checkout or merge abort fails.
        """
        ...

    async def list_active_workspaces(self) -> tuple[Workspace, ...]:
        """Return all currently active workspaces.

        Returns:
            Tuple of active workspaces.
        """
        ...

    def get_strategy_type(self) -> str:
        """Return the strategy type identifier.

        Returns:
            Strategy type name.
        """
        ...
