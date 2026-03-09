"""Persistence error hierarchy.

All persistence-related errors inherit from ``PersistenceError`` so
callers can catch the entire family with a single except clause.
"""


class PersistenceError(Exception):
    """Base exception for all persistence operations."""


class PersistenceConnectionError(PersistenceError):
    """Raised when a backend connection cannot be established or is lost."""


class MigrationError(PersistenceError):
    """Raised when a database migration fails."""


class RecordNotFoundError(PersistenceError):
    """Raised when a requested record does not exist.

    Currently unused — ``TaskRepository.get()`` returns ``None``
    on miss, and other repositories use collection-returning queries.
    Reserved for future strict-fetch methods (e.g. ``get_or_raise``).
    """


class DuplicateRecordError(PersistenceError):
    """Raised when inserting a record that already exists."""


class QueryError(PersistenceError):
    """Raised when a query fails due to invalid parameters or backend issues."""
