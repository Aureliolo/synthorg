"""Observability event constants for brownfield codebase intake.

Naming follows the existing ``workspace`` and ``knowledge`` modules:
``<domain>.<subject>.<verb>``.
"""

from typing import Final

BROWNFIELD_IMPORT_STARTED: Final[str] = "brownfield.import.started"
"""Import began for a project (workspace provisioning about to run)."""

BROWNFIELD_IMPORT_REJECTED: Final[str] = "brownfield.import.rejected"
"""Import refused: target workspace already holds a different codebase."""

BROWNFIELD_WORKSPACE_SEEDED: Final[str] = "brownfield.workspace.seeded"
"""Source repository cloned/copied into the project workspace."""

BROWNFIELD_STRUCTURE_SCANNED: Final[str] = "brownfield.structure.scanned"
"""Deterministic scan produced a codebase structure map."""

BROWNFIELD_STRUCTURE_QUERY_FAILED: Final[str] = "brownfield.structure.query_failed"
"""Agent structure-map query failed against the repository."""

BROWNFIELD_STRUCTURE_UNCHANGED: Final[str] = "brownfield.structure.unchanged"
"""Re-import of the same source: scan content hash matched; short-circuited."""

BROWNFIELD_CODEBASE_INDEXED: Final[str] = "brownfield.codebase.indexed"
"""Imported codebase ingested into the hybrid-retrieval knowledge store."""

BROWNFIELD_IMPORT_COMPLETED: Final[str] = "brownfield.import.completed"
"""Import finished; the analysis pass can now run through the spine."""

BROWNFIELD_ENTRY_WIRED: Final[str] = "brownfield.entry.wired"
"""Boot wiring attached (or skipped) the brownfield work-entry adapter."""

BROWNFIELD_PIPELINE_FAILED: Final[str] = "brownfield.pipeline.failed"
"""Background import + analysis pipeline run raised after the 202 ack."""
