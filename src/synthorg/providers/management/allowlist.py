"""Discovery allowlist manager -- SSRF bypass for trusted host:port pairs.

Manages the dynamic allowlist of ``host:port`` pairs that are trusted
for provider model discovery.  The allowlist is persisted in the
settings system and updated automatically during provider lifecycle
operations (create, update, delete).
"""

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.config.schema import ProviderConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_DISCOVERY_ALLOWLIST_CORRUPTED,
    PROVIDER_DISCOVERY_ALLOWLIST_SEEDED,
    PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
)
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
    build_seed_allowlist,
    extract_host_port,
    seed_from_presets,
)

if TYPE_CHECKING:
    # ConfigResolver / SettingsService are concrete classes injected
    # duck-typed via ``mock_of[...]`` in tests; a runtime import would
    # make typeguard enforce a nominal isinstance the mocks fail.
    from synthorg.settings.resolver import ConfigResolver
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


class DiscoveryAllowlistManager:
    """Manages the provider discovery SSRF allowlist.

    All mutation methods expect the caller to hold the service lock
    when atomicity with provider config changes is required.  The
    public ``add_entry`` and ``remove_entry`` methods are standalone
    and must be called under the lock by the service layer.

    Args:
        settings_service: Settings persistence layer.
        config_resolver: Typed config accessor.
    """

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        config_resolver: ConfigResolver,
    ) -> None:
        self._settings_service = settings_service
        self._config_resolver = config_resolver

    async def load(self) -> ProviderDiscoveryPolicy:
        """Load the current discovery policy from settings.

        If no persisted policy exists or the stored value is
        corrupted, seeds the allowlist from presets and installed
        providers, persists it, and returns the new policy.

        Returns:
            Current discovery policy.
        """
        result = await self._settings_service.get(
            "providers",
            "discovery_allowlist",
        )
        if result.value:
            try:
                data = json.loads(result.value)
                return ProviderDiscoveryPolicy.model_validate(data)
            except json.JSONDecodeError, ValidationError:
                logger.warning(
                    PROVIDER_DISCOVERY_ALLOWLIST_CORRUPTED,
                    raw_length=len(result.value),
                )

        # Seed from presets + installed providers.
        providers = await self._config_resolver.get_provider_configs()
        seeds = build_seed_allowlist(providers)
        policy = ProviderDiscoveryPolicy(host_port_allowlist=seeds)
        await self._persist(policy)
        logger.info(
            PROVIDER_DISCOVERY_ALLOWLIST_SEEDED,
            entry_count=len(policy.host_port_allowlist),
        )
        return policy

    async def _persist(self, policy: ProviderDiscoveryPolicy) -> None:
        """Serialize and persist the discovery policy to settings.

        Args:
            policy: Policy to persist.
        """
        await self._settings_service.set(
            "providers",
            "discovery_allowlist",
            json.dumps(policy.model_dump(mode="json")),
        )

    async def update_for_create(self, config: ProviderConfig) -> None:
        """Add a newly created provider's host:port to the allowlist.

        Best-effort: logs and returns on failure without raising.

        Args:
            config: The created provider config.
        """
        if config.base_url is None:
            return
        hp = extract_host_port(config.base_url)
        if hp is None:
            return
        try:
            policy = await self.load()
            if hp in policy.host_port_allowlist:
                return
            new_entries = (*policy.host_port_allowlist, hp)
            updated = ProviderDiscoveryPolicy(
                host_port_allowlist=new_entries,
                block_private_ips=policy.block_private_ips,
            )
            await self._persist(updated)
            logger.info(
                PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
                action="add",
                host_port=hp,
                entry_count=len(updated.host_port_allowlist),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
                action="add_failed",
                host_port=hp,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def update_for_delete(
        self,
        removed_config: ProviderConfig,
        remaining_providers: dict[str, ProviderConfig],
    ) -> None:
        """Remove a deleted provider's host:port from the allowlist.

        Only removes the entry if no other provider still uses the
        same host:port.  Best-effort: logs and returns on failure.

        Args:
            removed_config: The deleted provider's config.
            remaining_providers: All remaining provider configs.
        """
        if removed_config.base_url is None:
            return
        hp = extract_host_port(removed_config.base_url)
        if hp is None:
            return
        # Keep preset-seeded entries even if no provider uses them.
        if hp in seed_from_presets():
            return
        # Check whether any remaining provider shares this host:port.
        for config in remaining_providers.values():
            if config.base_url is not None:
                other_hp = extract_host_port(config.base_url)
                if other_hp == hp:
                    return
        try:
            policy = await self.load()
            if hp not in policy.host_port_allowlist:
                return
            new_entries = tuple(e for e in policy.host_port_allowlist if e != hp)
            updated = ProviderDiscoveryPolicy(
                host_port_allowlist=new_entries,
                block_private_ips=policy.block_private_ips,
            )
            await self._persist(updated)
            logger.info(
                PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
                action="remove",
                host_port=hp,
                entry_count=len(updated.host_port_allowlist),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
                action="remove_failed",
                host_port=hp,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def update_for_update(
        self,
        old_config: ProviderConfig,
        new_config: ProviderConfig,
        providers_after_update: dict[str, ProviderConfig],
    ) -> None:
        """Update the allowlist when a provider's base_url changes.

        Best-effort: logs and returns on failure.

        Args:
            old_config: Previous provider config.
            new_config: Updated provider config.
            providers_after_update: All provider configs after the
                update has been applied.
        """
        old_hp = extract_host_port(old_config.base_url) if old_config.base_url else None
        new_hp = extract_host_port(new_config.base_url) if new_config.base_url else None
        if old_hp == new_hp:
            return

        try:
            policy = await self.load()
            entries = _compute_update_entries(
                policy.host_port_allowlist,
                old_hp,
                new_hp,
                providers_after_update,
            )
            if entries == policy.host_port_allowlist:
                return
            updated = ProviderDiscoveryPolicy(
                host_port_allowlist=entries,
                block_private_ips=policy.block_private_ips,
            )
            await self._persist(updated)
            logger.info(
                PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
                action="update",
                old_host_port=old_hp,
                new_host_port=new_hp,
                entry_count=len(updated.host_port_allowlist),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
                action="update_failed",
                old_host_port=old_hp,
                new_host_port=new_hp,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def add_entry(
        self,
        host_port: str,
    ) -> ProviderDiscoveryPolicy:
        """Add a custom host:port entry to the discovery allowlist.

        Args:
            host_port: Entry to add (e.g. ``"my-server:8080"``).

        Returns:
            Updated policy.
        """
        policy = await self.load()
        normalized = host_port.lower()
        if normalized in policy.host_port_allowlist:
            return policy
        new_entries = (*policy.host_port_allowlist, normalized)
        updated = ProviderDiscoveryPolicy(
            host_port_allowlist=new_entries,
            block_private_ips=policy.block_private_ips,
        )
        await self._persist(updated)
        logger.info(
            PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
            action="add_custom",
            host_port=normalized,
            entry_count=len(updated.host_port_allowlist),
        )
        return updated

    async def remove_entry(
        self,
        host_port: str,
    ) -> ProviderDiscoveryPolicy:
        """Remove a host:port entry from the discovery allowlist.

        Args:
            host_port: Entry to remove.

        Returns:
            Updated policy.
        """
        policy = await self.load()
        normalized = host_port.lower()
        if normalized not in policy.host_port_allowlist:
            return policy
        new_entries = tuple(e for e in policy.host_port_allowlist if e != normalized)
        updated = ProviderDiscoveryPolicy(
            host_port_allowlist=new_entries,
            block_private_ips=policy.block_private_ips,
        )
        await self._persist(updated)
        logger.info(
            PROVIDER_DISCOVERY_ALLOWLIST_UPDATED,
            action="remove_custom",
            host_port=normalized,
            entry_count=len(updated.host_port_allowlist),
        )
        return updated


def _compute_update_entries(
    current: tuple[str, ...],
    old_hp: str | None,
    new_hp: str | None,
    providers_after_update: dict[str, ProviderConfig],
) -> tuple[str, ...]:
    """Compute allowlist entries after a provider URL change.

    Removes the old entry if unshared and not a preset seed, and
    adds the new entry if not already present.

    Args:
        current: Current allowlist entries.
        old_hp: Old host:port (may be ``None``).
        new_hp: New host:port (may be ``None``).
        providers_after_update: Provider configs after the update.

    Returns:
        Updated tuple of entries.
    """
    entries = list(current)
    preset_seeds = seed_from_presets()

    if old_hp is not None and old_hp in entries and old_hp not in preset_seeds:
        shared = any(
            extract_host_port(c.base_url) == old_hp
            for c in providers_after_update.values()
            if c.base_url is not None
        )
        if not shared:
            entries = [e for e in entries if e != old_hp]

    if new_hp is not None and new_hp not in entries:
        entries.append(new_hp)

    return tuple(entries)
