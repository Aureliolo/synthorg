"""Factory for the long-horizon project brain.

The boot wiring constructs the engine via :func:`build_project_brain_service`,
which assembles every collaborator from the shared dependencies (persistence,
memory backend, workspace service, git backend, clock) and returns the service
plus the per-task tool factory.

Unlike the docs engine, the brain does not build its own retrieval facade: brain
state is surfaced transparently through the single shared
:class:`ProjectAwareMemoryFacade` (built with ``brain_enabled=True`` by the docs
factory), so this factory returns only the service and the tool factory.
"""

from typing import TYPE_CHECKING

from synthorg.project_brain.chunker import BrainChunker
from synthorg.project_brain.indexer import BrainIndexer
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.tool_factory import (
    ProjectBrainToolFactory,
    build_project_brain_tool_factory,
)
from synthorg.project_brain.writer import BrainWriter

if TYPE_CHECKING:
    from synthorg.core.clock import Clock
    from synthorg.engine.workspace.git_backend.protocol import GitBackend
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.persistence.project_brain_protocol import ProjectBrainRepository


class ProjectBrainRuntime:
    """Bundle of the project brain's public surface.

    Held by the boot wiring; downstream callers reach the service via
    ``app_state.slice(ProjectBrainStateSlice).service`` and the tool factory via
    the per-task tool loader. The retrieval facade is the docs engine's shared
    one and is not held here.
    """

    __slots__ = ("brain_service", "tool_factory")

    def __init__(
        self,
        *,
        brain_service: ProjectBrainService,
        tool_factory: ProjectBrainToolFactory,
    ) -> None:
        self.brain_service = brain_service
        self.tool_factory = tool_factory


def build_project_brain_service(
    *,
    repo: ProjectBrainRepository,
    workspace_service: ProjectWorkspaceService,
    git_backend: GitBackend,
    memory_backend: MemoryBackend,
    clock: Clock | None = None,
) -> ProjectBrainRuntime:
    """Assemble the project brain from its dependencies.

    Args:
        repo: Append-only persistence for :class:`BrainEntry` revisions.
        workspace_service: Resolves the persistent per-project workspace.
        git_backend: Strategy used by the writer to push the docs branch.
        memory_backend: Agent memory backend used for chunk storage and search
            (and by the shared facade for the brain fan-out leg).
        clock: Clock seam; defaults to :class:`SystemClock`.

    Returns:
        :class:`ProjectBrainRuntime` carrying the service and tool factory.
    """
    chunker = BrainChunker()
    indexer = BrainIndexer(backend=memory_backend)
    writer = BrainWriter(
        workspace_service=workspace_service,
        git_backend=git_backend,
    )
    service = ProjectBrainService(
        repo=repo,
        workspace_service=workspace_service,
        chunker=chunker,
        indexer=indexer,
        writer=writer,
        backend=memory_backend,
        clock=clock,
    )
    tool_factory = build_project_brain_tool_factory(brain_service=service)
    return ProjectBrainRuntime(brain_service=service, tool_factory=tool_factory)
