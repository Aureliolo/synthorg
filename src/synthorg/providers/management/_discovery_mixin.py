# module-kind: service
"""Mixin for provider model-discovery operations.

Splits the ``ProviderManagementService`` body so the file owning the core
CRUD logic stays under its size ceiling. The mixin reads ``self._clock`` /
``self._app_state`` / ``self._allowlist`` and the helper methods declared on
``_ServiceProtocol``, all provided by the host service via MRO;
``TYPE_CHECKING``-style Protocol narrowing keeps mypy strict happy.
"""

from typing import Protocol

from synthorg.api.state import AppState
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.clock import Clock
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_DISCOVERY_FAILED
from synthorg.providers.discovery import discover_models, discover_models_strict
from synthorg.providers.discovery_policy import is_url_allowed
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderError
from synthorg.providers.management._credential_helpers import resolve_provider_api_key
from synthorg.providers.management._discovery_auth import (
    build_discovery_headers,
    resolve_discovery_hint,
)
from synthorg.providers.management.allowlist import DiscoveryAllowlistManager
from synthorg.providers.presets import CloudPreset, LocalPreset

logger = get_logger(__name__)


class _ServiceProtocol(Protocol):
    """Subset of ``ProviderManagementService`` accessed by the mixin.

    Declared as a typing ``Protocol`` so mypy strict can verify the mixin is
    composed onto a host class providing these attributes and methods, without
    importing the concrete service (avoiding a circular import).
    """

    _clock: Clock
    _app_state: AppState
    _allowlist: DiscoveryAllowlistManager

    async def get_provider(self, name: str) -> ProviderConfig:
        """Load a provider by name (provided by the host service)."""
        ...

    def _is_self_connection(self, base_url: str) -> bool:
        """Whether *base_url* points at this backend (provided by host)."""
        ...

    async def _apply_discovered_models(
        self,
        name: str,
        original_base_url: str,
        discovered: tuple[ProviderModelConfig, ...],
    ) -> bool:
        """Atomically verify base_url and persist models (provided by host)."""
        ...

    async def discover_models_readonly(
        self,
        name: str,
        *,
        preset_hint: str | None = None,
        strict: bool = False,
    ) -> tuple[ProviderModelConfig, ...]:
        """Read-only discovery (provided by this mixin via MRO)."""
        ...


