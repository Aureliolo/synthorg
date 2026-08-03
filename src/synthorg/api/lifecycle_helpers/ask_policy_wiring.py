"""Boot wiring for the ask policy.

Binds the process-global ambient ask-policy provider (read by the prompt build)
from the ``engine.ask_policy_*`` settings at startup. Needs only the settings
service. A settings change re-binds it via ``AskPolicySettingsSubscriber`` with
no restart.
"""

from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_ask_policy(app_state: AppState) -> None:
    """Build + bind the ask-policy provider from settings at boot.

    Binds the shipped default when the settings service is not available yet.
    Returning without binding would be a silent fail-to-OFF: an unbound
    provider means ``should_inject_ask_policy`` is false and the standing
    directive never reaches a prompt, which is precisely the failure this
    subsystem exists to prevent. Only a critical error propagates.
    """
    from synthorg.engine.ask_policy.models import AskPolicyConfig  # noqa: PLC0415
    from synthorg.engine.ask_policy.wiring import (  # noqa: PLC0415
        bind_ask_policy_config,
        rebuild_and_bind_ask_policy,
    )
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        settings_service_of,
    )

    if app_state.slice(SettingsStateSlice).settings_service is None:
        config = bind_ask_policy_config(AskPolicyConfig())
        logger.info(
            API_APP_STARTUP,
            service="ask_policy",
            note="settings service unavailable; bound the shipped default",
            enabled=config.enabled,
        )
        return

    # No try/except: the rebuild reports a settings fault through its own
    # logging and never raises for one, so a handler here would be dead code
    # for the failure it would claim to cover. A critical error propagates.
    config_or_kept = await rebuild_and_bind_ask_policy(settings_service_of(app_state))
    # ``None`` means a read failed and an existing binding was kept, which
    # cannot happen at boot (nothing is bound yet) but is cheap to honour.
    if config_or_kept is None:
        return
    logger.info(
        API_APP_STARTUP,
        service="ask_policy",
        note="wired",
        enabled=config_or_kept.enabled,
        extra_directive_count=len(config_or_kept.extra_directives),
    )


__all__ = ["wire_ask_policy"]
