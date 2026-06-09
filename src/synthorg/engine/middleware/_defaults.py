"""Default middleware registration.

Registers all built-in, S1, and coordination middleware factories so
that ``build_agent_middleware_chain`` and
``build_coordination_middleware_chain`` can resolve the default
chain names.

Call ``register_default_middleware()`` once at application startup
(e.g. from ``AgentEngine.__init__`` or the app entrypoint).
"""

from synthorg.engine.middleware.behavior_tagger import (
    BehaviorTaggerMiddleware,
)
from synthorg.engine.middleware.builtin import (
    ApprovalGateMiddleware,
    CheckpointResumeMiddleware,
    ClassificationMiddleware,
    CostRecordingMiddleware,
    SanitizeMessageMiddleware,
    SecurityInterceptorMiddleware,
)
from synthorg.engine.middleware.coordination_constraints import (
    PlanReviewGateMiddleware,
    ProgressLedgerMiddleware,
    ReplanMiddleware,
    TaskLedgerMiddleware,
)
from synthorg.engine.middleware.disclosure import DisclosureMiddleware
from synthorg.engine.middleware.policy_gate import PolicyGateMiddleware
from synthorg.engine.middleware.registry import (
    AgentMiddlewareFactory,
    CoordinationMiddlewareFactory,
    register_agent_middleware,
    register_coordination_middleware,
)
from synthorg.engine.middleware.s1_constraints import (
    AssumptionViolationMiddleware,
    AuthorityDeferenceCoordinationMiddleware,
    AuthorityDeferenceGuard,
    ClarificationGateMiddleware,
    DelegationChainHashMiddleware,
)
from synthorg.engine.middleware.semantic_drift import SemanticDriftDetector
from synthorg.observability import get_logger
from synthorg.observability.events.middleware import (
    MIDDLEWARE_DEFAULTS_REGISTERED,
)

logger = get_logger(__name__)

_registered = False

# ── Default middleware tables ─────────────────────────────────────

_AGENT_DEFAULTS: tuple[tuple[str, AgentMiddlewareFactory], ...] = (
    ("checkpoint_resume", CheckpointResumeMiddleware),
    ("delegation_chain_hash", DelegationChainHashMiddleware),
    ("authority_deference", AuthorityDeferenceGuard),
    ("sanitize_message", SanitizeMessageMiddleware),
    ("security_interceptor", SecurityInterceptorMiddleware),
    ("policy_gate", PolicyGateMiddleware),
    ("approval_gate", ApprovalGateMiddleware),
    ("assumption_violation", AssumptionViolationMiddleware),
    ("classification", ClassificationMiddleware),
    ("cost_recording", CostRecordingMiddleware),
    ("disclosure", DisclosureMiddleware),
)

# Opt-in middleware: registered in the factory but NOT in the
# default agent chain.  Enable by adding the name to the
# company's AgentMiddlewareConfig.chain.
_AGENT_OPT_IN: tuple[tuple[str, AgentMiddlewareFactory], ...] = (
    ("behavior_tagger", BehaviorTaggerMiddleware),
    ("semantic_drift_detector", SemanticDriftDetector),
)

_COORDINATION_DEFAULTS: tuple[tuple[str, CoordinationMiddlewareFactory], ...] = (
    ("clarification_gate", ClarificationGateMiddleware),
    ("task_ledger", TaskLedgerMiddleware),
    ("plan_review_gate", PlanReviewGateMiddleware),
    ("progress_ledger", ProgressLedgerMiddleware),
    ("coordination_replan", ReplanMiddleware),
    (
        "authority_deference_coordination",
        AuthorityDeferenceCoordinationMiddleware,
    ),
)


def register_default_middleware() -> None:
    """Register all built-in middleware factories.

    Idempotent: safe to call multiple times (subsequent calls are
    no-ops due to the registry's idempotency semantics).
    """
    global _registered  # noqa: PLW0603
    if _registered:
        return

    for name, factory in _AGENT_DEFAULTS:
        register_agent_middleware(name, factory)

    for name, factory in _AGENT_OPT_IN:
        register_agent_middleware(name, factory)

    for name, coord_factory in _COORDINATION_DEFAULTS:
        register_coordination_middleware(name, coord_factory)

    _registered = True
    logger.debug(
        MIDDLEWARE_DEFAULTS_REGISTERED,
        agent_count=len(_AGENT_DEFAULTS) + len(_AGENT_OPT_IN),
        coordination_count=len(_COORDINATION_DEFAULTS),
    )
