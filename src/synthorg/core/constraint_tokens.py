"""Stable constraint tokens for user-table DB invariants.

These tokens are returned by ``ConstraintViolationError.constraint`` and
are a cross-layer contract: the persistence backends (SQLite, Postgres)
emit them, and the API translates them into typed domain conflicts. They
are pure dependency-free string identifiers, so they live in ``core`` as
the single source of truth that both layers may import without crossing
the persistence boundary. ``persistence/constraint_tokens.py`` re-exports
them so the backend repositories keep their existing import surface.
"""

from typing import Final

USERS_USERNAME_UNIQUE: Final[str] = "users.username"
"""UNIQUE constraint on ``users.username``."""

IDX_SINGLE_CEO: Final[str] = "idx_single_ceo"
"""Partial unique index allowing at most one CEO."""

LAST_CEO_TRIGGER: Final[str] = "enforce_ceo_minimum"
"""Constraint trigger preventing removal of the last CEO."""

LAST_OWNER_TRIGGER: Final[str] = "enforce_owner_minimum"
"""Constraint trigger preventing removal of the last owner."""
