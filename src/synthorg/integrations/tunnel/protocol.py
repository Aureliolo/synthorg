"""Tunnel provider protocols and shared tunnel types.

The tunnel subsystem is multi-provider: a :class:`TunnelManager`
facade selects one concrete :class:`TunnelAdapter` (Cloudflare quick
tunnel, ngrok, GitHub Dev Tunnels) per the live
``integrations.tunnel_provider`` setting. The minimal
:class:`TunnelProvider` lifecycle contract is what the API controller,
MCP facade, and shutdown hook drive; adapters additionally describe
their identity, availability, and credential shape so the dashboard
can render a provider picker generically.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr


class TunnelCredentialKind(StrEnum):
    """How a tunnel provider authenticates.

    ``NONE``: works anonymously (no account).
    ``TOKEN``: an auth token pasted in the dashboard (stored in the
    encrypted connection catalog; an env var is the headless fallback).
    ``DEVICE_LOGIN``: an interactive device-code login owned by the
    provider's own CLI.
    """

    NONE = "none"
    TOKEN = "token"  # noqa: S105 -- credential-kind label, not a secret
    DEVICE_LOGIN = "device_login"


class TunnelProviderStatus(BaseModel):
    """One provider's identity + live readiness for the dashboard.

    Attributes:
        provider_id: Stable machine id (``cloudflare`` / ``ngrok`` /
            ``devtunnels``); doubles as the settings enum value.
        display_name: Human-readable provider name.
        credential_kind: How the provider authenticates.
        available: Whether the provider can run in this deployment
            (binary present or downloadable, library importable).
        detail: Human-readable reason when unavailable, or a setup
            hint; ``None`` when nothing needs saying.
        credential_configured: Whether the provider's credential is in
            place (always ``True`` for ``NONE``-kind providers).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_id: NotBlankStr
    display_name: NotBlankStr
    credential_kind: TunnelCredentialKind
    available: bool
    detail: str | None = None
    credential_configured: bool


class TunnelSnapshot(BaseModel):
    """Full tunnel state for the dashboard card.

    Attributes:
        public_url: Active tunnel URL, or ``None`` when stopped.
        selected_provider: The provider the next start will use (the
            live ``integrations.tunnel_provider`` setting).
        active_provider: The provider currently running a tunnel, or
            ``None`` when stopped.
        providers: Per-provider readiness, in stable display order.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    public_url: str | None = None
    selected_provider: NotBlankStr
    active_provider: NotBlankStr | None = None
    providers: tuple[TunnelProviderStatus, ...]


class DeviceLoginPrompt(BaseModel):
    """Device-code login instructions for a ``DEVICE_LOGIN`` provider.

    Attributes:
        verification_uri: Where the operator completes the login.
        user_code: The one-time code to enter there.
        already_logged_in: ``True`` when no login was needed; the URI
            and code are ``None`` in that case.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verification_uri: str | None = None
    user_code: str | None = None
    already_logged_in: bool = False


@runtime_checkable
class TunnelProvider(Protocol):
    """Public URL tunnel for webhook reception during local dev.

    The minimal lifecycle contract shared by every adapter and by the
    :class:`~synthorg.integrations.tunnel.manager.TunnelManager`
    facade the app state actually holds.
    """

    async def start(self) -> str:
        """Start the tunnel.

        Returns:
            The public URL.

        Raises:
            TunnelError: If the tunnel cannot be started.
        """
        ...

    async def stop(self) -> None:
        """Stop and clean up the tunnel."""
        ...

    async def get_url(self) -> str | None:
        """Return the current public URL, or ``None`` if not running."""
        ...


@runtime_checkable
class TunnelAdapter(TunnelProvider, Protocol):
    """One concrete tunneling backend the manager can drive."""

    @property
    def provider_id(self) -> str:
        """Stable machine id (settings enum value)."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    def credential_kind(self) -> TunnelCredentialKind:
        """How this provider authenticates."""
        ...

    async def availability(self) -> tuple[bool, str | None]:
        """Whether the adapter can run here.

        Returns:
            ``(available, detail)`` -- ``detail`` carries the reason
            when unavailable (or a setup hint), else ``None``.
        """
        ...

    async def credential_configured(self) -> bool:
        """Whether the provider's credential is in place."""
        ...
