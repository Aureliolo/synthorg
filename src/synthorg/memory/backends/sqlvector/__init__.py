"""SQL-backed memory backend (pgvector on Postgres, sqlite-vec on SQLite)."""

from synthorg.memory.backends.sqlvector.adapter import SqlVectorBackend

__all__ = ["SqlVectorBackend"]
