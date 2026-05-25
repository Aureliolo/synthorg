"""Compaction configuration and result models.

All models are frozen Pydantic models following the project's
immutability convention.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompactionConfig(BaseModel):
    """Configuration for context compaction behavior.

    Two operating modes:

    **Standard** (``agent_controlled=False``): automatic compaction
    triggers when context fill reaches ``fill_threshold_percent``.

    **Agent-controlled** (``agent_controlled=True``): agents manage
    compaction via the ``compact_context`` tool.  Automatic compaction
    is deferred to ``safety_threshold_percent`` (which must be higher
    than ``fill_threshold_percent``), giving agents headroom to decide
    when and how to compact while retaining a safety net.

    Attributes:
        fill_threshold_percent: Context fill percentage that triggers
            compaction in standard mode (e.g. 80.0 means compact when
            80% full).  In agent-controlled mode this threshold is
            NOT used for automatic compaction -- agents decide when to
            compact below ``safety_threshold_percent``.
        min_messages_to_compact: Minimum number of conversation
            messages required before compaction is allowed.
        preserve_recent_turns: Number of recent turn pairs to keep
            uncompressed after compaction.
        agent_controlled: Enable agent-initiated compaction via the
            ``compact_context`` tool.
        safety_threshold_percent: Auto-compaction threshold when
            ``agent_controlled`` is ``True`` (safety net).  Must be
            greater than ``fill_threshold_percent``.
        preserve_epistemic_markers: Detect and preserve epistemic
            markers (hedging, reconsideration, etc.) in summaries.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    fill_threshold_percent: float = Field(
        default=80.0,
        gt=0.0,
        le=100.0,
        description="Fill percentage that triggers compaction",
    )
    min_messages_to_compact: int = Field(
        default=4,
        ge=2,
        description="Minimum messages before compaction is allowed",
    )
    preserve_recent_turns: int = Field(
        default=3,
        ge=1,
        description="Recent turn pairs to keep uncompressed",
    )
    agent_controlled: bool = Field(
        default=False,
        description=(
            "Enable agent-initiated compaction via compact_context tool. "
            "When True, auto-compaction uses safety_threshold_percent."
        ),
    )
    safety_threshold_percent: float = Field(
        default=95.0,
        gt=0.0,
        le=100.0,
        description=(
            "Auto-compaction threshold when agent_controlled=True (safety net)."
        ),
    )
    preserve_epistemic_markers: bool = Field(
        default=True,
        description=("Detect and preserve epistemic markers in compaction summaries."),
    )

    @model_validator(mode="after")
    def _validate_safety_above_fill(self) -> Self:
        """Safety threshold must exceed fill threshold when agent-controlled.

        Returns:
            ``self`` unchanged when the threshold invariant holds.

        Raises:
            ValueError: When ``agent_controlled`` is set and the
                safety threshold is not strictly above the fill
                threshold.
        """
        if (
            self.agent_controlled
            and self.safety_threshold_percent <= self.fill_threshold_percent
        ):
            msg = (
                f"safety_threshold_percent ({self.safety_threshold_percent}) "
                f"must be greater than fill_threshold_percent "
                f"({self.fill_threshold_percent}) when agent_controlled=True"
            )
            raise ValueError(msg)
        return self


class CompressionMetadata(BaseModel):
    """Metadata about conversation compression on an ``AgentContext``.

    Attached to ``AgentContext.compression_metadata`` when conversation
    compaction has occurred, enabling compressed checkpoint recovery.

    Attributes:
        compression_point: Turn number at which compaction occurred.
        archived_turns: Number of turns that were archived.
        summary_tokens: Token count of the summary message.
        compactions_performed: Total number of compactions so far.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    compression_point: int = Field(
        ge=0,
        description="Turn number at which compaction occurred",
    )
    archived_turns: int = Field(
        ge=0,
        description="Number of turns archived",
    )
    summary_tokens: int = Field(
        ge=0,
        description="Token count of the summary message",
    )
    compactions_performed: int = Field(
        default=1,
        ge=1,
        description="Total compactions performed so far",
    )
