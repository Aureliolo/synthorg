"""Work-entry adapter protocol.

A :class:`WorkEntryAdapter` maps one real work-entry source onto the
pipeline spine. Sibling adapters take their own domain inputs: the
client-request intake adapter is parametrised over
:class:`~synthorg.client.models.ClientRequest`; the goal/objective
adapter is parametrised over
:class:`~synthorg.engine.pipeline.entry.objective_adapter.ObjectiveSubmission`.
The shared contract is narrow on purpose: expose the originating
:class:`WorkSource` for provenance and a single ``submit`` coroutine
that returns the terminal pipeline result.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.engine.pipeline.models import WorkPipelineResult, WorkSource


@runtime_checkable
class WorkEntryAdapter[T_Request](Protocol):
    """Maps a real work-entry source into the pipeline spine."""

    @property
    def source(self) -> WorkSource:
        """The :class:`WorkSource` this adapter stamps on work items."""
        ...

    async def submit(self, request: T_Request) -> WorkPipelineResult:
        """Map ``request`` onto a work item and drive the spine.

        Args:
            request: The domain input to enter into the pipeline. The
                concrete type is fixed per adapter via ``T_Request``.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: Propagated unchanged from the spine
                (subclasses carry the precise RFC 9457 status).
        """
        ...
