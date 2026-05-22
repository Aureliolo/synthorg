"""Structured event-name constants for the research subsystem.

Event names follow the ``research.<area>.<outcome>`` convention so the
sink pipeline can filter by prefix. Lifecycle transitions are logged at
INFO after the run record is persisted; failures at WARNING/ERROR with
safe context before raising.
"""

from typing import Final

RESEARCH_RUN_STARTED: Final[str] = "research.run.started"
"""Emitted at INFO when a research run begins, before planning."""

RESEARCH_RUN_PLANNED: Final[str] = "research.run.planned"
"""Emitted at INFO after the query planner produces a plan."""

RESEARCH_RUN_RETRIEVED: Final[str] = "research.run.retrieved"
"""Emitted at INFO after multi-source retrieval, with candidate counts."""

RESEARCH_SOURCE_FAILED: Final[str] = "research.source.failed"
"""Emitted at WARNING when one retrieval source fails; the run continues
with the remaining sources."""

RESEARCH_RUN_TRIAGED: Final[str] = "research.run.triaged"
"""Emitted at INFO after credibility triage, with retained counts."""

RESEARCH_RUN_DEDUPLICATED: Final[str] = "research.run.deduplicated"
"""Emitted at INFO after deduplication, with the collapsed count."""

RESEARCH_RUN_SYNTHESISED: Final[str] = "research.run.synthesised"
"""Emitted at INFO after synthesis produces a cited report."""

RESEARCH_RUN_COMPLETED: Final[str] = "research.run.completed"
"""Emitted at INFO after a completed run is persisted."""

RESEARCH_RUN_FAILED: Final[str] = "research.run.failed"
"""Emitted at WARNING when a run fails; the run row is persisted as FAILED."""

RESEARCH_LLM_OUTPUT_INVALID: Final[str] = "research.llm.output_invalid"
"""Emitted at WARNING when an LLM stage returns unparseable structured output."""
