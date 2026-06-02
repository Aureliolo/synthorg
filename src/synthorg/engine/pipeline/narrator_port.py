# module-kind: declarative
"""Port the work pipeline uses to trigger a post-run narrative.

The pipeline owns this abstraction (dependency inversion): it depends on
the port, not on the meta-layer ``ChiefOfStaffNarrator`` that implements
it, so the engine never imports the meta layer. ``generate`` returns
``object | None`` deliberately: the pipeline ignores the result (it logs
and moves on), and a concrete return type would pull ``docs_engine`` into
the engine layer and form an import cycle.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr


@runtime_checkable
class RunNarrator(Protocol):
    """Generates and persists a run narrative for a completed brief."""

    async def generate(
        self,
        *,
        task_id: NotBlankStr,
        project_id: NotBlankStr,
    ) -> object | None:
        """Produce the narrative for one completed brief.

        Implementations are best-effort: the pipeline treats any raised
        error as a degraded run, never a failed one, and discards the
        return value.
        """
        ...
