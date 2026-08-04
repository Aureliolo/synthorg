# module-kind: service
"""Mixin for the two provider-write transactions.

Create and update are the only provider mutations that touch the
credential catalog, the persisted config and the discovery allowlist in
one operation, which is what makes them transactions rather than writes:
each has to unwind whatever earlier phase already landed when a later one
fails. That unwinding is most of their body, and it is the part that has
to stay correct, so it lives here rather than crowding the CRUD core.

The mixin reads ``self._app_state`` / ``self._allowlist`` and the helper
methods the host service provides; ``_ServiceProtocol`` narrows that
contract for mypy strict without importing the concrete service, which
would close an import cycle back through ``service.py``.
"""

from collections.abc import Mapping
from typing import Protocol

from synthorg.api.state import AppState
from synthorg.config.schema import ProviderConfig
from synthorg.providers._auth_type_descriptor import AUTH_TYPE_DESCRIPTORS
from synthorg.providers.management._config_transforms import (
    build_provider_config,
)
from synthorg.providers.management._credential_helpers import (
    apply_update_with_credential,
    delete_provider_credential,
    resolve_provider_api_key,
    rollback_credential,
    store_provider_api_key,
    unwind,
)
from synthorg.providers.management.allowlist import DiscoveryAllowlistManager
from synthorg.providers.management.dtos import (
    CreateProviderRequest,
    UpdateProviderRequest,
)


class _ServiceProtocol(Protocol):
    """Subset of ``ProviderManagementService`` this mixin reaches for."""

    _app_state: AppState
    _allowlist: DiscoveryAllowlistManager

    async def _validate_and_persist(
        self, new_providers: dict[str, ProviderConfig]
    ) -> None:
        """Validate + persist + hot-reload providers (provided by host)."""
        ...

    async def _restore_providers(self, snapshot: Mapping[str, ProviderConfig]) -> bool:
        """Put the pre-mutation snapshot back (provided by host)."""
        ...


class ProviderTransactionMixin:
    """The create and update write transactions, with their rollbacks.

    Composed into ``ProviderManagementService`` via plain Python MRO.
    """

    async def _persist_new_provider(
        self: _ServiceProtocol,
        request: CreateProviderRequest,
        providers: Mapping[str, ProviderConfig],
    ) -> ProviderConfig:
        """Mint the credential, persist the config, and unwind on any failure.

        Catalog-only credentials: an api_key supplied at the boundary is
        minted into a ConnectionCatalog connection FIRST, then threaded into
        the config as connection_name -- API_KEY auth mandates it, so the
        config could not validate with the secret embedded or absent. The
        secret is never persisted on the ProviderConfig.

        Args:
            request: The create request carrying the optional api_key.
            providers: Pre-create snapshot, restored if persistence succeeds
                and a later step then fails.

        Returns:
            The persisted config.

        Raises:
            Exception: Whatever failed, after the credential and the config
                have been unwound to the pre-create state.
        """
        mints_api_key = AUTH_TYPE_DESCRIPTORS[request.auth_type].supports_api_key
        conn_name: str | None = None
        if mints_api_key and request.api_key is not None:
            conn_name = await store_provider_api_key(
                self._app_state,
                request.name,
                request.api_key.get_secret_value(),
            )
        try:
            # Config construction stays inside the try: a validation
            # failure here must also unwind the catalog mint above,
            # else the secret is left orphaned with no owning provider.
            new_config = build_provider_config(request, connection_name=conn_name)
            new_providers = {**providers, request.name: new_config}
            await self._validate_and_persist(new_providers)
        except BaseException:
            # Pre-persist failure (build / validate / persist): nothing is
            # durably stored, so drop the minted secret to avoid an
            # orphaned connection with no owning provider.
            if conn_name is not None:
                await unwind(
                    delete_provider_credential(self._app_state, request.name),
                    provider=request.name,
                    step="delete_credential",
                )
            raise
        try:
            await self._allowlist.update_for_create(new_config)
        except BaseException:
            # Post-persist failure: the config (referencing conn_name) is
            # already stored, so roll it back to the pre-create snapshot
            # BEFORE dropping the secret -- otherwise the persisted config
            # would point at a deleted credential. Only drop the secret if
            # the restore actually succeeded; a swallowed restore failure
            # leaves the config persisted, so deleting the credential it
            # references would orphan it.
            restored = await self._restore_providers(providers)
            if restored and conn_name is not None:
                await unwind(
                    delete_provider_credential(self._app_state, request.name),
                    provider=request.name,
                    step="delete_credential",
                )
            raise
        return new_config

    async def _persist_updated_provider(
        self: _ServiceProtocol,
        name: str,
        request: UpdateProviderRequest,
        existing: ProviderConfig,
        providers: Mapping[str, ProviderConfig],
    ) -> ProviderConfig:
        """Apply the update, persist it, and unwind both halves on failure.

        ``apply_update_with_credential`` mutates the catalog in both
        directions: it mints/replaces the secret when an api_key is supplied,
        and DELETES the backing connection when the update clears the key or
        switches to an auth type that has none. The prior secret is snapshotted
        before any of those so a failed persist / allowlist step restores it.

        Args:
            name: Provider being updated.
            request: The update to apply.
            existing: Config as it stands before the update.
            providers: Pre-update snapshot, restored if a later step fails.

        Returns:
            The persisted, updated config.

        Raises:
            Exception: Whatever failed, after the credential and the config
                have been unwound to the pre-update state.
        """
        final_auth_type = (
            request.auth_type if request.auth_type is not None else existing.auth_type
        )
        supports_api_key = AUTH_TYPE_DESCRIPTORS[final_auth_type].supports_api_key
        credential_mutated = (
            supports_api_key and (request.api_key is not None or request.clear_api_key)
        ) or (not supports_api_key and existing.connection_name is not None)
        prior_api_key: str | None = (
            await resolve_provider_api_key(self._app_state, existing)
            if credential_mutated
            else None
        )
        try:
            updated = await apply_update_with_credential(
                self._app_state, name, existing, request
            )
            new_providers = {**providers, name: updated}
            await self._validate_and_persist(new_providers)
        except BaseException:
            await unwind(
                rollback_credential(
                    self._app_state, name, prior_api_key, mutated=credential_mutated
                ),
                provider=name,
                step="rollback_credential",
            )
            raise
        try:
            await self._allowlist.update_for_update(existing, updated, new_providers)
        except BaseException:
            # Roll the config back first; only mutate the credential if the
            # restore succeeded, else the still-persisted updated config
            # would be left referencing a rolled-back credential.
            restored = await self._restore_providers(providers)
            if restored:
                await unwind(
                    rollback_credential(
                        self._app_state,
                        name,
                        prior_api_key,
                        mutated=credential_mutated,
                    ),
                    provider=name,
                    step="rollback_credential",
                )
            raise
        return updated


__all__ = ["ProviderTransactionMixin"]
