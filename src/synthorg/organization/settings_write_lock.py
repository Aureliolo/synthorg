# module-kind: code
"""Shared company-structure settings-write lock.

The company structure (agents / departments / teams) is a single JSON
settings blob. This ``asyncio.Lock`` serialises the REST setup / team
controllers, template-pack apply, and the MCP ``TeamService`` *within one
process*, so it lives in the organization layer for both the controller
layer and the organization services to share the one instance. It is only
an in-process optimisation for those callers: an ``asyncio.Lock`` gives no
protection across the multiple worker processes ``api/server.py`` supports
(``workers>1``), and REST department CRUD
(:class:`~synthorg.api.services.org_mutations.OrgMutationService`) writes
the same blob without acquiring this lock at all. Cross-process
lost-update safety therefore rests entirely on the per-key compare-and-set
token (``expected_updated_at``) that *every* writer passes to the settings
layer, not on this lock, so all writers cooperate on the one CAS token
rather than clobbering. The setup controllers re-export this lock as
``AGENT_LOCK`` and keep their documented ``COMPLETE_LOCK -> AGENT_LOCK``
acquisition order.
"""

import asyncio

ORG_SETTINGS_WRITE_LOCK = asyncio.Lock()

__all__ = ["ORG_SETTINGS_WRITE_LOCK"]
