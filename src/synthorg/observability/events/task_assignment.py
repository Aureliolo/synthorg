"""Task assignment event constants."""

from typing import Final

TASK_ASSIGNMENT_STARTED: Final[str] = "task_assignment.started"
TASK_ASSIGNMENT_COMPLETE: Final[str] = "task_assignment.complete"
TASK_ASSIGNMENT_FAILED: Final[str] = "task_assignment.failed"
TASK_ASSIGNMENT_NO_ELIGIBLE: Final[str] = "task_assignment.no_eligible"
TASK_ASSIGNMENT_AGENT_SCORED: Final[str] = "task_assignment.agent.scored"
TASK_ASSIGNMENT_AGENT_SELECTED: Final[str] = "task_assignment.agent.selected"
TASK_ASSIGNMENT_MANUAL_VALIDATED: Final[str] = "task_assignment.manual.validated"
TASK_ASSIGNMENT_WORKLOAD_BALANCED: Final[str] = "task_assignment.workload.balanced"
TASK_ASSIGNMENT_COST_OPTIMIZED: Final[str] = "task_assignment.cost_optimized"
TASK_ASSIGNMENT_HIERARCHICAL_DELEGATED: Final[str] = (
    "task_assignment.hierarchical.delegated"
)
TASK_ASSIGNMENT_AUCTION_BID: Final[str] = "task_assignment.auction.bid"
TASK_ASSIGNMENT_AUCTION_WON: Final[str] = "task_assignment.auction.won"
TASK_ASSIGNMENT_CAPABILITY_FALLBACK: Final[str] = "task_assignment.capability_fallback"
TASK_ASSIGNMENT_DELEGATOR_RESOLVED: Final[str] = "task_assignment.delegator.resolved"
TASK_ASSIGNMENT_HIERARCHY_TRANSITIVE: Final[str] = (
    "task_assignment.hierarchy.transitive_fallback"
)
TASK_ASSIGNMENT_WORKLOAD_MISSING: Final[str] = "task_assignment.agent.workload_missing"

# Project team filtering events
TASK_ASSIGNMENT_PROJECT_FILTERED: Final[str] = "task_assignment.project.filtered"
TASK_ASSIGNMENT_PROJECT_NO_ELIGIBLE: Final[str] = "task_assignment.project.no_eligible"

# Pool-filter reason rewriter (rewrite_success_reason callable on
# PoolFilterResult) raised an exception; the strategy fell back to
# the ranker's default reason. The assignment itself is still valid
# since the rewriter only affects the human-readable explanation.
TASK_ASSIGNMENT_REASON_REWRITER_FAILED: Final[str] = (
    "task_assignment.reason_rewriter_failed"
)

# HierarchicalPoolFilter could not consult the HierarchyResolver
# (transient backing-store error, malformed graph, ...). The filter
# returned an empty pool and the strategy treats this as
# no-eligible. Distinct from "delegator unknown" / "no subordinates"
# which are normal data outcomes, not operational failures.
TASK_ASSIGNMENT_HIERARCHY_LOOKUP_FAILED: Final[str] = (
    "task_assignment.hierarchy.lookup_failed"
)

# Assignment-strategy registry built at startup.  Build payload
# (``has_hierarchy``, ``custom_scorer``) lets operators confirm
# configuration parity across deployments.
TASK_ASSIGNMENT_REGISTRY_BUILD: Final[str] = "task_assignment.registry.build"
