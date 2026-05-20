"""Factory selecting an ExternalAccessProvider by config discriminator.

Keyed off the ``external_api.provider_type`` setting. Ships a single safe
default ('httpx'); future strategies register here.
"""

from typing import TYPE_CHECKING

from synthorg.tools.external_api.errors import ExternalApiError
from synthorg.tools.external_api.httpx_provider import HttpxExternalAccessProvider

if TYPE_CHECKING:
    from synthorg.tools.external_api.provider import ExternalAccessProvider

_PROVIDER_HTTPX = "httpx"


def build_external_access_provider(
    *,
    provider_type: str = _PROVIDER_HTTPX,
) -> ExternalAccessProvider:
    """Build the external-access provider for *provider_type*.

    Args:
        provider_type: Discriminator from the ``external_api.provider_type``
            setting. Only ``"httpx"`` is currently registered.

    Returns:
        A concrete :class:`ExternalAccessProvider`.

    Raises:
        ExternalApiError: If *provider_type* is not a registered strategy.
    """
    if provider_type == _PROVIDER_HTTPX:
        return HttpxExternalAccessProvider()
    msg = f"Unknown external-access provider type: {provider_type!r}"
    raise ExternalApiError(msg)
