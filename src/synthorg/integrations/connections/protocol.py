"""Protocols for the connection subsystem."""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.integrations.connections.models import ConnectionType  # noqa: TC001


# audit-keep 2026-05-10: central ConnectionType-to-impl registry in
# connections/types/__init__.py with per-type auth impls (GitHub, Slack, etc.).
@runtime_checkable
class ConnectionAuthenticator(Protocol):
    """Validates and enriches connection auth for a specific type.

    Each ``ConnectionType`` has an authenticator that knows which
    secret fields are required and how to validate them.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        ...

    def validate_credentials(
        self,
        credentials: dict[NotBlankStr, str],
    ) -> None:
        """Validate that required credential fields are present.

        Raises:
            InvalidConnectionAuthError: If validation fails.
        """
        ...

    def required_fields(self) -> tuple[NotBlankStr, ...]:
        """Return the credential field names required for this type."""
        ...
