# module-kind: code
"""Who writes the tree a run is executed from.

Planning is a seam rather than a call because the sweep has to be drivable
offline, against a hand-built tree, with no provider: the survival metric, the
depth binning and the arm wiring are all testable that way, and a harness whose
only path is a real recording is one nobody can regression-test.

The production implementation is the shipped owner-run planning session over
the shipped :class:`DecompositionService`, so what recursion does to a plan here
is what the product's recursion does to a plan. Its spend is booked to the run
like any other session, because a deep sweep pays for a planning session at
every node and a cost panel that omitted them would understate the deep end
exactly where the question is.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from evals.errors import RecursionDepthPlannerSubstitutedError
from evals.harness.binding import RunBinding
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.models import reject_negative_deltas, sum_costs
from evals.recursion_depth.session import (
    SessionLimits,
    SweepDeps,
    ledger_scope,
    session_spend,
    transcript_scope,
)
from evals.recursion_depth.staffing import SweepRoster
from evals.recursion_depth.tree import build_tree
from synthorg.budget.session_budget import SessionCeilings
from synthorg.budget.tracker_protocol import CostTrackerProtocol, collect_all_records
from synthorg.core.task import Task
from synthorg.engine.coordination.decomposition_strategy_factory import (
    build_decomposition_strategy,
)
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.decomposition.strategy_deps import (
    AgentSessionDecompositionConfig,
    DecompositionStrategyDeps,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_RECURSION_PLAN_BOOKING_FAILED,
    EVALS_RECURSION_PLAN_FAILED,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: The shipped planner: an owner-run session that reasons across turns and
#: submits its plan through a terminal tool. The single-shot alternative is
#: what this one falls back to, so naming it here would measure the fallback.
_STRATEGY_NAME: str = "agent-session"

#: What the planner is told the workspace holds. Stated rather than probed,
#: because the tree is planned before any unit's workspace exists and a planner
#: told nothing must assume nothing.
SEED_WORKSPACE_SUMMARY: str = (
    "The project workspace holds a single README.md and nothing else. Nothing "
    "is implemented: every line of the deliverable is still to be written."
)


class PlanningSpend:
    """What one cell's planning has cost, however it ends.

    Owned by the caller and booked into by the planner, rather than returned
    beside the tree, because the figure matters most on the path that returns
    no tree at all: a cell whose planning failed had still paid for every
    session it ran, and a record built from the exception alone reported a cell
    that cost nothing while two live cells burned an hour of provider time
    between them.

    Accumulating rather than assigning, because a cell's planning is retried
    and each attempt opens its own ledger: reading only the last one
    under-reports by exactly the attempts that failed.

    Tokens travel beside cost because cost alone measures nothing against a
    flat-rate subscription, where every record is priced at zero and the token
    count is the only thing that moves.

    Cost is ``None``-poisoned rather than accumulated once the planning
    session's own connection turns out not to price its calls: a booking that
    kept summing after that would carry a partial figure as though it were the
    whole one, which is the same defect a stored ``0.0`` was.
    """

    __slots__ = ("_cost", "_sessions", "_tokens")

    def __init__(self) -> None:
        self._cost: float | None = 0.0
        self._tokens = 0
        self._sessions = 0

    @property
    def cost(self) -> float | None:
        """What the planning attempts have spent, or ``None`` if unpriced."""
        return self._cost

    @property
    def tokens(self) -> int:
        """Input plus output tokens across the planning attempts."""
        return self._tokens

    @property
    def sessions(self) -> int:
        """How many planning sessions have run."""
        return self._sessions

    def book(self, *, cost: float | None, tokens: int, sessions: int) -> None:
        """Add one attempt's spend.

        Args:
            cost: What the attempt's ledger recorded, or ``None`` when the
                connection it ran on does not price its calls.
            tokens: Input plus output tokens over the same records.
            sessions: How many planning sessions the attempt ran.

        Raises:
            ValueError: Any of the three deltas is negative.
        """
        reject_negative_deltas("planning", cost=cost, tokens=tokens, sessions=sessions)
        self._cost = sum_costs((self._cost, cost))
        self._tokens += tokens
        self._sessions += sessions


@runtime_checkable
class TreePlanner(Protocol):
    """Whatever produces the tree one run is executed from."""

    async def plan(
        self,
        *,
        task: Task,
        depth_cap: int,
        execution_id: str,
        spend: PlanningSpend,
    ) -> DecompositionResult:
        """Decompose *task* down to *depth_cap*, booking what it costs."""
        ...


@dataclass(frozen=True)
class AgentSessionPlanner:
    """The shipped owner-run planning session, driven at one depth cap.

    Attributes:
        deps: The sweep's injected collaborators.
        roster: The org, for the lead that plans and the roles it may assign.
        executor: The pair the planning session dispatches on, which is the
            executor's: planning is work the organisation does, so it runs on
            the binding its builders carry rather than on the judge's.
        limits: The turn and spend bounds one planning session gets.
        config_resolver: How the decomposition service reads its live
            settings, which is what makes the recursion switch and the
            atomicity thresholds the operator's rather than this module's.
    """

    deps: SweepDeps
    roster: SweepRoster
    executor: ModelPair
    limits: SessionLimits
    config_resolver: ConfigResolverProtocol | None = None

    async def plan(
        self,
        *,
        task: Task,
        depth_cap: int,
        execution_id: str,
        spend: PlanningSpend,
    ) -> DecompositionResult:
        """Decompose *task* down to *depth_cap* and book what it cost.

        Args:
            task: The root objective.
            depth_cap: The ``max_depth`` this run is allowed.
            execution_id: What the ledger keys the planning spend on.
            spend: Where this attempt's spend is booked, whether or not it
                produces a tree.

        Whatever planning raises propagates unchanged, because the runner takes
        two classifications from the type itself: a systemic failure that stops
        the sweep, and a decomposition failure that earns the second attempt.
        Wrapping it would cost both, which is why the spend travels in *spend*
        rather than on an exception of this module's own.

        Returns:
            The tree.
        """
        provider = await self.deps.build_provider(self._binding(task, execution_id))
        priced = self.executor.provider in self.deps.priced_providers
        fallback = ProgressTrackingLedger()
        async with (
            # Planning is transcribed for the same reason execution is, and it
            # is the half worth reading most: the tree a run produced is the
            # experiment's independent variable, and why the planner split the
            # way it did survives nowhere else. Paired with the ledger scope
            # because both key on this execution id, so a planning transcript
            # and the spend it produced name the same session.
            transcript_scope(self.deps, execution_id),
            ledger_scope(self.deps, execution_id, fallback) as tracker,
        ):
            try:
                result = await build_tree(
                    # The strategy books to its OWN tracker, never the hosted
                    # one, for the reason `open_session` does the same: a
                    # planning completion goes out through the hosted gateway,
                    # which records it, and the strategy's own cost scope
                    # records it again, so a shared ledger counts every planning
                    # call twice and the cost panel overstates exactly the arm
                    # that plans the most. When no gateway is hosted the two are
                    # the same object by construction, and the read below is the
                    # only one either way.
                    service=self._service(provider, fallback),
                    task=task,
                    depth_cap=depth_cap,
                    workspace_summary=SEED_WORKSPACE_SUMMARY,
                    available_roles=self.roster.roles,
                    # The same lead the binding above dispatches as. Without it
                    # the agent-session strategy has no owner to plan as and
                    # falls back to the single-shot one, which is the planner
                    # this module exists to NOT measure: a live run reported
                    # `strategy=llm` under a sweep whose whole premise is the
                    # shipped planner.
                    owner=self.roster.lead,
                )
            except RecursionDepthPlannerSubstitutedError as substituted:
                # The one refusal raised holding a finished tree: every level
                # of it planned and was billed, and the floor below would
                # under-book the sweep's ceiling by everything under the root.
                await _shielded_book(
                    tracker,
                    spend,
                    sessions=substituted.sessions,
                    failure=substituted,
                    execution_id=execution_id,
                    depth_cap=depth_cap,
                    gateway_hosted=self.deps.open_run_ledger is not None,
                    priced=priced,
                )
                raise
            except Exception as exc:
                # A floor of one, because `decompose_task` attempts the root's
                # own planning call before any recursion, so at least that
                # session ran. What it split into cannot be counted without the
                # tree the failure withheld, and the money is exact regardless.
                await _shielded_book(
                    tracker,
                    spend,
                    sessions=1,
                    failure=exc,
                    execution_id=execution_id,
                    depth_cap=depth_cap,
                    gateway_hosted=self.deps.open_run_ledger is not None,
                    priced=priced,
                )
                raise
            # Counted from the tree rather than from the cap: a planner that
            # stopped splitting at three ran three levels of sessions whatever
            # it was allowed to run.
            await _book(
                tracker,
                spend,
                sessions=len(levels(result)),
                gateway_hosted=self.deps.open_run_ledger is not None,
                priced=priced,
                label=execution_id,
            )
        return result

    def _binding(self, task: Task, execution_id: str) -> RunBinding:
        """Describe the planning session as its bearer's facts.

        Returns:
            The binding.
        """
        return RunBinding(
            execution_id=execution_id,
            agent_id=str(self.roster.lead.id),
            task_id=str(task.id),
            ref=ModelRef(
                provider=self.executor.provider, model_id=self.executor.model_id
            ),
            cost_ceiling=self.limits.cost_ceiling,
            label=execution_id,
        )

    def _service(
        self, provider: CompletionProvider, tracker: CostTrackerProtocol
    ) -> DecompositionService:
        """Build the decomposition service one planning run uses.

        Returns:
            The service, reading its recursion settings live.
        """
        strategy = build_decomposition_strategy(
            provider,
            self.executor.model_id,
            strategy_name=_STRATEGY_NAME,
            deps=DecompositionStrategyDeps(
                # Every owner here is bound to the executor pair, so the
                # selector answers with the one driver whichever owner the plan
                # names. The binding stays explicit: it was minted for that
                # pair.
                provider_selector=lambda _identity: provider,
                cost_tracker=tracker,
                agent_session_config=AgentSessionDecompositionConfig(
                    max_turns=self.limits.max_turns,
                    ceilings=SessionCeilings.of(
                        cost_ceiling=self.limits.cost_ceiling,
                        token_ceiling=self.limits.token_ceiling,
                    ),
                ),
                # The same resolver the service reads its recursion settings
                # from. Without it the strategy falls back to its own
                # construction default for the output-token ceiling, which is
                # sized for a model that writes its answer directly: a
                # reasoning model spends that budget before writing anything
                # and the plan comes back empty.
                config_resolver=self.config_resolver,
            ),
        )
        return DecompositionService(
            strategy,
            TaskStructureClassifier(),
            config_resolver=self.config_resolver,
        )


async def _shielded_book(
    tracker: ProgressTrackingLedger,
    spend: PlanningSpend,
    *,
    sessions: int,
    failure: BaseException,
    execution_id: str,
    depth_cap: int,
    gateway_hosted: bool,
    priced: bool,
) -> None:
    """Book a failed attempt's spend without displacing the failure.

    ``asyncio.shield`` keeps the drain from being torn down when a cancellation
    arrives at this await: the booking runs to completion, so ``spend`` ends up
    carrying what the attempt cost instead of stopping mid-drain with the money
    recorded nowhere. It does not hold the cancellation back, and it says
    nothing about the other way this can go wrong.

    That other way is a booking which RAISES, and it is logged rather than
    allowed out, for the reason ``_book_planning_budget`` swallows its ceiling
    breach one layer up: both callers are mid-``raise``, and the runner
    classifies a cell by exception TYPE. ``_plan_with_retry`` retries only a
    ``DecompositionError`` and ``_run_and_record`` files a systemic failure on
    membership, so a drain that failed, or a negative delta reaching
    ``spend.book``, would take the planning failure's place: the cell would
    lose its second attempt and the operator would read the wrong reason.

    Args:
        tracker: The attempt's authoritative cost sink.
        spend: Where the cell's planning spend accumulates.
        sessions: How many planning sessions this attempt ran.
        failure: What planning raised, for the log line.
        execution_id: Which planning attempt this was.
        depth_cap: The ``max_depth`` it was planning to.
        gateway_hosted: Whether these calls crossed a hosted gateway.
        priced: Whether the connection this attempt ran on prices its calls.
    """
    try:
        await asyncio.shield(
            _book(
                tracker,
                spend,
                sessions=sessions,
                gateway_hosted=gateway_hosted,
                priced=priced,
                label=execution_id,
            )
        )
    except Exception as booking:  # noqa: BLE001 -- the planning failure wins
        logger.warning(
            EVALS_RECURSION_PLAN_BOOKING_FAILED,
            execution_id=execution_id,
            depth_cap=depth_cap,
            sessions=sessions,
            error_type=type(booking).__name__,
            error=safe_error_description(booking),
        )
    logger.warning(
        EVALS_RECURSION_PLAN_FAILED,
        execution_id=execution_id,
        depth_cap=depth_cap,
        sessions=sessions,
        cost=spend.cost,
        tokens=spend.tokens,
        error_type=type(failure).__name__,
        error=safe_error_description(failure),
    )


async def _book(
    tracker: ProgressTrackingLedger,
    spend: PlanningSpend,
    *,
    sessions: int,
    gateway_hosted: bool,
    priced: bool,
    label: str,
) -> None:
    """Book what *tracker* recorded for one planning attempt.

    Drained before it is read: the cost chokepoint submits each record on a
    background task, so reading straight after the last planning turn loses
    whatever is still in flight.

    Summed through ``session_spend``, the same owner the execution half reads
    its sessions through, so a planning session and a leaf session cannot come
    to mean different things by the same number.

    Args:
        tracker: The attempt's authoritative cost sink.
        spend: Where the cell's planning spend accumulates.
        sessions: How many planning sessions this attempt ran.
        gateway_hosted: Whether these calls crossed a hosted gateway, which
            decides whether a second account of one call is on the ledger.
        priced: Whether the connection this attempt ran on prices its calls.
        label: Names this attempt in the dedupe log line.
    """
    await tracker.drain_pending_records()
    records = await collect_all_records(tracker)
    booked = session_spend(
        records, gateway_hosted=gateway_hosted, label=label, priced=priced
    )
    spend.book(cost=booked.cost, tokens=booked.tokens, sessions=sessions)


def levels(result: DecompositionResult) -> tuple[DecompositionResult, ...]:
    """Every node of the tree, each of which was one planning session.

    Args:
        result: The tree.

    Returns:
        The nodes, this level first.
    """
    below = tuple(node for child in result.children for node in levels(child))
    return (result, *below)


__all__ = [
    "SEED_WORKSPACE_SUMMARY",
    "AgentSessionPlanner",
    "PlanningSpend",
    "TreePlanner",
    "levels",
]
