"""Conflict resolution subsystem (see Communication design page).

Strategy implementations (``AuthorityResolver``, ``DebateResolver``,
``HumanEscalationResolver``, ``HybridResolver``) are imported directly
from their respective modules rather than re-exported here, keeping
this sub-package init focused on core abstractions.
"""

from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
    DebateConfig,
    HybridConfig,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.conflict_resolution.protocol import (
    ConflictResolver,
    JudgeDecision,
    JudgeEvaluator,
)
from synthorg.communication.conflict_resolution.service import (
    ConflictResolutionService,
)

__all__ = [
    "Conflict",
    "ConflictPosition",
    "ConflictResolution",
    "ConflictResolutionConfig",
    "ConflictResolutionOutcome",
    "ConflictResolutionService",
    "ConflictResolver",
    "DebateConfig",
    "DissentRecord",
    "HybridConfig",
    "JudgeDecision",
    "JudgeEvaluator",
]
