"""Event constants for procedural memory auto-generation.

Used when a failed task execution triggers a proposer LLM call
to generate reusable procedural knowledge.
"""

from typing import Final

PROCEDURAL_MEMORY_START: Final[str] = "procedural_memory.proposal.start"
"""Pipeline started for a failed execution."""

PROCEDURAL_MEMORY_PAYLOAD_BUILT: Final[str] = "procedural_memory.payload.built"
"""Failure analysis payload constructed from execution data."""

PROCEDURAL_MEMORY_PROPOSED: Final[str] = "procedural_memory.proposal.complete"
"""Proposer LLM returned a valid proposal."""

PROCEDURAL_MEMORY_SKIPPED: Final[str] = "procedural_memory.proposal.skipped"
"""Proposal skipped (proposer returned None or disabled)."""

PROCEDURAL_MEMORY_LOW_CONFIDENCE: Final[str] = (
    "procedural_memory.proposal.low_confidence"
)
"""Proposal discarded because confidence was below threshold."""

PROCEDURAL_CAPTURE_QUALITY_BELOW_THRESHOLD: Final[str] = (
    "procedural_capture.quality_below_threshold"
)
"""Captured procedural memory discarded due to low quality score."""

PROCEDURAL_CAPTURE_STORED: Final[str] = "procedural_capture.stored"
"""Procedural memory capture entry stored."""

PROCEDURAL_CAPTURE_STORE_FAILED: Final[str] = "procedural_capture.store_failed"
"""Failed to store procedural memory capture entry."""

PROCEDURAL_MEMORY_STORED: Final[str] = "procedural_memory.entry.stored"
"""Procedural memory entry stored in backend."""

PROCEDURAL_MEMORY_STORE_FAILED: Final[str] = "procedural_memory.entry.store_failed"
"""Failed to store procedural memory entry."""

PROCEDURAL_PROPAGATION_TARGET_FAILED: Final[str] = (
    "procedural_propagation.target_failed"
)
"""Failed to propagate procedural memory to target agent."""

PROCEDURAL_CAPTURE_BUILD: Final[str] = "procedural_capture.build"
"""Procedural memory capture payload constructed."""

PROCEDURAL_MEMORY_ERROR: Final[str] = "procedural_memory.error"
"""Unrecoverable error in procedural memory pipeline."""

PROCEDURAL_MEMORY_DISABLED: Final[str] = "procedural_memory.disabled"
"""Procedural memory generation is disabled in config."""

PROCEDURAL_MEMORY_SKILL_MD: Final[str] = "procedural_memory.skill_md.written"
"""SKILL.md file materialized from a procedural memory proposal."""

PROCEDURAL_MEMORY_PROPOSER_INIT: Final[str] = "procedural_memory.proposer.init"
"""ProceduralMemoryProposer constructed with configuration."""

PROCEDURAL_PRUNING_UNKNOWN_TYPE: Final[str] = "procedural_pruning.unknown_type"
"""Unknown pruning strategy type in factory configuration."""

PROCEDURAL_MEMORY_CONFIG_RESOLVE_FAILED: Final[str] = (
    "procedural_memory.config.resolve_failed"
)
"""Resolved procedural settings failed validation; boot config kept."""
