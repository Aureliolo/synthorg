"""Stable constraint tokens for user-table DB invariants (re-export).

The canonical definitions live in :mod:`synthorg.core.constraint_tokens`
so the API layer can translate ``ConstraintViolationError.constraint``
tokens without importing a persistence-internal module. This module
re-exports them so the SQLite / Postgres user repositories keep their
existing import surface.
"""

from synthorg.core.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)

__all__ = [
    "IDX_SINGLE_CEO",
    "LAST_CEO_TRIGGER",
    "LAST_OWNER_TRIGGER",
    "USERS_USERNAME_UNIQUE",
]
