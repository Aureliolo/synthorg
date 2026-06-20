"""Idempotency service: claim/complete/fail wrapper with response caching.

Lives in a neutral top-level package (not ``api.services``) so that the
API controllers, the A2A gateway, and the MCP handler layer can all wrap
mutating operations in :meth:`IdempotencyService.run_idempotent` without
the ``meta``/``integrations`` layers reaching upward into ``api``.
"""

from synthorg.idempotency.service import IdempotencyResult, IdempotencyService

__all__ = ["IdempotencyResult", "IdempotencyService"]
