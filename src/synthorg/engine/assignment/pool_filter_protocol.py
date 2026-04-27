"""Candidate-pool filter protocol for task assignment.

Pre-scoring narrowing of the agent pool. Lets a strategy restrict
candidates along an axis the scorer cannot express -- e.g.
hierarchical subordinates of the task's delegator -- before the
scorer runs and before the ranker orders the survivors.

A pool filter may also report "no eligible pool" (empty result with
a reason) so the calling strategy can produce a rich
``AssignmentResult`` without repeating the filter's diagnostic
context.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.assignment.models import (
        AssignmentCandidate,
        AssignmentRequest,
    )


@dataclass(frozen=True, slots=True)
class PoolFilterResult:
    """Output of ``CandidatePoolFilter.filter()``.

    ``agents`` is the (possibly narrowed) pool that downstream
    scoring should consider. When the filter narrowed the pool to
    nothing, ``agents`` is empty and ``reason`` carries a
    human-readable explanation that the calling strategy uses for
    ``AssignmentResult.reason``.

    ``rewrite_success_reason`` is an optional hook that lets the
    filter surface its context in the success path. Given the
    ranker's selected candidate, it returns the final
    ``AssignmentResult.reason`` instead of the ranker's default. The
    hierarchical filter uses this to produce
    ``"Delegated from X to Y (score=Z)"`` so the result preserves
    the delegator information that the filter (not the ranker)
    knows about.

    Attributes:
        agents: The narrowed agent pool. Empty when the filter
            could not produce any eligible agents.
        reason: Human-readable explanation when ``agents`` is empty.
            ``None`` when ``agents`` is non-empty.
        rewrite_success_reason: Optional success-reason override.
    """

    agents: tuple[AgentIdentity, ...]
    reason: str | None = None
    rewrite_success_reason: Callable[[AssignmentCandidate], str] | None = None


@runtime_checkable
class CandidatePoolFilter(Protocol):
    """Protocol for narrowing the candidate pool before scoring.

    Implementations may return the request's pool unchanged
    (identity) or build a narrower tuple. They MUST NOT mutate
    the request. Empty-pool outcomes carry a ``reason`` so the
    calling strategy can produce a context-rich
    ``AssignmentResult``.
    """

    @property
    def name(self) -> str:
        """Filter identifier (used for logging and diagnostics)."""
        ...

    def filter(self, request: AssignmentRequest) -> PoolFilterResult:
        """Return the (possibly narrowed) candidate pool.

        Args:
            request: Original assignment request.

        Returns:
            A ``PoolFilterResult`` whose ``agents`` may equal
            ``request.available_agents`` (no-op), be a strict
            subset, or be empty. When empty, ``reason`` is set.
        """
        ...
