"""Coordination event name constants for observability."""

from typing import Final

COORDINATION_STARTED: Final[str] = "coordination.started"
COORDINATION_COMPLETED: Final[str] = "coordination.completed"
COORDINATION_FAILED: Final[str] = "coordination.failed"
COORDINATION_PHASE_STARTED: Final[str] = "coordination.phase.started"
COORDINATION_PHASE_COMPLETED: Final[str] = "coordination.phase.completed"
COORDINATION_PHASE_FAILED: Final[str] = "coordination.phase.failed"
COORDINATION_WAVE_STARTED: Final[str] = "coordination.wave.started"
COORDINATION_WAVE_COMPLETED: Final[str] = "coordination.wave.completed"
COORDINATION_TOPOLOGY_RESOLVED: Final[str] = "coordination.topology.resolved"
COORDINATION_CLEANUP_STARTED: Final[str] = "coordination.cleanup.started"
COORDINATION_CLEANUP_COMPLETED: Final[str] = "coordination.cleanup.completed"
COORDINATION_CLEANUP_FAILED: Final[str] = "coordination.cleanup.failed"
COORDINATION_WAVE_BUILT: Final[str] = "coordination.wave.built"
