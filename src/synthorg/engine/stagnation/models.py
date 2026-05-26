"""Stagnation detection models.

Frozen Pydantic models for stagnation detection configuration,
verdicts, and results.  Used by the ``StagnationDetector`` protocol
and its implementations.
"""

import copy
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StagnationVerdict(StrEnum):
    """Verdict from a stagnation check."""

    NO_STAGNATION = "no_stagnation"
    INJECT_PROMPT = "inject_prompt"
    TERMINATE = "terminate"


class StagnationReason(StrEnum):
    """Why stagnation was detected.

    Classifies the underlying cause (separate from ``StagnationVerdict``
    which says what action to take).
    """

    TOOL_REPETITION = "tool_repetition"
    CYCLE_DETECTION = "cycle_detection"
    QUALITY_EROSION = "quality_erosion"


class StagnationConfig(BaseModel):
    """Configuration for stagnation detection.

    Attributes:
        enabled: Whether stagnation detection is active.
        window_size: Number of recent tool-bearing turns to analyze.
        repetition_threshold: Excess-duplicate ratio that triggers
            detection.  Lower values are more sensitive.  ``0.0``
            triggers on every check (including zero duplicates);
            ``1.0`` effectively disables ratio-based detection (the
            theoretical maximum is ``(n-1)/n``).
        cycle_detection: Whether to detect repeating A->B->A->B patterns.
        max_corrections: Number of corrective prompt injections before
            terminating.  ``0`` means skip injection and terminate
            immediately.
        min_tool_turns: Minimum tool-bearing turns in the window
            before any check fires.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether stagnation detection is active",
    )
    window_size: int = Field(
        default=5,
        ge=2,
        le=50,
        description="Number of recent tool-bearing turns to analyze",
    )
    repetition_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Fraction of duplicate fingerprints that triggers detection",
    )
    cycle_detection: bool = Field(
        default=True,
        description="Whether to detect repeating A->B->A->B patterns",
    )
    max_corrections: int = Field(
        default=1,
        ge=0,
        description="Number of corrective prompt injections before terminating",
    )
    min_tool_turns: int = Field(
        default=2,
        ge=1,
        description="Minimum tool-bearing turns before any check fires",
    )

    @model_validator(mode="after")
    def _validate_min_within_window(self) -> Self:
        if self.min_tool_turns > self.window_size:
            msg = (
                f"min_tool_turns ({self.min_tool_turns}) exceeds "
                f"window_size ({self.window_size}) -- stagnation "
                f"check will never fire within the window"
            )
            raise ValueError(msg)
        return self


class StagnationResult(BaseModel):
    """Result of a stagnation check.

    Attributes:
        verdict: What action to take.
        corrective_message: Corrective prompt to inject (required when
            verdict is ``INJECT_PROMPT``, ``None`` otherwise).
        repetition_ratio: Fraction of duplicate fingerprints in the
            analysis window.
        cycle_length: Length of detected repeating cycle, or ``None``.
        details: Forward-compatible metadata dict.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: StagnationVerdict = Field(
        description="What action to take",
    )
    reason: StagnationReason | None = Field(
        default=None,
        description="Why stagnation was detected (None when no stagnation)",
    )
    corrective_message: str | None = Field(
        default=None,
        description="Corrective prompt to inject (INJECT_PROMPT only)",
    )
    repetition_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of duplicate fingerprints in the window",
    )
    cycle_length: int | None = Field(
        default=None,
        ge=2,
        description="Length of detected repeating cycle",
    )
    details: dict[str, object] = Field(
        default_factory=dict,
        description="Forward-compatible metadata",
    )

    def __init__(self, **data: object) -> None:
        """Deep-copy details dict at construction boundary."""
        if "details" in data and isinstance(data["details"], dict):
            data["details"] = copy.deepcopy(data["details"])
        super().__init__(**data)

    @model_validator(mode="after")
    def _validate_corrective_message(self) -> Self:
        if self.verdict == StagnationVerdict.INJECT_PROMPT:
            if self.corrective_message is None:
                msg = "corrective_message is required when verdict is INJECT_PROMPT"
                raise ValueError(msg)
        elif self.corrective_message is not None:
            msg = "corrective_message must be None when verdict is not INJECT_PROMPT"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_reason_with_verdict(self) -> Self:
        """Ensure reason is None when no stagnation detected.

        Returns:
            ``self`` unchanged when verdict / reason agree.

        Raises:
            ValueError: When ``verdict == NO_STAGNATION`` carries a
                non-None reason.
        """
        if self.verdict == StagnationVerdict.NO_STAGNATION and self.reason is not None:
            msg = "reason must be None when verdict is NO_STAGNATION"
            raise ValueError(msg)
        return self


NO_STAGNATION_RESULT = StagnationResult(
    verdict=StagnationVerdict.NO_STAGNATION,
)
"""Reusable result for the common no-stagnation case."""
