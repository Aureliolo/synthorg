"""Settings feature state slice.

Holds the settings service (DB-backed config writes), the settings
read service (MCP/read facade), and the config resolver (DB > env >
code-default precedence reads). The service is constructor-injected;
the resolver and read service are wired once persistence is connected.
All fields are ``None`` until wired; readers guard accordingly.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.infrastructure.services import SettingsReadService
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService


class SettingsStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the settings feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    settings_service: SettingsService | None = None
    settings_read_service: SettingsReadService | None = None
    config_resolver: ConfigResolver | None = None


def config_resolver_of(app_state: AppStateSliceMixin) -> ConfigResolver:
    """Return the wired config resolver, or raise 503.

    The resolver lives on the settings state slice and is wired once
    persistence is connected. Controllers that read DB-backed config
    resolve it through this accessor so the slice lookup is centralised
    here; an unwired resolver surfaces a clean ``ServiceUnavailableError``.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The wired config resolver.

    Raises:
        ServiceUnavailableError: When the resolver is not yet wired.
    """
    return require_service(
        app_state.slice(SettingsStateSlice).config_resolver, "Config Resolver"
    )


def settings_service_of(app_state: AppStateSliceMixin) -> SettingsService:
    """Return the wired settings service, or raise 503.

    The DB-backed settings service lives on the settings state slice and
    is wired once persistence is connected. Controllers that write or
    read settings resolve it through this accessor so the slice lookup
    is centralised here; an unwired service surfaces a clean
    ``ServiceUnavailableError``.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The wired settings service.

    Raises:
        ServiceUnavailableError: When the service is not yet wired.
    """
    return require_service(
        app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
    )


def settings_read_service_of(app_state: AppStateSliceMixin) -> SettingsReadService:
    """Return the wired settings read service, or raise 503.

    Returns:
        The wired settings read service.
    """
    return require_service(
        app_state.slice(SettingsStateSlice).settings_read_service,
        "Settings Read Service",
    )
