"""Backend-agnostic database-row protocol for repository marshallers.

Both ``aiosqlite.Row`` and psycopg ``dict_row`` mappings support
string-key indexing, so a single ``RowLike``-typed marshaller serves the
SQLite and Postgres repositories alike. The timestamp coercer in
:mod:`synthorg.persistence._shared.datetime_marshaller` normalises the
``TEXT`` / ``TIMESTAMPTZ`` divergence, so marshallers stay driver-agnostic.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class RowLike(Protocol):
    """A database row supporting string-key access (dict / sqlite Row)."""

    # ``key`` is positional-only: the real rows this abstracts
    # (``dict`` and ``sqlite3.Row``) both expose a positional-only
    # ``__getitem__``, so a named parameter here would make neither a
    # structural match under runtime protocol checking.
    def __getitem__(self, key: str, /) -> object: ...


__all__ = ["RowLike"]
