"""Typed connection definitions and registry.

The registry maps ``ConnectionType`` enum values to their
``ConnectionAuthenticator`` implementations.
"""

from types import MappingProxyType

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.connections.protocol import (
    ConnectionAuthenticator,  # noqa: TC001
)
from synthorg.integrations.connections.types.a2a_peer import (
    A2APeerAuthenticator,
)
from synthorg.integrations.connections.types.database import (
    DatabaseAuthenticator,
)
from synthorg.integrations.connections.types.generic_http import (
    GenericHttpAuthenticator,
)
from synthorg.integrations.connections.types.gitea import (
    ForgejoAuthenticator,
    GiteaAuthenticator,
)
from synthorg.integrations.connections.types.github import GitHubAuthenticator
from synthorg.integrations.connections.types.gitlab import GitLabAuthenticator
from synthorg.integrations.connections.types.oauth_app import (
    OAuthAppAuthenticator,
)
from synthorg.integrations.connections.types.slack import SlackAuthenticator
from synthorg.integrations.connections.types.smtp import SmtpAuthenticator
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_AUTHENTICATOR_MISSING,
)

logger = get_logger(__name__)

CONNECTION_TYPE_REGISTRY: MappingProxyType[ConnectionType, ConnectionAuthenticator] = (
    MappingProxyType(
        {
            ConnectionType.GITHUB: GitHubAuthenticator(),
            ConnectionType.GITLAB: GitLabAuthenticator(),
            ConnectionType.GITEA: GiteaAuthenticator(),
            ConnectionType.FORGEJO: ForgejoAuthenticator(),
            ConnectionType.SLACK: SlackAuthenticator(),
            ConnectionType.SMTP: SmtpAuthenticator(),
            ConnectionType.DATABASE: DatabaseAuthenticator(),
            ConnectionType.GENERIC_HTTP: GenericHttpAuthenticator(),
            ConnectionType.OAUTH_APP: OAuthAppAuthenticator(),
            ConnectionType.A2A_PEER: A2APeerAuthenticator(),
        },
    )
)


def get_authenticator(
    connection_type: ConnectionType,
) -> ConnectionAuthenticator:
    """Look up the authenticator for a connection type.

    Args:
        connection_type: The connection type to look up.

    Returns:
        The authenticator instance.

    Raises:
        KeyError: If the connection type has no registered authenticator.
    """
    try:
        return CONNECTION_TYPE_REGISTRY[connection_type]
    except KeyError:
        logger.exception(
            CONNECTION_AUTHENTICATOR_MISSING,
            connection_type=(
                connection_type.value
                if isinstance(connection_type, ConnectionType)
                else str(connection_type)
            ),
        )
        raise
