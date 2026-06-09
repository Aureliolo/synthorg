"""psycopg type adapter registration (placeholder).

psycopg 3 ships built-in adapters for the types the Postgres backend
relies on out of the box:

- ``dict`` / ``list`` <-> ``JSONB`` (via the ``psycopg.types.json``
  module, auto-registered)
- ``datetime`` <-> ``TIMESTAMPTZ`` (timezone-aware datetimes preserve
  their offset)
- ``bool`` <-> ``BOOLEAN``
- ``int`` <-> ``BIGINT`` / ``INTEGER``
- ``float`` <-> ``DOUBLE PRECISION``
- ``UUID`` <-> ``UUID``

No custom adapters are required for the current schema.  This module
exists as a deliberate home for future adapters (e.g. pgvector,
custom enum types, or domain-specific serialization) so repositories
stay small.
"""

import psycopg

from synthorg.observability import get_logger

logger = get_logger(__name__)


async def register_adapters(conn: psycopg.AsyncConnection[object]) -> None:
    """Register any custom psycopg adapters on *conn*.

    No-op on the initial port.  Future work (pgvector, TimescaleDB
    hypertable metadata, etc.) hooks here.
    """
    del conn  # intentionally unused until custom adapters land


__all__ = ["register_adapters"]
