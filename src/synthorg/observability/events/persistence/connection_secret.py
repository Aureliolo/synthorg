# module-kind: declarative
"""Persistence event constants for the connection_secret sub-domain.

Encrypted blob storage for ``SecretBackend``. ``noqa: S105`` on each literal:
the ``_SECRET_`` token in the constant *name* is an observability domain, not a
hardcoded credential value. The S105 check fires on the assigned string literal
because the identifier matches a secret-like pattern; suppressed once per literal.
"""

from typing import Final

PERSISTENCE_CONNECTION_SECRET_STORED: Final[str] = (
    "persistence.connection_secret.stored"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_STORE_FAILED: Final[str] = (
    "persistence.connection_secret.store_failed"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_RETRIEVED: Final[str] = (
    "persistence.connection_secret.retrieved"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_RETRIEVE_FAILED: Final[str] = (
    "persistence.connection_secret.retrieve_failed"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_DELETED: Final[str] = (
    "persistence.connection_secret.deleted"  # noqa: S105
)
PERSISTENCE_CONNECTION_SECRET_DELETE_FAILED: Final[str] = (
    "persistence.connection_secret.delete_failed"  # noqa: S105
)
