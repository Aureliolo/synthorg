"""Concrete ``CandidatePoolFilter`` implementations.

``IdentityPoolFilter`` is the default for every scoring strategy
that does not need pre-scoring narrowing.

``HierarchicalPoolFilter`` narrows the pool to subordinates of the
task's delegator, and is used by the ``hierarchical`` strategy.
"""

from typing import TYPE_CHECKING, Final

from synthorg.engine.assignment.pool_filter_protocol import PoolFilterResult
from synthorg.observability import get_logger
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_DELEGATOR_RESOLVED,
    TASK_ASSIGNMENT_HIERARCHY_TRANSITIVE,
)

if TYPE_CHECKING:
    from synthorg.communication.delegation.hierarchy import HierarchyResolver
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.assignment.models import AssignmentRequest

logger = get_logger(__name__)

POOL_FILTER_NAME_IDENTITY: Final[str] = "identity"
POOL_FILTER_NAME_HIERARCHICAL: Final[str] = "hierarchical"


class IdentityPoolFilter:
    """No-op pool filter -- returns the request's pool unchanged."""

    __slots__ = ()

    @property
    def name(self) -> str:
        """Filter identifier."""
        return POOL_FILTER_NAME_IDENTITY

    def filter(self, request: AssignmentRequest) -> PoolFilterResult:
        """Return the request's pool unchanged."""
        return PoolFilterResult(agents=request.available_agents)


class HierarchicalPoolFilter:
    """Narrows the pool to subordinates of the task's delegator.

    Resolves the delegator from ``task.delegation_chain[-1]`` (with
    ``task.created_by`` as fallback). Narrows ``available_agents``
    to the delegator's direct reports first, falling back to
    transitive subordinates if no direct report is in the pool. When
    the delegator is unknown to the hierarchy or no subordinates
    are present, returns an empty pool with a context-rich
    ``reason`` that the calling strategy uses for
    ``AssignmentResult.reason``.
    """

    __slots__ = ("_hierarchy",)

    def __init__(self, hierarchy: HierarchyResolver) -> None:
        self._hierarchy = hierarchy

    @property
    def name(self) -> str:
        """Filter identifier."""
        return POOL_FILTER_NAME_HIERARCHICAL

    def filter(self, request: AssignmentRequest) -> PoolFilterResult:
        """Narrow the pool to subordinates of the resolved delegator."""
        delegator = self._resolve_delegator(request)
        if not self._is_known_delegator(delegator):
            return PoolFilterResult(
                agents=(),
                reason=f"Delegator {delegator!r} not found in hierarchy",
            )
        subordinates = self._filter_by_hierarchy(request, delegator)
        if not subordinates:
            return PoolFilterResult(
                agents=(),
                reason=(
                    f"No subordinates of {delegator!r} found in "
                    f"available agents for task {request.task.id!r}"
                ),
            )
        return PoolFilterResult(
            agents=subordinates,
            rewrite_success_reason=lambda selected: (
                f"Delegated from {delegator!r} to "
                f"{selected.agent_identity.name!r} "
                f"(score={selected.score:.2f})"
            ),
        )

    def _resolve_delegator(self, request: AssignmentRequest) -> str:
        """Pick delegator from ``delegation_chain[-1]`` or ``created_by``."""
        task = request.task
        if task.delegation_chain:
            delegator = task.delegation_chain[-1]
            logger.debug(
                TASK_ASSIGNMENT_DELEGATOR_RESOLVED,
                task_id=task.id,
                delegator=delegator,
                source="delegation_chain",
            )
            return delegator
        logger.debug(
            TASK_ASSIGNMENT_DELEGATOR_RESOLVED,
            task_id=task.id,
            delegator=task.created_by,
            source="created_by",
        )
        return task.created_by

    def _filter_by_hierarchy(
        self,
        request: AssignmentRequest,
        delegator: str,
    ) -> tuple[AgentIdentity, ...]:
        """Subordinates of ``delegator`` (direct first, transitive fallback)."""
        direct_reports = set(self._hierarchy.get_direct_reports(delegator))
        direct = tuple(a for a in request.available_agents if a.name in direct_reports)
        if direct:
            return direct
        available_names = tuple(a.name for a in request.available_agents)
        logger.debug(
            TASK_ASSIGNMENT_HIERARCHY_TRANSITIVE,
            delegator=delegator,
            direct_reports=tuple(sorted(direct_reports)),
            available_agents=available_names,
        )
        return tuple(
            a
            for a in request.available_agents
            if self._hierarchy.is_subordinate(delegator, a.name)
        )

    def _is_known_delegator(self, delegator: str) -> bool:
        """True if delegator has direct reports or a supervisor."""
        has_reports = bool(self._hierarchy.get_direct_reports(delegator))
        has_supervisor = self._hierarchy.get_supervisor(delegator) is not None
        return has_reports or has_supervisor
