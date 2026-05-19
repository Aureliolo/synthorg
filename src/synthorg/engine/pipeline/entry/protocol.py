"""Work-entry adapter protocol.

A :class:`WorkEntryAdapter` maps one real work-entry source onto the
pipeline spine. The intake adapter (this child) takes a stored
:class:`~synthorg.client.models.ClientRequest`; sibling adapters take
their own domain inputs. The shared contract is narrow on purpose:
expose the originating :class:`WorkSource` for provenance and a single
``submit`` coroutine that returns the terminal pipeline result.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.client.models import ClientRequest
    from synthorg.engine.pipeline.models import WorkPipelineResult, WorkSource


@runtime_checkable
class WorkEntryAdapter(Protocol):
    """Maps a real work-entry source into the pipeline spine."""

    @property
    def source(self) -> WorkSource:
        """The :class:`WorkSource` this adapter stamps on work items."""
        ...

    async def submit(self, request: ClientRequest) -> WorkPipelineResult:
        """Map ``request`` onto a work item and drive the spine.

        Args:
            request: The stored client request to enter into the
                pipeline.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: Propagated unchanged from the spine
                (subclasses carry the precise RFC 9457 status).
        """
        ...
