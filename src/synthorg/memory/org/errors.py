"""Org memory error hierarchy.

All org-memory-related errors inherit from ``OrgMemoryError`` so callers
can catch the entire family with a single except clause.
"""


class OrgMemoryError(Exception):
    """Base exception for all org memory operations."""


class OrgMemoryConnectionError(OrgMemoryError):
    """Raised when the org memory backend connection fails."""


class OrgMemoryWriteError(OrgMemoryError):
    """Raised when a write operation to org memory fails."""


class OrgMemoryQueryError(OrgMemoryError):
    """Raised when a query operation on org memory fails."""


class OrgMemoryAccessDeniedError(OrgMemoryError):
    """Raised when write access control denies an operation."""


class OrgMemoryConfigError(OrgMemoryError):
    """Raised when org memory configuration is invalid."""
