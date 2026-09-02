# module-kind: declarative
"""What decides whether an action is allowed, and who is told when it is not."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.security.audit import AuditLog
from synthorg.security.config import SecurityConfig
from synthorg.security.policy_engine.protocol import PolicyEngine

if TYPE_CHECKING:
    # Genuine cycle breakers, and the reason this package holds no logic:
    # each reaches ``AgentEngine`` again on its own import path (the review
    # gate through the completion oracle and the red-team builder), so
    # naming them at module level closes the loop that ``agent_engine`` ->
    # ``dependencies`` opens. PEP 649 leaves the annotations unevaluated
    # and nothing here reads them at runtime.
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.review.pipeline import ReviewPipeline
    from synthorg.engine.review_gate import ReviewGateService


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineGovernance:
    """Approval, policy, audit and the review gates.

    Attributes:
        policy_engine: Evaluates a proposed tool call, or ``None`` when no
            policy governs this engine.
        security_config: The boot security configuration, or ``None``.
        security_config_provider: Reads the LIVE configuration per request
            so an operator toggle applies without a restart. ``None``
            falls back to :attr:`security_config`, which is what a
            direct construction gets.
        audit_log: Where governed actions are recorded. Carries no default
            because an engine that audits nowhere is a decision.
        approval_store: Where an escalation is filed, or ``None``, in
            which case the engine builds no gate of its own.
        approval_gate: A gate wired by the composition root, which wins
            unconditionally so the engine parks and ``/approvals`` resumes
            on one gate. ``None`` lets the engine build its own.
        parked_context_repo: Where a parked context is persisted. A gate
            without it refuses the park rather than reporting PARKED over
            nothing.
        approval_interrupt_timeout_seconds: Override for how long a gate
            waits on an interrupt, or ``None`` for the gate's own default.
        review_gate: Settles IN_REVIEW, or ``None``.
        review_pipeline: The staged pipeline auto-review runs, or ``None``
            when ``engine.auto_review_on_completion`` is off, which leaves
            completed work in IN_REVIEW for a human.
    """

    policy_engine: PolicyEngine | None
    security_config: SecurityConfig | None
    security_config_provider: Callable[[], SecurityConfig | None] | None
    audit_log: AuditLog
    approval_store: ApprovalStoreProtocol | None
    approval_gate: ApprovalGate | None
    parked_context_repo: ParkedContextRepository | None
    approval_interrupt_timeout_seconds: float | None
    review_gate: ReviewGateService | None
    review_pipeline: ReviewPipeline | None


__all__ = ["EngineGovernance"]
