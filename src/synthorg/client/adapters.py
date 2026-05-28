"""Entry-point adapters for routing client requests.

Adapters transform and annotate :class:`ClientRequest` instances
before they enter the intake pipeline. Each implements the
:class:`EntryPointStrategy` protocol: ``route(request) -> ClientRequest``.
"""

from synthorg.client.models import ClientRequest
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger

logger = get_logger(__name__)


class DirectAdapter:
    """Pass-through adapter that marks requests for direct intake.

    Stamps ``metadata["entry_point"] = "direct"`` so downstream
    intake engines and analytics can attribute the source. Does
    not change status.
    """

    async def route(self, request: ClientRequest) -> ClientRequest:
        """Stamp entry-point metadata and return the request."""
        metadata = dict(request.metadata)
        metadata["entry_point"] = "direct"
        return request.model_copy(update={"metadata": metadata})


class ProjectAdapter:
    """Attach a project context to the request.

    Sets ``metadata["project_id"]`` to the configured project and
    ``metadata["entry_point"] = "project"``. Useful for simulation
    runs scoped to a single project.
    """

    def __init__(self, *, project_id: NotBlankStr) -> None:
        """Initialize with the target project identifier."""
        self._project_id = project_id

    async def route(self, request: ClientRequest) -> ClientRequest:
        """Attach project context and return the updated request."""
        metadata = dict(request.metadata)
        metadata["entry_point"] = "project"
        metadata["project_id"] = self._project_id
        return request.model_copy(update={"metadata": metadata})


class IntakeAdapter:
    """Route requests through the full intake pipeline.

    Stamps ``metadata["entry_point"] = "intake"``. Actual intake
    orchestration is performed by the caller (IntakeEngine); this
    adapter only tags the request so intake agents can adjust
    their prompts based on provenance.
    """

    async def route(self, request: ClientRequest) -> ClientRequest:
        """Mark the request for full intake processing."""
        metadata = dict(request.metadata)
        metadata["entry_point"] = "intake"
        return request.model_copy(update={"metadata": metadata})
