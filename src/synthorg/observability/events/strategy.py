"""Strategy module event constants."""

from typing import Final

STRATEGY_PACK_LOADED: Final[str] = "strategy.pack.loaded"
STRATEGY_PACK_NOT_FOUND: Final[str] = "strategy.pack.not_found"
STRATEGY_PACK_INVALID: Final[str] = "strategy.pack.invalid"
STRATEGY_CONTEXT_BUILT: Final[str] = "strategy.context.built"
STRATEGY_OUTPUT_HANDLED: Final[str] = "strategy.output.handled"
STRATEGY_PROMPT_INJECTED: Final[str] = "strategy.prompt.injected"
STRATEGY_CONFIG_VALIDATED: Final[str] = "strategy.config.validated"
STRATEGY_PRINCIPLES_LOAD_FAILED: Final[str] = "strategy.principles.load_failed"
STRATEGY_CONTEXT_PROVIDER_FAILED: Final[str] = "strategy.context.provider_failed"
STRATEGY_CONTEXT_MEMORY_QUERIED: Final[str] = "strategy.context.memory_queried"
STRATEGY_LENS_DEFINITION_INCOMPLETE: Final[str] = "strategy.lens.definition_incomplete"
STRATEGY_LENS_LOOKUP_FAILED: Final[str] = "strategy.lens.lookup_failed"
STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED: Final[str] = (
    "strategy.active_principle.persistence_failed"
)
STRATEGY_ACTIVE_PRINCIPLE_SNAPSHOT_REFRESHED: Final[str] = (
    "strategy.active_principle.snapshot_refreshed"
)
STRATEGY_PRINCIPLE_OVERRIDE_SNAPSHOT_REFRESHED: Final[str] = (
    "strategy.principle_override.snapshot_refreshed"
)
STRATEGY_CONTEXT_SNAPSHOT_REFRESHED: Final[str] = "strategy.context.snapshot_refreshed"
