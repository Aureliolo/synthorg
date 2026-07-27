"""Assembly factory for the conflict-resolution service.

Builds the five concrete resolvers (authority, debate, hybrid, human
escalation, evidence-weighted) over a config-time
:class:`~synthorg.communication.delegation.hierarchy.HierarchyResolver` and
wires them into a :class:`ConflictResolutionService`. Co-located with the
resolvers so the boot path (``api.construction_phase``) stays a thin caller
and never imports each strategy class directly.
"""

from synthorg.communication.conflict_resolution.authority_strategy import (
    AuthorityResolver,
)
from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
)
from synthorg.communication.conflict_resolution.debate_strategy import DebateResolver
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.communication.conflict_resolution.evidence_strategy import (
    EvidenceWeightedResolver,
)
from synthorg.communication.conflict_resolution.human_strategy import (
    HumanEscalationResolver,
)
from synthorg.communication.conflict_resolution.hybrid_strategy import HybridResolver
from synthorg.communication.conflict_resolution.protocol import (
    ConflictResolver,
    JudgeEvaluator,
)
from synthorg.communication.conflict_resolution.service import (
    ConflictResolutionService,
)
from synthorg.communication.delegation.hierarchy import HierarchyResolver
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.core.company import Company


def build_conflict_resolution_service(
    *,
    config: ConflictResolutionConfig,
    company: Company,
    escalation_store: EscalationQueueStore,
    escalation_processor: DecisionProcessor,
    escalation_registry: PendingFuturesRegistry,
    event_hub: EventStreamHub | None = None,
    message_bus: object | None = None,
    judge_evaluator: JudgeEvaluator | None = None,
) -> ConflictResolutionService:
    """Build a fully-wired :class:`ConflictResolutionService`.

    The hierarchy is resolved from the boot-time company snapshot, matching
    the construction-time wiring of the escalation infrastructure this service
    shares (a later org mutation re-snapshots the company but, like the rest of
    the construction-phase wiring, the resolver hierarchy is fixed at boot). The
    human-escalation resolver reuses the already-built escalation store /
    processor / registry so a conflict escalated to a human surfaces on the same
    queue the standalone escalation subsystem serves.

    Args:
        config: Conflict-resolution configuration (strategy + sub-configs).
        company: Boot-time company structure for hierarchy/seniority lookups.
        escalation_store: Shared escalation queue store.
        escalation_processor: Shared escalation decision processor.
        escalation_registry: Shared pending-futures registry.
        event_hub: Optional event-stream hub for AG-UI dissent events.
        message_bus: Optional message bus for dissent broadcast.
        judge_evaluator: Optional LLM judge shared by the debate and hybrid
            resolvers. When ``None`` both fall back to authority-based
            judging (no auto-resolution, no ambiguity escalation).

    Returns:
        The wired :class:`ConflictResolutionService`.
    """
    hierarchy = HierarchyResolver(company)
    human_resolver = HumanEscalationResolver(
        store=escalation_store,
        processor=escalation_processor,
        registry=escalation_registry,
    )
    resolvers: dict[ConflictResolutionStrategy, ConflictResolver] = {
        ConflictResolutionStrategy.AUTHORITY: AuthorityResolver(hierarchy=hierarchy),
        ConflictResolutionStrategy.DEBATE: DebateResolver(
            hierarchy=hierarchy,
            config=config.debate,
            judge_evaluator=judge_evaluator,
        ),
        ConflictResolutionStrategy.HYBRID: HybridResolver(
            hierarchy=hierarchy,
            config=config.hybrid,
            human_resolver=human_resolver,
            review_evaluator=judge_evaluator,
        ),
        ConflictResolutionStrategy.HUMAN: human_resolver,
        ConflictResolutionStrategy.EVIDENCE_WEIGHTED: EvidenceWeightedResolver(),
    }
    return ConflictResolutionService(
        config=config,
        resolvers=resolvers,
        event_hub=event_hub,
        message_bus=message_bus,
    )


__all__ = ["build_conflict_resolution_service"]
