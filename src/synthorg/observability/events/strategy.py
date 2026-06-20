"""Strategy module event constants."""

from typing import Final

STRATEGY_PACK_LOADED: Final[str] = "strategy.pack.loaded"
STRATEGY_PACK_NOT_FOUND: Final[str] = "strategy.pack.not_found"
STRATEGY_PACK_INVALID: Final[str] = "strategy.pack.invalid"
STRATEGY_CONTEXT_BUILT: Final[str] = "strategy.context.built"
STRATEGY_IMPACT_SCORED: Final[str] = "strategy.impact.scored"
STRATEGY_CONFIDENCE_FORMATTED: Final[str] = "strategy.confidence.formatted"
STRATEGY_OUTPUT_HANDLED: Final[str] = "strategy.output.handled"
STRATEGY_TIER_RESOLVED: Final[str] = "strategy.tier.resolved"
STRATEGY_LENS_APPLIED: Final[str] = "strategy.lens.applied"
STRATEGY_PRINCIPLE_VALIDATED: Final[str] = "strategy.principle.validated"
STRATEGY_PROMPT_INJECTED: Final[str] = "strategy.prompt.injected"
STRATEGY_CONFIG_VALIDATED: Final[str] = "strategy.config.validated"
STRATEGY_PRINCIPLES_LOAD_FAILED: Final[str] = "strategy.principles.load_failed"
STRATEGY_CONTEXT_PROVIDER_FAILED: Final[str] = "strategy.context.provider_failed"
STRATEGY_CONTEXT_MEMORY_QUERIED: Final[str] = "strategy.context.memory_queried"
STRATEGY_LENS_DEFINITION_INCOMPLETE: Final[str] = "strategy.lens.definition_incomplete"
STRATEGY_LENS_LOOKUP_FAILED: Final[str] = "strategy.lens.lookup_failed"
STRATEGY_LENS_ASSIGNMENT_FAILED: Final[str] = "strategy.lens.assignment_failed"
STRATEGY_PREMORTEM_RESPONSE_SKIPPED: Final[str] = "strategy.premortem.response_skipped"
STRATEGY_CONFLICT_PARSE_FAILED: Final[str] = "strategy.conflict.parse_failed"
STRATEGY_CONSENSUS_INIT: Final[str] = "strategy.consensus.init"
STRATEGY_CONSENSUS_CONFIG_INVALID: Final[str] = "strategy.consensus.config_invalid"
STRATEGY_CONSENSUS_DETECTED: Final[str] = "strategy.consensus.detected"
STRATEGY_CONSENSUS_NOT_DETECTED: Final[str] = "strategy.consensus.not_detected"
STRATEGY_DETECTOR_UNAVAILABLE: Final[str] = "strategy.detector.unavailable"
STRATEGY_DETECTOR_FALLBACK: Final[str] = "strategy.detector.fallback"
STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED: Final[str] = (
    "strategy.active_principle.persistence_failed"
)
STRATEGY_ACTIVE_PRINCIPLE_SNAPSHOT_REFRESHED: Final[str] = (
    "strategy.active_principle.snapshot_refreshed"
)
