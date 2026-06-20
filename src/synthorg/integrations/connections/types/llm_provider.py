"""LLM-provider connection type.

Backs an LLM provider's API-key credential, minted by the provider
management service when a provider is created with an embedded key. Unlike
``GENERIC_HTTP`` it does NOT require a ``base_url``: a provider that routes
through litellm's default endpoints has none of its own. The provider's
``base_url`` (when set) lives on the ``ProviderConfig``, not in the
credential connection.
"""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class LLMProviderAuthenticator:
    """Validates LLM-provider connection credentials.

    Required fields: ``api_key``.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.LLM_PROVIDER

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields.

        Raises:
            InvalidConnectionAuthError: If ``api_key`` is missing,
                non-string, or blank.
        """
        api_key = credentials.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.LLM_PROVIDER.value,
                field="api_key",
                error="missing, non-string, or blank",
            )
            msg = "LLM provider connection requires an 'api_key' field"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("api_key",)
