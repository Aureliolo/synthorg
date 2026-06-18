"""Pagination helpers for SQLite + Postgres repos (re-export from core).

The canonical implementations live in :mod:`synthorg.core.pagination` so
domain code can import them without reaching up into the persistence
boundary. This module re-exports them unchanged so backend repositories
keep their existing ``from synthorg.persistence._shared import ...``
import surface.
"""

from synthorg.core.pagination import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    collect_all,
    collect_all_mapping,
    paginate,
    validate_pagination_args,
)

__all__ = (
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "collect_all",
    "collect_all_mapping",
    "paginate",
    "validate_pagination_args",
)
