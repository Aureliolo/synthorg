"""Pluggable strategies for eval-loop pattern identification and fix proposal.

``EvalLoopCoordinator`` delegates the IDENTIFY and PROPOSE steps to these
protocols so a deployment can swap the shipped deterministic strategies
(threshold counting + a static action table) for richer
provider-backed analysis without touching the orchestrator. Both
strategies speak the established token vocabulary: weakness patterns
(``"weakness:<pillar>"``) and free-form action identifiers.
"""

from typing import NamedTuple, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.models import EvaluationReport


class ProposedAction(NamedTuple):
    """A remediation action plus the weakness pattern(s) that produced it.

    Carrying the originating patterns lets the dispatcher attribute each
    operator alert to the specific weakness rather than the whole cycle's
    pattern set, so a routed remediation keeps accurate provenance.
    """

    action_id: NotBlankStr
    patterns: tuple[NotBlankStr, ...]


@runtime_checkable
class PatternIdentifier(Protocol):
    """Identifies cross-agent weakness patterns from evaluation reports."""

    async def identify(
        self,
        reports: tuple[EvaluationReport, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Identify patterns across the cycle's per-agent reports.

        Args:
            reports: Per-agent evaluation reports from the current cycle.

        Returns:
            Pattern tokens (e.g. ``"weakness:governance"``).
        """
        ...


@runtime_checkable
class FixProposer(Protocol):
    """Proposes remediation action identifiers for identified patterns."""

    async def propose(
        self,
        patterns: tuple[NotBlankStr, ...],
    ) -> tuple[ProposedAction, ...]:
        """Map identified patterns to remediation actions.

        Args:
            patterns: Pattern tokens from a :class:`PatternIdentifier`.

        Returns:
            Ordered, de-duplicated :class:`ProposedAction` entries, each
            carrying the originating pattern(s).
        """
        ...
