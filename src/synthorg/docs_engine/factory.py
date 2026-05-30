"""Factory for the living-documentation engine.

The boot wiring constructs the engine via :func:`build_docs_service`,
which assembles every collaborator from the shared dependencies
(persistence, memory backend, workspace service, git backend, clock).
The factory returns both the service and the retrieval facade so the
boot hook can attach the facade to the per-agent retrieval pipeline
seam alongside attaching the service to ``AppState``.
"""

from typing import TYPE_CHECKING

from synthorg.docs_engine.chunker import DocChunker
from synthorg.docs_engine.indexer import DocIndexer
from synthorg.docs_engine.retrieval_facade import ProjectAwareMemoryFacade
from synthorg.docs_engine.service import DocsService
from synthorg.docs_engine.writer import DocWriter

if TYPE_CHECKING:
    from synthorg.core.clock import Clock
    from synthorg.engine.workspace.git_backend.protocol import GitBackend
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.persistence.docs_protocol import DocsRepository


class DocsRuntime:
    """Bundle of the docs engine's public surface.

    Held by the boot wiring; downstream callers reach the service via
    ``app_state.docs_service`` and the facade via the patched per-agent
    retrieval pipeline.
    """

    __slots__ = ("docs_service", "memory_facade")

    def __init__(
        self,
        *,
        docs_service: DocsService,
        memory_facade: ProjectAwareMemoryFacade,
    ) -> None:
        self.docs_service = docs_service
        self.memory_facade = memory_facade


def build_docs_service(
    *,
    repo: DocsRepository,
    workspace_service: ProjectWorkspaceService,
    git_backend: GitBackend,
    memory_backend: MemoryBackend,
    clock: Clock | None = None,
) -> DocsRuntime:
    """Assemble the docs engine from its dependencies.

    Args:
        repo: Persistence for :class:`DocMetadata` rows.
        workspace_service: Resolves the persistent per-project workspace.
        git_backend: Strategy used by the writer to push the docs branch.
        memory_backend: Agent memory backend used for chunk storage +
            retrieval (and by the facade for fan-out).
        clock: Clock seam; defaults to :class:`SystemClock`.

    Returns:
        :class:`DocsRuntime` carrying the service + retrieval facade.
    """
    chunker = DocChunker()
    indexer = DocIndexer(backend=memory_backend)
    writer = DocWriter(
        workspace_service=workspace_service,
        git_backend=git_backend,
    )
    service = DocsService(
        repo=repo,
        workspace_service=workspace_service,
        chunker=chunker,
        indexer=indexer,
        writer=writer,
        backend=memory_backend,
        clock=clock,
    )
    # knowledge_enabled and brain_enabled let the same facade transparently
    # surface the knowledge corpus and the project brain alongside project
    # docs; harmless when neither has content yet (the extra legs return
    # empty). Brain content is fenced under TAG_BRAIN_STATE in the facade.
    facade = ProjectAwareMemoryFacade(
        backend=memory_backend,
        knowledge_enabled=True,
        brain_enabled=True,
    )
    return DocsRuntime(docs_service=service, memory_facade=facade)
