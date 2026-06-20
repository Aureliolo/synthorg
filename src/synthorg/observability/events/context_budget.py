"""Context budget management event constants for structured logging."""

from typing import Final

# Fill level tracking
CONTEXT_BUDGET_FILL_UPDATED: Final[str] = "context_budget.fill.updated"

# Compaction lifecycle
CONTEXT_BUDGET_COMPACTION_STARTED: Final[str] = "context_budget.compaction.started"
CONTEXT_BUDGET_COMPACTION_COMPLETED: Final[str] = "context_budget.compaction.completed"
CONTEXT_BUDGET_COMPACTION_FAILED: Final[str] = "context_budget.compaction.failed"
CONTEXT_BUDGET_COMPACTION_SKIPPED: Final[str] = "context_budget.compaction.skipped"

# Fallback summary (compaction proceeded but no assistant content for summary)
CONTEXT_BUDGET_COMPACTION_FALLBACK: Final[str] = "context_budget.compaction.fallback"

# Indicator injection
CONTEXT_BUDGET_INDICATOR_INJECTED: Final[str] = "context_budget.indicator.injected"

# Agent-controlled compaction
CONTEXT_BUDGET_AGENT_COMPACTION_REQUESTED: Final[str] = (
    "context_budget.agent_compaction.requested"
)
CONTEXT_BUDGET_EPISTEMIC_MARKERS_PRESERVED: Final[str] = (
    "context_budget.epistemic_markers.preserved"
)

# Phase-2: LLM-backed summariser
CONTEXT_BUDGET_COMPACTION_LLM_STARTED: Final[str] = (
    "context_budget.compaction.llm.started"
)
CONTEXT_BUDGET_COMPACTION_LLM_COMPLETED: Final[str] = (
    "context_budget.compaction.llm.completed"
)
CONTEXT_BUDGET_COMPACTION_LLM_FALLBACK: Final[str] = (
    "context_budget.compaction.llm.fallback"
)

# Phase-2: memory offload of archived turn batches
CONTEXT_BUDGET_COMPACTION_OFFLOAD_STORED: Final[str] = (
    "context_budget.compaction.offload.stored"
)
CONTEXT_BUDGET_COMPACTION_OFFLOAD_FAILED: Final[str] = (
    "context_budget.compaction.offload.failed"
)
CONTEXT_BUDGET_COMPACTION_OFFLOAD_REHYDRATED: Final[str] = (
    "context_budget.compaction.offload.rehydrated"
)
