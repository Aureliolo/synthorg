# module-kind: service
"""Catalog-backed provider-credential helpers.

Extracted from ``ProviderManagementService`` so the catalog-only
credential concern (mint / rotate / clear / resolve an API-key
connection) lives in one focused module. Every function takes the
``AppState`` it resolves the credential catalog from; the service
threads ``self._app_state`` through. Provider API-key credentials are
catalog-only: a secret supplied at the boundary is minted into a
``ConnectionCatalog`` entry and referenced by ``connection_name``.
"""

import contextlib

from synthorg.api.state import AppState
from synthorg.config.schema import ProviderConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
)
from synthorg.integrations.state import provider_credential_catalog_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_VALIDATION_FAILED
from synthorg.providers._auth_type_descriptor import AUTH_TYPE_DESCRIPTORS
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.management._helpers import apply_update
from synthorg.providers.management.dtos import UpdateProviderRequest

logger = get_logger(__name__)


def credential_connection_name(provider_name: str) -> str:
    """Catalog connection name backing a provider's API-key credential.

    Returns:
        The deterministic ``provider-<name>`` connection name.
    """
    return f"provider-{provider_name}"


async def store_provider_api_key(
    app_state: AppState, provider_name: str, api_key: str
) -> str:
    """Mint (or replace) the catalog connection holding the provider key.

    The ConnectionCatalog has no secret-update seam, so this deletes any
    existing connection and recreates it: idempotent, and it doubles as
    rotation. Runs inside the service lock held by the caller.

    Returns:
        The connection name to stamp onto ``ProviderConfig.connection_name``.

    Raises:
        ProviderValidationError: When no credential catalog is wired (a
            connected persistence backend is required for catalog-backed
            provider credentials).
    """
    catalog = provider_credential_catalog_of(app_state)
    if catalog is None:
        msg = (
            "Cannot store the provider API key: no credential catalog is "
            "available. A connected persistence backend is required for "
            "catalog-backed provider credentials."
        )
        logger.warning(PROVIDER_VALIDATION_FAILED, provider=provider_name, error=msg)
        raise ProviderValidationError(msg)
    conn_name = credential_connection_name(provider_name)
    with contextlib.suppress(ConnectionNotFoundError):
        await catalog.delete(conn_name)
    # No suppress on create: the delete above already cleared any existing
    # entry (ConnectionNotFoundError when absent is benign). A
    # DuplicateConnectionError here means the delete did not take effect, so
    # the new key was NOT stored -- swallowing it would return a connection
    # name pointing at the un-rotated secret. Let it propagate.
    await catalog.create(
        name=conn_name,
        connection_type=ConnectionType.LLM_PROVIDER,
        auth_method=AuthMethod.API_KEY.value,
        credentials={"api_key": api_key},
    )
    return conn_name


async def delete_provider_credential(app_state: AppState, provider_name: str) -> None:
    """Best-effort removal of a provider's backing credential connection.

    A missing connection is benign (already absent). Any other backend
    failure is logged with a redacted description rather than swallowed,
    so an orphaned secret left behind on delete is at least observable.
    """
    catalog = provider_credential_catalog_of(app_state)
    if catalog is None:
        return
    try:
        await catalog.delete(credential_connection_name(provider_name))
    except ConnectionNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
        reraise_critical(exc)
        logger.warning(
            PROVIDER_VALIDATION_FAILED,
            provider=provider_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def resolve_provider_api_key(
    app_state: AppState, config: ProviderConfig
) -> str | None:
    """Resolve a provider's api_key from its catalog connection.

    Returns ``None`` when the config carries no ``connection_name`` or no
    credential catalog is wired (the caller then proceeds without an
    Authorization header). Both ``None`` paths log a warning: a config
    that references a connection but resolves to no key signals a boot
    ordering race (catalog not yet wired) or a misconfigured connection,
    both of which otherwise surface only as opaque downstream auth 401s.

    Returns:
        The resolved api_key, or ``None``.
    """
    if config.connection_name is None:
        return None
    catalog = provider_credential_catalog_of(app_state)
    if catalog is None:
        logger.warning(
            PROVIDER_VALIDATION_FAILED,
            connection_name=config.connection_name,
            error="credential catalog not wired; proceeding without auth header",
        )
        return None
    creds = await catalog.get_credentials(config.connection_name)
    api_key = creds.get("api_key")
    if api_key is None:
        logger.warning(
            PROVIDER_VALIDATION_FAILED,
            connection_name=config.connection_name,
            error="catalog connection carries no api_key credential",
        )
    return api_key


async def apply_update_with_credential(
    app_state: AppState,
    name: str,
    existing: ProviderConfig,
    request: UpdateProviderRequest,
) -> ProviderConfig:
    """Merge an update, minting/clearing the backing credential connection.

    API-key credentials are catalog-only: a secret supplied at the
    boundary is minted into the connection catalog and referenced by
    ``connection_name``; a clear request (or a switch to an auth type
    that has no api-key credential) deletes the backing connection.
    The resolved reference is threaded into ``apply_update`` so the
    merged config validates with a complete credential.

    Returns:
        The merged ``ProviderConfig`` with ``connection_name`` reflecting
        the catalog mutation.

    Raises:
        ProviderValidationError: When clearing the API key of an
            API_KEY provider (which would leave it unable to
            authenticate); the operator must switch auth_type or delete.
    """
    final_auth_type = (
        request.auth_type if request.auth_type is not None else existing.auth_type
    )
    descriptor = AUTH_TYPE_DESCRIPTORS[final_auth_type]
    if descriptor.supports_api_key:
        if request.api_key is not None:
            conn_name = await store_provider_api_key(
                app_state, name, request.api_key.get_secret_value()
            )
            return apply_update(existing, request, connection_name=conn_name)
        if request.clear_api_key:
            if final_auth_type is AuthType.API_KEY:
                # API_KEY auth has no other credential source, so clearing
                # it would leave the provider unable to authenticate.
                # Force the operator to switch auth_type or delete instead.
                msg = (
                    "Cannot clear the API key of an API_KEY provider; "
                    "switch auth_type or delete the provider instead."
                )
                logger.warning(PROVIDER_VALIDATION_FAILED, provider=name, error=msg)
                raise ProviderValidationError(msg)
            await delete_provider_credential(app_state, name)
            return apply_update(existing, request, connection_name=None)
        return apply_update(existing, request)
    # The new auth type does not use an api-key connection; drop any
    # backing credential the provider previously referenced.
    if existing.connection_name is not None:
        await delete_provider_credential(app_state, name)
    return apply_update(existing, request, connection_name=None)


__all__ = [
    "apply_update_with_credential",
    "credential_connection_name",
    "delete_provider_credential",
    "resolve_provider_api_key",
    "store_provider_api_key",
]
