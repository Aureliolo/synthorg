# module-kind: code
"""Project a loop's execution result onto the metrics the A/B rubric ranks on.

Every figure here is already recorded by the loops themselves: ``TurnRecord``
carries tokens, tool calls, provider retries and cache hits. Nothing is
estimated or re-derived, so a metric in the scoreboard is a metric the loop
actually reported.

Dollar cost is deliberately absent: it is provider-specific, so the rubric ranks
on provider-neutral tokens and the authoritative per-``(provider, model)`` spend
is read separately from the gateway's cost ledger.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.loop_protocol import ExecutionResult


class RunMetrics(BaseModel):
    """Per-run figures the rubric consumes, read off the loop's own records."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    total_turns: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tool_calls: int = Field(ge=0)
    tool_call_names: tuple[str, ...] = Field(default=())
    repeated_tool_calls: int = Field(ge=0)
    provider_retries: int = Field(ge=0)
    cache_hits: int = Field(ge=0)

    # ``@property`` rather than ``@computed_field``: this model round-trips
    # through the scoreboard JSON, and a serialised derived value would land in
    # the input dict and trip ``extra="forbid"`` on reparse. Same pattern as
    # ``evals.models.scorecard.ProcessFactReport.is_clean``.
    @property
    def total_tokens(self) -> int:
        """Provider-neutral token total the rubric's cost dimension ranks on."""
        return self.input_tokens + self.output_tokens


def run_metrics(result: ExecutionResult, *, duration_seconds: float) -> RunMetrics:
    """Project *result* onto the rubric's per-run metrics.

    Args:
        result: The loop's execution result.
        duration_seconds: Wall-clock time for the run, measured by the engine.
            Passed in rather than derived because a replayed run completes
            instantly; latency is only meaningful as recorded at run time.

    Returns:
        The projected :class:`RunMetrics`.
    """
    turns = result.turns
    tool_call_names: tuple[str, ...] = tuple(
        name for turn in turns for name in turn.tool_calls_made
    )
    # Excess duplicates only: a fingerprint seen three times contributes two.
    # Same measure the stagnation detector applies, so "thrash" means the same
    # thing here as it does inside the loops.
    fingerprints = [fp for turn in turns for fp in turn.tool_call_fingerprints]
    return RunMetrics(
        total_turns=len(turns),
        duration_seconds=duration_seconds,
        input_tokens=sum(turn.input_tokens for turn in turns),
        output_tokens=sum(turn.output_tokens for turn in turns),
        total_tool_calls=len(tool_call_names),
        tool_call_names=tool_call_names,
        repeated_tool_calls=len(fingerprints) - len(set(fingerprints)),
        # ``retry_count`` / ``cache_hit`` are ``None`` when the provider did not
        # measure them, which counts the same as "did not happen" for the rubric.
        provider_retries=sum(turn.retry_count or 0 for turn in turns),
        cache_hits=sum(1 for turn in turns if turn.cache_hit),
    )


__all__ = ["RunMetrics", "run_metrics"]
