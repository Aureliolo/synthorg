"""Protocol for the evolution outcome store.

Records the terminal outcome of every improvement proposal the
self-improvement cycle processes: what axis it targeted, whether it
applied, and when it was proposed.  The evolution signal aggregator
queries this store to roll up proposals into an
:class:`OrgEvolutionSummary` per observation window.

Design:
- A dedicated store (rather than reusing :class:`ApprovalStore`) because
  approvals are generic human-in-the-loop items; evolution outcomes are
  domain records with required agent/axis attribution for signals.
- Summaries are produced in the store so aggregators stay thin; the
  category-roll-up logic (approval rate, most adapted axis, recent
  outcomes tuple) has a single owner.
- Query methods are async to leave room for a durable implementation
  behind the same protocol without changing any caller.
"""

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

_DEFAULT_MAX_RECENT: Final[int] = 10

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.core.types import NotBlankStr
    from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
    from synthorg.meta.signal_models import OrgEvolutionSummary


@runtime_checkable
class EvolutionOutcomeStore(Protocol):
    """Stores terminal proposal outcomes and produces windowed summaries.

    Implementations must be safe under concurrent ``record`` /
    ``summarize`` calls; the in-memory default uses a deque and an
    ``asyncio.Lock``.
    """

    async def record(
        self,
        *,
        agent_id: NotBlankStr,
        axis: NotBlankStr,
        applied: bool,
        proposed_at: datetime,
    ) -> None:
        """Record a terminal outcome for a proposal.

        Best-effort: implementations must log and swallow their own
        errors (except ``MemoryError`` / ``RecursionError``) so the
        self-improvement cycle is never blocked by store failures.

        Args:
            agent_id: Which agent was the target of the proposal.
            axis: Which altitude/axis was adapted (maps to
                :class:`ProposalAltitude`).
            applied: Whether the adaptation was applied (``True``) or
                rejected / rolled back (``False``).
            proposed_at: When the proposal was originally generated.
        """
        ...

    async def query(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[EvolutionOutcomeRecord, ...]:
        """Return outcomes recorded within ``[since, until)``.

        Args:
            since: Start of the observation window (UTC).
            until: End of the observation window (UTC).

        Returns:
            Outcomes ordered newest-first.
        """
        ...

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
        max_recent: int = _DEFAULT_MAX_RECENT,
    ) -> OrgEvolutionSummary:
        """Produce the org-wide evolution summary for the window.

        Args:
            since: Start of the observation window (UTC).
            until: End of the observation window (UTC).
            max_recent: Cap on how many outcomes to surface in the
                ``recent_outcomes`` tuple.

        Returns:
            Populated :class:`OrgEvolutionSummary`; empty when the
            window contains no recorded outcomes.
        """
        ...

    async def count(self) -> int:
        """Return the current number of stored outcomes."""
        ...

    async def clear(self) -> None:
        """Drop all stored outcomes.  Intended for test isolation."""
        ...
