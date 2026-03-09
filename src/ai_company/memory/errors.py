"""Memory error hierarchy.

All memory-related errors inherit from ``MemoryError`` so callers can
catch the entire family with a single except clause.

Note: this shadows the built-in ``MemoryError`` (which signals
out-of-memory conditions in CPython).  Within the ``ai_company``
namespace the domain-specific meaning is unambiguous; callers outside
the package should import explicitly.
"""


class MemoryError(Exception):  # noqa: A001
    """Base exception for all memory operations."""


class MemoryConnectionError(MemoryError):
    """Raised when a backend connection cannot be established or is lost."""


class MemoryStoreError(MemoryError):
    """Raised when a store operation fails."""


class MemoryRetrievalError(MemoryError):
    """Raised when a retrieve or search operation fails."""


class MemoryNotFoundError(MemoryError):
    """Raised when a specific memory ID is not found."""


class MemoryConfigError(MemoryError):
    """Raised when memory configuration is invalid."""


class MemoryCapabilityError(MemoryError):
    """Raised when an unsupported operation is attempted for a backend."""
