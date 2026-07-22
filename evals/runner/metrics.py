# module-kind: code
"""Project a loop's execution result onto the metrics the A/B rubric ranks on.

Every figure here is already recorded by the loops themselves: ``TurnRecord``
carries tokens, tool calls, provider retries and cache hits, and the planning
loops stash their replan count in ``ExecutionResult.metadata``. Nothing is
estimated or re-derived, so a metric in the scoreboard is a metric the loop
actually reported.

Dollar cost is deliberately absent: it is provider-specific, so the rubric ranks
on provider-neutral tokens and the authoritative per-``(provider, model)`` spend
is read separately from the gateway's cost ledger.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.loop_protocol import ExecutionResult

#: Key the planning loops stash their replan count under in result metadata.
#: ``react`` and ``openhands`` cannot replan and never set it; its absence is a
#: true zero, treated by the rubric as a rework cost rather than a credit.
REPLANS_USED_KEY = "replans_used"


class RunMetrics(BaseModel):
    """Per-run figures the rubric consumes, read off the loop's own records."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    total_turns: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tool_calls: int = Field(ge=0)
    tool_call_names: tuple[str, ...] = Field(default=())
    provider_retries: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    replans_used: int = Field(ge=0)

    # ``@property`` rather than ``@computed_field``: this model round-trips
    # through the scoreboard JSON, and a serialised derived value would land in
    # the input dict and trip ``extra="forbid"`` on reparse. Same pattern as
    # ``evals.models.scorecard.ProcessFactReport.is_clean``.
    @property
    def total_tokens(self) -> int:
        """Provider-neutral token total the rubric's cost dimension ranks on."""
        return self.input_tokens + self.output_tokens


def _replans_used(metadata: dict[str, object]) -> int:
    """Read the replan count from untyped loop metadata.

    Returns:
        The replan count, or 0 when the loop does not report one.

    Raises:
        ValueError: The key is present but not a non-negative integer, which
            means the loop's metadata contract drifted. Scoring it as zero
            would silently understate that loop's rework.
    """
    if REPLANS_USED_KEY not in metadata:
        return 0
    raw = metadata[REPLANS_USED_KEY]
    # ``bool`` is an ``int`` subclass and is never a valid count here.
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        msg = (
            f"ExecutionResult.metadata[{REPLANS_USED_KEY!r}]={raw!r} is not a "
            "non-negative integer; the loop's metadata contract has drifted"
        )
        raise ValueError(msg)
    return raw


def run_metrics(result: ExecutionResult, *, duration_seconds: float) -> RunMetrics:
    """Project *result* onto the rubric's per-run metrics.

    Args:
        result: The loop's execution result.
        duration_seconds: Wall-clock time for the run, measured by the engine.
            Passed in rather than derived because a replayed run completes
            instantly; latency is only meaningful as recorded at run time.

    Returns:
        The projected :class:`RunMetrics`.

    Raises:
        ValueError: The loop's replan metadata is present but malformed.
    """
    turns = result.turns
    tool_call_names: tuple[str, ...] = tuple(
        name for turn in turns for name in turn.tool_calls_made
    )
    return RunMetrics(
        total_turns=len(turns),
        duration_seconds=duration_seconds,
        input_tokens=sum(turn.input_tokens for turn in turns),
        output_tokens=sum(turn.output_tokens for turn in turns),
        total_tool_calls=len(tool_call_names),
        tool_call_names=tool_call_names,
        # ``retry_count`` / ``cache_hit`` are ``None`` when the provider did not
        # measure them, which counts the same as "did not happen" for the rubric.
        provider_retries=sum(turn.retry_count or 0 for turn in turns),
        cache_hits=sum(1 for turn in turns if turn.cache_hit),
        replans_used=_replans_used(result.metadata),
    )


__all__ = ["REPLANS_USED_KEY", "RunMetrics", "run_metrics"]
