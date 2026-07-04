# module-kind: code
"""Shared company-structure settings-write lock.

The company structure (agents / departments / teams) is a single JSON
settings blob, so every read-modify-write of it must serialise through one
process-wide lock, whether the writer is a REST setup / team controller or
the MCP ``TeamService``. It lives in the organization layer so both the
controller layer and the organization services can share the single
instance (a per-layer lock would leave cross-surface writes able to lose
updates). The setup controllers re-export it as ``AGENT_LOCK`` and keep
their documented ``COMPLETE_LOCK -> AGENT_LOCK`` acquisition order.
"""

import asyncio

ORG_SETTINGS_WRITE_LOCK = asyncio.Lock()

__all__ = ["ORG_SETTINGS_WRITE_LOCK"]