class ProviderDiscoveryMixin:
    """Model auto-discovery for the provider-management service.

    Composed into ``ProviderManagementService`` via plain Python MRO. The host
    class supplies the attributes and CRUD helpers declared on
    ``_ServiceProtocol``.
    """

    async def _maybe_discover_preset_models(
        self: _ServiceProtocol,
        preset: CloudPreset | LocalPreset,
        base_url: str | None,
        models: tuple[ProviderModelConfig, ...],
        *,
        auth_type: AuthType,
        api_key: str | None = None,
    ) -> tuple[ProviderModelConfig, ...]:
        """Auto-discover models for no-auth or live-discovery presets.

        Two discovery modes:

        - **No-auth local** (``auth_type == NONE``): discover only when no
          seed models were supplied, with no auth headers.
        - **Live-discovery gateway** (``preset.prefer_live_discovery``, an
          API-key ``api_key``, and the base URL still pointing at the
          preset's canonical host): discover even when a curated seed
          exists, sending the key as a Bearer credential, so the full live
          catalogue replaces the seed on create. The Bearer credential is
          sent only when ``base_url`` matches ``preset.default_base_url``;
          a user-overridden host keeps the seed and is never handed the
          key (confused-deputy guard).

        Args:
            preset: Resolved preset definition.
            base_url: Provider base URL (may be user-overridden).
            models: Seed models (may be empty).
            auth_type: Effective auth type.
            api_key: Plaintext API key for authenticated discovery.

        Returns:
            The discovered catalogue on success. For a seeded gateway (or a
            no-auth local probe) a failed discovery falls back to ``models``; a
            seedless live-discovery gateway has no fallback, so its failure
            propagates rather than persisting an empty catalogue.

        Raises:
            ProviderError: When a seedless live-discovery gateway's discovery
                fails (after the transient-retry budget), so the create caller
                surfaces the specific reason instead of a "0 models" success.
        """
        prefer_live = isinstance(preset, CloudPreset) and preset.prefer_live_discovery
        headers: dict[str, str] | None
        if auth_type == AuthType.NONE:
            if models:
                return models
            headers = None
        elif (
            prefer_live
            and auth_type == AuthType.API_KEY
            and api_key
            and base_url == preset.default_base_url
        ):
            headers = {"Authorization": f"Bearer {api_key}"}
        else:
            return models
        if not base_url or self._is_self_connection(base_url):
            return models
        policy = await self._allowlist.load()
        trust = is_url_allowed(base_url, policy)
        if prefer_live and not models:
            # A seedless live-discovery gateway's catalogue IS its models, so a
            # failed round-trip has nothing to fall back to and must surface the
            # specific reason (bad key / 429 / unreachable) rather than persist a
            # provider with zero models indistinguishable from a genuinely empty
            # one. A transient failure is retried first; a terminal one propagates.
            try:
                return await discover_models_strict(
                    base_url,
                    preset.name,
                    headers=headers,
                    trust_url=trust,
                    clock=self._clock,
                )
            except ProviderError as exc:
                logger.warning(
                    PROVIDER_DISCOVERY_FAILED,
                    provider=preset.name,
                    reason="live_discovery_failed_on_create",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
        # A seeded gateway (curated ``default_models``) or a no-auth local probe
        # degrades to the seed on a failed discovery: the seed is a valid
        # create-time catalogue, so a transient blip need not fail the save.
        discovered = await discover_models(
            base_url,
            preset.name,
            headers=headers,
            trust_url=trust,
        )
        return discovered or models

    async def discover_models_readonly(
        self: _ServiceProtocol,
        name: str,
        *,
        preset_hint: str | None = None,
        strict: bool = False,
    ) -> tuple[ProviderModelConfig, ...]:
        """Discover a provider's live models without persisting anything.

        The read-only core of :meth:`discover_models_for_provider`: it
        resolves the endpoint, builds auth headers, applies the SSRF
        trust decision, and queries the provider. It performs NO
        persistence, so the periodic model-refresh probe can use it for
        detection without mutating the configured model list.

        Args:
            name: Provider name.
            preset_hint: Optional preset name for endpoint selection.
            strict: When True, a failed round-trip raises the specific
                :class:`ProviderError` (after a transient-retry budget) rather
                than degrading to an empty tuple. The user-initiated re-sync
                endpoint sets this so an operator sees the real reason; the
                background staleness probe leaves it False.

        Returns:
            The discovered models, or an empty tuple when the provider
            has no base URL, targets this backend, or (when not ``strict``)
            discovery fails or returns nothing.

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            ProviderError: When ``strict`` and the discovery round-trip fails.
        """
        config = await self.get_provider(name)

        if config.base_url is None:
            logger.info(
                PROVIDER_DISCOVERY_FAILED,
                provider=name,
                reason="no_base_url",
            )
            return ()

        if self._is_self_connection(config.base_url):
            return ()

        resolved_hint = resolve_discovery_hint(config, preset_hint)
        api_key = await resolve_provider_api_key(self._app_state, config)
        headers = build_discovery_headers(config, api_key)
        policy = await self._allowlist.load()
        trust = is_url_allowed(config.base_url, policy)
        if strict:
            return await discover_models_strict(
                config.base_url,
                resolved_hint,
                headers=headers,
                trust_url=trust,
                clock=self._clock,
            )
        return await discover_models(
            config.base_url,
            resolved_hint,
            headers=headers,
            trust_url=trust,
        )

    async def discover_models_for_provider(
        self: _ServiceProtocol,
        name: str,
        *,
        preset_hint: str | None = None,
        strict: bool = False,
    ) -> tuple[ProviderModelConfig, ...]:
        """Discover and update models for an existing provider.

        Args:
            name: Provider name.
            preset_hint: Optional preset name for endpoint selection.
            strict: When True, a failed discovery raises the specific
                :class:`ProviderError` instead of returning an empty tuple. The
                user-initiated re-sync endpoint sets this so the failure reaches
                the operator; internal callers leave it False.

        Returns:
            A tuple of discovered ``ProviderModelConfig`` instances (may
            be empty if the provider has no base URL, targets the backend
            itself, or discovery returns nothing).

        Raises:
            ProviderNotFoundError: If the provider does not exist.
            ProviderError: When ``strict`` and the discovery round-trip fails.
        """
        # Capture the pre-discovery base_url so ``_apply_discovered_models``
        # can detect a concurrent change (TOCTOU): if the provider's
        # base_url is updated while discovery is in flight, the apply is
        # refused and the discovered models are dropped.
        config = await self.get_provider(name)
        discovered = await self.discover_models_readonly(
            name, preset_hint=preset_hint, strict=strict
        )

        if discovered and config.base_url is not None:
            applied = await self._apply_discovered_models(
                name,
                config.base_url,
                discovered,
            )
            if not applied:
                return ()

        return discovered
