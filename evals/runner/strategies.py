# module-kind: code
"""Deterministic scripted strategies for the benchmark runner.

These drive the live agent loop with zero real LLM spend. The default
:class:`QualityVaryingStrategy` completes every brief in one turn with a stable
per-brief deliverable and a small per-turn cost, so the runner can measure a
run's cost against the company's per-run budget ceiling (the broken-company
budget discriminator) AND grade a real deliverable.

The strategy returns a different deliverable per brief, keyed by the active
brief id the runner stamps via :meth:`QualityVaryingStrategy.activate` before
each brief runs. For the executable brief, the deliverable also varies by
quality profile: a ``COMPETENT`` profile produces a solution that passes the
brief's hidden tests, a ``DEGRADED`` profile one that compiles but fails them.
That gives the suite a genuine, grader-measured quality delta independent of
the budget knob -- the executable grader computes the grade by running the
artifact, it is never declared here.
"""

from collections.abc import Mapping
from typing import Final

from evals.runner.profiles import BenchmarkStrategyProfile
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)

# Per-turn cost stamped on the default benchmark completion. Non-zero so the
# runner's budget-ceiling comparison is meaningful; small so a generously
# budgeted (reference) company never trips it.
_DEFAULT_TURN_COST: Final[float] = 0.01
_DEFAULT_INPUT_TOKENS: Final[int] = 16
_DEFAULT_OUTPUT_TOKENS: Final[int] = 8


class QualityVaryingStrategy:
    """Scripted strategy returning a per-brief, profile-keyed deliverable.

    The runner calls :meth:`activate` with the current brief id before each
    brief runs; :meth:`next_response` then returns that brief's deliverable for
    the configured quality profile, falling back to a generic clean deliverable
    for briefs with no profile-specific entry (e.g. judged briefs, scored by the
    calibrated judge rather than the executable grader).

    Args:
        profile: The quality profile to render profile-specific deliverables at.
        deliverables: ``{brief_id: {profile: deliverable_text}}`` map of the
            briefs whose deliverable varies by profile.
        default_content: Deliverable returned for any brief absent from
            ``deliverables`` (and for any profile a brief does not define).
        turn_cost: Per-turn cost stamped on every completion.
    """

    def __init__(
        self,
        *,
        profile: BenchmarkStrategyProfile,
        deliverables: Mapping[str, Mapping[BenchmarkStrategyProfile, str]],
        default_content: str,
        turn_cost: float = _DEFAULT_TURN_COST,
    ) -> None:
        self._profile = profile
        self._deliverables = deliverables
        self._default_content = default_content
        self._turn_cost = turn_cost
        self._active_brief_id: str | None = None

    def activate(self, brief_id: str) -> None:
        """Select the brief whose deliverable the next completion returns."""
        self._active_brief_id = brief_id

    def _content(self) -> str:
        """Resolve the deliverable for the active brief + configured profile.

        Returns:
            The profile-specific deliverable, or ``default_content`` when the
            active brief has no entry for the profile.
        """
        by_profile = self._deliverables.get(self._active_brief_id or "")
        if by_profile is None:
            return self._default_content
        return by_profile.get(self._profile, self._default_content)

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return the active brief's deliverable as a one-turn STOP completion.

        Returns:
            A STOP completion carrying the active brief's deliverable + cost.
        """
        del messages, tools, config
        return CompletionResponse(
            content=self._content(),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=_DEFAULT_INPUT_TOKENS,
                output_tokens=_DEFAULT_OUTPUT_TOKENS,
                cost=self._turn_cost,
            ),
            model=model,
        )


__all__ = ["QualityVaryingStrategy"]
