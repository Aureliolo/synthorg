# module-kind: declarative
"""Work pipeline protocol.

The single coherent path every entry adapter feeds: a typed
:class:`WorkItem` in, a :class:`WorkPipelineResult` out.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.engine.pipeline.narrator_port import RunNarrator

if TYPE_CHECKING:
    from synthorg.engine.pipeline.models import WorkItem, WorkPipelineResult


@runtime_checkable
class WorkPipeline(Protocol):
    """Composes intake, the solo-vs-team decision, and execution.

    Implementations are the single integration point every entry
    adapter feeds; they own no user-facing choice of solo vs team.
    """

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        """Drive *work_item* through the full spine.

        Args:
            work_item: The typed entry envelope.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: On any phase failure (subclasses carry
                the precise RFC 9457 status).
        """
        ...

    def attach_narrator(self, narrator: RunNarrator) -> None:
        """Attach the post-run narrator (documentary mode).

        Late-bind seam: the narrator depends on services that wire only
        after persistence connects, so the startup hook attaches it to
        the already-built pipeline rather than passing it at construction.
        """
        ...
