"""Delegation event constants."""

from typing import Final

# Delegation lifecycle
DELEGATION_REQUESTED: Final[str] = "delegation.requested"
DELEGATION_AUTHORIZED: Final[str] = "delegation.authorized"
DELEGATION_AUTHORITY_DENIED: Final[str] = "delegation.authority_denied"
DELEGATION_CREATED: Final[str] = "delegation.created"
DELEGATION_RESULT_SENT: Final[str] = "delegation.result_sent"
DELEGATION_SUB_TASK_FAILED: Final[str] = "delegation.sub_task.failed"

# Loop prevention
DELEGATION_LOOP_BLOCKED: Final[str] = "delegation.loop.blocked"
DELEGATION_LOOP_DEPTH_EXCEEDED: Final[str] = "delegation.loop.depth_exceeded"
DELEGATION_LOOP_ANCESTRY_BLOCKED: Final[str] = "delegation.loop.ancestry_blocked"
DELEGATION_LOOP_DEDUP_BLOCKED: Final[str] = "delegation.loop.dedup_blocked"
DELEGATION_LOOP_RATE_LIMITED: Final[str] = "delegation.loop.rate_limited"
DELEGATION_LOOP_CIRCUIT_OPEN: Final[str] = "delegation.loop.circuit_open"
DELEGATION_LOOP_CIRCUIT_RESET: Final[str] = "delegation.loop.circuit_reset"
DELEGATION_LOOP_ESCALATED: Final[str] = "delegation.loop.escalated"

# Hierarchy
DELEGATION_HIERARCHY_BUILT: Final[str] = "delegation.hierarchy.built"
DELEGATION_HIERARCHY_CYCLE: Final[str] = "delegation.hierarchy.cycle"
