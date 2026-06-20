# module-kind: code
"""Authentication keyword-argument assembly for the LiteLLM driver.

Isolates the mapping from a provider's ``AuthType`` plus the
catalog-resolved credentials onto litellm's ``acompletion`` auth
parameters (``api_key`` / ``extra_headers``). Kept out of
``litellm_driver`` so dispatch and response mapping stay focused, and so
the fail-closed credential policy lives in one auditable place.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn

from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_AUTH_ERROR
from synthorg.providers import errors
from synthorg.providers.drivers.litellm_kwargs import _AcompletionKwargs
from synthorg.providers.enums import AuthType

logger = get_logger(__name__)


@dataclass(frozen=True)
class AuthContext:
    """Inputs the driver supplies to resolve auth onto a request.

    Attributes:
        config: The provider config carrying the auth type and any
            embedded (catalog-less) credential fields.
        resolved: Catalog-resolved credentials, or ``None`` when no
            catalog resolution ran.
        catalog_present: Whether a credential catalog is wired (drives
            the fail-closed branch when a wired catalog did not resolve).
        provider_name: Provider name, for error context and logging.
        litellm_model: The litellm model id, for error logging.
    """

    config: ProviderConfig
    resolved: Mapping[str, str] | None
    catalog_present: bool
    provider_name: str
    litellm_model: str


def _raise_unresolved_credential(
    provider_name: str, litellm_model: str, kind: str
) -> NoReturn:
    """Fail closed when a wired catalog did not resolve the credential.

    Raises:
        AuthenticationError: Always.
    """
    logger.error(PROVIDER_AUTH_ERROR, provider=provider_name, model=litellm_model)
    msg = f"{kind} credentials were not resolved from the catalog"
    raise errors.AuthenticationError(msg, context={"provider": provider_name})


def apply_auth_kwargs(kwargs: _AcompletionKwargs, ctx: AuthContext) -> None:
    """Merge auth credentials onto ``kwargs`` per the provider auth type.

    Catalog-resolved credentials win. A wired-but-unresolved catalog
    fails closed (an unauthenticated request would leak the prompt),
    while the catalog-less degraded/test path omits the credential.
    """
    config = ctx.config
    resolved = ctx.resolved
    match config.auth_type:
        case AuthType.API_KEY:
            # Catalog-only key. Fail closed when a catalog is wired but did
            # not resolve it; with no catalog at all (degraded / test) omit.
            key = resolved.get("api_key") if resolved else None
            if key is not None:
                kwargs["api_key"] = key
            elif ctx.catalog_present:
                _raise_unresolved_credential(
                    ctx.provider_name, ctx.litellm_model, "API-key"
                )
        case AuthType.OAUTH:
            # Catalog-backed OAuth: bearer under ``access_token`` (or the
            # fallback ``api_key`` key). No embedded credential, so fail
            # closed when a catalog is wired but neither resolves.
            key = None
            if resolved:
                key = resolved.get("access_token") or resolved.get("api_key")
            if key is not None:
                kwargs["api_key"] = key
            elif ctx.catalog_present:
                _raise_unresolved_credential(
                    ctx.provider_name, ctx.litellm_model, "OAuth"
                )
        case AuthType.CUSTOM_HEADER:
            # Prefer catalog-resolved credentials so a ``connection_name``
            # provider can ship the header without duplicating it in
            # config. Fall back to the embedded fields for the
            # catalog-less path.
            header_name = resolved.get("custom_header_name") if resolved else None
            if header_name is None:
                header_name = config.custom_header_name
            header_value = resolved.get("custom_header_value") if resolved else None
            if header_value is None:
                header_value = config.custom_header_value
            if header_name and header_value:
                kwargs["extra_headers"] = {header_name: header_value}
        case AuthType.SUBSCRIPTION:
            # Pass as api_key -- the correct kwarg for LiteLLM
            # authentication.  Do NOT use "auth_token" -- it is
            # not a litellm.completion() parameter and is silently
            # discarded.
            token = resolved.get("subscription_token") if resolved else None
            if token is None:
                token = config.subscription_token
            if token is not None:
                kwargs["api_key"] = token
        case AuthType.NONE:
            pass


__all__ = ["AuthContext", "apply_auth_kwargs"]
