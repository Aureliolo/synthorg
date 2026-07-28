# module-kind: service
"""Multi-provider tunnel facade.

Holds one adapter per tunnel provider and delegates the minimal
:class:`~synthorg.integrations.tunnel.protocol.TunnelProvider`
lifecycle to whichever provider the live
``integrations.tunnel_provider`` setting selects, resolved fresh at
every ``start()`` so a Settings change applies without a restart.
Starting while a *different* provider's tunnel is running stops that
tunnel first (single-tunnel invariant across providers).

Token-kind credentials are dashboard-managed: :meth:`store_token`
mints a ``tunnel-<provider>`` connection in the encrypted connection
catalog (delete-then-create, doubling as rotation) and adapters read
it back through a bound source at start time. Env vars remain the
headless fallback inside each adapter.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from functools import partial

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    DuplicateConnectionError,
    TunnelError,
)
from synthorg.integrations.tunnel.devtunnels_adapter import DevTunnelsAdapter
from synthorg.integrations.tunnel.ngrok_adapter import NgrokAdapter
from synthorg.integrations.tunnel.protocol import (
    DeviceLoginPrompt,
    TunnelAdapter,
    TunnelCredentialKind,
    TunnelProviderStatus,
    TunnelSnapshot,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    TUNNEL_CREDENTIAL_CLEARED,
    TUNNEL_CREDENTIAL_STORED,
    TUNNEL_ERROR,
    TUNNEL_PROVIDER_SWITCHED,
)

logger = get_logger(__name__)

type SelectionSource = Callable[[], Awaitable[str | None]]
type CatalogSource = Callable[[], ConnectionCatalog | None]

DEFAULT_PROVIDER_ID = "cloudflare"

_CATALOG_UNAVAILABLE_MESSAGE = (
    "Storing a tunnel credential requires a connected persistence"
    " backend (the encrypted connection catalog is not available)."
)
_TOKEN_CREDENTIAL_KEY = "auth_token"  # noqa: S105 -- key name, not a secret

_CONNECTION_NAME_PREFIX = "tunnel-"


def credential_connection_name(provider_id: str) -> str:
    """Catalog connection name backing a tunnel provider's token.

    Returns:
        The deterministic ``tunnel-<provider>`` connection name.
    """
    return f"{_CONNECTION_NAME_PREFIX}{provider_id}"


def tunnel_provider_id_for_connection(connection_name: str) -> str | None:
    """Inverse of :func:`credential_connection_name`.

    Returns:
        The provider id, or ``None`` when the name does not follow the
        ``tunnel-<provider>`` convention.
    """
    if not connection_name.startswith(_CONNECTION_NAME_PREFIX):
        return None
    return connection_name.removeprefix(_CONNECTION_NAME_PREFIX)


class TunnelManager:
    """Facade the app state holds as its single tunnel provider.

    Args:
        adapters: Concrete adapters in display order.
        default_provider_id: Fallback selection when the settings
            resolver is not (yet) wired.
    """

    def __init__(
        self,
        *,
        adapters: Sequence[TunnelAdapter],
        default_provider_id: str = DEFAULT_PROVIDER_ID,
    ) -> None:
        self._adapters: dict[str, TunnelAdapter] = {
            adapter.provider_id: adapter for adapter in adapters
        }
        if default_provider_id not in self._adapters:
            msg = f"Unknown default tunnel provider '{default_provider_id}'"
            raise TunnelError(msg)
        self._default_provider_id = default_provider_id
        self._selection_source: SelectionSource | None = None
        self._catalog_source: CatalogSource | None = None
        self._active_id: str | None = None
        # Serialises start/stop across providers: without it two
        # concurrent starts against different selections could leave
        # two tunnels running. Eager init: stop() must be safe before
        # start().
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        for adapter in self._adapters.values():
            if isinstance(adapter, NgrokAdapter):
                adapter.bind_credential_source(
                    partial(self._stored_token, adapter.provider_id)
                )

    def bind_runtime(
        self,
        *,
        selection_source: SelectionSource,
        catalog_source: CatalogSource,
    ) -> None:
        """Bind the live settings + catalog lookups (post-construction).

        Both are resolved lazily per call, so late wiring (settings and
        persistence come up after the manager is built) and runtime
        re-wiring are handled without holding stale references.
        """
        self._selection_source = selection_source
        self._catalog_source = catalog_source

    async def start(self) -> str:
        """Start the selected provider's tunnel.

        A tunnel already running on a *different* provider is stopped
        first; on the same provider ``start()`` is idempotent.

        Returns:
            The public URL.

        Raises:
            TunnelError: When the selected provider cannot start.
        """
        async with self._lifecycle_lock:
            selected = await self._selected_id()
            if self._active_id is not None and self._active_id != selected:
                logger.info(
                    TUNNEL_PROVIDER_SWITCHED,
                    from_provider=self._active_id,
                    to_provider=selected,
                )
                await self._adapters[self._active_id].stop()
                self._active_id = None
            url = await self._adapters[selected].start()
            self._active_id = selected
            return url

    async def stop(self) -> None:
        """Stop the active tunnel (no-op when stopped)."""
        async with self._lifecycle_lock:
            if self._active_id is None:
                return
            await self._adapters[self._active_id].stop()
            self._active_id = None

    async def get_url(self) -> str | None:
        """Return the active tunnel's public URL, or ``None``.

        Returns:
            The URL from the active adapter, or ``None`` when stopped.
        """
        if self._active_id is None:
            return None
        return await self._adapters[self._active_id].get_url()

    async def snapshot(self) -> TunnelSnapshot:
        """Full tunnel state for the dashboard card.

        Per-adapter readiness reads are best-effort: a probe failure
        degrades that provider to unavailable rather than failing the
        status endpoint.

        Returns:
            The snapshot (selected provider, active provider, URL,
            per-provider readiness).
        """
        selected = await self._selected_id()
        statuses = [await self._status_of(a) for a in self._adapters.values()]
        return TunnelSnapshot(
            public_url=await self.get_url(),
            selected_provider=selected,
            active_provider=self._active_id,
            providers=tuple(statuses),
        )

    async def provider_status(self, provider_id: str) -> TunnelProviderStatus | None:
        """One provider's live readiness (the connection health source).

        Returns:
            The status, or ``None`` for an unknown provider id.
        """
        adapter = self._adapters.get(provider_id)
        if adapter is None:
            return None
        return await self._status_of(adapter)

    async def store_token(self, provider_id: str, token: str) -> None:
        """Mint (or rotate) the catalog connection holding a token.

        The catalog has no secret-update seam, so this deletes any
        existing connection and recreates it (idempotent; doubles as
        rotation).

        Raises:
            TunnelError: For an unknown provider, a provider that does
                not take a token, or a missing catalog.
        """
        adapter = self._token_adapter(provider_id)
        catalog = self._require_catalog()
        name = credential_connection_name(provider_id)
        with contextlib.suppress(ConnectionNotFoundError):
            await catalog.delete(name)
        await catalog.create(
            name=name,
            connection_type=ConnectionType.TUNNEL,
            auth_method=AuthMethod.API_KEY.value,
            credentials={_TOKEN_CREDENTIAL_KEY: token},
            health_check_enabled=False,
        )
        logger.info(TUNNEL_CREDENTIAL_STORED, provider=adapter.provider_id)

    async def clear_token(self, provider_id: str) -> None:
        """Delete a provider's stored token (idempotent).

        Raises:
            TunnelError: For an unknown provider, a provider that does
                not take a token, or a missing catalog.
        """
        self._token_adapter(provider_id)
        catalog = self._require_catalog()
        try:
            await catalog.delete(credential_connection_name(provider_id))
        except ConnectionNotFoundError:
            return
        logger.info(TUNNEL_CREDENTIAL_CLEARED, provider=provider_id)

    async def begin_device_login(self, provider_id: str) -> DeviceLoginPrompt:
        """Start a device-code login on a ``DEVICE_LOGIN`` provider.

        Returns:
            The login prompt from the adapter.

        Raises:
            TunnelError: For an unknown provider or one whose
                credential is not a device login.
        """
        adapter = self._adapter_or_raise(provider_id)
        if not isinstance(adapter, DevTunnelsAdapter):
            msg = f"Tunnel provider '{provider_id}' has no device login."
            raise TunnelError(msg)
        await self._ensure_device_login_connection(adapter)
        return await adapter.begin_login()

    async def _selected_id(self) -> str:
        """Resolve the live provider selection (settings, then default).

        Returns:
            A provider id guaranteed to exist in the adapter map.
        """
        if self._selection_source is None:
            return self._default_provider_id
        try:
            value = await self._selection_source()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.debug(
                TUNNEL_ERROR,
                phase="selection",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return self._default_provider_id
        if value is None:
            return self._default_provider_id
        if value not in self._adapters:
            logger.warning(
                TUNNEL_ERROR,
                phase="selection",
                selected=value,
                note="unknown tunnel provider selected; using default",
            )
            return self._default_provider_id
        return value

    async def _status_of(self, adapter: TunnelAdapter) -> TunnelProviderStatus:
        # Separate probes so the log names which check failed, and an
        # availability failure does not clobber the credential answer.
        try:
            available, detail = await adapter.availability()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TUNNEL_ERROR,
                phase="status",
                check="availability",
                provider=adapter.provider_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            available, detail = False, "Availability probe failed."
        try:
            credential = await adapter.credential_configured()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TUNNEL_ERROR,
                phase="status",
                check="credential_configured",
                provider=adapter.provider_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            credential = False
        return TunnelProviderStatus(
            provider_id=adapter.provider_id,
            display_name=adapter.display_name,
            credential_kind=adapter.credential_kind,
            available=available,
            detail=detail,
            credential_configured=credential,
        )

    async def _ensure_device_login_connection(self, adapter: TunnelAdapter) -> None:
        """Seed a read-only catalog row for a device-login tunnel provider.

        A token provider gets its ``tunnel-<provider>`` row from
        :meth:`store_token`; a device-login provider stores no secret, so
        without this seed it would never appear in the Connections list. The
        row carries no credentials and is health-checked through the tunnel
        status lookup, not a generic HTTP probe.

        Only :meth:`begin_device_login` may call this. Seeding from a status
        read would mint a connection the operator never asked for and silently
        recreate one they had just deleted, since the dashboard polls status.
        Idempotent and best-effort: an already-seeded row, a missing catalog
        (persistence not yet up), or a create race is swallowed so the login
        call never fails on it.
        """
        if adapter.credential_kind is not TunnelCredentialKind.DEVICE_LOGIN:
            return
        catalog = self._catalog_source() if self._catalog_source else None
        if catalog is None:
            return
        name = credential_connection_name(adapter.provider_id)
        try:
            if await catalog.get(name) is not None:
                return
            await catalog.create(
                name=name,
                connection_type=ConnectionType.TUNNEL,
                auth_method=AuthMethod.CUSTOM.value,
                credentials={},
                health_check_enabled=False,
            )
        except DuplicateConnectionError:
            return
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # WARNING, not DEBUG: an already-seeded row is the expected case
            # (DuplicateConnectionError above), so reaching here means the
            # catalog write genuinely failed and the tunnel would silently
            # never appear in the Connections list until an operator noticed.
            logger.warning(
                TUNNEL_ERROR,
                phase="seed_connection",
                provider=adapter.provider_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _stored_token(self, provider_id: str) -> str | None:
        """Read a provider's dashboard-managed token from the catalog.

        Returns:
            The stored token, or ``None`` when absent / catalog unwired.
        """
        catalog = self._catalog_source() if self._catalog_source else None
        if catalog is None:
            return None
        creds = await catalog.get_credentials_or_none(
            credential_connection_name(provider_id)
        )
        if creds is None:
            return None
        return creds.get(_TOKEN_CREDENTIAL_KEY)

    def _adapter_or_raise(self, provider_id: str) -> TunnelAdapter:
        adapter = self._adapters.get(provider_id)
        if adapter is None:
            msg = f"Unknown tunnel provider '{provider_id}'."
            raise TunnelError(msg)
        return adapter

    def _token_adapter(self, provider_id: str) -> TunnelAdapter:
        adapter = self._adapter_or_raise(provider_id)
        if adapter.credential_kind is not TunnelCredentialKind.TOKEN:
            msg = f"Tunnel provider '{provider_id}' does not take an auth token."
            raise TunnelError(msg)
        return adapter

    def _require_catalog(self) -> ConnectionCatalog:
        catalog = self._catalog_source() if self._catalog_source else None
        if catalog is None:
            raise TunnelError(_CATALOG_UNAVAILABLE_MESSAGE)
        return catalog
