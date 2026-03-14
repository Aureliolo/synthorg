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
