# module-kind: code
"""The state channel ``AgentContext`` carries for a pending compaction request.

A leaf module (no ``AgentContext`` import), the same shape
``background_job_watch_channel.py`` uses for the same reason: both
``context.py`` and the modules that build or consume the request need the
type without a cycle through ``context.py`` itself.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class CompactionRequest(BaseModel):
    """One agent-directed compaction request, pending the turn boundary.

    Attributes:
        strategy: The compaction strategy the agent asked for.
        reason: The agent's stated reason for requesting it.
        preserve_markers: Whether to preserve epistemic markers in the
            compaction summary, overriding the configured default for this
            one call.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr = Field(description="Compaction strategy requested")
    reason: NotBlankStr = Field(description="The agent's stated reason")
    preserve_markers: bool = Field(
        description="Per-call override for preserve_epistemic_markers"
    )


__all__ = ["CompactionRequest"]
