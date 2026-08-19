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
COORDINATION_WAVE_AWAITING_HUMAN: Final[str] = "coordination.wave.awaiting_human"
COORDINATION_TOPOLOGY_RESOLVED: Final[str] = "coordination.topology.resolved"
COORDINATION_CLEANUP_STARTED: Final[str] = "coordination.cleanup.started"
COORDINATION_CLEANUP_COMPLETED: Final[str] = "coordination.cleanup.completed"
COORDINATION_CLEANUP_FAILED: Final[str] = "coordination.cleanup.failed"
COORDINATION_WAVE_BUILT: Final[str] = "coordination.wave.built"
COORDINATION_WAVE_DEPENDENCY_UNMET: Final[str] = "coordination.wave.dependency_unmet"
COORDINATION_WAVE_DEPENDENCY_AWAITED: Final[str] = (
    "coordination.wave.dependency_awaited"
)
COORDINATION_WAVES_ABANDONED: Final[str] = "coordination.waves.abandoned"
COORDINATION_WAVE_ASSIGNMENT_RELEASE_FAILED: Final[str] = (
    "coordination.wave.assignment_release_failed"
)
COORDINATION_RUN_CLAIM_REFUSED: Final[str] = "coordination.run.claim_refused"
COORDINATION_UNROUTABLE_PARKED: Final[str] = "coordination.unroutable.parked"
COORDINATION_UNROUTABLE_PARK_FAILED: Final[str] = "coordination.unroutable.park_failed"
COORDINATION_FACTORY_BUILT: Final[str] = "coordination.factory.built"
COORDINATION_ATTRIBUTION_BUILT: Final[str] = "coordination.attribution.built"
COORDINATION_OVERRIDE_APPLIED: Final[str] = "coordination.override.applied"
COORDINATION_ISOLATION_DEGRADED: Final[str] = "coordination.isolation.degraded"
