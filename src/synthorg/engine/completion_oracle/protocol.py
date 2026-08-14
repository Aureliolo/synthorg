# module-kind: declarative
"""Completion-oracle peer-review service protocols.

Three seams the gate consumes:

- :class:`ReviewerAgentRunner` invokes the peer-review agent for one
  deliverable. Production wraps ``AgentEngine.run``; tests stub it.
- :class:`CompletionOracleReportRepository` stores the structured verdict
  the reviewer files via the ``submit_completion_oracle_verdict`` tool.
- :class:`CompletionOracleGate` is the gate's outward surface; the
  ReviewGateService consumes it as an injected dependency.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleGateResult,
    CompletionOracleReport,
)


@runtime_checkable
class ReviewerAgentRunner(Protocol):
    """Invoke the peer-review agent for one deliverable.

    Production wraps :class:`AgentEngine.run` against a transient review
    Task built from the deliverable. The agent's only side effect is filing
    a :class:`CompletionOracleReport` via the
    ``submit_completion_oracle_verdict`` tool, which the gate then reads
    from the report repository.

    Implementations MUST NOT raise on adversarial deliverable content; the
    deliverable is wrapped with ``wrap_untrusted`` at the prompt boundary.
    """

    async def run(
        self,
        *,
        review_input: CompletionOracleReviewInput,
        reviewer: AgentIdentity,
    ) -> None:
        """Run ``reviewer`` against ``review_input``.

        The reviewer is chosen per review from the roster, so it arrives as
        an argument rather than being bound once at construction: a gate that
        held one identity could only ever have one reviewer.

        The agent is expected to file exactly one verdict via the
        ``submit_completion_oracle_verdict`` tool. Returning without a filed
        verdict is a contract violation; the gate escalates to a human.
        """
        ...


@runtime_checkable
class CompletionOracleReportRepository(Protocol):
    """Per-execution storage for peer-review verdicts.

    Single-shot per ``execution_id``: ``put`` must reject a second
    submission for the same key (with
    :class:`CompletionOracleVerdictAlreadyExistsError`).
    """

    async def put(
        self,
        *,
        execution_id: NotBlankStr,
        report: CompletionOracleReport,
    ) -> None:
        """Persist ``report`` under ``execution_id``.

        Raises:
            CompletionOracleVerdictAlreadyExistsError: If a report already
                exists for the same ``execution_id``.
        """
        ...

    async def get(
        self,
        *,
        execution_id: NotBlankStr,
    ) -> CompletionOracleReport:
        """Return the report stored for ``execution_id``.

        Raises:
            CompletionOracleVerdictNotFoundError: If no report exists.
        """
        ...


@runtime_checkable
class CompletionOracleGate(Protocol):
    """Outward gate surface consumed by the ReviewGateService.

    A single ``evaluate`` call drives the full workflow: invoke the reviewer
    runner, read the filed verdict, and return a structured
    :class:`CompletionOracleGateResult`.
    """

    async def evaluate(
        self,
        review_input: CompletionOracleReviewInput,
    ) -> CompletionOracleGateResult:
        """Evaluate ``review_input`` and return the reviewer's verdict.

        Concrete implementations apply a fail-CLOSED policy: a reviewer
        dispatch failure, a missing verdict, or an unresolvable distinct
        reviewer yields an ``ESCALATE`` verdict (parked for a human), never
        a silent pass. Only :class:`asyncio.CancelledError` propagates.
        """
        ...
