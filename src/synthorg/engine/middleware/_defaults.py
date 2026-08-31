"""Default middleware registration.

Registers the built-in, S1, and coordination middleware factories so
that ``build_agent_middleware_chain`` and
``build_coordination_middleware_chain`` can resolve the default
chain names. Split into ``register_agent_defaults()`` and
``register_coordination_defaults()`` because the engine assembly and
the coordinator assembly each wire only their own half: the
coordination middleware pipeline is the sole registry consumer wired
in production, so it registers just the coordination factories rather
than pulling the agent-side ones too.
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
    TaskLedgerMiddleware,
)
from synthorg.engine.middleware.disclosure import DisclosureMiddleware
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

# ── Default middleware tables ─────────────────────────────────────

_AGENT_DEFAULTS: tuple[tuple[str, AgentMiddlewareFactory], ...] = (
    ("checkpoint_resume", CheckpointResumeMiddleware),
    ("delegation_chain_hash", DelegationChainHashMiddleware),
    ("authority_deference", AuthorityDeferenceGuard),
    ("sanitize_message", SanitizeMessageMiddleware),
    ("security_interceptor", SecurityInterceptorMiddleware),
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
    (
        "authority_deference_coordination",
        AuthorityDeferenceCoordinationMiddleware,
    ),
)


def register_coordination_defaults() -> None:
    """Register only the coordination middleware factories.

    The coordination middleware pipeline is the sole registry consumer
    wired in production, so the coordinator assembly registers just these
    factories rather than the whole default set (which would also pull
    the agent-side factories). Idempotent via the registry's
    register-once semantics.
    """
    for name, coord_factory in _COORDINATION_DEFAULTS:
        register_coordination_middleware(name, coord_factory)


def register_agent_defaults() -> None:
    """Register only the agent middleware factories (defaults + opt-in).

    The engine assembly registers just the agent-side factories so the
    agent chain can resolve its default names without also pulling the
    coordination factories. Idempotent via the registry's register-once
    semantics.
    """
    for name, factory in _AGENT_DEFAULTS:
        register_agent_middleware(name, factory)
    for name, factory in _AGENT_OPT_IN:
        register_agent_middleware(name, factory)
