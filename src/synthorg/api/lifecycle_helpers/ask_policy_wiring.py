"""Boot wiring for the ask policy.

Binds the process-global ambient ask-policy provider (read by the prompt build)
from the ``engine.ask_policy_*`` settings at startup. Needs only the settings
service. A settings change re-binds it via ``AskPolicySettingsSubscriber`` with
no restart.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_ask_policy(app_state: AppState) -> None:
    """Build + bind the ask-policy provider from settings at boot.

    A no-op when the settings service is not yet available.
    ``rebuild_and_bind_ask_policy`` always binds a provider (fail-to-on for any
    recoverable error), so a settings fault never leaves the organisation
    silently unable to ask; only a critical error propagates and is re-raised.
    """
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        settings_service_of,
    )

    if app_state.slice(SettingsStateSlice).settings_service is None:
        return
    from synthorg.engine.ask_policy.wiring import (  # noqa: PLC0415
        rebuild_and_bind_ask_policy,
    )

    try:
        config = await rebuild_and_bind_ask_policy(settings_service_of(app_state))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="ask_policy",
            note="ask-policy wiring unavailable; skipped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(
        API_APP_STARTUP,
        service="ask_policy",
        note="wired",
        enabled=config.enabled,
        extra_directive_count=len(config.extra_directives),
    )


__all__ = ["wire_ask_policy"]
