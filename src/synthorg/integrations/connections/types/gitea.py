"""Gitea / Forgejo connection types.

Forgejo is a hard-fork of Gitea that today retains Gitea's API and
token-auth model.  They are modelled as SEPARATE connection identities
(separate saved-connection rows + icons) so a future API divergence
never invalidates a user's existing saved connection, but they share
the credential-validation code via :class:`_GiteaFamilyAuthenticator`.
When the APIs actually diverge, split the subclass bodies; the
connection identities and saved rows stay unchanged.
"""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class _GiteaFamilyAuthenticator:
    """Shared token-auth validation for the Gitea/Forgejo family.

    Subclasses set :attr:`connection_type`; the validation surface is
    identical while the two forges remain API/token compatible.

    Required fields: ``token`` (personal access token).
    Optional fields: ``api_url`` (self-hosted base URL).
    """

    @property
    def connection_type(self) -> ConnectionType:  # pragma: no cover - overridden
        raise NotImplementedError

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields."""
        token = credentials.get("token")
        if not isinstance(token, str) or not token.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=self.connection_type.value,
                field="token",
                error="missing, non-string, or blank",
            )
            msg = (
                f"{self.connection_type.value.capitalize()} connection "
                "requires a 'token' field"
            )
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("token",)


class GiteaAuthenticator(_GiteaFamilyAuthenticator):
    """Validates Gitea connection credentials."""

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.GITEA


class ForgejoAuthenticator(_GiteaFamilyAuthenticator):
    """Validates Forgejo connection credentials."""

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.FORGEJO
