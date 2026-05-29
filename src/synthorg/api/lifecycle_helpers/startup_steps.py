"""Module-level startup steps extracted from ``create_app``.

Relocated out of the ``create_app`` body so the composition root stays a
thin caller. Each step takes its dependencies explicitly (rather than
closing over ``create_app`` locals) and is scheduled into the Litestar
``on_startup`` sequence by the composition root.
"""

from pydantic import ValidationError

from synthorg.api.middleware import set_docs_csp_origins
from synthorg.api.state import AppState
from synthorg.core.error_taxonomy import set_error_docs_base_url
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_BRIDGE_CONFIG_RESOLVE_FAILED
from synthorg.observability.events.settings import SETTINGS_VALUE_RESOLVED
from synthorg.settings.errors import SettingNotFoundError, SettingsEncryptionError

logger = get_logger(__name__)


async def resolve_runtime_security_settings(app_state: AppState) -> None:
    """Resolve operator-overridable API security settings into module state.

    Each security key resolves independently so a validation failure on an
    unrelated ``api.*`` field (e.g. a bad ``request_max_body_size_bytes``)
    does not silently suppress CSP-origin or error-docs overrides. The
    shared ``ApiBridgeConfig`` validator still runs per key by constructing
    a one-field model -- defaults satisfy the remaining fields without
    re-resolving them. Failure branches actively re-write the module global
    to ``ApiBridgeConfig()`` defaults, not just log "fallback": a previous
    app instance (or earlier test on the same worker) may have already
    mutated the global, in which case skipping the write would silently
    keep a stale override instead of the documented default.
    """
    from synthorg.settings.bridge_configs import (  # noqa: PLC0415
        ApiBridgeConfig,
    )
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        config_resolver_of,
    )

    defaults = ApiBridgeConfig()

    if app_state.slice(SettingsStateSlice).config_resolver is None:
        set_docs_csp_origins(defaults.csp_docs_external_origins)
        set_error_docs_base_url(defaults.error_docs_base_url)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="api",
            reason="config_resolver_unavailable",
            fallback="module_defaults",
        )
        return
    resolver = config_resolver_of(app_state)

    try:
        origins_raw = await resolver.get_json("api", "csp_docs_external_origins")
        # Pass the raw JSON shape directly so ApiBridgeConfig sees
        # the unmodified payload. ``tuple(...)`` would coerce a
        # mapping to its keys (and other non-iterable shapes to
        # TypeError), masking the real validation failure. Pydantic
        # returns a ``tuple[str, ...]`` after its own validation
        # runs, so ``set_docs_csp_origins`` still receives the
        # correct shape.
        csp_bridge = ApiBridgeConfig(csp_docs_external_origins=origins_raw)
        set_docs_csp_origins(csp_bridge.csp_docs_external_origins)
    except (
        SettingNotFoundError,
        SettingsEncryptionError,
        ValueError,
        ValidationError,
    ) as exc:
        set_docs_csp_origins(defaults.csp_docs_external_origins)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="api",
            key="csp_docs_external_origins",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback="module_default",
        )

    try:
        url_raw = await resolver.get_str("api", "error_docs_base_url")
        error_bridge = ApiBridgeConfig(error_docs_base_url=url_raw)
        set_error_docs_base_url(error_bridge.error_docs_base_url)
        logger.info(
            SETTINGS_VALUE_RESOLVED,
            namespace="api",
            key="error_docs_base_url",
            value=error_bridge.error_docs_base_url,
        )
    except (
        SettingNotFoundError,
        SettingsEncryptionError,
        ValueError,
        ValidationError,
    ) as exc:
        set_error_docs_base_url(defaults.error_docs_base_url)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="api",
            key="error_docs_base_url",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback="module_default",
        )
