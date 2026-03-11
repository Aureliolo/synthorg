"""Conflict resolution event constants (see Communication design page)."""

from typing import Final

# Lifecycle
CONFLICT_DETECTED: Final[str] = "conflict.detected"
CONFLICT_RESOLUTION_STARTED: Final[str] = "conflict.resolution.started"
CONFLICT_RESOLVED: Final[str] = "conflict.resolved"
CONFLICT_RESOLUTION_FAILED: Final[str] = "conflict.resolution.failed"
CONFLICT_ESCALATED: Final[str] = "conflict.escalated"
CONFLICT_DISSENT_RECORDED: Final[str] = "conflict.dissent.recorded"

# Authority strategy
CONFLICT_AUTHORITY_DECIDED: Final[str] = "conflict.authority.decided"

# Debate strategy
CONFLICT_DEBATE_STARTED: Final[str] = "conflict.debate.started"
CONFLICT_DEBATE_JUDGE_DECIDED: Final[str] = "conflict.debate.judge_decided"

# Hybrid strategy
CONFLICT_HYBRID_REVIEW: Final[str] = "conflict.hybrid.review"
CONFLICT_HYBRID_AUTO_RESOLVED: Final[str] = "conflict.hybrid.auto_resolved"

# Human escalation
CONFLICT_HUMAN_ESCALATION_STUB: Final[str] = "conflict.human.escalation_stub"

# Validation
CONFLICT_VALIDATION_ERROR: Final[str] = "conflict.validation.error"
CONFLICT_NO_RESOLVER: Final[str] = "conflict.no_resolver"

# Fallback
CONFLICT_AUTHORITY_FALLBACK: Final[str] = "conflict.authority_fallback"
CONFLICT_AMBIGUOUS_RESULT: Final[str] = "conflict.ambiguous_result"

# Shared
CONFLICT_CROSS_DEPARTMENT: Final[str] = "conflict.cross_department"
CONFLICT_LCM_LOOKUP: Final[str] = "conflict.lcm_lookup"
CONFLICT_DISSENT_QUERIED: Final[str] = "conflict.dissent.queried"
CONFLICT_HIERARCHY_ERROR: Final[str] = "conflict.hierarchy.error"
CONFLICT_STRATEGY_ERROR: Final[str] = "conflict.strategy.error"
