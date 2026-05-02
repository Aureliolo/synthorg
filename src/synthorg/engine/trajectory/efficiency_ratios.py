"""Efficiency ratio models and computation.

Defines ``IdealTrajectoryBaseline`` (per-task-type ideal reference)
and ``EfficiencyRatios`` (per-run ratios against that baseline),
including verbosity, structural erosion, and Program Test Efficiency (PTE) metrics.
"""

from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
)

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger

logger = get_logger(__name__)


class IdealTrajectoryBaseline(BaseModel):
    """Per-task-type ideal baseline for efficiency ratio computation.

    Baselines are versioned and refresh-gated -- they are NOT
    auto-updated from observed runs, because "ideal" drifts as
    models improve only when humans explicitly re-baseline.

    Attributes:
        task_type: Category of task (e.g. "research", "code").
        ideal_step_count: Expected number of execution steps.
        ideal_tool_call_count: Expected number of tool calls.
        ideal_latency_seconds: Expected wall-clock time.
        ideal_output_tokens: Expected output tokens (verbosity baseline).
        ideal_structural_score: Expected structural quality score.
        ideal_pte: Expected Prefill Token Equivalents.
        recorded_at: When this baseline was established.
        recorded_by_agent_id: Agent that produced the baseline run.
        model_tier: Model tier at baseline time.
        notes: Optional human-readable notes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_type: NotBlankStr = Field(
        description="Category of task (e.g. 'research', 'code')",
    )
    ideal_step_count: int = Field(
        ge=1,
        description="Expected number of execution steps",
    )
    ideal_tool_call_count: int = Field(
        ge=0,
        description="Expected number of tool calls",
    )
    ideal_latency_seconds: float = Field(
        gt=0.0,
        description="Expected wall-clock time in seconds",
    )
    ideal_output_tokens: int = Field(
        ge=1,
        description="Expected output tokens (verbosity baseline)",
    )
    ideal_structural_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Expected structural quality score (0=eroded, 1=clean)",
    )
    ideal_pte: float = Field(
        gt=0.0,
        description="Expected Prefill Token Equivalents",
    )
    recorded_at: AwareDatetime = Field(
        description="When this baseline was established",
    )
    recorded_by_agent_id: NotBlankStr = Field(
        description="Agent that produced the baseline run",
    )
    model_tier: Literal["small", "medium", "large"] = Field(
        description="Model tier at baseline time",
    )
    notes: str = Field(
        default="",
        description="Optional human-readable notes",
    )


class EfficiencyRatios(BaseModel):
    """Per-run efficiency ratios against an ``IdealTrajectoryBaseline``.

    All ratios are observed / ideal where 1.0 = on target,
    >1.0 = worse than baseline, <1.0 = better.

    Attributes:
        step_ratio: Observed steps / ideal steps.
        tool_call_ratio: Observed tool calls / ideal calls.
            When ideal is 0 and observed is also 0, returns 0.0.
            When ideal is 0 but observed > 0, returns the raw
            observed count to signal unexpected extra calls.
        latency_ratio: Observed latency / ideal latency.
        verbosity_ratio: Observed output tokens / ideal tokens.
        structural_erosion_score: Composite erosion (0.0-1.0).
        verbosity_delta_per_iteration: Per-iteration verbosity delta
            for iterative loops.
        pte: Prefill Token Equivalents (hardware-aware cost).
        pte_ratio: Observed PTE / ideal PTE.
        baseline_version: Reference to the baseline used.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    step_ratio: float = Field(
        ge=0.0,
        description="Observed steps / ideal steps (1.0 = on target)",
    )
    tool_call_ratio: float = Field(
        ge=0.0,
        description="Observed tool calls / ideal calls",
    )
    latency_ratio: float = Field(
        ge=0.0,
        description="Observed latency / ideal latency",
    )
    verbosity_ratio: float = Field(
        ge=0.0,
        description="Observed output tokens / ideal tokens",
    )
    structural_erosion_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Composite structural erosion (0=none, 1=severe)",
    )
    verbosity_delta_per_iteration: tuple[float, ...] = Field(
        default=(),
        description="Per-iteration verbosity delta for iterative loops",
    )
    pte: float = Field(
        ge=0.0,
        description="Prefill Token Equivalents (hardware-aware cost)",
    )
    pte_ratio: float = Field(
        ge=0.0,
        description="Observed PTE / ideal PTE",
    )
    baseline_version: NotBlankStr = Field(
        description="Reference to the IdealTrajectoryBaseline used",
    )


def compute_efficiency_ratios(  # noqa: PLR0913
    *,
    baseline: IdealTrajectoryBaseline,
    observed_steps: int,
    observed_tool_calls: int,
    observed_latency_seconds: float,
    observed_output_tokens: int,
    structural_erosion_score: float,
    verbosity_deltas: tuple[float, ...],
    observed_pte: float,
) -> EfficiencyRatios:
    """Compute efficiency ratios from a baseline and observed values.

    Args:
        baseline: The ideal trajectory baseline to compare against.
        observed_steps: Actual step count.
        observed_tool_calls: Actual tool call count.
        observed_latency_seconds: Actual wall-clock time.
        observed_output_tokens: Actual output tokens.
        structural_erosion_score: Computed erosion score (0.0-1.0).
        verbosity_deltas: Per-iteration verbosity deltas.
        observed_pte: Computed PTE for this run.

    Returns:
        Computed efficiency ratios.
    """
    if baseline.ideal_tool_call_count > 0:
        tool_call_ratio = observed_tool_calls / baseline.ideal_tool_call_count
    elif observed_tool_calls == 0:
        tool_call_ratio = 0.0
    else:
        # Baseline expects zero tool calls but agent used tools;
        # use raw count as ratio to signal unexpected extra calls.
        tool_call_ratio = float(observed_tool_calls)

    return EfficiencyRatios(
        step_ratio=observed_steps / baseline.ideal_step_count,
        tool_call_ratio=tool_call_ratio,
        latency_ratio=observed_latency_seconds / baseline.ideal_latency_seconds,
        verbosity_ratio=observed_output_tokens / baseline.ideal_output_tokens,
        structural_erosion_score=structural_erosion_score,
        verbosity_delta_per_iteration=verbosity_deltas,
        pte=observed_pte,
        pte_ratio=observed_pte / baseline.ideal_pte,
        baseline_version=f"{baseline.task_type}:{baseline.recorded_at:%Y%m%d}",
    )
