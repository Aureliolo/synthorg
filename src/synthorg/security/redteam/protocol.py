"""Adversarial red-team service protocols.

Three seams the gate consumes:

- :class:`AgentRunner` invokes the red-team agent for one deliverable.
  Production wraps ``AgentEngine.run``; tests stub it without an LLM.
- :class:`RedTeamReportRepository` stores the structured report the
  agent files via the ``submit_red_team_report`` tool.
- :class:`RedTeamGate` is the gate's outward surface; the
  ReviewGateService consumes it as an injected dependency.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from synthorg.security.redteam.models import (
        RedTeamGateResult,
        RedTeamReport,
    )


@runtime_checkable
class AgentRunner(Protocol):
    """Invoke the red-team agent for one deliverable.

    Production implementation wraps :class:`AgentEngine.run` against a
    transient red-team Task built from the deliverable. The agent's
    only side effect is filing a :class:`RedTeamReport` via the
    ``submit_red_team_report`` tool, which the gate then reads from
    the report repository.

    Tests inject a scripted runner that pre-populates the repo without
    spinning up a provider; this is what keeps the planted-defect
    acceptance test deterministic.
    """

    async def run(
        self,
        *,
        review_input: RedTeamReviewInput,
    ) -> None:
        """Run the red-team agent for ``review_input``.

        The agent is expected to file exactly one report via the
        ``submit_red_team_report`` tool. Returning without a filed
        report is a contract violation; the gate raises
        :class:`RedTeamReportNotFoundError` and applies its fail-OPEN
        policy.

        Implementations MUST NOT raise on adversarial deliverable
        content; the deliverable is wrapped with ``wrap_untrusted``
        at the prompt boundary, so prompt-injection attempts surface
        as findings (or, in the worst case, a missing report) but not
        as exceptions.
        """
        ...


@runtime_checkable
class RedTeamReportRepository(Protocol):
    """Per-execution storage for red-team reports.

    Single-shot per ``execution_id``: ``put`` must reject a second
    submission for the same key (with
    :class:`RedTeamReportAlreadyExistsError`). The protocol does not
    mandate persistence so an in-memory implementation suffices for
    deployments that scope a single agent run to one process.
    """

    async def put(
        self,
        *,
        execution_id: NotBlankStr,
        report: RedTeamReport,
    ) -> None:
        """Persist ``report`` under ``execution_id``.

        Raises:
            RedTeamReportAlreadyExistsError: If a report already
                exists for the same ``execution_id``.
        """
        ...

    async def get(
        self,
        *,
        execution_id: NotBlankStr,
    ) -> RedTeamReport:
        """Return the report stored for ``execution_id``.

        Raises:
            RedTeamReportNotFoundError: If no report exists.
        """
        ...


@runtime_checkable
class RedTeamGate(Protocol):
    """Outward gate surface consumed by the ReviewGateService.

    A single ``evaluate`` call drives the full gate workflow:
    invoke the agent runner, read the filed report, run the
    grounding checker, compute the verdict, return a structured
    :class:`RedTeamGateResult`.
    """

    async def evaluate(
        self,
        review_input: RedTeamReviewInput,
    ) -> RedTeamGateResult:
        """Evaluate ``review_input`` and return the gate's verdict.

        Concrete implementations apply a fail-OPEN policy: an agent
        dispatch failure or grounding-checker exception is converted
        to a synthetic INFO-severity finding, never propagated. Only
        :class:`asyncio.CancelledError` (and unexpected programming
        errors) propagate to the caller.
        """
        ...
