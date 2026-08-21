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

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from evals.harness.binding import RunBinding
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.session import SessionLimits, SweepDeps, ledger_scope
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
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

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


@dataclass(frozen=True)
class PlannedTree:
    """A decomposition tree and what producing it cost.

    Attributes:
        result: The tree.
        cost: What the planning sessions spent, across every level.
        sessions: How many planning sessions ran, which is one per node that
            planned rather than one per run.
    """

    result: DecompositionResult
    cost: float
    sessions: int


@runtime_checkable
class TreePlanner(Protocol):
    """Whatever produces the tree one run is executed from."""

    async def plan(
        self, *, task: Task, depth_cap: int, execution_id: str
    ) -> PlannedTree:
        """Decompose *task* down to *depth_cap*."""
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
        self, *, task: Task, depth_cap: int, execution_id: str
    ) -> PlannedTree:
        """Decompose *task* down to *depth_cap* and book what it cost.

        Args:
            task: The root objective.
            depth_cap: The ``max_depth`` this run is allowed.
            execution_id: What the ledger keys the planning spend on.

        Returns:
            The tree and its cost.
        """
        provider = await self.deps.build_provider(self._binding(task, execution_id))
        fallback = ProgressTrackingLedger()
        async with ledger_scope(self.deps, execution_id, fallback) as tracker:
            result = await build_tree(
                # The strategy books to its OWN tracker, never the hosted one,
                # for the reason `open_session` does the same: a planning
                # completion goes out through the hosted gateway, which records
                # it, and the strategy's own cost scope records it again, so a
                # shared ledger counts every planning call twice and the cost
                # panel overstates exactly the arm that plans the most. When no
                # gateway is hosted the two are the same object by
                # construction, and the sum below is the only read either way.
                service=self._service(provider, fallback),
                task=task,
                depth_cap=depth_cap,
                workspace_summary=SEED_WORKSPACE_SUMMARY,
                available_roles=self.roster.roles,
            )
            # Drained before it is read: the cost chokepoint submits each
            # record on a background task, so reading straight after the last
            # planning turn loses whatever is still in flight.
            await tracker.drain_pending_records()
            cost = sum(record.cost for record in await collect_all_records(tracker))
        return PlannedTree(
            result=result,
            cost=cost,
            # Counted from the tree rather than from the cap: a planner that
            # stopped splitting at three ran three levels of sessions whatever
            # it was allowed to run.
            sessions=len(levels(result)),
        )

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
            tool_provider=None,
            # Every owner here is bound to the executor pair, so the selector
            # answers with the one driver whichever owner the plan names. The
            # binding stays explicit: it was minted for that pair.
            provider_selector=lambda _identity: provider,
            cost_tracker=tracker,
            agent_session_max_turns=self.limits.max_turns,
            agent_session_ceilings=SessionCeilings.of(
                cost_ceiling=self.limits.cost_ceiling, token_ceiling=None
            ),
        )
        return DecompositionService(
            strategy,
            TaskStructureClassifier(),
            config_resolver=self.config_resolver,
        )


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
    "PlannedTree",
    "TreePlanner",
    "levels",
]
