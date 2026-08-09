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
    provider_name: str, litellm_model: str, kind: str, detail: str
) -> NoReturn:
    """Fail closed when the credential this auth type requires is absent.

    Raises:
        AuthenticationError: Always.
    """
    logger.error(PROVIDER_AUTH_ERROR, provider=provider_name, model=litellm_model)
    msg = f"{kind} credentials {detail}"
    raise errors.AuthenticationError(msg, context={"provider": provider_name})


@dataclass(frozen=True)
class AuthMaterial:
    """The credential a provider's auth type resolves to, transport-agnostic.

    Attributes:
        api_key: Credential litellm sends as ``api_key``, when there is one.
        extra_headers: Custom auth headers, when the auth type uses them.
    """

    api_key: str | None = None
    extra_headers: Mapping[str, str] | None = None


def _catalog_bound(ctx: AuthContext) -> bool:
    """Whether the catalog is the sole source of this provider's credential.

    A provider naming a connection has had its credential moved into the
    catalog, so the embedded config fields are whatever was there before
    that move. Reading them once the catalog is wired resurrects a
    credential the operator has since rotated or revoked.

    Returns:
        True when the embedded config fields must not be consulted.
    """
    return ctx.catalog_present and ctx.config.connection_name is not None


def _resolve_bearer(ctx: AuthContext) -> str | None:
    """Resolve the catalog-only bearer key for ``API_KEY``.

    Returns:
        The key, or ``None`` when the catalog did not supply one.
    """
    return ctx.resolved.get("api_key") if ctx.resolved else None


def _resolve_oauth(ctx: AuthContext) -> str | None:
    """Resolve the OAuth bearer, which has no embedded fallback.

    Returns:
        The token under ``access_token`` (or the ``api_key`` fallback),
        or ``None`` when the catalog supplied neither.
    """
    if not ctx.resolved:
        return None
    return ctx.resolved.get("access_token") or ctx.resolved.get("api_key")


def _resolve_custom_header(ctx: AuthContext) -> Mapping[str, str] | None:
    """Resolve the custom auth header, preferring the catalog.

    Returns:
        The single-entry header mapping, or ``None`` when either half is
        missing from every source this provider may read.
    """
    resolved = ctx.resolved
    name = resolved.get("custom_header_name") if resolved else None
    value = resolved.get("custom_header_value") if resolved else None
    if not _catalog_bound(ctx):
        name = name or ctx.config.custom_header_name
        value = value or ctx.config.custom_header_value
    if name and value:
        return {name: value}
    return None


def _unresolved(ctx: AuthContext, kind: str) -> AuthMaterial:
    """Answer for an auth type whose credential nothing supplied.

    Two shapes fail closed, because sending the request on unauthenticated
    would leak the prompt to an endpoint that never accepted it.

    A wired catalog owed this credential, so not having it is a failure.

    A provider config naming a ``connection_name`` has declared that its
    credential lives in the catalog, so reaching dispatch with no catalog
    bound is a wiring failure, not a licence to omit: a registry swapped in
    without the catalog produced exactly that, and the request went out with
    no key and came back with the gateway's own unrelated complaint about a
    missing environment variable.

    Only a config naming no connection has nowhere to have resolved from, and
    for it omitting the credential is the honest answer.

    Args:
        ctx: The resolution context, read for the catalog and the names
            the error reports.
        kind: What the missing credential is called, for the error.

    Returns:
        Empty material, for a config naming no connection and no catalog.

    Raises:
        AuthenticationError: If a catalog is wired, or the config names a
            connection whose catalog is not bound.
    """
    if ctx.catalog_present:
        _raise_unresolved_credential(
            ctx.provider_name,
            ctx.litellm_model,
            kind,
            "were not resolved from the catalog",
        )
    if ctx.config.connection_name is not None:
        _raise_unresolved_credential(
            ctx.provider_name,
            ctx.litellm_model,
            kind,
            (
                f"live in connection {ctx.config.connection_name!r} but no "
                "credential catalog is bound to this driver"
            ),
        )
    return AuthMaterial()


def _resolve_subscription(ctx: AuthContext) -> str | None:
    """Resolve the subscription token, preferring the catalog.

    Returns:
        The token, or ``None`` when no readable source has one.
    """
    token = ctx.resolved.get("subscription_token") if ctx.resolved else None
    if token is None and not _catalog_bound(ctx):
        token = ctx.config.subscription_token
    return token


def resolve_auth_material(ctx: AuthContext) -> AuthMaterial:
    """Resolve a provider's credential per its auth type.

    Catalog-resolved credentials win. A wired-but-unresolved catalog fails
    closed, as does a config naming a connection with no catalog bound (an
    unauthenticated request would leak the prompt); only a config naming no
    connection omits the credential.

    Shared by completion and embedding dispatch so one auth type cannot
    mean two different things depending on which call is being made.

    Returns:
        The credential material to put on the outbound request.

    Raises:
        AuthenticationError: If the credential the auth type requires was
            not resolved and the config expected it to be.
    """
    # Every arm returns, so the match has to stay exhaustive over
    # AuthType: a new member added without an arm here falls off the end
    # and mypy reports the missing return. The alternative, a shared
    # fall-through, would let that member reach the wire with no
    # credential and no complaint.
    match ctx.config.auth_type:
        case AuthType.API_KEY:
            if (key := _resolve_bearer(ctx)) is not None:
                return AuthMaterial(api_key=key)
            return _unresolved(ctx, "API-key")
        case AuthType.OAUTH:
            if (key := _resolve_oauth(ctx)) is not None:
                return AuthMaterial(api_key=key)
            return _unresolved(ctx, "OAuth")
        case AuthType.CUSTOM_HEADER:
            if (headers := _resolve_custom_header(ctx)) is not None:
                return AuthMaterial(extra_headers=headers)
            return _unresolved(ctx, "Custom-header")
        case AuthType.SUBSCRIPTION:
            # Passed as api_key -- the correct kwarg for LiteLLM
            # authentication. Do NOT use "auth_token"; it is not a
            # litellm.completion() parameter and is silently discarded.
            if (token := _resolve_subscription(ctx)) is not None:
                return AuthMaterial(api_key=token)
            return _unresolved(ctx, "Subscription")
        case AuthType.NONE:
            return AuthMaterial()


def apply_auth_kwargs(kwargs: _AcompletionKwargs, ctx: AuthContext) -> None:
    """Merge auth credentials onto ``kwargs`` per the provider auth type.

    Raises:
        AuthenticationError: Propagated from :func:`resolve_auth_material`
            when the credential the auth type requires was not resolved and
            the config expected it to be; sending the request unauthenticated
            would leak the prompt to an endpoint that never accepted it.
    """
    material = resolve_auth_material(ctx)
    if material.api_key is not None:
        kwargs["api_key"] = material.api_key
    if material.extra_headers is not None:
        kwargs["extra_headers"] = dict(material.extra_headers)


__all__ = ["AuthContext", "AuthMaterial", "apply_auth_kwargs", "resolve_auth_material"]
