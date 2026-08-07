"""Decomposition event constants."""

from typing import Final

DECOMPOSITION_STARTED: Final[str] = "decomposition.started"
DECOMPOSITION_COMPLETED: Final[str] = "decomposition.completed"
DECOMPOSITION_SUBTASK_CREATED: Final[str] = "decomposition.subtask.created"
DECOMPOSITION_VALIDATION_ERROR: Final[str] = "decomposition.validation.error"
DECOMPOSITION_STRUCTURE_CLASSIFIED: Final[str] = "decomposition.structure.classified"
DECOMPOSITION_ROLLUP_COMPUTED: Final[str] = "decomposition.rollup.computed"
DECOMPOSITION_GRAPH_VALIDATED: Final[str] = "decomposition.graph.validated"
DECOMPOSITION_GRAPH_CYCLE: Final[str] = "decomposition.graph.cycle"
DECOMPOSITION_FAILED: Final[str] = "decomposition.failed"
DECOMPOSITION_REFERENCE_ERROR: Final[str] = "decomposition.reference.error"
DECOMPOSITION_GRAPH_BUILT: Final[str] = "decomposition.graph.built"
DECOMPOSITION_LLM_CALL_START: Final[str] = "decomposition.llm.call.start"
DECOMPOSITION_LLM_CALL_COMPLETE: Final[str] = "decomposition.llm.call.complete"
DECOMPOSITION_LLM_PARSE_ERROR: Final[str] = "decomposition.llm.parse.error"
DECOMPOSITION_LLM_RETRY: Final[str] = "decomposition.llm.retry"

# Agent-session decomposition strategy: the owner-run planning loop.
DECOMPOSITION_SESSION_STARTED: Final[str] = "decomposition.session.started"
DECOMPOSITION_SESSION_COMPLETED: Final[str] = "decomposition.session.completed"
DECOMPOSITION_SESSION_NO_PLAN: Final[str] = "decomposition.session.no_plan"
DECOMPOSITION_SESSION_FALLBACK: Final[str] = "decomposition.session.fallback"
DECOMPOSITION_SESSION_TOOL_DROPPED: Final[str] = "decomposition.session.tool_dropped"
DECOMPOSITION_SESSION_DUPLICATE_SUBMIT: Final[str] = (
    "decomposition.session.duplicate_submit"
)

# Coordination-constraints middleware received an empty plan text from
# the LLM.  Caller falls back to a default plan; the event preserves the
# failure mode for triage.
DECOMPOSITION_EMPTY_PLAN_TEXT: Final[str] = "decomposition.empty_plan_text"

DECOMPOSITION_MODEL_UNSET: Final[str] = "decomposition.model_unset"
"""No explicit provider + model pair is bound for decomposition.

The runtime then builds without a coordinator. Its own event so that
outcome is attributable on its own terms rather than only through the
degraded-mode line that follows it."""

DECOMPOSITION_REBUILT_FROM_PLAN: Final[str] = "decomposition.rebuilt_from_plan"
"""An approved plan was projected back onto a dispatchable decomposition.

Emitted on the approval-dispatch path, where the reviewed plan (not the
snapshot captured at gate time) decides what builds. It records how many
items were dropped as decisions and how many edges survived, which is the
only place the difference between what an operator approved and what was
dispatched is visible."""
