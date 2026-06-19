"""Signature verifier factory."""

from synthorg.a2a.push_verifier import A2APushVerifier
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.integrations.webhooks.verifiers.forge_verifiers import (
    ForgejoHmacVerifier,
    GiteaHmacVerifier,
    GitLabTokenVerifier,
)
from synthorg.integrations.webhooks.verifiers.generic_hmac import (
    GenericHmacVerifier,
)
from synthorg.integrations.webhooks.verifiers.github_hmac import (
    GitHubHmacVerifier,
)
from synthorg.integrations.webhooks.verifiers.protocol import (
    SignatureVerifier,
)
from synthorg.integrations.webhooks.verifiers.slack_signing import (
    SlackSigningVerifier,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    WEBHOOK_VERIFIER_UNSUPPORTED_TYPE,
)

logger = get_logger(__name__)

_VERIFIER_FACTORIES: dict[ConnectionType, type[SignatureVerifier]] = {
    ConnectionType.GITHUB: GitHubHmacVerifier,
    ConnectionType.GITLAB: GitLabTokenVerifier,
    ConnectionType.GITEA: GiteaHmacVerifier,
    ConnectionType.FORGEJO: ForgejoHmacVerifier,
    ConnectionType.SLACK: SlackSigningVerifier,
    ConnectionType.GENERIC_HTTP: GenericHmacVerifier,
    ConnectionType.A2A_PEER: A2APushVerifier,
}


def get_verifier(connection_type: ConnectionType) -> SignatureVerifier:
    """Return the appropriate verifier for a connection type.

    Fails closed for unsupported types: silently defaulting to a
    generic HMAC verifier would apply the wrong signature scheme
    and weaken webhook authenticity guarantees.

    Args:
        connection_type: The connection type.

    Returns:
        A ``SignatureVerifier`` instance.

    Raises:
        InvalidConnectionAuthError: If no verifier is registered
            for ``connection_type``.
    """
    verifier_cls = _VERIFIER_FACTORIES.get(connection_type)
    if verifier_cls is None:
        logger.error(
            WEBHOOK_VERIFIER_UNSUPPORTED_TYPE,
            connection_type=connection_type.value,
        )
        msg = (
            "No webhook signature verifier registered for "
            f"connection_type={connection_type.value}"
        )
        raise InvalidConnectionAuthError(msg)
    return verifier_cls()
