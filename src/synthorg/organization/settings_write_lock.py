# module-kind: code
"""Shared company-structure settings-write lock.

The company structure (agents / departments / teams) is a single JSON
settings blob written by several surfaces (REST setup / team controllers,
template-pack apply, the MCP ``TeamService``). This ``asyncio.Lock``
serialises those writers *within one process*, so it lives in the
organization layer for both the controller layer and the organization
services to share the one instance. It is only an in-process optimisation:
an ``asyncio.Lock`` gives no protection across the multiple worker
processes ``api/server.py`` supports (``workers>1``). Cross-process
lost-update safety rests entirely on the per-key compare-and-set token
(``expected_updated_at``) that
:mod:`synthorg.organization.team_navigation` threads through every
``company.departments`` write, so all writers cooperate on the one CAS
token rather than clobbering. The setup controllers re-export this lock as
``AGENT_LOCK`` and keep their documented ``COMPLETE_LOCK -> AGENT_LOCK``
acquisition order.
"""

import asyncio

ORG_SETTINGS_WRITE_LOCK = asyncio.Lock()

__all__ = ["ORG_SETTINGS_WRITE_LOCK"]
