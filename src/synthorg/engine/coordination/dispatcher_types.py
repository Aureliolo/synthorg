"""Shared types for topology dispatchers.

``DispatchResult`` is the return type of every dispatcher's
``dispatch()`` method. ``TopologyDispatcher`` is the runtime-
checkable Protocol all dispatchers implement.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationWave,
)
from synthorg.engine.workspace.models import (
    Workspace,
    WorkspaceGroupResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.core.types import NotBlankStr
    from synthorg.engine.coordination.config import CoordinationConfig
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.routing.models import RoutingResult
    from synthorg.engine.workspace.service import WorkspaceIsolationService


class DispatchResult(BaseModel):
    """Result of a topology dispatcher's execution.

    Attributes:
        waves: Executed waves with their results.
        workspaces: Workspaces created during execution.
        workspace_merge: Merge result if workspaces were merged.
        phases: Phase results generated during dispatch.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    waves: tuple[CoordinationWave, ...] = Field(
        default=(),
        description="Executed waves",
    )
    workspaces: tuple[Workspace, ...] = Field(
        default=(),
        description="Workspaces created during execution",
    )
    workspace_merge: WorkspaceGroupResult | None = Field(
        default=None,
        description="Workspace merge result",
    )
    phases: tuple[CoordinationPhaseResult, ...] = Field(
        default=(),
        description="Phase results from dispatch",
    )


@runtime_checkable
class TopologyDispatcher(Protocol):
    """Protocol for topology-specific dispatch strategies."""

    async def dispatch(  # noqa: PLR0913 -- dispatch contract surface
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
        project_id: NotBlankStr | None = None,
        repo_root: Path | None = None,
    ) -> DispatchResult:
        """Execute subtasks according to topology-specific rules.

        Args:
            decomposition_result: Decomposition with subtasks.
            routing_result: Routing decisions for subtasks.
            parallel_executor: Executor for parallel agent runs.
            workspace_service: Optional workspace isolation service.
            config: Coordination configuration.
            project_id: Owning project for the post-wave merge. When set
                with *repo_root* and a git backend, the merge routes
                through the per-project push queue.
            repo_root: Project working tree the push runs from; ``None``
                falls back to the in-memory ``merge_group``.

        Returns:
            Dispatch result with waves, workspaces, and phases.
        """
        ...
