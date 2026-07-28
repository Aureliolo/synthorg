"""Protocols for the connection subsystem."""

from typing import Final, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import ConnectionType

#: Key under which the create path exposes the declared auth method to an
#: authenticator. It travels in the validation view rather than the
#: signature so a type that cares can hold credentials to the shape the
#: method promises without every other type restating a parameter it
#: ignores. The dunder spelling cannot collide with a credential field,
#: whose names are slugs, and the view is never persisted.
AUTH_METHOD_VIEW_KEY: Final[str] = "__auth_method__"


# Central ConnectionType-to-impl registry in connections/types/__init__.py
# with per-type auth implementations.
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
