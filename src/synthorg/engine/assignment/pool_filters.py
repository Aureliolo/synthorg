"""Concrete ``CandidatePoolFilter`` implementations.

``IdentityPoolFilter`` is the default for every scoring strategy
that does not need pre-scoring narrowing.

``HierarchicalPoolFilter`` narrows the pool to subordinates of the
task's delegator, and is used by the ``hierarchical`` strategy.
"""

from typing import TYPE_CHECKING, Final

from synthorg.engine.assignment.pool_filter_protocol import PoolFilterResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_DELEGATOR_RESOLVED,
    TASK_ASSIGNMENT_HIERARCHY_LOOKUP_FAILED,
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
        """Narrow the pool to subordinates of the resolved delegator.

        A failure in the underlying ``HierarchyResolver`` (transient
        backing-store error, malformed graph, ...) is logged and
        treated as "no eligible pool" so the assignment falls through
        to a structured ``AssignmentResult(selected=None, ...)``
        instead of crashing the engine.
        """
        delegator = self._resolve_delegator(request)
        try:
            known = self._is_known_delegator(delegator)
        except Exception as exc:
            return self._hierarchy_lookup_failure(
                request,
                delegator,
                exc,
                stage="is_known_delegator",
            )
        if not known:
            return PoolFilterResult(
                agents=(),
                reason=f"Delegator {delegator!r} not found in hierarchy",
            )
        try:
            subordinates = self._filter_by_hierarchy(request, delegator)
        except Exception as exc:
            return self._hierarchy_lookup_failure(
                request,
                delegator,
                exc,
                stage="filter_by_hierarchy",
            )
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

    @staticmethod
    def _hierarchy_lookup_failure(
        request: AssignmentRequest,
        delegator: str,
        exc: Exception,
        *,
        stage: str,
    ) -> PoolFilterResult:
        """Log a hierarchy lookup failure and return an empty pool."""
        logger.warning(
            TASK_ASSIGNMENT_HIERARCHY_LOOKUP_FAILED,
            task_id=request.task.id,
            delegator=delegator,
            stage=stage,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return PoolFilterResult(
            agents=(),
            reason=(
                f"Hierarchy lookup failed for delegator {delegator!r} "
                f"({stage}): {type(exc).__name__}"
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
            # Prefer direct reports so delegation stays close: each hop
            # adds reporting overhead and dilutes accountability.
            return direct
        # No direct report is in the available pool. Fall back to any
        # transitive subordinate so the task still flows down the
        # management chain rather than silently failing.
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
        """True if delegator participates in the org hierarchy at all.

        "Known" here means the delegator has either direct reports or
        a supervisor recorded. An agent that exists in the system but
        is not wired into the hierarchy (no reports AND no supervisor)
        is treated as unknown so the strategy returns a precise
        no-eligible reason rather than silently picking up the leaf
        with-no-subordinates path.
        """
        has_reports = bool(self._hierarchy.get_direct_reports(delegator))
        has_supervisor = self._hierarchy.get_supervisor(delegator) is not None
        return has_reports or has_supervisor
